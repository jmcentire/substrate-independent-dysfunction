"""
Tests for Availability Service API.
Verifies /check, /acquire, /release, /next-available, /search endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import date

from services.availability.api import app

client = TestClient(app)

UNIT_ID = "550e8400-e29b-41d4-a716-446655440000"
UNIT_ID_2 = "660e8400-e29b-41d4-a716-446655440001"
UNIT_ID_3 = "770e8400-e29b-41d4-a716-446655440002"


class TestHealthCheck:
    def test_health(self):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["service"] == "availability"


class TestCheckAvailability:
    def test_available_dates(self, clean_db):
        resp = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 200
        assert resp.json()["available"] == True
    
    def test_blocked_dates(self, clean_db):
        # First acquire some dates
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
        })
        
        resp = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 200
        assert resp.json()["available"] == False
    
    def test_invalid_dates(self):
        resp = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-10",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 400


class TestAcquireDates:
    def test_acquire_available(self, clean_db):
        resp = client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "acquired"
    
    def test_acquire_conflict(self, clean_db):
        # First acquire
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
        })
        
        # Second acquire overlapping dates
        resp = client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-02",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 409
    
    def test_acquire_adjacent_no_conflict(self, clean_db):
        # Acquire days 1-3
        resp1 = client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-03",
        })
        assert resp1.status_code == 200
        
        # Acquire days 4-6 (adjacent, no overlap)
        resp2 = client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-04",
            "end_date": "2025-06-06",
        })
        assert resp2.status_code == 200
    
    def test_acquire_cross_month(self, clean_db):
        resp = client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-28",
            "end_date": "2025-07-02",
        })
        assert resp.status_code == 200
        
        # Verify both months are blocked
        check1 = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-29",
            "end_date": "2025-06-30",
        })
        assert check1.json()["available"] == False
        
        check2 = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-07-01",
            "end_date": "2025-07-01",
        })
        assert check2.json()["available"] == False


class TestReleaseDates:
    def test_release_blocked_dates(self, clean_db):
        # Acquire then release
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        
        resp = client.post("/release", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "released"
        
        # Verify dates are available again
        check = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert check.json()["available"] == True
    
    def test_partial_release(self, clean_db):
        # Acquire 5 days, release 2
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        
        client.post("/release", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-02",
        })
        
        # Days 1-2 should be available
        check1 = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-02",
        })
        assert check1.json()["available"] == True
        
        # Days 3-5 should still be blocked
        check2 = client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-03",
            "end_date": "2025-06-05",
        })
        assert check2.json()["available"] == False


class TestNextAvailable:
    def test_find_next_window(self, clean_db):
        # Block days 1-10
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-10",
        })
        
        resp = client.get("/next-available", params={
            "unit_id": UNIT_ID,
            "nights": 5,
            "after": "2025-06-01",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["found"] == True
        assert data["start_date"] == "2025-06-11"
    
    def test_find_window_next_month(self, clean_db):
        """Block entire June, should find in July with sufficient search window."""
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-30",
        })
        
        resp = client.get("/next-available", params={
            "unit_id": UNIT_ID,
            "nights": 5,
            "after": "2025-06-01",
            "limit_months": 3,  # Search 3 months (Jun, Jul, Aug)
        })
        data = resp.json()
        assert data["found"] == True
        assert data["start_date"].startswith("2025-07")
    
    def test_all_available_returns_first_date(self, clean_db):
        resp = client.get("/next-available", params={
            "unit_id": UNIT_ID,
            "nights": 3,
            "after": "2025-06-01",
        })
        data = resp.json()
        assert data["found"] == True
        assert data["start_date"] == "2025-06-01"


class TestSearchByRegion:
    def test_search_available_units(self, clean_db, db_connection):
        # Setup: Add units to region
        cur = db_connection.cursor()
        cur.execute("INSERT INTO unit_region (unit_id, region) VALUES (%s, 'US-CO-ASPEN')", (UNIT_ID,))
        cur.execute("INSERT INTO unit_region (unit_id, region) VALUES (%s, 'US-CO-ASPEN')", (UNIT_ID_2,))
        cur.execute("INSERT INTO unit_region (unit_id, region) VALUES (%s, 'US-CO-ASPEN')", (UNIT_ID_3,))
        cur.close()
        
        # Block one unit
        client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        
        resp = client.get("/search", params={
            "region": "US-CO-ASPEN",
            "start_date": "2025-06-01",
            "end_date": "2025-06-05",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["unit_ids"]) == 2
        assert UNIT_ID not in data["unit_ids"]
