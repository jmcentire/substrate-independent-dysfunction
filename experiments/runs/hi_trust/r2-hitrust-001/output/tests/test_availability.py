"""
Tests for Availability Service.

Tests bitmask operations, store logic, and API endpoints.
"""

import pytest
from datetime import date
from httpx import AsyncClient, ASGITransport

from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.availability.bitmask import (
    date_range_to_masks, check_conflict, apply_block, release_block,
    compute_contiguous_metadata, find_next_available_window, mask_to_string
)
from services.availability.store import AvailabilityStore
from services.availability.app import app, set_store


# ---- Bitmask Unit Tests ----

class TestBitmask:
    def test_date_range_to_masks_single_month(self):
        """March 15-19 should produce a single month mask."""
        masks = date_range_to_masks(date(2025, 3, 15), date(2025, 3, 19))
        assert "2025-03" in masks
        assert len(masks) == 1
        # Bits 15, 16, 17, 18, 19 should be set
        mask = masks["2025-03"]
        for d in range(15, 20):
            assert mask & (1 << d), f"Day {d} should be set"
        for d in [1, 14, 20, 31]:
            assert not (mask & (1 << d)), f"Day {d} should NOT be set"

    def test_date_range_to_masks_cross_month(self):
        """Range spanning two months should produce two masks."""
        masks = date_range_to_masks(date(2025, 3, 28), date(2025, 4, 3))
        assert "2025-03" in masks
        assert "2025-04" in masks
        assert len(masks) == 2

    def test_check_conflict_no_overlap(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)  # Days 5-7
        request = (1 << 10) | (1 << 11)              # Days 10-11
        assert not check_conflict(existing, request)

    def test_check_conflict_overlap(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)  # Days 5-7
        request = (1 << 6) | (1 << 7) | (1 << 8)    # Days 6-8
        assert check_conflict(existing, request)

    def test_apply_block(self):
        existing = (1 << 5) | (1 << 6)
        block = (1 << 10) | (1 << 11)
        result = apply_block(existing, block)
        assert result & (1 << 5)
        assert result & (1 << 6)
        assert result & (1 << 10)
        assert result & (1 << 11)

    def test_release_block(self):
        existing = (1 << 5) | (1 << 6) | (1 << 7)
        release = (1 << 6)
        result = release_block(existing, release)
        assert result & (1 << 5)
        assert not (result & (1 << 6))
        assert result & (1 << 7)

    def test_contiguous_metadata_all_free(self):
        meta = compute_contiguous_metadata(0, "2025-03")
        assert meta["start_free"] == 31
        assert meta["end_free"] == 31
        assert meta["max_free"] == 31

    def test_contiguous_metadata_partial_block(self):
        # Block days 10-15
        mask = 0
        for d in range(10, 16):
            mask |= (1 << d)
        meta = compute_contiguous_metadata(mask, "2025-03")
        assert meta["start_free"] == 9  # Days 1-9 free
        assert meta["end_free"] == 16  # Days 16-31 free
        assert meta["max_free"] == 16  # Longest run: 16-31

    def test_find_next_available_window(self):
        # Block days 1-10 in June 2025
        mask_june = 0
        for d in range(1, 11):
            mask_june |= (1 << d)
        masks = {"2025-06": mask_june}
        
        found, start = find_next_available_window(
            masks, 5, date(2025, 6, 1), 12
        )
        assert found
        assert start == "2025-06-11"

    def test_find_next_available_window_not_found(self):
        # Block everything
        mask = 0xFFFFFFFF
        masks = {}
        for m in range(1, 13):
            masks[f"2025-{m:02d}"] = mask
        
        found, start = find_next_available_window(
            masks, 5, date(2025, 1, 1), 12
        )
        assert not found
        assert start is None

    def test_mask_to_string(self):
        mask = (1 << 5) | (1 << 6) | (1 << 7)
        s = mask_to_string(mask, 10)
        assert s == "0000111000"


# ---- Store Tests ----

class TestAvailabilityStore:
    def setup_method(self):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = AvailabilityStore(self.db, self.cache)
        self.unit_id = "550e8400-e29b-41d4-a716-446655440000"

    def test_check_available(self):
        """Unit with no blocks should be fully available."""
        result = self.store.check(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        assert result is True

    def test_acquire_and_check(self):
        """After acquiring, dates should show as blocked."""
        success, status = self.store.acquire(
            self.unit_id, date(2025, 6, 1), date(2025, 6, 5)
        )
        assert success is True
        assert status == "acquired"

        result = self.store.check(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        assert result is False

    def test_acquire_conflict(self):
        """Double-acquiring same dates should fail with conflict."""
        self.store.acquire(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        success, status = self.store.acquire(
            self.unit_id, date(2025, 6, 3), date(2025, 6, 7)
        )
        assert success is False
        assert status == "conflict"

    def test_release(self):
        """After releasing, dates should be available again."""
        self.store.acquire(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        self.store.release(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        result = self.store.check(self.unit_id, date(2025, 6, 1), date(2025, 6, 5))
        assert result is True

    def test_search_by_region(self):
        """Search should return available units in a region."""
        uid1 = "550e8400-0001-0000-0000-000000000000"
        uid2 = "550e8400-0002-0000-0000-000000000000"
        uid3 = "550e8400-0003-0000-0000-000000000000"

        self.store.register_unit(uid1, "US-CO-ASPEN")
        self.store.register_unit(uid2, "US-CO-ASPEN")
        self.store.register_unit(uid3, "US-CO-ASPEN")

        # Block uid2 for the search dates
        self.store.acquire(uid2, date(2025, 6, 1), date(2025, 6, 5))

        results = self.store.search("US-CO-ASPEN", date(2025, 6, 1), date(2025, 6, 5))
        assert uid1 in results
        assert uid2 not in results
        assert uid3 in results
        assert len(results) == 2

    def test_next_available(self):
        """Should find next available window after blocked dates."""
        self.store.acquire(self.unit_id, date(2025, 6, 1), date(2025, 6, 10))
        found, start = self.store.next_available(self.unit_id, 5, date(2025, 6, 1))
        assert found is True
        assert start == "2025-06-11"

    def test_cache_populated_on_read(self):
        """Reading should populate the cache."""
        self.store.check(self.unit_id, date(2025, 6, 1), date(2025, 6, 1))
        cache_key = f"avail:{self.unit_id}:2025-06"
        assert self.cache.get(cache_key) is not None


# ---- API Tests ----

@pytest.mark.asyncio
class TestAvailabilityAPI:
    async def setup_method(self, method):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = AvailabilityStore(self.db, self.cache)
        set_store(self.store)
        self.unit_id = "550e8400-e29b-41d4-a716-446655440000"

    async def test_check_available(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/availability/check",
                params={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            assert resp.status_code == 200
            assert resp.json()["available"] is True

    async def test_check_invalid_dates(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/availability/check",
                params={"unit_id": self.unit_id, "start_date": "2025-06-05", "end_date": "2025-06-01"}
            )
            assert resp.status_code == 400

    async def test_acquire_and_check_blocked(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Acquire
            resp = await client.post(
                "/api/v1/availability/acquire",
                json={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "acquired"

            # Check should show blocked
            resp = await client.get(
                "/api/v1/availability/check",
                params={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            assert resp.status_code == 200
            assert resp.json()["available"] is False

    async def test_acquire_conflict(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/v1/availability/acquire",
                json={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            resp = await client.post(
                "/api/v1/availability/acquire",
                json={"unit_id": self.unit_id, "start_date": "2025-06-03", "end_date": "2025-06-07"}
            )
            assert resp.status_code == 409

    async def test_release(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await client.post(
                "/api/v1/availability/acquire",
                json={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            resp = await client.post(
                "/api/v1/availability/release",
                json={"unit_id": self.unit_id, "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            assert resp.status_code == 200
            assert resp.json()["status"] == "released"

    async def test_next_available(self):
        self.store.acquire(self.unit_id, date(2025, 6, 1), date(2025, 6, 10))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/availability/next-available",
                params={"unit_id": self.unit_id, "nights": 5, "after": "2025-06-01"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["found"] is True
            assert data["start_date"] == "2025-06-11"

    async def test_search_by_region(self):
        uid1 = "550e8400-0001-0000-0000-000000000000"
        uid2 = "550e8400-0002-0000-0000-000000000000"
        self.store.register_unit(uid1, "US-CO-ASPEN")
        self.store.register_unit(uid2, "US-CO-ASPEN")
        self.store.acquire(uid2, date(2025, 6, 1), date(2025, 6, 5))

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/availability/search",
                params={"region": "US-CO-ASPEN", "start_date": "2025-06-01", "end_date": "2025-06-05"}
            )
            assert resp.status_code == 200
            data = resp.json()
            assert uid1 in data["unit_ids"]
            assert uid2 not in data["unit_ids"]

    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["service"] == "availability"
