"""
Property Service API
Property content with 4-tier override hierarchy.
PMS(10) < PM(20) < Wander(30) < Admin(40) — higher priority wins.

Endpoints:
  GET  /properties/{id}           - Get effective property data
  GET  /properties                - List/filter properties
  POST /properties                - Create property
  POST /properties/{id}/overrides - Set field override
  GET  /properties/{id}/overrides/conflicts - List conflicts
  POST /properties/{id}/overrides/conflicts/{cid}/resolve - Resolve conflict
  GET  /properties/{id}/fees      - Get fee config
  GET  /properties/{id}/policies  - Get policies
"""
from fastapi import FastAPI, Query, HTTPException, Path
from pydantic import BaseModel
from datetime import date
from typing import Optional, List, Any
import json
import logging

from shared.database import get_db_cursor, get_redis, init_schema
from shared.events import emit

logger = logging.getLogger("property")

app = FastAPI(title="Property Service", version="1.0.0")

PRIORITY_LABELS = {10: "PMS", 20: "PM", 30: "Wander", 40: "Admin"}
VALID_PRIORITIES = {10, 20, 30, 40}


# --- Models ---

class CreatePropertyRequest(BaseModel):
    name: str
    organization_id: Optional[str] = None
    description: Optional[str] = None
    property_type: str = "HOUSE"
    status: str = "ACTIVE"
    is_bookable: bool = False
    bedrooms: int = 0
    bathrooms: float = 0
    max_guests: int = 1
    beds: int = 0
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    country: str = "US"
    postal_code: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    region: Optional[str] = None
    timezone: str = "America/New_York"
    management_type: str = "BRANDED"
    pms_type: Optional[str] = None
    amenities: list = []
    currency: str = "USD"


class OverrideRequest(BaseModel):
    field: str
    from_value: Optional[Any] = None
    to_value: Any
    priority: int = 20
    actor: str = "system"
    note: Optional[str] = None


class ResolveConflictRequest(BaseModel):
    resolution: str  # KEEP_OVERRIDE, ACCEPT_NEW, NEW_OVERRIDE
    actor: str = "system"
    new_value: Optional[Any] = None


# --- Override Helpers ---

def _apply_overrides(property_data: dict, overrides: list) -> dict:
    """Apply overrides to base property data. Higher priority wins per field."""
    effective = dict(property_data)
    sorted_overrides = sorted(overrides, key=lambda o: o['priority'])
    
    has_overrides = len(overrides) > 0
    for ovr in sorted_overrides:
        field = ovr['field']
        if field in effective:
            effective[field] = json.loads(ovr['to_value']) if isinstance(ovr['to_value'], str) else ovr['to_value']
    
    effective['has_overrides'] = has_overrides
    return effective


def _cache_key(property_id: str) -> str:
    return f"prop:effective:{property_id}"


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/property/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "property"}


@app.post("/properties")
async def create_property(req: CreatePropertyRequest):
    """Create a new property."""
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            INSERT INTO property (name, organization_id, description, property_type, status,
                is_bookable, bedrooms, bathrooms, max_guests, beds,
                address_line1, city, state, country, postal_code,
                latitude, longitude, region, timezone, management_type, pms_type,
                amenities, currency)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (
            req.name, req.organization_id, req.description, req.property_type, req.status,
            req.is_bookable, req.bedrooms, req.bathrooms, req.max_guests, req.beds,
            req.address_line1, req.city, req.state, req.country, req.postal_code,
            req.latitude, req.longitude, req.region, req.timezone, req.management_type, req.pms_type,
            json.dumps(req.amenities), req.currency,
        ))
        row = cur.fetchone()
        conn.commit()
    
    emit("property.created", "property", str(row['id']), context={
        "organization_id": req.organization_id,
    })
    
    return {"id": str(row['id']), "name": req.name, "created_at": row['created_at'].isoformat()}


@app.get("/properties/{property_id}")
async def get_property(property_id: str):
    """Get effective property data (base + overrides applied)."""
    r = get_redis()
    
    # Try cache
    if r:
        cached = r.get(_cache_key(property_id))
        if cached:
            return json.loads(cached)
    
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM property WHERE id = %s", (property_id,))
        prop = cur.fetchone()
        if not prop:
            raise HTTPException(status_code=404, detail={"code": "PROPERTY_NOT_FOUND", "message": "Property not found"})
        
        # Get overrides
        cur.execute(
            "SELECT field, to_value, priority, actor FROM property_override WHERE property_id = %s ORDER BY priority",
            (property_id,)
        )
        overrides = cur.fetchall()
        
        # Check for conflicts
        cur.execute(
            "SELECT COUNT(*) as cnt FROM property_override_conflict WHERE property_id = %s AND status = 'PENDING'",
            (property_id,)
        )
        conflict_count = cur.fetchone()['cnt']
    
    # Convert to serializable dict
    prop_data = {}
    for key, val in prop.items():
        if hasattr(val, 'isoformat'):
            prop_data[key] = val.isoformat()
        elif isinstance(val, (dict, list)):
            prop_data[key] = val
        else:
            prop_data[key] = val
    
    # Convert UUID
    prop_data['id'] = str(prop_data['id'])
    if prop_data.get('organization_id'):
        prop_data['organization_id'] = str(prop_data['organization_id'])
    
    effective = _apply_overrides(prop_data, overrides)
    effective['has_conflicts'] = conflict_count > 0
    
    # Cache effective data
    if r:
        try:
            r.set(_cache_key(property_id), json.dumps(effective, default=str), ex=3600)
        except Exception:
            pass
    
    return effective


@app.get("/properties")
async def list_properties(
    organization_id: Optional[str] = None,
    region: Optional[str] = None,
    status: Optional[str] = None,
    is_bookable: Optional[bool] = None,
    management_type: Optional[str] = None,
    pms_type: Optional[str] = None,
    limit: int = Query(20, le=100),
    cursor: Optional[str] = None,
):
    """List properties with filters."""
    conditions = []
    params = []
    
    if organization_id:
        conditions.append("organization_id = %s")
        params.append(organization_id)
    if region:
        conditions.append("region = %s")
        params.append(region)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if is_bookable is not None:
        conditions.append("is_bookable = %s")
        params.append(is_bookable)
    if management_type:
        conditions.append("management_type = %s")
        params.append(management_type)
    if pms_type:
        conditions.append("pms_type = %s")
        params.append(pms_type)
    if cursor:
        conditions.append("id > %s")
        params.append(cursor)
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit + 1)
    
    with get_db_cursor() as (cur, conn):
        cur.execute(f"SELECT * FROM property {where} ORDER BY id LIMIT %s", params)
        rows = cur.fetchall()
    
    has_more = len(rows) > limit
    data = rows[:limit]
    
    results = []
    for row in data:
        item = {}
        for key, val in row.items():
            if hasattr(val, 'isoformat'):
                item[key] = val.isoformat()
            else:
                item[key] = val
        item['id'] = str(item['id'])
        if item.get('organization_id'):
            item['organization_id'] = str(item['organization_id'])
        results.append(item)
    
    return {
        "data": results,
        "next_cursor": str(data[-1]['id']) if has_more else None,
    }


@app.post("/properties/{property_id}/overrides")
async def create_override(property_id: str, req: OverrideRequest):
    """Set a field override at a given priority."""
    if req.priority not in VALID_PRIORITIES:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_PRIORITY", "message": f"Priority must be one of {sorted(VALID_PRIORITIES)}"
        })
    
    with get_db_cursor() as (cur, conn):
        # Check property exists
        cur.execute("SELECT id FROM property WHERE id = %s", (property_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail={"code": "PROPERTY_NOT_FOUND", "message": "Property not found"})
        
        # Check for existing override at same priority
        cur.execute(
            "SELECT id FROM property_override WHERE property_id = %s AND field = %s AND priority = %s",
            (property_id, req.field, req.priority)
        )
        if cur.fetchone():
            raise HTTPException(status_code=409, detail={
                "code": "OVERRIDE_EXISTS", "message": f"Override already exists for field '{req.field}' at priority {req.priority}"
            })
        
        cur.execute("""
            INSERT INTO property_override (property_id, field, from_value, to_value, priority, actor, note)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            RETURNING id
        """, (
            property_id, req.field,
            json.dumps(req.from_value) if req.from_value is not None else None,
            json.dumps(req.to_value),
            req.priority, req.actor, req.note,
        ))
        override = cur.fetchone()
        conn.commit()
    
    # Invalidate cache
    r = get_redis()
    if r:
        r.delete(_cache_key(property_id))
    
    emit("property.override_set", "property", property_id, actor=req.actor, context={
        "field": req.field, "priority": req.priority,
    })
    
    return {"id": str(override['id']), "status": "created"}


@app.get("/properties/{property_id}/overrides/conflicts")
async def list_conflicts(property_id: str):
    """List pending override conflicts for a property."""
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            SELECT c.id, c.field, c.old_base, c.new_base, c.status, c.created_at,
                   o.to_value, o.priority, o.actor
            FROM property_override_conflict c
            JOIN property_override o ON c.override_id = o.id
            WHERE c.property_id = %s AND c.status = 'PENDING'
            ORDER BY c.created_at
        """, (property_id,))
        conflicts = cur.fetchall()
    
    return {
        "conflicts": [
            {
                "id": str(c['id']),
                "field": c['field'],
                "old_base": c['old_base'],
                "new_base": c['new_base'],
                "current_override": c['to_value'],
                "override_priority": c['priority'],
                "override_actor": c['actor'],
                "status": c['status'],
                "created_at": c['created_at'].isoformat(),
            }
            for c in conflicts
        ]
    }


@app.post("/properties/{property_id}/overrides/conflicts/{conflict_id}/resolve")
async def resolve_conflict(property_id: str, conflict_id: str, req: ResolveConflictRequest):
    """Resolve an override conflict."""
    valid_resolutions = {"KEEP_OVERRIDE", "ACCEPT_NEW", "NEW_OVERRIDE"}
    if req.resolution not in valid_resolutions:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_RESOLUTION", "message": f"Resolution must be one of {valid_resolutions}"
        })
    
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "SELECT id, override_id, new_base FROM property_override_conflict WHERE id = %s AND property_id = %s",
            (conflict_id, property_id)
        )
        conflict = cur.fetchone()
        if not conflict:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Conflict not found"})
        
        if req.resolution == "ACCEPT_NEW":
            # Drop the override
            cur.execute("DELETE FROM property_override WHERE id = %s", (conflict['override_id'],))
        elif req.resolution == "KEEP_OVERRIDE":
            # Update from_value to match new base
            cur.execute(
                "UPDATE property_override SET from_value = %s WHERE id = %s",
                (json.dumps(conflict['new_base']), conflict['override_id'])
            )
        elif req.resolution == "NEW_OVERRIDE" and req.new_value is not None:
            cur.execute(
                "UPDATE property_override SET to_value = %s, from_value = %s WHERE id = %s",
                (json.dumps(req.new_value), json.dumps(conflict['new_base']), conflict['override_id'])
            )
        
        cur.execute("""
            UPDATE property_override_conflict
            SET status = 'RESOLVED', resolution = %s, resolved_at = NOW(), resolved_by = %s
            WHERE id = %s
        """, (req.resolution, req.actor, conflict_id))
        conn.commit()
    
    # Invalidate cache
    r = get_redis()
    if r:
        r.delete(_cache_key(property_id))
    
    return {"status": "resolved", "resolution": req.resolution}


@app.get("/properties/{property_id}/fees")
async def get_fees(property_id: str):
    """Get fee configuration for a property."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT currency FROM property WHERE id = %s", (property_id,))
        prop = cur.fetchone()
        if not prop:
            raise HTTPException(status_code=404, detail={"code": "PROPERTY_NOT_FOUND", "message": "Property not found"})
        
        cur.execute(
            "SELECT kind, label, amount_cents, per, required, conditions FROM property_fee WHERE property_id = %s",
            (property_id,)
        )
        fees = cur.fetchall()
    
    return {
        "property_id": property_id,
        "currency": prop['currency'],
        "fees": [dict(f) for f in fees],
    }


@app.get("/properties/{property_id}/policies")
async def get_policies(property_id: str):
    """Get policies and cancellation policy for a property."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM property WHERE id = %s", (property_id,))
        prop = cur.fetchone()
        if not prop:
            raise HTTPException(status_code=404, detail={"code": "PROPERTY_NOT_FOUND", "message": "Property not found"})
        
        cur.execute(
            "SELECT name, tiers FROM cancellation_policy WHERE property_id = %s ORDER BY created_at DESC LIMIT 1",
            (property_id,)
        )
        cancel_policy = cur.fetchone()
    
    return {
        "property_id": property_id,
        "max_guests": prop['max_guests'],
        "house_rules": prop['house_rules'],
        "cancellation_policy": {
            "name": cancel_policy['name'] if cancel_policy else "FLEXIBLE",
            "tiers": cancel_policy['tiers'] if cancel_policy else [
                {"days_before": 7, "refund_percent": 100},
                {"days_before": 0, "refund_percent": 50},
            ],
        },
    }


@app.post("/properties/{property_id}/cache/flush")
async def flush_cache(property_id: str):
    """Flush cached effective data for a property."""
    r = get_redis()
    if r:
        r.delete(_cache_key(property_id))
    return {"status": "flushed"}
