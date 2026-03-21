"""
Property Service — Property data with 4-tier override hierarchy.

Base URL: /api/v1/properties
Source of Truth: Owns Wander view of properties.

Override hierarchy: PMS (10) < PM (20) < Wander (30) < Admin (40)
Higher priority wins per field.

Endpoints:
  GET    /properties/{id}                                    - Get effective property
  GET    /properties                                         - List properties
  POST   /properties                                         - Create property
  POST   /properties/{id}/overrides                          - Set override
  GET    /properties/{id}/overrides/conflicts                - Get conflicts
  POST   /properties/{id}/overrides/conflicts/{cid}/resolve  - Resolve conflict
  GET    /properties/{id}/fees                               - Get fees
  POST   /properties/{id}/fees                               - Set fee
  GET    /properties/{id}/policies                           - Get policies
  POST   /properties/{id}/policies                           - Set policies
  GET    /properties/{id}/sources                            - Get data sources
  POST   /properties/{id}/cache/flush                        - Flush cache
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.shared.events import emit_event
from services.property.store import PropertyStore, TIER_BY_NAME


app = FastAPI(title="Property Service", version="1.0.0")

_store: Optional[PropertyStore] = None


def get_store() -> PropertyStore:
    global _store
    if _store is None:
        _store = PropertyStore(Database(), get_cache())
    return _store


def set_store(store: PropertyStore):
    global _store
    _store = store


# --- Request Models ---

class CreatePropertyRequest(BaseModel):
    id: Optional[str] = None
    organization_id: Optional[str] = None
    name: str
    description: Optional[str] = None
    property_type: str = "HOUSE"
    status: str = "ACTIVE"
    is_bookable: bool = True
    bedrooms: int = 0
    bathrooms: float = 0
    max_guests: int = 1
    address_line1: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zip: Optional[str] = None
    country: str = "US"
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    timezone: str = "America/New_York"
    pms_type: Optional[str] = None
    external_id: Optional[str] = None
    currency: str = "USD"


class OverrideRequest(BaseModel):
    field: str
    from_value: Optional[str] = None
    to_value: str
    priority: int = 20  # Default PM level
    actor: Optional[str] = None
    note: Optional[str] = None


class ResolveConflictRequest(BaseModel):
    resolution: str  # KEEP_OVERRIDE, ACCEPT_NEW, NEW_OVERRIDE


class FeeRequest(BaseModel):
    kind: str
    label: str
    amount_cents: int
    per: str = "PER_STAY"
    required: bool = True
    conditions: Optional[dict] = None


class PolicyRequest(BaseModel):
    check_in_time: str = "16:00"
    check_out_time: str = "11:00"
    min_nights: int = 1
    max_nights: int = 365
    cancellation_policy: str = "FLEXIBLE"
    cancellation_tiers: Optional[list] = None
    pets_allowed: bool = False
    smoking_allowed: bool = False
    events_allowed: bool = False
    house_rules: Optional[str] = None


# --- Endpoints ---

@app.post("/api/v1/properties")
def create_property(request: CreatePropertyRequest):
    """Create a new property."""
    store = get_store()
    prop = store.create_property(request.model_dump())

    emit_event(
        "property.created",
        "property",
        prop["id"],
        context={"organization_id": prop.get("organization_id")}
    )

    return prop


@app.get("/api/v1/properties/{property_id}")
def get_property(property_id: str):
    """Get effective property data (base + overrides applied)."""
    store = get_store()
    prop = store.get_property(property_id)
    if not prop:
        raise HTTPException(404, detail={"error": {"code": "PROPERTY_NOT_FOUND", "message": "Property not found"}})
    return prop


@app.get("/api/v1/properties")
def list_properties(
    organization_id: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    is_bookable: Optional[bool] = Query(None),
    pms_type: Optional[str] = Query(None),
    limit: int = Query(50),
    cursor: Optional[str] = Query(None)
):
    """List properties with filters."""
    store = get_store()
    filters = {}
    if organization_id:
        filters["organization_id"] = organization_id
    if city:
        filters["city"] = city
    if state:
        filters["state"] = state
    if status:
        filters["status"] = status
    if is_bookable is not None:
        filters["is_bookable"] = is_bookable
    if pms_type:
        filters["pms_type"] = pms_type

    properties, next_cursor = store.list_properties(filters, limit, cursor)
    return {"data": properties, "next_cursor": next_cursor}


@app.post("/api/v1/properties/{property_id}/overrides")
def set_override(property_id: str, request: OverrideRequest):
    """Set an override for a property field."""
    store = get_store()
    result = store.set_override(
        property_id, request.field, request.from_value,
        request.to_value, request.priority, request.actor, request.note
    )

    if "error" in result:
        if result["error"] == "PROPERTY_NOT_FOUND":
            raise HTTPException(404, detail={"error": {"code": "PROPERTY_NOT_FOUND"}})
        raise HTTPException(400, detail={"error": {"code": result["error"]}})

    emit_event(
        "property.override_set",
        "property",
        property_id,
        context={"field": request.field, "priority": request.priority}
    )

    return result


@app.get("/api/v1/properties/{property_id}/overrides/conflicts")
def get_conflicts(property_id: str):
    """Get pending override conflicts."""
    store = get_store()
    conflicts = store.get_conflicts(property_id)
    return {"conflicts": conflicts}


@app.post("/api/v1/properties/{property_id}/overrides/conflicts/{conflict_id}/resolve")
def resolve_conflict(property_id: str, conflict_id: str, request: ResolveConflictRequest):
    """Resolve an override conflict."""
    store = get_store()
    result = store.resolve_conflict(property_id, conflict_id, request.resolution)

    if "error" in result:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})

    return result


@app.get("/api/v1/properties/{property_id}/fees")
def get_fees(property_id: str):
    """Get fees for a property."""
    store = get_store()
    prop = store.get_property(property_id)
    if not prop:
        raise HTTPException(404, detail={"error": {"code": "PROPERTY_NOT_FOUND"}})

    fees = store.get_fees(property_id)
    return {
        "property_id": property_id,
        "currency": prop.get("currency", "USD"),
        "fees": fees
    }


@app.post("/api/v1/properties/{property_id}/fees")
def set_fee(property_id: str, request: FeeRequest):
    """Add/update a fee for a property."""
    store = get_store()
    result = store.set_fee(property_id, request.model_dump())
    return result


@app.get("/api/v1/properties/{property_id}/policies")
def get_policies(property_id: str):
    """Get policies for a property."""
    store = get_store()
    policies = store.get_policies(property_id)
    if not policies:
        return {
            "property_id": property_id,
            "check_in_time": "16:00",
            "check_out_time": "11:00",
            "min_nights": 1,
            "max_nights": 365,
            "cancellation_policy": "FLEXIBLE",
            "pets_allowed": False
        }
    return policies


@app.post("/api/v1/properties/{property_id}/policies")
def set_policies(property_id: str, request: PolicyRequest):
    """Set policies for a property."""
    store = get_store()
    result = store.set_policies(property_id, request.model_dump())
    return result


@app.get("/api/v1/properties/{property_id}/sources")
def get_sources(property_id: str):
    """Get data sources for a property."""
    store = get_store()
    return store.get_sources(property_id)


@app.post("/api/v1/properties/{property_id}/cache/flush")
def flush_cache(property_id: str):
    """Flush property cache."""
    store = get_store()
    store._invalidate_cache(property_id)
    return {"status": "flushed"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "property"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8003)
