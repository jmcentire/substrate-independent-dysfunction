"""
Booking Orchestrator — synchronous booking flow (<3s).

Flow:
1. Validate request + check idempotency
2. Check availability (fast cache check)
3. Acquire dates (block in availability)
4. Get pricing quote
5. Create invoice + authorize payment
6. Verify PMS availability + create PMS reservation
7. Capture payment
8. Finalize booking

If any step fails after payment auth: refund + release dates.
"""

import time
from typing import Optional, Tuple
from datetime import datetime

from services.shared.events import emit_event


class BookingOrchestrator:
    """
    Coordinates the booking transaction across services.
    
    Dependencies are injected via constructor to allow testing
    with mock service clients.
    """

    def __init__(self, booking_store, availability_client, pricing_client,
                 payments_client, sync_client, property_client):
        self.booking_store = booking_store
        self.availability = availability_client
        self.pricing = pricing_client
        self.payments = payments_client
        self.sync = sync_client
        self.property = property_client

    def create_booking(self, request: dict) -> Tuple[dict, int]:
        """
        Execute the synchronous booking flow.
        
        Returns (result_dict, http_status_code).
        Target: < 3 seconds total.
        """
        start_time = time.time()

        # Step 0: Check idempotency
        idempotency_key = request.get("idempotency_key")
        if idempotency_key:
            existing = self.booking_store.check_idempotency(idempotency_key)
            if existing:
                return existing, 200

        property_id = request["property_id"]
        check_in = request["check_in"]
        check_out = request["check_out"]
        guest_id = request.get("guest_id", "")

        # Step 1: Get property info (check bookability)
        prop = self.property.get_property(property_id)
        if not prop:
            return {"error": {"code": "PROPERTY_NOT_FOUND", "message": "Property not found"}}, 404
        if not prop.get("is_bookable", True):
            return {"error": {"code": "PROPERTY_NOT_BOOKABLE", "message": "Property not accepting bookings"}}, 422

        # Step 2: Check availability (fast cache check)
        available = self.availability.check(property_id, check_in, check_out)
        if not available:
            return {"error": {"code": "DATES_UNAVAILABLE", "message": "Dates not available"}}, 409

        # Step 3: Acquire dates (block in availability)
        acquired = self.availability.acquire(property_id, check_in, check_out)
        if not acquired:
            return {"error": {"code": "DATES_UNAVAILABLE", "message": "Dates no longer available"}}, 409

        try:
            # Step 4: Get pricing
            pricing_result = self.pricing.get_rates(property_id, check_in, check_out)
            
            # Step 5: Build price snapshot
            price_data = self._build_price_snapshot(pricing_result, prop)

            # Step 6: Create invoice + record payment
            guest_info = request.get("guests", {})
            primary = guest_info.get("primary", {})

            payment_result = self.payments.create_and_pay(
                booking_id="pending",  # Will be updated
                customer_id=request.get("customer_id", ""),
                total_cents=price_data["total_cents"],
                payment_intent_id=request.get("payment_method_id", ""),
                currency=price_data.get("currency", "USD"),
            )

            if payment_result.get("error"):
                # Payment failed — release dates
                self.availability.release(property_id, check_in, check_out)
                return {"error": {"code": "PAYMENT_FAILED", "message": payment_result.get("message", "Payment failed")}}, 402

            # Step 7: Create PMS reservation (via Sync Service)
            pms_result = self.sync.create_reservation(
                property_id, check_in, check_out,
                {
                    "first_name": primary.get("first_name", ""),
                    "last_name": primary.get("last_name", ""),
                    "email": primary.get("email", ""),
                    "phone": primary.get("phone", ""),
                    "property_external_id": prop.get("external_id"),
                }
            )

            if pms_result.get("error"):
                # PMS failed — refund payment + release dates
                self.payments.refund(payment_result.get("booking_id", ""), price_data["total_cents"])
                self.availability.release(property_id, check_in, check_out)
                return {"error": {"code": "PMS_UNAVAILABLE", "message": "PMS reservation failed"}}, 503

            # Step 8: Finalize booking
            booking_data = {
                "property_id": property_id,
                "check_in": check_in,
                "check_out": check_out,
                "guest_id": guest_id,
                "guest_name": f"{primary.get('first_name', '')} {primary.get('last_name', '')}".strip() or "Guest",
                "guest_email": primary.get("email", ""),
                "guest_phone": primary.get("phone"),
                "adults": guest_info.get("adults", 1),
                "children": guest_info.get("children", 0),
                "infants": guest_info.get("infants", 0),
                "pets": guest_info.get("pets", 0),
                "payment_intent_id": request.get("payment_method_id"),
                "customer_id": request.get("customer_id"),
                "currency": price_data.get("currency", "USD"),
                "pms_confirmation_id": pms_result.get("confirmation_code"),
                "idempotency_key": idempotency_key,
                "source_channel": request.get("source_channel"),
                "promo_code": request.get("promo_code"),
                "guests": primary,
            }

            booking = self.booking_store.create_booking(booking_data, price_data)

            elapsed = time.time() - start_time

            # Emit events
            emit_event(
                "booking.confirmed",
                "booking",
                booking["id"],
                context={
                    "ubr": booking["ubr"],
                    "property_id": property_id,
                    "total_cents": price_data["total_cents"],
                    "pms_confirmation_id": pms_result.get("confirmation_code"),
                    "elapsed_ms": int(elapsed * 1000),
                }
            )

            return booking, 201

        except Exception as e:
            # Any error after acquiring dates — release them
            self.availability.release(property_id, check_in, check_out)

            emit_event(
                "booking.failed",
                "booking",
                "none",
                context={
                    "property_id": property_id,
                    "error": str(e),
                }
            )

            raise

    def cancel_booking(self, booking_id: str, reason: str, actor: str = None,
                       dry_run: bool = False) -> Tuple[dict, int]:
        """Cancel a booking with refund calculation."""
        booking = self.booking_store.get_booking(booking_id)
        if not booking:
            return {"error": {"code": "NOT_FOUND", "message": "Booking not found"}}, 404

        if booking["status"] == "CANCELLED":
            return {"error": {"code": "ALREADY_CANCELLED", "message": "Booking already cancelled"}}, 400

        # Calculate refund based on cancellation policy
        total = booking.get("total_cents", 0)
        price_data = booking.get("price", {})
        total = price_data.get("total_cents", total)

        # Simple cancellation: full refund for now
        # In production, would look up property's cancellation policy
        refund_percent = 100
        refund_amount = total
        penalty = 0

        if dry_run:
            return {
                "dry_run": True,
                "cancellation": {
                    "refund_amount_cents": refund_amount,
                    "penalty_cents": penalty,
                    "refund_percent": refund_percent,
                    "policy_name": "FLEXIBLE",
                }
            }, 200

        # Execute cancellation
        # 1. Cancel in PMS
        if booking.get("pms_confirmation_id"):
            self.sync.cancel_reservation(booking["pms_confirmation_id"])

        # 2. Release availability
        self.availability.release(
            booking["property_id"], booking["check_in"], booking["check_out"]
        )

        # 3. Process refund
        if refund_amount > 0:
            self.payments.refund(booking_id, refund_amount, reason)

        # 4. Update booking record
        result = self.booking_store.cancel_booking(
            booking_id, reason, actor,
            refund_amount, penalty, refund_percent
        )

        emit_event(
            "booking.cancelled",
            "booking",
            booking_id,
            context={
                "reason": reason,
                "refund_amount_cents": refund_amount,
            }
        )

        return result, 200

    def _build_price_snapshot(self, pricing_result: dict, prop: dict) -> dict:
        """Build a complete price snapshot from pricing data and property config."""
        rates = pricing_result.get("rates", [])
        currency = pricing_result.get("currency", "USD")

        subtotal = sum(r.get("rate_cents", 0) for r in rates)

        # Get fees from property
        fees = []
        fees_total = 0
        prop_fees = prop.get("fees", [])
        for fee in prop_fees:
            fee_cents = fee.get("amount_cents", 0)
            fees.append({
                "kind": fee.get("kind", "cleaning"),
                "label": fee.get("label", "Fee"),
                "cents": fee_cents,
            })
            fees_total += fee_cents

        # Simple tax calculation (12% on subtotal + fees)
        tax_rate = 0.12
        tax_cents = round((subtotal + fees_total) * tax_rate)
        taxes = [{
            "kind": "occupancy",
            "label": f"Occupancy Tax ({int(tax_rate * 100)}%)",
            "rate": tax_rate,
            "cents": tax_cents,
        }]

        total = subtotal + fees_total + tax_cents

        return {
            "currency": currency,
            "nightly_rates": rates,
            "subtotal_cents": subtotal,
            "fees": fees,
            "fees_total_cents": fees_total,
            "taxes": taxes,
            "taxes_total_cents": tax_cents,
            "discounts": [],
            "discounts_total_cents": 0,
            "total_cents": total,
        }
