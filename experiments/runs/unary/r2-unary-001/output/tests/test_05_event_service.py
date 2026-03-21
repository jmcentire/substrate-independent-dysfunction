"""
Tests for Event Service API.
Verifies content-addressable IDs, deduplication, audit log immutability.
"""
import pytest
from fastapi.testclient import TestClient
from services.event.api import app, _dedup_cache

client = TestClient(app)


class TestEventIngestion:
    def test_ingest_event(self, clean_db):
        _dedup_cache.clear()
        resp = client.post("/events", json={
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_123",
            "actor": "system",
            "context": {"ubr": "UBR-test123", "total_cents": 243660},
            "level": "info",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "accepted"
        assert data["event_id"].startswith("evt_")
    
    def test_content_addressable_id(self, clean_db):
        """Same content → same event_id → automatic idempotency."""
        _dedup_cache.clear()
        event_data = {
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_456",
            "actor": "system",
            "context": {"total": 100},
        }
        
        resp1 = client.post("/events", json=event_data)
        resp2 = client.post("/events", json=event_data)
        
        assert resp1.json()["event_id"] == resp2.json()["event_id"]
        # Second should be deduplicated
        assert resp2.json()["status"] == "deduplicated"
    
    def test_different_content_different_id(self, clean_db):
        _dedup_cache.clear()
        resp1 = client.post("/events", json={
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_001",
        })
        resp2 = client.post("/events", json={
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "booking_002",
        })
        assert resp1.json()["event_id"] != resp2.json()["event_id"]


class TestAuditQuery:
    def test_query_audit_events(self, clean_db):
        _dedup_cache.clear()
        # Ingest some events
        for i in range(3):
            client.post("/events", json={
                "event_kind": f"test.event_{i}",
                "entity_kind": "test",
                "entity_id": "entity_1",
            })
        
        resp = client.get("/events/audit", params={
            "entity_kind": "test",
            "entity_id": "entity_1",
        })
        assert resp.status_code == 200
        assert len(resp.json()["events"]) == 3
    
    def test_query_by_event_kind(self, clean_db):
        _dedup_cache.clear()
        client.post("/events", json={
            "event_kind": "booking.confirmed",
            "entity_kind": "booking",
            "entity_id": "b1",
        })
        client.post("/events", json={
            "event_kind": "booking.cancelled",
            "entity_kind": "booking",
            "entity_id": "b2",
        })
        
        resp = client.get("/events/audit", params={"event_kind": "booking.confirmed"})
        events = resp.json()["events"]
        assert all(e["event_kind"] == "booking.confirmed" for e in events)


class TestEntityTimeline:
    def test_entity_timeline(self, clean_db):
        _dedup_cache.clear()
        for kind in ["booking.requested", "booking.confirmed", "booking.completed"]:
            client.post("/events", json={
                "event_kind": kind,
                "entity_kind": "booking",
                "entity_id": "booking_timeline",
            })
        
        resp = client.get("/events/audit/entity/booking/booking_timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert data["entity_kind"] == "booking"
        assert len(data["events"]) == 3


class TestEventDetails:
    def test_get_event_by_id(self, clean_db):
        _dedup_cache.clear()
        create = client.post("/events", json={
            "event_kind": "test.detail",
            "entity_kind": "test",
            "entity_id": "detail_1",
            "context": {"key": "value"},
        })
        event_id = create.json()["event_id"]
        
        resp = client.get(f"/events/audit/{event_id}")
        assert resp.status_code == 200
        assert resp.json()["event_kind"] == "test.detail"
        assert resp.json()["context"]["key"] == "value"
    
    def test_event_not_found(self):
        resp = client.get("/events/audit/evt_nonexistent")
        assert resp.status_code == 404


class TestMetrics:
    def test_metrics_endpoint(self, clean_db):
        resp = client.get("/events/metrics")
        assert resp.status_code == 200
        assert "ingestion" in resp.json()


class TestHandlerStatus:
    def test_handler_status(self, clean_db):
        resp = client.get("/events/handlers/status")
        assert resp.status_code == 200
        assert "handlers" in resp.json()
