"""
Event Service API
Central event bus with content-addressable event IDs.
Append-only audit log. Routing via bitmask destinations.

Endpoints:
  POST /events              - Ingest event
  GET  /events/audit        - Query audit events
  GET  /events/audit/{id}   - Get single event
  GET  /events/audit/entity/{kind}/{id} - Entity timeline
  GET  /events/handlers/status - Handler status
  GET  /events/metrics      - Ingestion metrics
"""
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from datetime import datetime, timezone
from typing import Optional, List, Any
import hashlib
import json
import logging

from shared.database import get_db_cursor, get_redis, init_schema
from shared.events import compute_event_id

logger = logging.getLogger("event")

app = FastAPI(title="Event Service", version="1.0.0")

# Routing bitmask flags
DESTINATIONS = {
    "AUDIT":       0x1000,
    "PAGERDUTY":   0x0001,
    "SLACK":       0x0002,
    "EMAIL":       0x0004,
    "DATADOG":     0x0008,
    "CLOUDWATCH":  0x0010,
    "STDOUT":      0x0020,
    "PROMETHEUS":  0x0040,
    "STATSD":      0x0080,
    "AMPLITUDE":   0x0100,
    "SEGMENT":     0x0200,
    "BIGQUERY":    0x0400,
}

# Default routing: all events go to audit + stdout
DEFAULT_DESTINATIONS = DESTINATIONS["AUDIT"] | DESTINATIONS["STDOUT"]

# In-memory metrics
_metrics = {
    "events_ingested": 0,
    "events_deduplicated": 0,
    "events_stored": 0,
}

# Dedup set (in production, this is Redis with 5min TTL)
_dedup_cache = set()


# --- Models ---

class EventRequest(BaseModel):
    event_kind: str
    entity_kind: str
    entity_id: str
    actor: str = "system"
    context: dict = {}
    workflow_kind: Optional[str] = None
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    external_refs: list = []
    level: str = "info"


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/event/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Dedup ---

def _is_duplicate(event_id: str) -> bool:
    """Check if event is a duplicate (5min window)."""
    r = get_redis()
    if r:
        # Redis SETNX with 5min TTL
        result = r.set(f"evt:dedup:{event_id}", "1", nx=True, ex=300)
        return result is None  # None = key already existed
    
    # Fallback: in-memory set
    if event_id in _dedup_cache:
        return True
    _dedup_cache.add(event_id)
    return False


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "event"}


@app.post("/events")
async def ingest_event(req: EventRequest):
    """
    Ingest an event. Content-addressable ID = automatic deduplication.
    Routes to subsystems based on bitmask configuration.
    """
    event_id = compute_event_id(
        req.event_kind, req.entity_kind, req.entity_id,
        req.actor, req.context, req.workflow_id or ""
    )
    
    _metrics["events_ingested"] += 1
    
    # Dedup check
    if _is_duplicate(event_id):
        _metrics["events_deduplicated"] += 1
        return {"event_id": event_id, "status": "deduplicated"}
    
    # Store in audit log
    try:
        with get_db_cursor() as (cur, conn):
            cur.execute("""
                INSERT INTO audit_event 
                    (event_id, event_kind, entity_kind, entity_id, actor,
                     context, workflow_kind, workflow_id, session_id, external_refs, level)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id) DO NOTHING
            """, (
                event_id, req.event_kind, req.entity_kind, req.entity_id, req.actor,
                json.dumps(req.context), req.workflow_kind, req.workflow_id,
                req.session_id, json.dumps(req.external_refs), req.level,
            ))
            conn.commit()
        _metrics["events_stored"] += 1
    except Exception as e:
        logger.error(f"Failed to store event: {e}")
    
    return {
        "event_id": event_id,
        "status": "accepted",
        "destinations": DEFAULT_DESTINATIONS,
    }


@app.get("/events/audit")
async def query_audit(
    entity_kind: Optional[str] = None,
    entity_id: Optional[str] = None,
    event_kind: Optional[str] = None,
    actor: Optional[str] = None,
    workflow_id: Optional[str] = None,
    level: Optional[str] = None,
    from_ts: Optional[str] = None,
    to_ts: Optional[str] = None,
    limit: int = Query(50, le=200),
    cursor: Optional[str] = None,
):
    """Query audit events with filters."""
    conditions = []
    params = []
    
    if entity_kind:
        conditions.append("entity_kind = %s")
        params.append(entity_kind)
    if entity_id:
        conditions.append("entity_id = %s")
        params.append(entity_id)
    if event_kind:
        conditions.append("event_kind = %s")
        params.append(event_kind)
    if actor:
        conditions.append("actor = %s")
        params.append(actor)
    if workflow_id:
        conditions.append("workflow_id = %s")
        params.append(workflow_id)
    if level:
        conditions.append("level = %s")
        params.append(level)
    if from_ts:
        conditions.append("timestamp >= %s")
        params.append(from_ts)
    if to_ts:
        conditions.append("timestamp <= %s")
        params.append(to_ts)
    if cursor:
        conditions.append("event_id > %s")
        params.append(cursor)
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit + 1)
    
    with get_db_cursor() as (cur, conn):
        cur.execute(f"SELECT * FROM audit_event {where} ORDER BY timestamp DESC LIMIT %s", params)
        rows = cur.fetchall()
    
    has_more = len(rows) > limit
    events = rows[:limit]
    
    return {
        "events": [
            {
                "event_id": e['event_id'],
                "event_kind": e['event_kind'],
                "entity_kind": e['entity_kind'],
                "entity_id": e['entity_id'],
                "actor": e['actor'],
                "context": e['context'],
                "workflow_id": e['workflow_id'],
                "level": e['level'],
                "timestamp": e['timestamp'].isoformat(),
            }
            for e in events
        ],
        "next_cursor": events[-1]['event_id'] if has_more else None,
    }


@app.get("/events/audit/{event_id}")
async def get_event(event_id: str):
    """Get a single audit event by ID."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM audit_event WHERE event_id = %s", (event_id,))
        event = cur.fetchone()
    
    if not event:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Event not found"})
    
    return {
        "event_id": event['event_id'],
        "event_kind": event['event_kind'],
        "entity_kind": event['entity_kind'],
        "entity_id": event['entity_id'],
        "actor": event['actor'],
        "context": event['context'],
        "workflow_kind": event['workflow_kind'],
        "workflow_id": event['workflow_id'],
        "external_refs": event['external_refs'],
        "level": event['level'],
        "timestamp": event['timestamp'].isoformat(),
    }


@app.get("/events/audit/entity/{entity_kind}/{entity_id}")
async def entity_timeline(entity_kind: str, entity_id: str, limit: int = Query(50)):
    """Get full event timeline for an entity."""
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM audit_event WHERE entity_kind = %s AND entity_id = %s ORDER BY timestamp DESC LIMIT %s",
            (entity_kind, entity_id, limit)
        )
        events = cur.fetchall()
    
    return {
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "events": [
            {
                "event_id": e['event_id'],
                "event_kind": e['event_kind'],
                "actor": e['actor'],
                "context": e['context'],
                "level": e['level'],
                "timestamp": e['timestamp'].isoformat(),
            }
            for e in events
        ],
    }


@app.get("/events/handlers/status")
async def handler_status():
    """Get status of event handlers/subsystems."""
    return {
        "handlers": [
            {"name": "audit", "status": "active", "queue_depth": 0, "processed_24h": _metrics["events_stored"]},
            {"name": "logging", "status": "active", "queue_depth": 0, "processed_24h": _metrics["events_ingested"]},
        ]
    }


@app.get("/events/metrics")
async def get_metrics():
    """Get ingestion and processing metrics."""
    return {
        "ingestion": {
            "events_total": _metrics["events_ingested"],
            "events_deduplicated": _metrics["events_deduplicated"],
            "events_stored": _metrics["events_stored"],
        },
    }
