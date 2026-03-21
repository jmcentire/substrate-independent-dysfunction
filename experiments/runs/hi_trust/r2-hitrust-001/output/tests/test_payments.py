"""
Tests for Payments Service.

Tests invoice lifecycle, refunds, payout hash chain, and reconciliation.
"""

import pytest
from httpx import AsyncClient, ASGITransport

from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.payments.store import PaymentsStore
from services.payments.app import app, set_store


class TestPaymentsStore:
    def setup_method(self):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PaymentsStore(self.db, self.cache)

    def test_create_invoice(self):
        """Create an invoice with line items."""
        result = self.store.create_invoice(
            "booking_1", "cus_123",
            [
                {"type": "booking", "description": "5 nights", "cents": 225000},
                {"type": "fee", "description": "Cleaning", "cents": 15000},
                {"type": "tax", "description": "Tax", "cents": 28800},
            ]
        )
        assert result["status"] == "OPEN"
        assert result["subtotal_cents"] == 240000  # 225000 + 15000
        assert result["tax_cents"] == 28800
        assert result["total_cents"] == 268800

    def test_get_invoice(self):
        """Retrieve invoice with line items."""
        created = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        fetched = self.store.get_invoice(created["id"])
        assert fetched is not None
        assert len(fetched["items"]) == 1

    def test_pay_invoice(self):
        """Pay an invoice."""
        invoice = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        result = self.store.pay_invoice(invoice["id"], "pi_test_123", 100000)
        assert result["status"] == "PAID"
        assert result["paid_cents"] == 100000
        assert result["due_cents"] == 0

    def test_partial_payment(self):
        """Partial payment should show PARTIALLY_PAID."""
        invoice = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        result = self.store.pay_invoice(invoice["id"], "pi_test_123", 50000)
        assert result["status"] == "PARTIALLY_PAID"
        assert result["due_cents"] == 50000

    def test_void_invoice(self):
        """Void an unpaid invoice."""
        invoice = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        result = self.store.void_invoice(invoice["id"], "Test reason")
        assert result["status"] == "VOID"

    def test_refund(self):
        """Create a refund against a paid invoice."""
        invoice = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        self.store.pay_invoice(invoice["id"], "pi_test_123", 100000)

        result = self.store.create_refund("booking_1", 50000, "Guest request")
        assert result["status"] == "SUCCEEDED"
        assert result["amount_cents"] == 50000
        assert result["stripe_refund_id"].startswith("re_sim_")

    def test_refund_exceeds_paid(self):
        """Refund should fail if amount exceeds paid."""
        invoice = self.store.create_invoice(
            "booking_1", "cus_123",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        self.store.pay_invoice(invoice["id"], "pi_test_123", 100000)

        result = self.store.create_refund("booking_1", 150000)
        assert result.get("error") == "EXCEEDS_PAID"

    def test_payout_hash_chain(self):
        """Payout snapshots should form a hash chain."""
        # First payout
        p1 = self.store.create_payout("owner_1", "2025-W01", [
            {"booking_id": "b1", "gross_cents": 100000, "platform_fee_cents": 15000, "adjustments_cents": 0},
        ])
        assert p1["prev_hash"] is None
        assert p1["content_hash"] is not None

        # Second payout
        p2 = self.store.create_payout("owner_1", "2025-W02", [
            {"booking_id": "b2", "gross_cents": 80000, "platform_fee_cents": 12000, "adjustments_cents": 0},
        ])
        assert p2["prev_hash"] == p1["content_hash"]  # Chain!

        # Third payout
        p3 = self.store.create_payout("owner_1", "2025-W03", [
            {"booking_id": "b3", "gross_cents": 120000, "platform_fee_cents": 18000, "adjustments_cents": 0},
        ])
        assert p3["prev_hash"] == p2["content_hash"]

        # Verify chain
        verification = self.store.verify_hash_chain("owner_1")
        assert verification["valid"] is True
        assert verification["count"] == 3

    def test_payout_dry_run(self):
        """Dry run should return calculations without persisting."""
        result = self.store.create_payout("owner_1", "2025-W01", [
            {"booking_id": "b1", "gross_cents": 100000, "platform_fee_cents": 15000, "adjustments_cents": 0},
        ], dry_run=True)
        assert result["dry_run"] is True
        assert result["total_cents"] == 85000  # 100000 - 15000

        # Should not be persisted
        payouts, _ = self.store.get_owner_payouts("owner_1")
        assert len(payouts) == 0

    def test_payout_calculation(self):
        """Payout total = gross - platform_fee + adjustments."""
        result = self.store.create_payout("owner_1", "2025-W01", [
            {"booking_id": "b1", "gross_cents": 100000, "platform_fee_cents": 15000, "adjustments_cents": 5000},
            {"booking_id": "b2", "gross_cents": 80000, "platform_fee_cents": 12000, "adjustments_cents": -2000},
        ])
        # Total: (100000 + 80000) - (15000 + 12000) + (5000 - 2000) = 156000
        assert result["total_cents"] == 156000

    def test_reconciliation_status(self):
        """Reconciliation status should return."""
        status = self.store.get_reconciliation_status()
        assert status["status"] == "ok"
        assert status["exceptions_pending"] == 0


@pytest.mark.asyncio
class TestPaymentsAPI:
    async def setup_method(self, method):
        self.db = Database()
        self.cache = MemoryCache()
        self.store = PaymentsStore(self.db, self.cache)
        set_store(self.store)

    async def test_create_invoice(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/payments/invoices", json={
                "booking_id": "booking_1",
                "customer_id": "cus_123",
                "items": [
                    {"type": "booking", "description": "5 nights", "cents": 225000},
                    {"type": "fee", "description": "Cleaning", "cents": 15000},
                ],
            })
            assert resp.status_code == 201
            assert resp.json()["status"] == "OPEN"

    async def test_pay_invoice(self):
        invoice = self.store.create_invoice(
            "b1", "cus_1",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(f"/api/v1/payments/invoices/{invoice['id']}/pay", json={
                "stripe_payment_intent_id": "pi_test_123",
                "amount_cents": 100000,
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "PAID"

    async def test_create_refund(self):
        invoice = self.store.create_invoice(
            "b1", "cus_1",
            [{"type": "booking", "description": "Booking", "cents": 100000}]
        )
        self.store.pay_invoice(invoice["id"], "pi_test", 100000)

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/payments/refunds", json={
                "booking_id": "b1",
                "amount_cents": 50000,
                "reason": "Guest request",
            })
            assert resp.status_code == 201
            assert resp.json()["status"] == "SUCCEEDED"

    async def test_payout_run(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post("/api/v1/payments/payouts/run", json={
                "cycle": "2025-W01",
                "owner_id": "owner_1",
                "bookings": [
                    {"booking_id": "b1", "gross_cents": 100000, "platform_fee_cents": 15000},
                ],
            })
            assert resp.status_code == 200
            assert resp.json()["status"] == "DRAFT"

    async def test_verify_hash_chain(self):
        self.store.create_payout("owner_1", "2025-W01", [
            {"booking_id": "b1", "gross_cents": 100000, "platform_fee_cents": 15000, "adjustments_cents": 0},
        ])
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/payments/payouts/verify-chain",
                params={"owner_id": "owner_1"}
            )
            assert resp.status_code == 200
            assert resp.json()["valid"] is True

    async def test_reconciliation_status(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/payments/reconciliation/status")
            assert resp.status_code == 200
            assert resp.json()["status"] == "ok"

    async def test_health(self):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/health")
            assert resp.status_code == 200
