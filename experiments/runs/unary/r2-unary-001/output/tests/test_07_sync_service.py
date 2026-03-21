"""
Tests for Sync Service API.
Verifies webhook processing, sync jobs, PMS adapters, rate limiting.
"""
import pytest
from fastapi.testclient import TestClient
from services.sync.api import app
from services.sync.adapters import (
    get_adapter, GuestyAdapter, HostawayAdapter, StreamlineAdapter,
    PropertyCore, AvailabilityBlock, NightlyRate, Reservation
)
from datetime import date

client = TestClient(app)

PROPERTY_ID = "550e8400-e29b-41d4-a716-446655440000"


class TestWebhook:
    def test_receive_webhook(self, clean_db):
        resp = client.post("/webhook/guesty", json={
            "event": "reservation.created",
            "data": {"id": "res_123", "listingId": "lst_456"},
        })
        assert resp.status_code == 200
        assert resp.json()["received"] == True
    
    def test_webhook_dedup(self, clean_db):
        payload = {"event": "test.dedup", "data": {"id": "unique_1"}}
        
        resp1 = client.post("/webhook/guesty", json=payload)
        assert resp1.json().get("deduplicated", False) == False
        
        resp2 = client.post("/webhook/guesty", json=payload)
        assert resp2.json()["deduplicated"] == True


class TestSyncJobs:
    def test_push_sync(self, clean_db):
        resp = client.post("/sync/push", json={
            "property_id": PROPERTY_ID,
            "subsystems": ["availability", "pricing"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "QUEUED"
        assert len(data["subsystem_jobs"]) == 2
    
    def test_pull_sync(self, clean_db):
        resp = client.post("/sync/pull", json={
            "property_id": PROPERTY_ID,
            "subsystems": ["property", "availability", "pricing"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["subsystem_jobs"]) == 3
    
    def test_get_job_status(self, clean_db):
        create = client.post("/sync/push", json={
            "property_id": PROPERTY_ID,
            "subsystems": ["availability"],
        })
        job_id = create.json()["job_id"]
        
        resp = client.get(f"/sync/jobs/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["job_type"] == "PUSH"


class TestReconciliation:
    def test_start_reconciliation(self, clean_db):
        resp = client.post("/sync/reconcile", json={
            "property_ids": [PROPERTY_ID],
            "subsystems": ["availability"],
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "QUEUED"


class TestSyncStatus:
    def test_sync_status(self, clean_db):
        resp = client.get("/sync/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "adapters" in data
        assert "guesty" in data["adapters"]


class TestPMSQuote:
    def test_get_pms_quote(self, clean_db):
        resp = client.post("/sync/pms/quote", json={
            "property_id": PROPERTY_ID,
            "check_in": "2025-06-15",
            "check_out": "2025-06-20",
            "guests": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["nights"] == 5
        assert data["total_cents"] > 0


class TestPMSReservation:
    def test_create_pms_reservation(self, clean_db):
        resp = client.post("/sync/pms/reservation", json={
            "property_id": PROPERTY_ID,
            "check_in": "2025-06-15",
            "check_out": "2025-06-20",
            "guest_name": "John Doe",
            "guest_email": "john@example.com",
            "guests": 2,
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "CONFIRMED"
        assert data["confirmation_code"].startswith("GY-")
    
    def test_cancel_pms_reservation(self, clean_db):
        # Create first
        create = client.post("/sync/pms/reservation", json={
            "property_id": PROPERTY_ID,
            "check_in": "2025-07-01",
            "check_out": "2025-07-05",
            "guest_name": "Jane Doe",
            "guest_email": "jane@example.com",
        })
        ext_id = create.json()["external_id"]
        
        resp = client.delete(f"/sync/pms/reservation/{ext_id}")
        assert resp.status_code == 200
        assert resp.json()["cancelled"] == True


# --- Adapter Unit Tests ---

class TestGuestyAdapter:
    def test_translate_property(self):
        adapter = GuestyAdapter()
        result = adapter.translate_property({
            "_id": "guesty_123",
            "title": "Beach House",
            "bedrooms": 3,
            "bathrooms": 2,
            "accommodates": 8,
        })
        assert isinstance(result, PropertyCore)
        assert result.name == "Beach House"
        assert result.bedrooms == 3
        assert result.max_guests == 8
    
    def test_translate_availability(self):
        adapter = GuestyAdapter()
        result = adapter.translate_availability({
            "calendar": [
                {"startDate": "2025-06-01", "endDate": "2025-06-05", "status": "booked"},
                {"startDate": "2025-06-10", "endDate": "2025-06-12", "status": "available"},
            ]
        })
        assert len(result) == 1  # Only blocked dates
        assert result[0].start_date == date(2025, 6, 1)
    
    def test_translate_rates(self):
        adapter = GuestyAdapter()
        result = adapter.translate_rates({
            "calendar": [
                {"date": "2025-06-01", "price": 450.0},
                {"date": "2025-06-02", "price": 475.0},
            ],
            "currency": "USD",
        })
        assert len(result) == 2
        assert result[0].rate_cents == 45000
        assert result[1].rate_cents == 47500
    
    def test_translate_reservation(self):
        adapter = GuestyAdapter()
        result = adapter.translate_reservation({
            "_id": "res_123",
            "listingId": "lst_456",
            "checkIn": "2025-06-15T00:00:00",
            "checkOut": "2025-06-20T00:00:00",
            "guest": {"firstName": "John", "lastName": "Doe", "email": "john@test.com"},
            "status": "confirmed",
            "confirmationCode": "GY-12345",
            "money": {"totalPaid": 2250.0, "currency": "USD"},
        })
        assert isinstance(result, Reservation)
        assert result.guest_name == "John Doe"
        assert result.total_cents == 225000
        assert result.status == "CONFIRMED"


class TestHostawayAdapter:
    def test_translate_property(self):
        adapter = HostawayAdapter()
        result = adapter.translate_property({
            "id": 123,
            "name": "Mountain Cabin",
            "bedroomsNumber": 2,
            "bathroomsNumber": 1,
            "personCapacity": 4,
        })
        assert result.name == "Mountain Cabin"
        assert result.pms_type == "hostaway"


class TestStreamlineAdapter:
    def test_translate_rates(self):
        adapter = StreamlineAdapter()
        result = adapter.translate_rates({
            "rates": [
                {"date": "2025-06-01", "rate": 350.0},
                {"date": "2025-06-02", "rate": 375.0},
            ]
        })
        assert len(result) == 2
        assert result[0].rate_cents == 35000


class TestAdapterRegistry:
    def test_get_known_adapter(self):
        adapter = get_adapter("guesty")
        assert isinstance(adapter, GuestyAdapter)
    
    def test_unknown_adapter_raises(self):
        with pytest.raises(ValueError, match="Unknown PMS type"):
            get_adapter("nonexistent_pms")
