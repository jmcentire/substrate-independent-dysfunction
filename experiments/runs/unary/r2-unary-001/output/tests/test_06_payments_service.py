"""
Tests for Payments Service API.
Verifies invoices, refunds, hash-chained payouts, reconciliation.
"""
import pytest
from fastapi.testclient import TestClient
from services.payments.api import app, compute_payout_hash

client = TestClient(app)

BOOKING_ID = "550e8400-e29b-41d4-a716-446655440001"
BOOKING_ID_2 = "550e8400-e29b-41d4-a716-446655440002"
CUSTOMER_ID = "cus_test123"


class TestCreateInvoice:
    def test_create_invoice(self, clean_db):
        resp = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [
                {"item_type": "NIGHTLY", "description": "5 nights @ $450", "cents": 225000},
                {"item_type": "CLEANING_FEE", "description": "Cleaning Fee", "cents": 25000},
                {"item_type": "TAX", "description": "Occupancy Tax", "cents": 30000},
            ],
            "currency": "USD",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "OPEN"
        assert data["total_cents"] == 280000  # sum of all items
        assert data["invoice_number"].startswith("INV-")
    
    def test_duplicate_invoice_rejected(self, clean_db):
        items = [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}]
        
        client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": items,
        })
        
        resp = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": items,
        })
        assert resp.status_code == 409


class TestGetInvoice:
    def test_get_invoice_with_items(self, clean_db):
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [
                {"item_type": "NIGHTLY", "description": "5 nights", "cents": 225000},
            ],
        })
        inv_id = create.json()["id"]
        
        resp = client.get(f"/invoices/{inv_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total_cents"] == 225000


class TestPayInvoice:
    def test_pay_invoice(self, clean_db):
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        resp = client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_test123",
            "amount_cents": 100000,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "PAID"
        assert resp.json()["due_cents"] == 0
    
    def test_partial_payment(self, clean_db):
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        resp = client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_test456",
            "amount_cents": 50000,
        })
        assert resp.json()["status"] == "PARTIALLY_PAID"
        assert resp.json()["due_cents"] == 50000


class TestVoidInvoice:
    def test_void_invoice(self, clean_db):
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        resp = client.post(f"/invoices/{inv_id}/void")
        assert resp.status_code == 200
        assert resp.json()["status"] == "VOID"


class TestRefunds:
    def test_create_refund(self, clean_db):
        # Create and pay invoice
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_test789",
            "amount_cents": 100000,
        })
        
        # Refund
        resp = client.post("/refunds", json={
            "booking_id": BOOKING_ID,
            "amount_cents": 50000,
            "reason": "Guest cancellation",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["amount_cents"] == 50000
        assert data["status"] == "SUCCEEDED"
        assert data["stripe_refund_id"].startswith("re_")
    
    def test_refund_exceeds_paid(self, clean_db):
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_test_abc",
            "amount_cents": 100000,
        })
        
        resp = client.post("/refunds", json={
            "booking_id": BOOKING_ID,
            "amount_cents": 150000,  # Exceeds paid amount
            "reason": "Too much",
        })
        assert resp.status_code == 400
        assert resp.json()["detail"]["code"] == "EXCEEDS_PAID"


class TestPayoutHashChain:
    """Test immutable, hash-chained payout snapshots."""
    
    def test_payout_hash_deterministic(self):
        """Same inputs → same hash."""
        h1 = compute_payout_hash("owner_1", [{"booking_id": "b1", "gross_cents": 100}], {"total": 85})
        h2 = compute_payout_hash("owner_1", [{"booking_id": "b1", "gross_cents": 100}], {"total": 85})
        assert h1 == h2
    
    def test_payout_hash_chain(self):
        """Hash chain links payout snapshots."""
        h1 = compute_payout_hash("owner_1", [{"booking_id": "b1"}], {"total": 100})
        h2 = compute_payout_hash("owner_1", [{"booking_id": "b2"}], {"total": 200}, prev_hash=h1)
        h3 = compute_payout_hash("owner_1", [{"booking_id": "b3"}], {"total": 300}, prev_hash=h2)
        
        # Each hash is different
        assert h1 != h2
        assert h2 != h3
        
        # Changing prev_hash changes result
        h2_alt = compute_payout_hash("owner_1", [{"booking_id": "b2"}], {"total": 200}, prev_hash="tampered")
        assert h2 != h2_alt
    
    def test_run_payout(self, clean_db):
        # Create and pay an invoice first
        create = client.post("/invoices", json={
            "booking_id": BOOKING_ID,
            "customer_id": CUSTOMER_ID,
            "items": [{"item_type": "NIGHTLY", "description": "Test", "cents": 100000}],
        })
        inv_id = create.json()["id"]
        
        client.post(f"/invoices/{inv_id}/pay", json={
            "stripe_payment_intent_id": "pi_payout_test",
            "amount_cents": 100000,
        })
        
        resp = client.post("/payouts/run", json={
            "cycle": "2025-W12",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["cycle"] == "2025-W12"
        assert len(data["payout_snapshots"]) > 0


class TestVerifyChain:
    def test_verify_empty_chain(self, clean_db):
        resp = client.get("/payouts/verify-chain", params={"owner_id": "nonexistent"})
        assert resp.status_code == 200
        assert resp.json()["valid"] == True
        assert resp.json()["snapshots_checked"] == 0


class TestReconciliation:
    def test_reconciliation_status(self, clean_db):
        resp = client.get("/reconciliation/status")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
    
    def test_list_exceptions(self, clean_db):
        resp = client.get("/reconciliation/exceptions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
