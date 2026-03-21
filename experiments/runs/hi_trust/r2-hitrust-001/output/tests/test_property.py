"""
Tests for Property Service.

Tests 4-tier override hierarchy, conflict resolution, and API endpoints.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.property.store import PropertyStore, TIER_PMS, TIER_PM, TIER_WANDER, TIER_ADMIN
from services.property.app import app, set_store


class TestPropertyStore:
    def setup_method(self):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PropertyStore(self.db, self.cache)

    def test_create_and_get_property(self):
        """Create a property and retrieve it."""
        prop = self.store.create_property({
            "name": "Sunset Retreat",
            "city": "Aspen",
            "state": "CO",
            "bedrooms": 3,
            "bathrooms": 2.5,
            "max_guests": 8,
            "is_bookable": True,
        })
        assert prop["name"] == "Sunset Retreat"
        assert prop["city"] == "Aspen"
        assert prop["bedrooms"] == 3
        assert prop["is_bookable"] is True

        # Retrieve
        fetched = self.store.get_property(prop["id"])
        assert fetched["name"] == "Sunset Retreat"

    def test_override_hierarchy(self):
        """Higher priority overrides should win."""
        prop = self.store.create_property({"name": "Original Name"})
        pid = prop["id"]

        # PMS override (priority 10)
        self.store.set_override(pid, "name", None, "PMS Name", TIER_PMS)

        # PM override (priority 20) - should win over PMS
        self.store.set_override(pid, "name", None, "PM Name", TIER_PM)

        fetched = self.store.get_property(pid)
        assert fetched["name"] == "PM Name"  # PM wins over PMS

        # Wander override (priority 30) - should win over PM
        self.store.set_override(pid, "name", None, "Wander Name", TIER_WANDER)

        fetched = self.store.get_property(pid)
        assert fetched["name"] == "Wander Name"

        # Admin override (priority 40) - highest, always wins
        self.store.set_override(pid, "name", None, "Admin Name", TIER_ADMIN)

        fetched = self.store.get_property(pid)
        assert fetched["name"] == "Admin Name"

    def test_override_per_field(self):
        """Overrides apply per-field, not as a whole document."""
        prop = self.store.create_property({
            "name": "Original",
            "city": "Aspen",
            "bedrooms": 3,
        })
        pid = prop["id"]

        # Override only the name
        self.store.set_override(pid, "name", "Original", "New Name", TIER_PM)

        fetched = self.store.get_property(pid)
        assert fetched["name"] == "New Name"
        assert fetched["city"] == "Aspen"  # Unchanged
        assert fetched["bedrooms"] == 3     # Unchanged

    def test_has_overrides_flag(self):
        """Property should indicate when overrides exist."""
        prop = self.store.create_property({"name": "Test"})
        pid = prop["id"]

        fetched = self.store.get_property(pid)
        assert fetched["has_overrides"] is False

        self.store.set_override(pid, "name", None, "Override", TIER_PM)
        fetched = self.store.get_property(pid)
        assert fetched["has_overrides"] is True

    def test_fees(self):
        """Property fees can be set and retrieved."""
        prop = self.store.create_property({"name": "Test"})
        pid = prop["id"]

        self.store.set_fee(pid, {
            "kind": "cleaning",
            "label": "Cleaning Fee",
            "amount_cents": 15000,
            "per": "PER_STAY",
            "required": True,
        })
        self.store.set_fee(pid, {
            "kind": "pet",
            "label": "Pet Fee",
            "amount_cents": 5000,
            "per": "PER_STAY",
            "required": False,
        })

        fees = self.store.get_fees(pid)
        assert len(fees) == 2
        kinds = [f["kind"] for f in fees]
        assert "cleaning" in kinds
        assert "pet" in kinds

    def test_policies(self):
        """Property policies can be set and retrieved."""
        prop = self.store.create_property({"name": "Test"})
        pid = prop["id"]

        self.store.set_policies(pid, {
            "min_nights": 3,
            "max_nights": 30,
            "cancellation_policy": "MODERATE",
            "pets_allowed": True,
        })

        policies = self.store.get_policies(pid)
        assert policies["min_nights"] == 3
        assert policies["cancellation_policy"] == "MODERATE"
        assert policies["pets_allowed"] is True

    def test_list_properties(self):
        """List properties with filters."""
        self.store.create_property({"name": "Prop1", "city": "Aspen", "status": "ACTIVE"})
        self.store.create_property({"name": "Prop2", "city": "Aspen", "status": "ACTIVE"})
        self.store.create_property({"name": "Prop3", "city": "Vail", "status": "ACTIVE"})

        # Filter by city
        props, cursor = self.store.list_properties({"city": "Aspen"})
        assert len(props) == 2

        # No filter
        all_props, _ = self.store.list_properties()
        assert len(all_props) == 3

    def test_cache_invalidation(self):
        """Override should invalidate cache."""
        prop = self.store.create_property({"name": "Test"})
        pid = prop["id"]

        # First fetch caches it
        self.store.get_property(pid)
        assert self.cache.get(f"property:{pid}") is not None

        # Override invalidates cache
        self.store.set_override(pid, "name", None, "New Name", TIER_PM)
        assert self.cache.get(f"property:{pid}") is None

        # Next fetch gets fresh data
        fetched = self.store.get_property(pid)
        assert fetched["name"] == "New Name"


@pytest.mark.asyncio
class TestPropertyAPI:
    async def setup_method(self, method):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PropertyStore(self.db, self.cache)
        set_store(self.store)

    async def test_create_and_get(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Create
            resp = await client.post("/api/v1/properties", json={
                "name": "Sunset Retreat",
                "city": "Aspen",
                "state": "CO",
                "bedrooms": 3,
                "max_guests": 8,
            })
            assert resp.status_code == 200
            prop = resp.json()
            pid = prop["id"]

            # Get
            resp = await client.get(f"/api/v1/properties/{pid}")
            assert resp.status_code == 200
            assert resp.json()["name"] == "Sunset Retreat"

    async def test_not_found(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/properties/nonexistent")
            assert resp.status_code == 404

    async def test_set_override(self):
        prop = self.store.create_property({"name": "Test"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/properties/{prop['id']}/overrides", json={
                "field": "name",
                "to_value": "Overridden Name",
                "priority": 20,
            })
            assert resp.status_code == 200

            resp = await client.get(f"/api/v1/properties/{prop['id']}")
            assert resp.json()["name"] == "Overridden Name"

    async def test_fees_endpoint(self):
        prop = self.store.create_property({"name": "Test"})
        self.store.set_fee(prop["id"], {
            "kind": "cleaning", "label": "Cleaning", "amount_cents": 15000
        })
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/api/v1/properties/{prop['id']}/fees")
            assert resp.status_code == 200
            assert len(resp.json()["fees"]) == 1

    async def test_policies_endpoint(self):
        prop = self.store.create_property({"name": "Test"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/properties/{prop['id']}/policies", json={
                "min_nights": 3,
                "pets_allowed": True,
            })
            assert resp.status_code == 200

            resp = await client.get(f"/api/v1/properties/{prop['id']}/policies")
            assert resp.status_code == 200
            assert resp.json()["min_nights"] == 3

    async def test_cache_flush(self):
        prop = self.store.create_property({"name": "Test"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/properties/{prop['id']}/cache/flush")
            assert resp.status_code == 200
            assert resp.json()["status"] == "flushed"

    async def test_list_properties(self):
        self.store.create_property({"name": "P1", "city": "Aspen"})
        self.store.create_property({"name": "P2", "city": "Aspen"})
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/properties", params={"city": "Aspen"})
            assert resp.status_code == 200
            assert len(resp.json()["data"]) == 2

    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
