"""
Tests for Pricing Service API.
Verifies /rate, /calendar, /bulk, /set endpoints.
"""
import pytest
from fastapi.testclient import TestClient
from services.pricing.api import app

client = TestClient(app)

UNIT_ID_1 = "550e8400-e29b-41d4-a716-446655440001"
UNIT_ID_2 = "550e8400-e29b-41d4-a716-446655440002"
UNIT_ID_3 = "550e8400-e29b-41d4-a716-446655440003"


class TestSetRates:
    def test_set_rates(self, clean_db):
        resp = client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [
                {"date": "2025-03-15", "rate_cents": 45000},
                {"date": "2025-03-16", "rate_cents": 45000},
                {"date": "2025-03-17", "rate_cents": 52000},
            ]
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "updated"
        assert resp.json()["count"] == 3
    
    def test_upsert_rates(self, clean_db):
        # Set initial
        client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [{"date": "2025-03-15", "rate_cents": 45000}],
        })
        
        # Update same date
        resp = client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [{"date": "2025-03-15", "rate_cents": 50000}],
        })
        assert resp.status_code == 200
        
        # Verify updated
        rate = client.get("/rate", params={
            "unit_id": UNIT_ID_1, "start_date": "2025-03-15",
        })
        assert rate.json()["rates"][0]["rate_cents"] == 50000


class TestGetRate:
    def test_single_rate(self, clean_db):
        # Set up rate
        client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [{"date": "2025-03-15", "rate_cents": 45000}],
        })
        
        resp = client.get("/rate", params={
            "unit_id": UNIT_ID_1, "start_date": "2025-03-15",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["rates"][0]["rate_cents"] == 45000
        assert data["missing_dates"] == []
    
    def test_rate_with_missing_dates(self, clean_db):
        # Set rates for some days only
        client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [
                {"date": "2025-03-15", "rate_cents": 45000},
                {"date": "2025-03-16", "rate_cents": 45000},
            ],
        })
        
        resp = client.get("/rate", params={
            "unit_id": UNIT_ID_1,
            "start_date": "2025-03-15",
            "end_date": "2025-03-19",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rates"]) == 2
        assert len(data["missing_dates"]) == 3
        assert "2025-03-17" in data["missing_dates"]
    
    def test_invalid_date_range(self):
        resp = client.get("/rate", params={
            "unit_id": UNIT_ID_1,
            "start_date": "2025-03-20",
            "end_date": "2025-03-15",
        })
        assert resp.status_code == 400


class TestCalendar:
    def test_calendar_view(self, clean_db):
        # Set some rates
        client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [
                {"date": "2025-03-01", "rate_cents": 40000},
                {"date": "2025-03-02", "rate_cents": 42000},
                {"date": "2025-03-03", "rate_cents": 45000},
            ],
        })
        
        resp = client.get("/calendar", params={
            "unit_id": UNIT_ID_1, "month": "2025-03",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["month"] == "2025-03"
        assert len(data["rates"]) == 3
        assert len(data["missing_days"]) == 28  # 31 - 3 = 28 missing


class TestBulkRates:
    def test_bulk_multiple_units(self, clean_db):
        # Set rates for two units
        for uid, rate in [(UNIT_ID_1, 45000), (UNIT_ID_2, 50000)]:
            client.post("/set", json={
                "unit_id": uid,
                "currency": "USD",
                "rates": [
                    {"date": "2025-03-15", "rate_cents": rate},
                    {"date": "2025-03-16", "rate_cents": rate},
                    {"date": "2025-03-17", "rate_cents": rate},
                ],
            })
        
        resp = client.post("/bulk", json={
            "unit_ids": [UNIT_ID_1, UNIT_ID_2],
            "start_date": "2025-03-15",
            "end_date": "2025-03-17",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["rates"]) == 2
        
        # Unit 1: 3 x 45000 = 135000
        u1 = next(r for r in data["rates"] if r["unit_id"] == UNIT_ID_1)
        assert u1["sum_cents"] == 135000
        assert u1["avg_cents"] == 45000
        assert u1["missing_dates"] == []
    
    def test_bulk_with_missing(self, clean_db):
        # Set only some dates for one unit
        client.post("/set", json={
            "unit_id": UNIT_ID_1,
            "currency": "USD",
            "rates": [{"date": "2025-03-15", "rate_cents": 45000}],
        })
        
        resp = client.post("/bulk", json={
            "unit_ids": [UNIT_ID_1],
            "start_date": "2025-03-15",
            "end_date": "2025-03-17",
        })
        data = resp.json()
        u1 = data["rates"][0]
        assert u1["sum_cents"] is None  # Aggregates null when missing
        assert len(u1["missing_dates"]) > 0
    
    def test_bulk_rejects_over_500(self, clean_db):
        too_many = [f"550e8400-e29b-41d4-a716-{str(i).zfill(12)}" for i in range(501)]
        resp = client.post("/bulk", json={
            "unit_ids": too_many,
            "start_date": "2025-03-15",
            "end_date": "2025-03-17",
        })
        assert resp.status_code == 400
