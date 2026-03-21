"""
Event Service SDK - fire-and-forget event emission.
Content-addressable event IDs (SHA256 of event content).
"""
import hashlib
import json
import time
from datetime import datetime, timezone
from typing import Optional
import logging

logger = logging.getLogger("event_sdk")

# In-memory event buffer for the SDK
_event_buffer = []
_event_store = []  # For testing / local Event Service


def compute_event_id(event_kind: str, entity_kind: str, entity_id: str,
                     actor: str = "", context: dict = None, workflow_id: str = "") -> str:
    """Content-addressable event ID: SHA256 of event content."""
    content = json.dumps({
        "event_kind": event_kind,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "actor": actor,
        "context": json.dumps(context or {}, sort_keys=True),
        "workflow_id": workflow_id,
    }, sort_keys=True)
    hash_hex = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"evt_{hash_hex}"


def emit(event_kind: str, entity_kind: str, entity_id: str,
         actor: str = "system", context: dict = None,
         workflow_id: str = "", level: str = "info") -> str:
    """
    Emit an event (fire-and-forget).
    Returns the content-addressable event ID.
    """
    event_id = compute_event_id(event_kind, entity_kind, entity_id, actor, context, workflow_id)
    event = {
        "event_id": event_id,
        "event_kind": event_kind,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "actor": actor,
        "context": context or {},
        "workflow_id": workflow_id,
        "level": level,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    _event_buffer.append(event)
    _event_store.append(event)
    logger.info(f"Event emitted: {event_kind} for {entity_kind}/{entity_id}")
    return event_id


def get_events(entity_kind: str = None, entity_id: str = None,
               event_kind: str = None, limit: int = 50) -> list:
    """Query events (local store for integration)."""
    results = _event_store.copy()
    if entity_kind:
        results = [e for e in results if e["entity_kind"] == entity_kind]
    if entity_id:
        results = [e for e in results if e["entity_id"] == entity_id]
    if event_kind:
        results = [e for e in results if e["event_kind"] == event_kind]
    return results[-limit:]


def flush_buffer() -> list:
    """Flush event buffer (would send to Kafka in production)."""
    events = _event_buffer.copy()
    _event_buffer.clear()
    return events


def clear_store():
    """Clear the event store (for testing)."""
    _event_store.clear()
    _event_buffer.clear()
