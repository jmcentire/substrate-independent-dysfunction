"""
Event Service data store — audit log and event routing.

Features:
- Content-addressable event IDs (SHA256)
- Append-only audit log (immutable)
- Event routing via bitmask flags
- Deduplication with 5-min window
"""

import json
import hashlib
import time
from typing import Optional, List
from services.shared.db import Database
from services.shared.cache import MemoryCache


# Routing bitmask flags
ROUTE_PAGERDUTY   = 0x0001
ROUTE_SLACK       = 0x0002
ROUTE_EMAIL       = 0x0004
ROUTE_DATADOG     = 0x0008
ROUTE_CLOUDWATCH  = 0x0010
ROUTE_STDOUT      = 0x0020
ROUTE_PROMETHEUS  = 0x0040
ROUTE_STATSD      = 0x0080
ROUTE_AMPLITUDE   = 0x0100
ROUTE_SEGMENT     = 0x0200
ROUTE_BIGQUERY    = 0x0400
ROUTE_AUDIT       = 0x1000

# Default routing rules
DEFAULT_ROUTES = {
    "booking.*": ROUTE_AUDIT | ROUTE_SLACK | ROUTE_DATADOG | ROUTE_AMPLITUDE,
    "payment.*": ROUTE_AUDIT | ROUTE_DATADOG | ROUTE_AMPLITUDE,
    "availability.*": ROUTE_AUDIT | ROUTE_DATADOG,
    "pricing.*": ROUTE_AUDIT | ROUTE_DATADOG,
    "property.*": ROUTE_AUDIT | ROUTE_DATADOG | ROUTE_SLACK,
    "sync.*": ROUTE_AUDIT | ROUTE_DATADOG,
    "*": ROUTE_AUDIT | ROUTE_STDOUT,
}


def compute_event_id(event_kind: str, entity_kind: str, entity_id: str,
                     actor: str = None, context: dict = None,
                     workflow_id: str = None, external_refs: list = None) -> str:
    """Content-addressable event ID: SHA256 of event content."""
    content = json.dumps({
        "event_kind": event_kind,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "actor": actor,
        "context": context,
        "workflow_id": workflow_id,
        "external_refs": external_refs,
    }, sort_keys=True)
    h = hashlib.sha256(content.encode()).hexdigest()[:16]
    return f"evt_{h}"


def get_route_mask(event_kind: str) -> int:
    """Determine routing bitmask for an event kind."""
    for pattern, mask in DEFAULT_ROUTES.items():
        if pattern == "*":
            return mask
        prefix = pattern.replace("*", "")
        if event_kind.startswith(prefix):
            return mask
    return ROUTE_AUDIT | ROUTE_STDOUT


class EventStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()
        self._metrics = {
            "events_received": 0,
            "events_deduplicated": 0,
            "events_stored": 0,
        }

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS audit_event (
                event_id TEXT PRIMARY KEY,
                event_kind TEXT NOT NULL,
                entity_kind TEXT NOT NULL,
                entity_id TEXT NOT NULL,
                actor TEXT,
                context TEXT,
                workflow_kind TEXT,
                workflow_id TEXT,
                session_id TEXT,
                external_refs TEXT,
                level TEXT DEFAULT 'info',
                route_mask INTEGER DEFAULT 0,
                timestamp TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_event(entity_kind, entity_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_event_kind ON audit_event(event_kind)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_workflow ON audit_event(workflow_kind, workflow_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_event(actor)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_event(timestamp)"
        )
        self.db.commit()

    def ingest(self, event_data: dict) -> dict:
        """Ingest an event: deduplicate, classify, route, store."""
        self._metrics["events_received"] += 1

        # Compute content-addressable ID
        event_id = compute_event_id(
            event_data.get("event_kind", ""),
            event_data.get("entity_kind", ""),
            event_data.get("entity_id", ""),
            event_data.get("actor"),
            event_data.get("context"),
            event_data.get("workflow_id"),
            event_data.get("external_refs"),
        )

        # Deduplication: check if event seen in last 5 minutes
        dedup_key = f"event:dedup:{event_id}"
        if self.cache.exists(dedup_key):
            self._metrics["events_deduplicated"] += 1
            return {"event_id": event_id, "status": "deduplicated"}

        # Mark as seen (5-min window)
        self.cache.setex(dedup_key, 300, "1")

        # Determine routing
        event_kind = event_data.get("event_kind", "")
        route_mask = get_route_mask(event_kind)

        # Store to audit log (append-only)
        timestamp = event_data.get("timestamp") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.db.execute("""
            INSERT OR IGNORE INTO audit_event (
                event_id, event_kind, entity_kind, entity_id,
                actor, context, workflow_kind, workflow_id,
                session_id, external_refs, level, route_mask, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            event_id,
            event_kind,
            event_data.get("entity_kind", ""),
            event_data.get("entity_id", ""),
            event_data.get("actor"),
            json.dumps(event_data.get("context")) if event_data.get("context") else None,
            event_data.get("workflow_kind"),
            event_data.get("workflow_id"),
            event_data.get("session_id"),
            json.dumps(event_data.get("external_refs")) if event_data.get("external_refs") else None,
            event_data.get("level", "info"),
            route_mask,
            timestamp,
        ])
        self.db.commit()
        self._metrics["events_stored"] += 1

        return {
            "event_id": event_id,
            "status": "ingested",
            "route_mask": route_mask,
        }

    def query_audit(self, filters: dict = None, limit: int = 100,
                    cursor: str = None) -> tuple[list, Optional[str]]:
        """Query audit events."""
        query = "SELECT * FROM audit_event WHERE 1=1"
        params = []

        if filters:
            if filters.get("entity_kind"):
                query += " AND entity_kind = ?"
                params.append(filters["entity_kind"])
            if filters.get("entity_id"):
                query += " AND entity_id = ?"
                params.append(filters["entity_id"])
            if filters.get("event_kind"):
                query += " AND event_kind = ?"
                params.append(filters["event_kind"])
            if filters.get("actor"):
                query += " AND actor = ?"
                params.append(filters["actor"])
            if filters.get("workflow_id"):
                query += " AND workflow_id = ?"
                params.append(filters["workflow_id"])
            if filters.get("level"):
                query += " AND level = ?"
                params.append(filters["level"])
            if filters.get("from_ts"):
                query += " AND timestamp >= ?"
                params.append(filters["from_ts"])
            if filters.get("to_ts"):
                query += " AND timestamp <= ?"
                params.append(filters["to_ts"])

        if cursor:
            query += " AND event_id > ?"
            params.append(cursor)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit + 1)

        rows = self.db.fetchall(query, params)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        events = []
        for r in rows:
            event = dict(r)
            if event.get("context"):
                try:
                    event["context"] = json.loads(event["context"])
                except (json.JSONDecodeError, TypeError):
                    pass
            if event.get("external_refs"):
                try:
                    event["external_refs"] = json.loads(event["external_refs"])
                except (json.JSONDecodeError, TypeError):
                    pass
            events.append(event)

        next_cursor = rows[-1]["event_id"] if has_more else None
        return events, next_cursor

    def get_event(self, event_id: str) -> Optional[dict]:
        """Get a single audit event by ID."""
        row = self.db.fetchone("SELECT * FROM audit_event WHERE event_id = ?", [event_id])
        if not row:
            return None
        event = dict(row)
        if event.get("context"):
            try:
                event["context"] = json.loads(event["context"])
            except (json.JSONDecodeError, TypeError):
                pass
        if event.get("external_refs"):
            try:
                event["external_refs"] = json.loads(event["external_refs"])
            except (json.JSONDecodeError, TypeError):
                pass
        return event

    def get_entity_timeline(self, entity_kind: str, entity_id: str) -> list[dict]:
        """Get all events for an entity."""
        events, _ = self.query_audit(
            {"entity_kind": entity_kind, "entity_id": entity_id},
            limit=1000
        )
        return events

    def get_metrics(self) -> dict:
        """Get event processing metrics."""
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM audit_event")
        return {
            "ingestion": {
                "events_received": self._metrics["events_received"],
                "events_deduplicated": self._metrics["events_deduplicated"],
                "events_stored": self._metrics["events_stored"],
            },
            "total_audit_events": total["cnt"] if total else 0,
        }
