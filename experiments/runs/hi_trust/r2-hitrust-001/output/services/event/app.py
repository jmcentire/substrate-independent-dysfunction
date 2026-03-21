"""
Event Service — Event routing, audit logging, observability.

Base URL: /api/v1/events
Source of Truth: Owns event log.

Features:
- Content-addressable event IDs (SHA256)
- Deduplication (5-min window)
- Append-only audit log
- Routing bitmask to subsystems

Endpoints:
  POST /events              - Ingest an event
  GET  /events/audit        - Query audit events
  GET  /events/audit/{id}   - Get single event
  GET  /events/audit/entity/{kind}/{id}     - Entity timeline
  GET  /events/audit/workflow/{kind}/{id}    - Workflow events
  GET  /events/handlers/status               - Handler status
  GET  /events/metrics                       - Processing metrics
"""

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.event.store import EventStore


app = FastAPI(title="Event Service", version="1.0.0")

_store: Optional[EventStore] = None


def get_store() -> EventStore:
    global _store
    if _store is None:
        _store = EventStore(Database(), get_cache())
    return _store


def set_store(store: EventStore):
    global _store
    _store = store


# --- Request Models ---

class IngestEventRequest(BaseModel):
    event_kind: str
    entity_kind: str
    entity_id: str
    actor: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    workflow_kind: Optional[str] = None
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    external_refs: Optional[List[Dict[str, str]]] = None
    level: str = "info"
    timestamp: Optional[str] = None


# --- Endpoints ---

@app.post("/api/v1/events")
def ingest_event(request: IngestEventRequest):
    """Ingest an event into the event bus."""
    store = get_store()
    result = store.ingest(request.model_dump())
    return result


@app.get("/api/v1/events/audit")
def query_audit(
    entity_kind: Optional[str] = Query(None),
    entity_id: Optional[str] = Query(None),
    event_kind: Optional[str] = Query(None),
    actor: Optional[str] = Query(None),
    workflow_id: Optional[str] = Query(None),
    level: Optional[str] = Query(None),
    from_ts: Optional[str] = Query(None),
    to_ts: Optional[str] = Query(None),
    limit: int = Query(100),
    cursor: Optional[str] = Query(None)
):
    """Query audit events with filters."""
    store = get_store()
    filters = {}
    if entity_kind: filters["entity_kind"] = entity_kind
    if entity_id: filters["entity_id"] = entity_id
    if event_kind: filters["event_kind"] = event_kind
    if actor: filters["actor"] = actor
    if workflow_id: filters["workflow_id"] = workflow_id
    if level: filters["level"] = level
    if from_ts: filters["from_ts"] = from_ts
    if to_ts: filters["to_ts"] = to_ts

    events, next_cursor = store.query_audit(filters, limit, cursor)
    return {"events": events, "next_cursor": next_cursor, "total": len(events)}


@app.get("/api/v1/events/audit/{event_id}")
def get_event(event_id: str):
    """Get a single audit event."""
    store = get_store()
    event = store.get_event(event_id)
    if not event:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
    return event


@app.get("/api/v1/events/audit/entity/{entity_kind}/{entity_id}")
def get_entity_timeline(entity_kind: str, entity_id: str):
    """Get all events for an entity."""
    store = get_store()
    events = store.get_entity_timeline(entity_kind, entity_id)
    return {"entity_kind": entity_kind, "entity_id": entity_id, "events": events}


@app.get("/api/v1/events/audit/workflow/{workflow_kind}/{workflow_id}")
def get_workflow_events(workflow_kind: str, workflow_id: str):
    """Get all events for a workflow."""
    store = get_store()
    events, _ = store.query_audit({"workflow_id": workflow_id}, limit=1000)
    return {"workflow_kind": workflow_kind, "workflow_id": workflow_id, "events": events}


@app.get("/api/v1/events/audit/actor/{actor_kind}/{actor_id}")
def get_actor_events(actor_kind: str, actor_id: str):
    """Get all events by an actor."""
    store = get_store()
    actor_str = f"{actor_kind}:{actor_id}"
    events, _ = store.query_audit({"actor": actor_str}, limit=1000)
    return {"actor_kind": actor_kind, "actor_id": actor_id, "events": events}


@app.get("/api/v1/events/handlers/status")
def handlers_status():
    """Get handler status."""
    return {
        "handlers": [
            {"name": "audit", "status": "active", "queue_depth": 0},
            {"name": "logging", "status": "active", "queue_depth": 0},
            {"name": "metrics", "status": "active", "queue_depth": 0},
        ]
    }


@app.get("/api/v1/events/metrics")
def get_metrics():
    """Get event processing metrics."""
    store = get_store()
    return store.get_metrics()


@app.get("/health")
def health():
    return {"status": "ok", "service": "event"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8004)
