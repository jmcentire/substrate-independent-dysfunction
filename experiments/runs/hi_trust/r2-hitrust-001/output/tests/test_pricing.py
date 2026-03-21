"""
Tests for Pricing Service.

Tests rate storage, cache behavior, bulk queries, and API endpoints.
"""

import pytest
from datetime import date
from httpx import AsyncClient, ASGITransport

from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.pricing.store import PricingStore
from services.pricing.app import app, set_store


UNIT_ID_1 = "550e8400-e29b-41d4-a716-446655440001"
UNIT_ID_2 = "550e8400-e29b-41d4-a716-446655440002"
UNIT_ID_3 = "550e8400-e29b-41d4-a716-446655440003"


class TestPricingStore:
    def setup_method(self):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PricingStore(self.db, self.cache)

    def test_set_and_get_single_rate(self):
        """Set a rate and retrieve it."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000}
        ])
        currency, rates, missing = self.store.get_rate(UNIT_ID_1, date(2025, 3, 15))
        assert len(rates) == 1
        assert rates[0]["rate_cents"] == 45000
        assert len(missing) == 0

    def test_set_and_get_range(self):
        """Set multiple rates and retrieve a range."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-16", "rate_cents": 45000},
            {"date": "2025-03-17", "rate_cents": 52000},
        ])
        currency, rates, missing = self.store.get_rate(
            UNIT_ID_1, date(2025, 3, 15), date(2025, 3, 17)
        )
        assert len(rates) == 3
        assert currency == "USD"
        assert len(missing) == 0

    def test_missing_dates(self):
        """Dates without rates should appear in missing_dates."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-16", "rate_cents": 45000},
        ])
        currency, rates, missing = self.store.get_rate(
            UNIT_ID_1, date(2025, 3, 15), date(2025, 3, 19)
        )
        assert len(rates) == 2
        assert len(missing) == 3
        assert "2025-03-17" in missing
        assert "2025-03-18" in missing
        assert "2025-03-19" in missing

    def test_rate_update(self):
        """Updating a rate should overwrite the old value."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000}
        ])
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 50000}
        ])
        _, rates, _ = self.store.get_rate(UNIT_ID_1, date(2025, 3, 15))
        assert rates[0]["rate_cents"] == 50000

    def test_calendar(self):
        """Calendar should return all days in a month."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-01", "rate_cents": 40000},
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-31", "rate_cents": 50000},
        ])
        currency, rates, missing_days = self.store.get_calendar(UNIT_ID_1, "2025-03")
        assert len(rates) == 3
        assert len(missing_days) == 28  # 31 - 3

    def test_bulk_rates(self):
        """Bulk should return aggregates for multiple units."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-16", "rate_cents": 50000},
        ])
        self.store.set_rates(UNIT_ID_2, "USD", [
            {"date": "2025-03-15", "rate_cents": 35000},
            {"date": "2025-03-16", "rate_cents": 35000},
        ])

        results = self.store.bulk_rates(
            [UNIT_ID_1, UNIT_ID_2],
            date(2025, 3, 15), date(2025, 3, 16)
        )
        assert len(results) == 2

        r1 = next(r for r in results if r["unit_id"] == UNIT_ID_1)
        assert r1["sum_cents"] == 95000
        assert r1["avg_cents"] == 47500
        assert r1["min_cents"] == 45000
        assert r1["max_cents"] == 50000

        r2 = next(r for r in results if r["unit_id"] == UNIT_ID_2)
        assert r2["sum_cents"] == 70000

    def test_bulk_with_missing(self):
        """Bulk aggregates should be null if any date is missing."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
        ])
        results = self.store.bulk_rates(
            [UNIT_ID_1],
            date(2025, 3, 15), date(2025, 3, 16)
        )
        r = results[0]
        assert r["sum_cents"] is None  # Null because 3/16 missing
        assert len(r["missing_dates"]) == 1

    def test_cache_populated(self):
        """Reading should populate the cache."""
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000}
        ])
        self.store.get_rate(UNIT_ID_1, date(2025, 3, 15))
        cached = self.cache.get(f"price:{UNIT_ID_1}:2025-03-15")
        assert cached == "45000"


@pytest.mark.asyncio
class TestPricingAPI:
    async def setup_method(self, method):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PricingStore(self.db, self.cache)
        set_store(self.store)

    async def test_set_and_get_rates(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Set rates
            resp = await client.post("/api/v1/pricing/set", json={
                "unit_id": UNIT_ID_1,
                "currency": "USD",
                "rates": [
                    {"date": "2025-03-15", "rate_cents": 45000},
                    {"date": "2025-03-16", "rate_cents": 45000},
                ]
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "updated"
            assert resp.json()["count"] == 2

            # Get rates
            resp = await client.get(
                "/api/v1/pricing/rate",
                params={"unit_id": UNIT_ID_1, "start_date": "2025-03-15", "end_date": "2025-03-16"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["rates"]) == 2
            assert len(data["missing_dates"]) == 0

    async def test_get_rate_with_missing(self):
        self.store.set_rates(UNIT_ID_2, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-16", "rate_cents": 45000},
        ])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/pricing/rate",
                params={"unit_id": UNIT_ID_2, "start_date": "2025-03-15", "end_date": "2025-03-19"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert len(data["rates"]) == 2
            assert len(data["missing_dates"]) == 3

    async def test_invalid_date_range(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/pricing/rate",
                params={"unit_id": UNIT_ID_1, "start_date": "2025-03-20", "end_date": "2025-03-15"}
            )
            assert resp.status_code == 400

    async def test_bulk_rates(self):
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
            {"date": "2025-03-16", "rate_cents": 45000},
        ])
        self.store.set_rates(UNIT_ID_2, "USD", [
            {"date": "2025-03-15", "rate_cents": 35000},
            {"date": "2025-03-16", "rate_cents": 35000},
        ])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/pricing/bulk", json={
                "unit_ids": [UNIT_ID_1, UNIT_ID_2],
                "start_date": "2025-03-15",
                "end_date": "2025-03-16"
            })
            assert resp.status_code == 200
            assert len(resp.json()["rates"]) == 2

    async def test_bulk_rejects_over_500(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unit_ids = [f"550e8400-e29b-41d4-a716-{str(i).zfill(12)}" for i in range(501)]
            resp = await client.post("/api/v1/pricing/bulk", json={
                "unit_ids": unit_ids,
                "start_date": "2025-03-15",
                "end_date": "2025-03-16"
            })
            assert resp.status_code == 400

    async def test_calendar(self):
        self.store.set_rates(UNIT_ID_1, "USD", [
            {"date": "2025-03-15", "rate_cents": 45000},
        ])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/pricing/calendar",
                params={"unit_id": UNIT_ID_1, "month": "2025-03"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["month"] == "2025-03"
            assert len(data["rates"]) == 1

    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
