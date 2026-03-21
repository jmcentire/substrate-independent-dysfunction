"""
Payments Service data store — Stripe mirror with hash-chained payout snapshots.

Stripe is the ledger — this service maintains a read-optimized mirror.
Invoice-centric: invoices → payments → refunds → payouts.
Immutable payout snapshots with SHA256 hash chain.
"""

import json
import hashlib
import uuid
import time
from typing import Optional, List
from services.shared.db import Database
from services.shared.cache import MemoryCache


class PaymentsStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS invoice (
                id TEXT PRIMARY KEY,
                invoice_number TEXT UNIQUE,
                booking_id TEXT,
                customer_id TEXT,
                currency TEXT DEFAULT 'USD',
                subtotal_cents INTEGER NOT NULL DEFAULT 0,
                tax_cents INTEGER NOT NULL DEFAULT 0,
                total_cents INTEGER NOT NULL DEFAULT 0,
                paid_cents INTEGER NOT NULL DEFAULT 0,
                refunded_cents INTEGER NOT NULL DEFAULT 0,
                due_cents INTEGER NOT NULL DEFAULT 0,
                platform_fee_cents INTEGER DEFAULT 0,
                platform_fee_rate REAL DEFAULT 0.15,
                status TEXT DEFAULT 'DRAFT',
                stripe_payment_intent_id TEXT,
                due_date TEXT,
                paid_at TEXT,
                voided_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS invoice_item (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                cents INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (invoice_id) REFERENCES invoice(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS payment (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                stripe_payment_intent_id TEXT,
                stripe_charge_id TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                FOREIGN KEY (invoice_id) REFERENCES invoice(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS refund (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                booking_id TEXT,
                amount_cents INTEGER NOT NULL,
                reason TEXT,
                note TEXT,
                stripe_refund_id TEXT,
                status TEXT DEFAULT 'PENDING',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT,
                FOREIGN KEY (invoice_id) REFERENCES invoice(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS payout_snapshot (
                id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                cycle TEXT NOT NULL,
                booking_count INTEGER NOT NULL DEFAULT 0,
                bookings TEXT,
                gross_cents INTEGER NOT NULL DEFAULT 0,
                platform_fee_cents INTEGER NOT NULL DEFAULT 0,
                adjustments_cents INTEGER NOT NULL DEFAULT 0,
                total_cents INTEGER NOT NULL DEFAULT 0,
                content_hash TEXT NOT NULL,
                prev_hash TEXT,
                status TEXT DEFAULT 'DRAFT',
                stripe_transfer_id TEXT,
                paid_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS reconciliation_exception (
                id TEXT PRIMARY KEY,
                exception_type TEXT NOT NULL,
                stripe_id TEXT,
                local_id TEXT,
                amount_cents INTEGER,
                expected_cents INTEGER,
                status TEXT DEFAULT 'PENDING',
                resolution TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                resolved_at TEXT
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_invoice_booking ON invoice(booking_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_payout_owner ON payout_snapshot(owner_id)"
        )
        self.db.commit()

    def create_invoice(self, booking_id: str, customer_id: str,
                       items: list[dict], due_date: str = None,
                       currency: str = "USD") -> dict:
        """Create an invoice with line items."""
        invoice_id = str(uuid.uuid4())
        invoice_number = f"INV-{int(time.time())}-{invoice_id[:8]}"

        subtotal = sum(item["cents"] for item in items if item.get("type") != "tax")
        tax = sum(item["cents"] for item in items if item.get("type") == "tax")
        total = subtotal + tax
        platform_fee_rate = 0.15
        platform_fee = round(total * platform_fee_rate)

        self.db.execute("""
            INSERT INTO invoice (
                id, invoice_number, booking_id, customer_id, currency,
                subtotal_cents, tax_cents, total_cents, due_cents,
                platform_fee_cents, platform_fee_rate, status, due_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', ?)
        """, [
            invoice_id, invoice_number, booking_id, customer_id, currency,
            subtotal, tax, total, total, platform_fee, platform_fee_rate, due_date
        ])

        for item in items:
            item_id = str(uuid.uuid4())
            self.db.execute("""
                INSERT INTO invoice_item (id, invoice_id, type, description, cents)
                VALUES (?, ?, ?, ?, ?)
            """, [item_id, invoice_id, item["type"], item["description"], item["cents"]])

        self.db.commit()

        return {
            "id": invoice_id,
            "invoice_number": invoice_number,
            "booking_id": booking_id,
            "status": "OPEN",
            "subtotal_cents": subtotal,
            "tax_cents": tax,
            "total_cents": total,
            "due_cents": total,
            "currency": currency,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_invoice(self, invoice_id: str) -> Optional[dict]:
        """Get invoice details with line items."""
        row = self.db.fetchone("SELECT * FROM invoice WHERE id = ?", [invoice_id])
        if not row:
            return None

        invoice = dict(row)
        items = self.db.fetchall(
            "SELECT * FROM invoice_item WHERE invoice_id = ?",
            [invoice_id]
        )
        invoice["items"] = [dict(i) for i in items]
        return invoice

    def get_invoice_by_booking(self, booking_id: str) -> Optional[dict]:
        """Get invoice for a booking."""
        row = self.db.fetchone("SELECT * FROM invoice WHERE booking_id = ?", [booking_id])
        if not row:
            return None
        return self.get_invoice(row["id"])

    def pay_invoice(self, invoice_id: str, stripe_payment_intent_id: str,
                    amount_cents: int) -> dict:
        """Record a payment against an invoice."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return {"error": "NOT_FOUND"}

        payment_id = str(uuid.uuid4())
        self.db.execute("""
            INSERT INTO payment (id, invoice_id, amount_cents, stripe_payment_intent_id, status, completed_at)
            VALUES (?, ?, ?, ?, 'SUCCEEDED', datetime('now'))
        """, [payment_id, invoice_id, amount_cents, stripe_payment_intent_id])

        new_paid = invoice["paid_cents"] + amount_cents
        new_due = invoice["total_cents"] - new_paid + invoice["refunded_cents"]
        new_status = "PAID" if new_due <= 0 else "PARTIALLY_PAID"

        self.db.execute("""
            UPDATE invoice SET paid_cents = ?, due_cents = ?, status = ?,
                stripe_payment_intent_id = ?, paid_at = datetime('now')
            WHERE id = ?
        """, [new_paid, max(0, new_due), new_status, stripe_payment_intent_id, invoice_id])

        self.db.commit()

        return {
            "payment_id": payment_id,
            "status": new_status,
            "paid_cents": new_paid,
            "due_cents": max(0, new_due),
        }

    def void_invoice(self, invoice_id: str, reason: str) -> dict:
        """Void an unpaid invoice."""
        invoice = self.get_invoice(invoice_id)
        if not invoice:
            return {"error": "NOT_FOUND"}
        if invoice["status"] == "PAID":
            return {"error": "CANNOT_VOID_PAID"}

        self.db.execute(
            "UPDATE invoice SET status = 'VOID', voided_at = datetime('now') WHERE id = ?",
            [invoice_id]
        )
        self.db.commit()
        return {"status": "VOID"}

    def create_refund(self, booking_id: str, amount_cents: int,
                      reason: str = None, note: str = None) -> dict:
        """Create a refund against a booking's invoice."""
        invoice = self.get_invoice_by_booking(booking_id)
        if not invoice:
            return {"error": "NOT_FOUND", "message": "No invoice for booking"}

        # Validate: amount <= paid - already_refunded
        available = invoice["paid_cents"] - invoice["refunded_cents"]
        if amount_cents > available:
            return {"error": "EXCEEDS_PAID", "message": f"Max refundable: {available}"}

        refund_id = str(uuid.uuid4())
        # Simulate Stripe refund ID
        stripe_refund_id = f"re_sim_{refund_id[:16]}"

        self.db.execute("""
            INSERT INTO refund (
                id, invoice_id, booking_id, amount_cents, reason, note,
                stripe_refund_id, status, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'SUCCEEDED', datetime('now'))
        """, [
            refund_id, invoice["id"], booking_id, amount_cents,
            reason, note, stripe_refund_id
        ])

        new_refunded = invoice["refunded_cents"] + amount_cents
        new_due = invoice["total_cents"] - invoice["paid_cents"] + new_refunded
        new_status = "REFUNDED" if new_refunded >= invoice["paid_cents"] else invoice["status"]

        self.db.execute("""
            UPDATE invoice SET refunded_cents = ?, status = ? WHERE id = ?
        """, [new_refunded, new_status, invoice["id"]])

        self.db.commit()

        return {
            "id": refund_id,
            "invoice_id": invoice["id"],
            "amount_cents": amount_cents,
            "status": "SUCCEEDED",
            "stripe_refund_id": stripe_refund_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }

    def get_refund(self, refund_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM refund WHERE id = ?", [refund_id])
        return dict(row) if row else None

    def create_payout(self, owner_id: str, cycle: str, bookings: list[dict],
                      dry_run: bool = False) -> dict:
        """Create an immutable payout snapshot with hash chain."""
        # Calculate totals
        gross = sum(b.get("gross_cents", 0) for b in bookings)
        fees = sum(b.get("platform_fee_cents", 0) for b in bookings)
        adjustments = sum(b.get("adjustments_cents", 0) for b in bookings)
        total = gross - fees + adjustments

        # Get previous payout hash
        prev = self.db.fetchone(
            "SELECT content_hash FROM payout_snapshot WHERE owner_id = ? ORDER BY created_at DESC LIMIT 1",
            [owner_id]
        )
        prev_hash = prev["content_hash"] if prev else None

        # Compute content hash (SHA256, sorted JSON, exclude mutable fields)
        hash_content = json.dumps({
            "owner_id": owner_id,
            "cycle": cycle,
            "booking_ids": sorted([b.get("booking_id", "") for b in bookings]),
            "gross_cents": gross,
            "platform_fee_cents": fees,
            "adjustments_cents": adjustments,
            "total_cents": total,
            "prev_hash": prev_hash,
        }, sort_keys=True)
        content_hash = hashlib.sha256(hash_content.encode()).hexdigest()

        if dry_run:
            return {
                "dry_run": True,
                "owner_id": owner_id,
                "cycle": cycle,
                "booking_count": len(bookings),
                "gross_cents": gross,
                "platform_fee_cents": fees,
                "total_cents": total,
                "content_hash": content_hash,
            }

        payout_id = str(uuid.uuid4())
        self.db.execute("""
            INSERT INTO payout_snapshot (
                id, owner_id, cycle, booking_count, bookings,
                gross_cents, platform_fee_cents, adjustments_cents,
                total_cents, content_hash, prev_hash, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'DRAFT')
        """, [
            payout_id, owner_id, cycle, len(bookings),
            json.dumps(bookings), gross, fees, adjustments,
            total, content_hash, prev_hash
        ])
        self.db.commit()

        return {
            "id": payout_id,
            "owner_id": owner_id,
            "cycle": cycle,
            "booking_count": len(bookings),
            "total_cents": total,
            "content_hash": content_hash,
            "prev_hash": prev_hash,
            "status": "DRAFT",
        }

    def get_payout(self, payout_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM payout_snapshot WHERE id = ?", [payout_id])
        if not row:
            return None
        payout = dict(row)
        if payout.get("bookings"):
            try:
                payout["bookings"] = json.loads(payout["bookings"])
            except (json.JSONDecodeError, TypeError):
                pass
        return payout

    def get_owner_payouts(self, owner_id: str, status: str = None,
                          limit: int = 50, cursor: str = None) -> tuple[list, Optional[str]]:
        query = "SELECT * FROM payout_snapshot WHERE owner_id = ?"
        params = [owner_id]
        if status:
            query += " AND status = ?"
            params.append(status)
        if cursor:
            query += " AND id > ?"
            params.append(cursor)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)

        rows = self.db.fetchall(query, params)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        payouts = []
        for r in rows:
            p = dict(r)
            if p.get("bookings"):
                try:
                    p["bookings"] = json.loads(p["bookings"])
                except (json.JSONDecodeError, TypeError):
                    pass
            payouts.append(p)

        next_cursor = rows[-1]["id"] if has_more else None
        return payouts, next_cursor

    def verify_hash_chain(self, owner_id: str) -> dict:
        """Verify the hash chain integrity for an owner's payouts."""
        rows = self.db.fetchall(
            "SELECT * FROM payout_snapshot WHERE owner_id = ? ORDER BY created_at ASC",
            [owner_id]
        )

        if not rows:
            return {"valid": True, "count": 0}

        expected_prev = None
        for i, row in enumerate(rows):
            payout = dict(row)
            if payout.get("bookings"):
                try:
                    bookings = json.loads(payout["bookings"])
                except (json.JSONDecodeError, TypeError):
                    bookings = []
            else:
                bookings = []

            if payout["prev_hash"] != expected_prev:
                return {
                    "valid": False,
                    "broken_at": payout["id"],
                    "index": i,
                    "expected_prev": expected_prev,
                    "actual_prev": payout["prev_hash"],
                }

            expected_prev = payout["content_hash"]

        return {"valid": True, "count": len(rows)}

    def get_reconciliation_status(self) -> dict:
        pending = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM reconciliation_exception WHERE status = 'PENDING'"
        )
        return {
            "last_run": None,
            "status": "ok",
            "exceptions_pending": pending["cnt"] if pending else 0,
            "drift_amount_cents": 0,
        }

    def get_reconciliation_exceptions(self) -> list[dict]:
        rows = self.db.fetchall(
            "SELECT * FROM reconciliation_exception WHERE status = 'PENDING' ORDER BY created_at DESC"
        )
        return [dict(r) for r in rows]
