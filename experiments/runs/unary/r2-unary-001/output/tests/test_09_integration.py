"""
Integration tests — verify services work together.
Tests the full booking flow and cross-service interactions.
"""
import pytest
from fastapi.testclient import TestClient
from datetime import date, timedelta

from services.availability.api import app as availability_app
from services.pricing.api import app as pricing_app
from services.property.api import app as property_app
from services.event.api import app as event_app, _dedup_cache
from services.payments.api import app as payments_app
from services.sync.api import app as sync_app

avail_client = TestClient(availability_app)
pricing_client = TestClient(pricing_app)
property_client = TestClient(property_app)
event_client = TestClient(event_app)
payments_client = TestClient(payments_app)
sync_client = TestClient(sync_app)

UNIT_ID = "550e8400-e29b-41d4-a716-446655440099"


class TestSearchFlow:
    """
    Search Flow Integration:
    1. Availability.search(region, dates) → unit_ids
    2. Pricing.bulk(unit_ids, dates) → aggregated prices
    """
    
    def test_search_to_pricing(self, clean_db, db_connection):
        unit_ids = [
            "550e8400-e29b-41d4-a716-446655440101",
            "550e8400-e29b-41d4-a716-446655440102",
            "550e8400-e29b-41d4-a716-446655440103",
        ]
        
        # Setup: Register units in region
        cur = db_connection.cursor()
        for uid in unit_ids:
            cur.execute("INSERT INTO unit_region (unit_id, region) VALUES (%s, 'US-CO-ASPEN')", (uid,))
        cur.close()
        
        # Setup: Set pricing for all units
        for uid in unit_ids:
            pricing_client.post("/set", json={
                "unit_id": uid,
                "currency": "USD",
                "rates": [
                    {"date": "2025-06-15", "rate_cents": 45000},
                    {"date": "2025-06-16", "rate_cents": 45000},
                    {"date": "2025-06-17", "rate_cents": 50000},
                ],
            })
        
        # Block one unit
        avail_client.post("/acquire", json={
            "unit_id": unit_ids[0],
            "start_date": "2025-06-15",
            "end_date": "2025-06-17",
        })
        
        # Step 1: Search available units
        search = avail_client.get("/search", params={
            "region": "US-CO-ASPEN",
            "start_date": "2025-06-15",
            "end_date": "2025-06-17",
        })
        available = search.json()["unit_ids"]
        assert len(available) == 2  # One blocked
        
        # Step 2: Get pricing for available units
        bulk = pricing_client.post("/bulk", json={
            "unit_ids": available,
            "start_date": "2025-06-15",
            "end_date": "2025-06-17",
        })
        rates = bulk.json()["rates"]
        assert len(rates) == 2
        
        for rate in rates:
            assert rate["sum_cents"] == 140000  # 45000 + 45000 + 50000
            assert rate["missing_dates"] == []


class TestPropertyOverrideFlow:
    """
    Property Override Flow:
    1. Create property (base data from PMS)
    2. PM sets override (priority 20)
    3. Admin sets override (priority 40)
    4. Admin override wins
    """
    
    def test_full_override_hierarchy(self, clean_db):
        # Create property (simulates PMS sync)
        create = property_client.post("/properties", json={
            "name": "Mountain Lodge",
            "description": "A cozy mountain lodge",
            "bedrooms": 3,
            "max_guests": 6,
        })
        pid = create.json()["id"]
        
        # PM thinks it has 4 bedrooms (priority 20)
        property_client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms", "to_value": 4, "priority": 20, "actor": "pm@lodge.com",
        })
        
        prop = property_client.get(f"/properties/{pid}").json()
        assert prop["bedrooms"] == 4
        
        # Wander team corrects to 5 (priority 30)
        property_client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms", "to_value": 5, "priority": 30, "actor": "wander_team",
        })
        
        prop = property_client.get(f"/properties/{pid}").json()
        assert prop["bedrooms"] == 5  # Higher priority wins
        
        # Admin final say: 4 bedrooms (priority 40)
        property_client.post(f"/properties/{pid}/overrides", json={
            "field": "bedrooms", "to_value": 4, "priority": 40, "actor": "admin",
        })
        
        prop = property_client.get(f"/properties/{pid}").json()
        assert prop["bedrooms"] == 4  # Admin (highest priority) wins
        assert prop["has_overrides"] == True


class TestPaymentRefundFlow:
    """
    Payment + Refund Flow:
    1. Create invoice
    2. Pay invoice
    3. Partial refund
    4. Verify amounts
    """
    
    def test_full_payment_lifecycle(self, clean_db):
        booking_id = "550e8400-e29b-41d4-a716-446655550001"
        
        # Create invoice
        inv = payments_client.post("/invoices", json={
            "booking_id": booking_id,
            "customer_id": "cus_int_test",
            "items": [
                {"item_type": "NIGHTLY", "description": "5 nights @ $400", "cents": 200000},
                {"item_type": "CLEANING_FEE", "description": "Cleaning", "cents": 25000},
                {"item_type": "TAX", "description": "Tax", "cents": 27000},
            ],
        })
        inv_id = inv.json()["id"]
        total = inv.json()["total_cents"]
        assert total == 252000
        
        # Pay full amount
        pay = payments_client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_int_test",
            "amount_cents": total,
        })
        assert pay.json()["status"] == "PAID"
        
        # Partial refund (50%)
        refund = payments_client.post("/refunds", json={
            "booking_id": booking_id,
            "amount_cents": 126000,
            "reason": "Cancellation - 50% refund",
        })
        assert refund.json()["status"] == "SUCCEEDED"
        
        # Verify invoice state
        inv_detail = payments_client.get(f"/invoices/{inv_id}")
        data = inv_detail.json()
        assert data["paid_cents"] == 252000
        assert data["refunded_cents"] == 126000


class TestEventAuditTrail:
    """
    Event Audit Flow:
    1. Emit events from various services
    2. Query audit trail
    3. Verify entity timelines
    """
    
    def test_audit_trail(self, clean_db):
        _dedup_cache.clear()
        
        # Simulate booking lifecycle events
        events = [
            ("booking.requested", "booking", "booking_audit_1", {"step": "initiated"}),
            ("booking.confirmed", "booking", "booking_audit_1", {"total_cents": 243660}),
            ("payment.succeeded", "invoice", "inv_audit_1", {"amount_cents": 243660}),
            ("availability.changed", "unit", "unit_audit_1", {"blocked": True}),
        ]
        
        event_ids = []
        for kind, entity_kind, entity_id, ctx in events:
            resp = event_client.post("/events", json={
                "event_kind": kind,
                "entity_kind": entity_kind,
                "entity_id": entity_id,
                "context": ctx,
            })
            event_ids.append(resp.json()["event_id"])
        
        # Query booking timeline
        timeline = event_client.get("/events/audit/entity/booking/booking_audit_1")
        assert len(timeline.json()["events"]) == 2
        
        # Query by event kind
        bookings = event_client.get("/events/audit", params={"event_kind": "booking.confirmed"})
        assert any(e["entity_id"] == "booking_audit_1" for e in bookings.json()["events"])


class TestSyncToAvailabilityFlow:
    """
    Sync → Availability Flow:
    1. Receive PMS webhook
    2. Sync calls Availability.acquire to block dates
    3. Verify dates are blocked
    """
    
    def test_sync_blocks_availability(self, clean_db):
        # Step 1: PMS sends webhook (simulated)
        webhook = sync_client.post("/webhook/guesty", json={
            "event": "calendar.updated",
            "data": {"listing_id": UNIT_ID},
        })
        assert webhook.json()["received"] == True
        
        # Step 2: Sync updates availability (directly, as sync would call)
        acquire = avail_client.post("/acquire", json={
            "unit_id": UNIT_ID,
            "start_date": "2025-07-01",
            "end_date": "2025-07-05",
        })
        assert acquire.json()["status"] == "acquired"
        
        # Step 3: Verify blocked
        check = avail_client.get("/check", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-07-01",
            "end_date": "2025-07-05",
        })
        assert check.json()["available"] == False


class TestPricingToBookingFlow:
    """
    Pricing → Booking Data Flow:
    1. Set rates via Pricing Service
    2. Query rates (as Booking Service would)
    3. Verify rate data available for quote
    """
    
    def test_rates_available_for_booking(self, clean_db):
        # Sync sets rates (as PMS sync would)
        pricing_client.post("/set", json={
            "unit_id": UNIT_ID,
            "currency": "USD",
            "rates": [
                {"date": "2025-08-01", "rate_cents": 55000},
                {"date": "2025-08-02", "rate_cents": 55000},
                {"date": "2025-08-03", "rate_cents": 60000},
                {"date": "2025-08-04", "rate_cents": 60000},
                {"date": "2025-08-05", "rate_cents": 55000},
            ],
        })
        
        # Booking Service queries rates for quote
        rates = pricing_client.get("/rate", params={
            "unit_id": UNIT_ID,
            "start_date": "2025-08-01",
            "end_date": "2025-08-05",
        })
        data = rates.json()
        assert len(data["rates"]) == 5
        assert data["missing_dates"] == []
        
        # Calculate total (as Booking Service would)
        total = sum(r["rate_cents"] for r in data["rates"])
        assert total == 285000  # 55000*3 + 60000*2


class TestEndToEndBookingSimulation:
    """
    Simulated end-to-end booking flow (without HTTP calls between services).
    Tests the data flow: property → availability → pricing → booking → payment.
    """
    
    def test_complete_booking_data_flow(self, clean_db, db_connection):
        prop_id = "550e8400-e29b-41d4-a716-446655440200"
        
        # 1. Create property
        prop = property_client.post("/properties", json={
            "name": "Alpine Chalet",
            "region": "US-CO-ASPEN",
            "is_bookable": True,
            "bedrooms": 4,
            "max_guests": 10,
            "currency": "USD",
        })
        actual_pid = prop.json()["id"]
        
        # Register unit for availability
        cur = db_connection.cursor()
        cur.execute("INSERT INTO unit_region (unit_id, region) VALUES (%s, 'US-CO-ASPEN') ON CONFLICT DO NOTHING", (prop_id,))
        cur.close()
        
        # 2. Set up pricing
        pricing_client.post("/set", json={
            "unit_id": prop_id,
            "currency": "USD",
            "rates": [
                {"date": "2025-09-01", "rate_cents": 50000},
                {"date": "2025-09-02", "rate_cents": 50000},
                {"date": "2025-09-03", "rate_cents": 55000},
            ],
        })
        
        # 3. Verify availability
        check = avail_client.get("/check", params={
            "unit_id": prop_id,
            "start_date": "2025-09-01",
            "end_date": "2025-09-03",
        })
        assert check.json()["available"] == True
        
        # 4. Get rates for quote
        rates = pricing_client.get("/rate", params={
            "unit_id": prop_id,
            "start_date": "2025-09-01",
            "end_date": "2025-09-03",
        })
        rate_data = rates.json()
        subtotal = sum(r["rate_cents"] for r in rate_data["rates"])
        assert subtotal == 155000
        
        # 5. Acquire dates
        acquire = avail_client.post("/acquire", json={
            "unit_id": prop_id,
            "start_date": "2025-09-01",
            "end_date": "2025-09-03",
        })
        assert acquire.json()["status"] == "acquired"
        
        # 6. Verify dates now blocked
        check2 = avail_client.get("/check", params={
            "unit_id": prop_id,
            "start_date": "2025-09-01",
            "end_date": "2025-09-03",
        })
        assert check2.json()["available"] == False
        
        # 7. Create invoice
        booking_id = "550e8400-e29b-41d4-a716-446655440201"
        tax = round(subtotal * 0.12)
        total = subtotal + tax
        
        inv = payments_client.post("/invoices", json={
            "booking_id": booking_id,
            "customer_id": "cus_e2e",
            "items": [
                {"item_type": "NIGHTLY", "description": f"3 nights", "cents": subtotal},
                {"item_type": "TAX", "description": "Tax", "cents": tax},
            ],
        })
        assert inv.status_code == 200
        
        # 8. Pay invoice
        pay = payments_client.post(f"/invoices/{inv.json()['id']}/pay", json={
            "stripe_payment_intent_id": "pi_e2e_test",
            "amount_cents": total,
        })
        assert pay.json()["status"] == "PAID"
        
        # 9. Create PMS reservation
        pms = sync_client.post("/sync/pms/reservation", json={
            "property_id": prop_id,
            "check_in": "2025-09-01",
            "check_out": "2025-09-04",
            "guest_name": "Test Guest",
            "guest_email": "guest@test.com",
        })
        assert pms.json()["status"] == "CONFIRMED"
        
        # Final: Dates still blocked, payment processed, PMS confirmed
        final_check = avail_client.get("/check", params={
            "unit_id": prop_id,
            "start_date": "2025-09-01",
            "end_date": "2025-09-03",
        })
        assert final_check.json()["available"] == False
