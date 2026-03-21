"""
Booking Service data store — booking records, price snapshots, state machine.

Dual-table design:
- booking: Wander SoR (with EXCLUDE constraint for double-booking prevention)
- inventory_block: tracks date blocks (from bookings and external sources)

State machine: CONFIRMED → COMPLETED (with CANCELLED branch)
"""

import json
import uuid
import time
from typing import Optional, List
from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.booking.ubr import generate_ubr


class BookingStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS booking (
                id TEXT PRIMARY KEY,
                ubr TEXT UNIQUE NOT NULL,
                property_id TEXT NOT NULL,
                guest_id TEXT NOT NULL,
                guest_name TEXT NOT NULL,
                guest_email TEXT NOT NULL,
                guest_phone TEXT,
                check_in TEXT NOT NULL,
                check_out TEXT NOT NULL,
                nights INTEGER NOT NULL,
                adults INTEGER DEFAULT 1,
                children INTEGER DEFAULT 0,
                infants INTEGER DEFAULT 0,
                pets INTEGER DEFAULT 0,
                payment_intent_id TEXT,
                customer_id TEXT,
                currency TEXT DEFAULT 'USD',
                total_cents INTEGER NOT NULL,
                status TEXT DEFAULT 'CONFIRMED',
                pms_confirmation_id TEXT,
                source_channel TEXT,
                promo_code TEXT,
                idempotency_key TEXT UNIQUE,
                cancellation TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                confirmed_at TEXT,
                cancelled_at TEXT,
                completed_at TEXT
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS booking_price (
                booking_id TEXT PRIMARY KEY,
                currency TEXT DEFAULT 'USD',
                nightly_rates TEXT NOT NULL,
                subtotal_cents INTEGER NOT NULL,
                fees TEXT NOT NULL,
                fees_total_cents INTEGER NOT NULL,
                taxes TEXT NOT NULL,
                taxes_total_cents INTEGER NOT NULL,
                discounts TEXT,
                discounts_total_cents INTEGER DEFAULT 0,
                total_cents INTEGER NOT NULL,
                FOREIGN KEY (booking_id) REFERENCES booking(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS inventory_block (
                id TEXT PRIMARY KEY,
                booking_id TEXT,
                property_id TEXT NOT NULL,
                check_in TEXT NOT NULL,
                check_out TEXT NOT NULL,
                status TEXT DEFAULT 'CONFIRMED',
                guest_first_name TEXT,
                guest_last_name TEXT,
                guest_email TEXT,
                guest_phone TEXT,
                guests INTEGER,
                source TEXT NOT NULL,
                external_pms_type TEXT,
                external_reservation_id TEXT,
                notes TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT,
                FOREIGN KEY (booking_id) REFERENCES booking(id)
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_guest ON booking(guest_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_status ON booking(status, created_at)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_booking_property ON booking(property_id)"
        )
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_block_property ON inventory_block(property_id, check_in)"
        )
        self.db.commit()

    def check_idempotency(self, idempotency_key: str) -> Optional[dict]:
        """Check if a booking with this idempotency key already exists."""
        if not idempotency_key:
            return None
        row = self.db.fetchone(
            "SELECT * FROM booking WHERE idempotency_key = ?",
            [idempotency_key]
        )
        if row:
            return self._enrich_booking(dict(row))
        return None

    def create_booking(self, data: dict, price_data: dict) -> dict:
        """Create a booking with price snapshot."""
        booking_id = str(uuid.uuid4())
        from datetime import datetime
        check_in = datetime.strptime(data["check_in"], "%Y-%m-%d")
        check_out = datetime.strptime(data["check_out"], "%Y-%m-%d")
        nights = (check_out - check_in).days

        ubr = generate_ubr(data["property_id"], data["check_in"], data["check_out"], data.get("guest_id"))

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

        self.db.execute("""
            INSERT INTO booking (
                id, ubr, property_id, guest_id, guest_name, guest_email, guest_phone,
                check_in, check_out, nights, adults, children, infants, pets,
                payment_intent_id, customer_id, currency, total_cents, status,
                pms_confirmation_id, source_channel, promo_code,
                idempotency_key, confirmed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, ?, ?, ?)
        """, [
            booking_id, ubr, data["property_id"],
            data["guest_id"],
            data.get("guest_name", ""),
            data.get("guest_email", ""),
            data.get("guest_phone"),
            data["check_in"], data["check_out"], nights,
            data.get("adults", 1),
            data.get("children", 0),
            data.get("infants", 0),
            data.get("pets", 0),
            data.get("payment_intent_id"),
            data.get("customer_id"),
            data.get("currency", "USD"),
            price_data["total_cents"],
            data.get("pms_confirmation_id"),
            data.get("source_channel"),
            data.get("promo_code"),
            data.get("idempotency_key"),
            now,
        ])

        # Save price snapshot
        self.db.execute("""
            INSERT INTO booking_price (
                booking_id, currency, nightly_rates, subtotal_cents,
                fees, fees_total_cents, taxes, taxes_total_cents,
                discounts, discounts_total_cents, total_cents
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            booking_id,
            price_data.get("currency", "USD"),
            json.dumps(price_data.get("nightly_rates", [])),
            price_data.get("subtotal_cents", 0),
            json.dumps(price_data.get("fees", [])),
            price_data.get("fees_total_cents", 0),
            json.dumps(price_data.get("taxes", [])),
            price_data.get("taxes_total_cents", 0),
            json.dumps(price_data.get("discounts", [])),
            price_data.get("discounts_total_cents", 0),
            price_data["total_cents"],
        ])

        # Create inventory block
        block_id = str(uuid.uuid4())
        guests_obj = data.get("guests", {})
        self.db.execute("""
            INSERT INTO inventory_block (
                id, booking_id, property_id, check_in, check_out,
                status, guest_first_name, guest_last_name, guest_email,
                guests, source
            ) VALUES (?, ?, ?, ?, ?, 'CONFIRMED', ?, ?, ?, ?, 'WANDER')
        """, [
            block_id, booking_id, data["property_id"],
            data["check_in"], data["check_out"],
            guests_obj.get("first_name") if isinstance(guests_obj, dict) else None,
            guests_obj.get("last_name") if isinstance(guests_obj, dict) else None,
            data.get("guest_email"),
            data.get("adults", 1) + data.get("children", 0),
        ])

        self.db.commit()

        return self.get_booking(booking_id)

    def get_booking(self, booking_id: str) -> Optional[dict]:
        """Get booking with price details."""
        row = self.db.fetchone("SELECT * FROM booking WHERE id = ?", [booking_id])
        if not row:
            return None
        return self._enrich_booking(dict(row))

    def get_booking_by_ubr(self, ubr: str) -> Optional[dict]:
        """Get booking by UBR."""
        row = self.db.fetchone("SELECT * FROM booking WHERE ubr = ?", [ubr])
        if not row:
            return None
        return self._enrich_booking(dict(row))

    def list_bookings(self, filters: dict = None, limit: int = 50,
                      cursor: str = None) -> tuple[list, Optional[str]]:
        """List bookings with filters."""
        query = "SELECT * FROM booking WHERE 1=1"
        params = []

        if filters:
            if filters.get("guest_id"):
                query += " AND guest_id = ?"
                params.append(filters["guest_id"])
            if filters.get("property_id"):
                query += " AND property_id = ?"
                params.append(filters["property_id"])
            if filters.get("status"):
                query += " AND status = ?"
                params.append(filters["status"])

        if cursor:
            query += " AND id > ?"
            params.append(cursor)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit + 1)

        rows = self.db.fetchall(query, params)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        bookings = [self._enrich_booking(dict(r)) for r in rows]
        next_cursor = rows[-1]["id"] if has_more else None
        return bookings, next_cursor

    def cancel_booking(self, booking_id: str, reason: str, actor: str = None,
                       refund_amount_cents: int = 0, penalty_cents: int = 0,
                       refund_percent: float = 100) -> Optional[dict]:
        """Cancel a booking."""
        booking = self.get_booking(booking_id)
        if not booking:
            return None

        if booking["status"] == "CANCELLED":
            return booking

        cancellation = {
            "reason": reason,
            "actor": actor,
            "refund_amount_cents": refund_amount_cents,
            "penalty_cents": penalty_cents,
            "refund_percent": refund_percent,
        }

        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        self.db.execute("""
            UPDATE booking SET status = 'CANCELLED', cancellation = ?, cancelled_at = ?
            WHERE id = ?
        """, [json.dumps(cancellation), now, booking_id])

        # Update inventory block
        self.db.execute(
            "UPDATE inventory_block SET status = 'CANCELLED', updated_at = ? WHERE booking_id = ?",
            [now, booking_id]
        )

        self.db.commit()
        return self.get_booking(booking_id)

    def _enrich_booking(self, booking: dict) -> dict:
        """Add price snapshot to booking data."""
        price_row = self.db.fetchone(
            "SELECT * FROM booking_price WHERE booking_id = ?",
            [booking["id"]]
        )
        if price_row:
            price = dict(price_row)
            for field in ("nightly_rates", "fees", "taxes", "discounts"):
                if price.get(field):
                    try:
                        price[field] = json.loads(price[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            booking["price"] = price

        if booking.get("cancellation"):
            try:
                booking["cancellation"] = json.loads(booking["cancellation"])
            except (json.JSONDecodeError, TypeError):
                pass

        booking["guests"] = {
            "adults": booking.pop("adults", 1),
            "children": booking.pop("children", 0),
            "infants": booking.pop("infants", 0),
            "pets": booking.pop("pets", 0),
        }
        booking["guests"]["total"] = (
            booking["guests"]["adults"] + booking["guests"]["children"]
        )

        return booking
