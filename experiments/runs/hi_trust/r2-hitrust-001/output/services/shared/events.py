"""
Event SDK - fire-and-forget event emission.

In production, sends to Kafka. For testing, uses an in-memory queue.
Events use content-addressable IDs (SHA256 of content).
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any
from collections import deque
import threading


@dataclass
class Event:
    event_kind: str
    entity_kind: str
    entity_id: str
    actor: Optional[str] = None
    context: Optional[Dict[str, Any]] = None
    workflow_id: Optional[str] = None
    session_id: Optional[str] = None
    external_refs: Optional[List[Dict[str, str]]] = None
    level: str = "info"
    timestamp: Optional[str] = None
    event_id: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        if self.event_id is None:
            self.event_id = self._compute_id()

    def _compute_id(self) -> str:
        """Content-addressable event ID: SHA256 of event content."""
        content = json.dumps({
            "event_kind": self.event_kind,
            "entity_kind": self.entity_kind,
            "entity_id": self.entity_id,
            "actor": self.actor,
            "context": self.context,
            "workflow_id": self.workflow_id,
            "external_refs": self.external_refs,
        }, sort_keys=True)
        h = hashlib.sha256(content.encode()).hexdigest()[:16]
        return f"evt_{h}"

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


class EventBus:
    """In-memory event bus for testing. Production would use Kafka."""

    def __init__(self):
        self._events: deque = deque(maxlen=10000)
        self._lock = threading.Lock()
        self._subscribers: List = []

    def emit(self, event: Event):
        """Fire-and-forget event emission."""
        with self._lock:
            self._events.append(event)
            for sub in self._subscribers:
                try:
                    sub(event)
                except Exception:
                    pass

    def subscribe(self, callback):
        with self._lock:
            self._subscribers.append(callback)

    def get_events(self, entity_kind: Optional[str] = None,
                   entity_id: Optional[str] = None,
                   event_kind: Optional[str] = None,
                   limit: int = 100) -> List[Event]:
        with self._lock:
            events = list(self._events)

        if entity_kind:
            events = [e for e in events if e.entity_kind == entity_kind]
        if entity_id:
            events = [e for e in events if e.entity_id == entity_id]
        if event_kind:
            events = [e for e in events if e.event_kind == event_kind]

        return events[-limit:]

    def clear(self):
        with self._lock:
            self._events.clear()
            self._subscribers.clear()


# Global event bus
_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus


def emit_event(event_kind: str, entity_kind: str, entity_id: str, **kwargs):
    """Convenience function to emit an event."""
    event = Event(
        event_kind=event_kind,
        entity_kind=entity_kind,
        entity_id=entity_id,
        **kwargs
    )
    get_event_bus().emit(event)
    return event


def reset_event_bus():
    global _bus
    _bus = EventBus()
