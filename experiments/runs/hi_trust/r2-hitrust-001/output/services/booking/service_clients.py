"""
Service clients for the Booking Service orchestrator.

These clients wrap the internal service APIs that the Booking Service
calls during the synchronous booking flow. In production, these would
make HTTP calls to the respective service endpoints. For testing,
they can be backed by direct store references.
"""

from typing import Optional


class AvailabilityClient:
    """Client for Availability Service API."""

    def __init__(self, store=None):
        """
        Args:
            store: Direct AvailabilityStore reference (for testing/in-process).
                   In production, would use HTTP client to availability service.
        """
        self.store = store

    def check(self, unit_id: str, start_date: str, end_date: str) -> bool:
        from datetime import datetime
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        return self.store.check(unit_id, sd, ed)

    def acquire(self, unit_id: str, start_date: str, end_date: str) -> bool:
        from datetime import datetime
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        success, _ = self.store.acquire(unit_id, sd, ed)
        return success

    def release(self, unit_id: str, start_date: str, end_date: str):
        from datetime import datetime
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        ed = datetime.strptime(end_date, "%Y-%m-%d").date()
        self.store.release(unit_id, sd, ed)


class PricingClient:
    """Client for Pricing Service API."""

    def __init__(self, store=None):
        self.store = store

    def get_rates(self, unit_id: str, start_date: str, end_date: str) -> dict:
        from datetime import datetime, timedelta
        sd = datetime.strptime(start_date, "%Y-%m-%d").date()
        # check_out is exclusive, so last night is day before
        ed = datetime.strptime(end_date, "%Y-%m-%d").date() - timedelta(days=1)
        if ed < sd:
            ed = sd
        currency, rates, missing = self.store.get_rate(unit_id, sd, ed)
        return {"currency": currency, "rates": rates, "missing_dates": missing}


class PaymentsClient:
    """Client for Payments Service API."""

    def __init__(self, store=None):
        self.store = store

    def create_and_pay(self, booking_id: str, customer_id: str,
                       total_cents: int, payment_intent_id: str,
                       currency: str = "USD") -> dict:
        """Create invoice and record payment in one call."""
        items = [
            {"type": "booking", "description": "Booking total", "cents": total_cents}
        ]
        invoice = self.store.create_invoice(booking_id, customer_id, items, currency=currency)

        # Record payment
        result = self.store.pay_invoice(
            invoice["id"],
            payment_intent_id or f"pi_sim_{booking_id[:16]}",
            total_cents
        )

        if "error" in result:
            return {"error": result["error"], "message": "Payment failed"}

        return {
            "invoice_id": invoice["id"],
            "payment_intent_id": payment_intent_id,
            "status": "paid",
            "booking_id": booking_id,
        }

    def refund(self, booking_id: str, amount_cents: int, reason: str = None) -> dict:
        result = self.store.create_refund(booking_id, amount_cents, reason)
        return result


class SyncClient:
    """Client for Sync Service API."""

    def __init__(self, store=None):
        self.store = store

    def create_reservation(self, property_id: str, check_in: str,
                           check_out: str, guest: dict) -> dict:
        """Create a reservation in the PMS."""
        import uuid
        external_id = f"PMS-{str(uuid.uuid4())[:8].upper()}"
        confirmation_code = f"WDR-{str(uuid.uuid4())[:6].upper()}"

        if self.store:
            result = self.store.create_reservation_mapping(
                guest.get("booking_id", ""),
                property_id, "simulated",
                external_id, confirmation_code
            )
            return result

        return {
            "external_id": external_id,
            "confirmation_code": confirmation_code,
            "status": "CONFIRMED",
        }

    def cancel_reservation(self, external_id: str) -> dict:
        return {"external_id": external_id, "cancelled": True}


class PropertyClient:
    """Client for Property Service API."""

    def __init__(self, store=None):
        self.store = store

    def get_property(self, property_id: str) -> Optional[dict]:
        prop = self.store.get_property(property_id)
        if prop:
            # Add fees
            fees = self.store.get_fees(property_id)
            prop["fees"] = fees
        return prop
