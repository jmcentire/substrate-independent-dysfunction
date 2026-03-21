"""
Tests for Event Service.

Tests content-addressable IDs, deduplication, audit log, and routing.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.event.store import (
    EventStore, compute_event_id, get_route_mask,
    ROUTE_AUDIT, ROUTE_SLACK, ROUTE_DATADOG
)
from services.event.app import app, set_store


class TestContentAddressableIds:
    def test_same_content_same_id(self):
        """Same event content should produce same event ID."""
        id1 = compute_event_id("booking.confirmed", "booking", "b123", actor="user_1")
        id2 = compute_event_id("booking.confirmed", "booking", "b123", actor="user_1")
        assert id1 == id2

    def test_different_content_different_id(self):
        """Different content should produce different IDs."""
        id1 = compute_event_id("booking.confirmed", "booking", "b123")
        id2 = compute_event_id("booking.confirmed", "booking", "b456")
        assert id1 != id2

    def test_id_format(self):
        """Event ID should have evt_ prefix."""
        eid = compute_event_id("booking.confirmed", "booking", "b123")
        assert eid.startswith("evt_")
        assert len(eid) == 20  # evt_ + 16 hex chars


class TestRouting:
    def test_booking_routes(self):
        """Booking events should route to audit + slack + datadog + amplitude."""
        mask = get_route_mask("booking.confirmed")
        assert mask & ROUTE_AUDIT
        assert mask & ROUTE_SLACK
        assert mask & ROUTE_DATADOG

    def test_payment_routes(self):
        mask = get_route_mask("payment.succeeded")
        assert mask & ROUTE_AUDIT
        assert mask & ROUTE_DATADOG

    def test_default_routes(self):
        mask = get_route_mask("unknown.event")
        assert mask & ROUTE_AUDIT


class TestEventStore:
    def setup_method(self):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = EventStore(self.db, self.cache)

    def test_ingest_event(self):
        """Ingesting an event should store it in audit log."""
        result = self.store.ingest({
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_123",
            "actor": "user:user_456",
            "context": {"total_cents": 245000},
        })
        assert result["status"] == "ingested"
        assert result["event_id"].startswith("evt_")

    def test_deduplication(self):
        """Same event content should be deduplicated."""
        event = {
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_123",
        }
        r1 = self.store.ingest(event)
        assert r1["status"] == "ingested"

        r2 = self.store.ingest(event)
        assert r2["status"] == "deduplicated"

    def test_query_audit_by_entity(self):
        """Query audit events by entity."""
        self.store.ingest({
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_123",
        })
        self.store.ingest({
            "event_kind": "booking.cancelled",
            "entity_kind": "booking",
            "entity_id": "booking_123",
            "actor": "admin",
        })
        self.store.ingest({
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_456",
        })

        events, _ = self.store.query_audit(
            {"entity_kind": "booking", "entity_id": "booking_123"}
        )
        assert len(events) == 2

    def test_entity_timeline(self):
        """Entity timeline should return all events for an entity."""
        self.store.ingest({
            "event_kind": "property.created",
            "entity_kind": "property",
            "entity_id": "prop_123",
        })
        self.store.ingest({
            "event_kind": "property.updated",
            "entity_kind": "property",
            "entity_id": "prop_123",
            "context": {"fields_changed": ["name"]},
        })

        events = self.store.get_entity_timeline("property", "prop_123")
        assert len(events) == 2

    def test_get_single_event(self):
        """Get a specific event by ID."""
        result = self.store.ingest({
            "event_kind": "payment.succeeded",
            "entity_kind": "invoice",
            "entity_id": "inv_123",
        })
        event = self.store.get_event(result["event_id"])
        assert event is not None
        assert event["event_kind"] == "payment.succeeded"

    def test_metrics(self):
        """Metrics should track counts."""
        self.store.ingest({
            "event_kind": "test.event",
            "entity_kind": "test",
            "entity_id": "test_1",
        })
        metrics = self.store.get_metrics()
        assert metrics["ingestion"]["events_received"] == 1
        assert metrics["ingestion"]["events_stored"] == 1
        assert metrics["total_audit_events"] == 1


@pytest.mark.asyncio
class TestEventAPI:
    async def setup_method(self, method):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = EventStore(self.db, self.cache)
        set_store(self.store)

    async def test_ingest_event(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/events", json={
                "event_kind": "booking.confirmed",
                "entity_kind": "booking",
                "entity_id": "booking_123",
                "actor": "user:user_456",
                "context": {"total_cents": 245000},
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "ingested"

    async def test_query_audit(self):
        self.store.ingest({
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_123",
        })
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/events/audit", params={
                "entity_kind": "booking",
                "entity_id": "booking_123",
            })
            assert resp.status_code == 200
            assert len(resp.json()["events"]) >= 1

    async def test_get_event(self):
        result = self.store.ingest({
            "event_kind": "test.event",
            "entity_kind": "test",
            "entity_id": "t1",
        })
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/events/audit/{result['event_id']}")
            assert resp.status_code == 200
            assert resp.json()["event_kind"] == "test.event"

    async def test_entity_timeline(self):
        self.store.ingest({
            "event_kind": "property.created",
            "entity_kind": "property",
            "entity_id": "p1",
        })
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/events/audit/entity/property/p1")
            assert resp.status_code == 200
            assert len(resp.json()["events"]) >= 1

    async def test_metrics(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/events/metrics")
            assert resp.status_code == 200

    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
