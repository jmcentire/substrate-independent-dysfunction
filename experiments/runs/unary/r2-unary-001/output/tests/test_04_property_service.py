"""
Tests for Property Service API.
Verifies 4-tier override hierarchy and conflict resolution.
"""
import pytest
from fastapi.testclient import TestClient
from services.property.api import app

client = TestClient(app)


class TestCreateProperty:
    def test_create_property(self, clean_db):
        resp = client.post("/properties", json={
            "name": "Sunset Retreat",
            "region": "US-CO-ASPEN",
            "is_bookable": True,
            "bedrooms": 3,
            "bathrooms": 2.5,
            "max_guests": 8,
            "city": "Aspen",
            "state": "Colorado",
            "currency": "USD",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Sunset Retreat"
        assert "id" in data
    
    def test_create_with_amenities(self, clean_db):
        resp = client.post("/properties", json={
            "name": "Pool Villa",
            "amenities": ["pool", "hot_tub", "ski_in_ski_out"],
            "max_guests": 12,
        })
        assert resp.status_code == 200


class TestGetProperty:
    def test_get_property(self, clean_db):
        # Create
        create = client.post("/properties", json={
            "name": "Test Property",
            "bedrooms": 2,
            "max_guests": 4,
        })
        pid = create.json()["id"]
        
        # Get
        resp = client.get(f"/properties/{pid}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Property"
        assert data["bedrooms"] == 2
        assert data["has_overrides"] == False
        assert data["has_conflicts"] == False
    
    def test_property_not_found(self):
        resp = client.get("/properties/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


class TestListProperties:
    def test_list_properties(self, clean_db):
        # Create some properties
        for name in ["Prop A", "Prop B", "Prop C"]:
            client.post("/properties", json={"name": name})
        
        resp = client.get("/properties")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["data"]) == 3
    
    def test_list_with_filter(self, clean_db):
        client.post("/properties", json={"name": "Active", "status": "ACTIVE"})
        client.post("/properties", json={"name": "Inactive", "status": "INACTIVE"})
        
        resp = client.get("/properties", params={"status": "ACTIVE"})
        data = resp.json()
        assert all(p["status"] == "ACTIVE" for p in data["data"])


class TestOverrideHierarchy:
    """Test 4-tier override: PMS(10) < PM(20) < Wander(30) < Admin(40)."""
    
    def test_create_override(self, clean_db):
        create = client.post("/properties", json={"name": "Override Test", "bedrooms": 2})
        pid = create.json()["id"]
        
        resp = client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms",
            "from_value": 2,
            "to_value": 3,
            "priority": 20,  # PM override
            "actor": "property_manager@test.com",
            "note": "Corrected bedroom count",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"
    
    def test_override_applies_to_effective_data(self, clean_db):
        create = client.post("/properties", json={"name": "Override Apply", "bedrooms": 2})
        pid = create.json()["id"]
        
        # Set PM override (priority 20)
        client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms",
            "to_value": 3,
            "priority": 20,
            "actor": "pm",
        })
        
        # Get effective data
        resp = client.get(f"/properties/{pid}")
        data = resp.json()
        assert data["bedrooms"] == 3  # Override applied
        assert data["has_overrides"] == True
    
    def test_higher_priority_wins(self, clean_db):
        create = client.post("/properties", json={"name": "Priority Test", "bedrooms": 2})
        pid = create.json()["id"]
        
        # PM says 3 bedrooms (priority 20)
        client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms", "to_value": 3, "priority": 20, "actor": "pm",
        })
        
        # Wander says 4 bedrooms (priority 30)
        client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms", "to_value": 4, "priority": 30, "actor": "wander",
        })
        
        resp = client.get(f"/properties/{pid}")
        assert resp.json()["bedrooms"] == 4  # Higher priority wins
    
    def test_duplicate_override_rejected(self, clean_db):
        create = client.post("/properties", json={"name": "Dup Test"})
        pid = create.json()["id"]
        
        client.post(f"/properties/{pid}/overrides", json={
            "field": "name", "to_value": "New Name", "priority": 20, "actor": "pm",
        })
        
        resp = client.post(f"/properties/{pid}/overrides", json={
            "field": "name", "to_value": "Another Name", "priority": 20, "actor": "pm",
        })
        assert resp.status_code == 409  # OVERRIDE_EXISTS


class TestFees:
    def test_get_fees(self, clean_db, db_connection):
        # Create property
        create = client.post("/properties", json={"name": "Fee Test", "currency": "USD"})
        pid = create.json()["id"]
        
        # Insert fees directly
        cur = db_connection.cursor()
        cur.execute("""
            INSERT INTO property_fee (property_id, kind, label, amount_cents, per, required)
            VALUES (%s, 'cleaning', 'Cleaning Fee', 25000, 'PER_STAY', true),
                   (%s, 'pet', 'Pet Fee', 15000, 'PER_PET', false)
        """, (pid, pid))
        cur.close()
        
        resp = client.get(f"/properties/{pid}/fees")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["fees"]) == 2
        assert data["currency"] == "USD"


class TestPolicies:
    def test_get_policies(self, clean_db):
        create = client.post("/properties", json={"name": "Policy Test", "max_guests": 8})
        pid = create.json()["id"]
        
        resp = client.get(f"/properties/{pid}/policies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_guests"] == 8
        assert "cancellation_policy" in data


class TestCacheFlush:
    def test_flush_cache(self, clean_db):
        create = client.post("/properties", json={"name": "Cache Test"})
        pid = create.json()["id"]
        
        resp = client.post(f"/properties/{pid}/cache/flush")
        assert resp.status_code == 200
        assert resp.json()["status"] == "flushed"
