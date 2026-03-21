"""
Booking Service API
Synchronous booking orchestration (<3s flow).
Owns booking state, UBR generation, policy enforcement.

Flow: Check → Acquire → Charge → Book in PMS → Confirm
If PMS fails after charge: auto-refund + release availability.

Endpoints:
  POST /bookings              - Create booking with payment
  GET  /bookings/{id}         - Get booking details
  GET  /bookings/ubr/{ubr}    - Get booking by UBR
  GET  /bookings              - List bookings with filters
  POST /bookings/{id}/cancel  - Cancel booking
"""
from fastapi import FastAPI, Query, HTTPException, Path
from pydantic import BaseModel
from datetime import date, datetime, timezone, timedelta
from typing import Optional, List
import json
import uuid
import logging
import httpx

from .ubr import generate_ubr, validate_ubr
from shared.database import get_db_cursor, init_schema
from shared.events import emit

logger = logging.getLogger("booking")

app = FastAPI(title="Booking Service", version="1.0.0")

# Service URLs (in production, these are service discovery / k8s DNS)
import os
AVAILABILITY_URL = os.environ.get("AVAILABILITY_URL", "http://localhost:8001")
PRICING_URL = os.environ.get("PRICING_URL", "http://localhost:8002")
PROPERTY_URL = os.environ.get("PROPERTY_URL", "http://localhost:8003")
PAYMENTS_URL = os.environ.get("PAYMENTS_URL", "http://localhost:8005")
SYNC_URL = os.environ.get("SYNC_URL", "http://localhost:8006")


# --- Models ---

class GuestPrimary(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: Optional[str] = None


class GuestsInfo(BaseModel):
    adults: int = 1
    children: int = 0
    infants: int = 0
    pets: int = 0
    primary: GuestPrimary


class CreateBookingRequest(BaseModel):
    idempotency_key: str
    property_id: str
    check_in: date
    check_out: date
    guest_id: str
    guests: GuestsInfo
    payment_method_id: str
    customer_id: str
    promo_code: Optional[str] = None


class CancelBookingRequest(BaseModel):
    reason: str
    actor: str = "guest"
    dry_run: bool = False


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/booking/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Service Calls ---

async def _check_availability(property_id: str, check_in: date, check_out: date) -> bool:
    """Check availability via Availability Service."""
    # Convert from check_in/check_out to inclusive start/end dates
    end_date = check_out - timedelta(days=1)  # check_out is exclusive
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{AVAILABILITY_URL}/check",
                params={
                    "unit_id": property_id,
                    "start_date": check_in.isoformat(),
                    "end_date": end_date.isoformat(),
                }
            )
            return resp.json().get("available", False)
    except Exception as e:
        logger.error(f"Availability check failed: {e}")
        return True  # Fail open; PMS will be final verification


async def _acquire_dates(property_id: str, check_in: date, check_out: date) -> bool:
    """Acquire dates via Availability Service."""
    end_date = check_out - timedelta(days=1)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{AVAILABILITY_URL}/acquire",
                json={
                    "unit_id": property_id,
                    "start_date": check_in.isoformat(),
                    "end_date": end_date.isoformat(),
                }
            )
            return resp.status_code == 200
    except Exception as e:
        logger.error(f"Availability acquire failed: {e}")
        return False


async def _release_dates(property_id: str, check_in: date, check_out: date):
    """Release dates via Availability Service."""
    end_date = check_out - timedelta(days=1)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{AVAILABILITY_URL}/release",
                json={
                    "unit_id": property_id,
                    "start_date": check_in.isoformat(),
                    "end_date": end_date.isoformat(),
                }
            )
    except Exception as e:
        logger.error(f"Availability release failed: {e}")


async def _get_rates(property_id: str, check_in: date, check_out: date) -> dict:
    """Get rates from Pricing Service."""
    end_date = check_out - timedelta(days=1)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{PRICING_URL}/rate",
                params={
                    "unit_id": property_id,
                    "start_date": check_in.isoformat(),
                    "end_date": end_date.isoformat(),
                }
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Pricing lookup failed: {e}")
        return None


async def _get_property(property_id: str) -> dict:
    """Get property from Property Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{PROPERTY_URL}/properties/{property_id}")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Property lookup failed: {e}")
    return None


async def _get_fees(property_id: str) -> dict:
    """Get fees from Property Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{PROPERTY_URL}/properties/{property_id}/fees")
            if resp.status_code == 200:
                return resp.json()
    except Exception as e:
        logger.error(f"Fee lookup failed: {e}")
    return {"fees": []}


async def _create_invoice(booking_id: str, customer_id: str, items: list, currency: str = "USD") -> dict:
    """Create invoice via Payments Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{PAYMENTS_URL}/invoices",
                json={
                    "booking_id": booking_id,
                    "customer_id": customer_id,
                    "items": items,
                    "currency": currency,
                }
            )
            if resp.status_code in (200, 201):
                return resp.json()
    except Exception as e:
        logger.error(f"Invoice creation failed: {e}")
    return None


async def _pay_invoice(invoice_id: str, payment_intent_id: str, amount_cents: int) -> dict:
    """Pay invoice via Payments Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{PAYMENTS_URL}/invoices/{invoice_id}/pay",
                json={
                    "stripe_payment_intent_id": payment_intent_id,
                    "amount_cents": amount_cents,
                }
            )
            return resp.json()
    except Exception as e:
        logger.error(f"Invoice payment failed: {e}")
    return None


async def _create_pms_reservation(property_id: str, check_in: date, check_out: date,
                                    guest_name: str, guest_email: str, guests: int) -> dict:
    """Create PMS reservation via Sync Service."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{SYNC_URL}/sync/pms/reservation",
                json={
                    "property_id": property_id,
                    "check_in": check_in.isoformat(),
                    "check_out": check_out.isoformat(),
                    "guest_name": guest_name,
                    "guest_email": guest_email,
                    "guests": guests,
                }
            )
            if resp.status_code in (200, 201):
                return resp.json()
    except Exception as e:
        logger.error(f"PMS reservation failed: {e}")
    return None


async def _refund_booking(booking_id: str, amount_cents: int, reason: str):
    """Refund via Payments Service."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(
                f"{PAYMENTS_URL}/refunds",
                json={
                    "booking_id": booking_id,
                    "amount_cents": amount_cents,
                    "reason": reason,
                }
            )
    except Exception as e:
        logger.error(f"Refund failed: {e}")


# --- Policy Enforcement ---

def _validate_booking_policy(property_data: dict, check_in: date, check_out: date,
                              total_guests: int) -> Optional[str]:
    """Enforce booking policies. Returns error message or None."""
    if not property_data:
        return None  # Skip if property data unavailable
    
    if not property_data.get("is_bookable", True):
        return "Property is not accepting bookings"
    
    # Check max guests
    max_guests = property_data.get("max_guests", 99)
    if total_guests > max_guests:
        return f"Maximum {max_guests} guests allowed"
    
    # Check min stay (default 1 night)
    nights = (check_out - check_in).days
    if nights < 1:
        return "Minimum 1 night stay required"
    
    return None


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "booking"}


@app.post("/bookings", status_code=201)
async def create_booking(req: CreateBookingRequest):
    """
    Create a booking with payment. Synchronous orchestration (<3s).
    
    Flow:
    1. Idempotency check
    2. Property validation
    3. Policy enforcement
    4. Availability check + acquire
    5. Get pricing
    6. Create invoice + payment
    7. Create PMS reservation
    8. If PMS fails: auto-refund + release dates
    9. Confirm booking
    """
    now = datetime.now(timezone.utc)
    nights = (req.check_out - req.check_in).days
    
    if nights < 1:
        raise HTTPException(status_code=400, detail={"code": "INVALID_REQUEST", "message": "check_out must be after check_in"})
    
    # 1. Idempotency check
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT id, ubr, status, total_cents, confirmed_at FROM booking WHERE idempotency_key = %s", (req.idempotency_key,))
        existing = cur.fetchone()
        if existing:
            return {
                "id": str(existing['id']),
                "ubr": existing['ubr'],
                "status": existing['status'],
                "total_cents": existing['total_cents'],
                "confirmed_at": existing['confirmed_at'].isoformat() if existing['confirmed_at'] else None,
                "idempotent_replay": True,
            }
    
    # 2. Property validation
    property_data = await _get_property(req.property_id)
    
    # 3. Policy enforcement
    total_guests = req.guests.adults + req.guests.children
    policy_error = _validate_booking_policy(property_data, req.check_in, req.check_out, total_guests)
    if policy_error:
        raise HTTPException(status_code=422, detail={"code": "PROPERTY_NOT_BOOKABLE", "message": policy_error})
    
    # 4. Availability check + acquire
    available = await _check_availability(req.property_id, req.check_in, req.check_out)
    if not available:
        raise HTTPException(status_code=409, detail={"code": "DATES_UNAVAILABLE", "message": "Requested dates are not available"})
    
    acquired = await _acquire_dates(req.property_id, req.check_in, req.check_out)
    if not acquired:
        raise HTTPException(status_code=409, detail={"code": "DATES_UNAVAILABLE", "message": "Could not acquire dates"})
    
    # 5. Get pricing
    pricing = await _get_rates(req.property_id, req.check_in, req.check_out)
    fee_data = await _get_fees(req.property_id)
    
    # Calculate totals
    nightly_rates = []
    subtotal_cents = 0
    if pricing and pricing.get("rates"):
        for rate in pricing["rates"]:
            nightly_rates.append(rate)
            subtotal_cents += rate["rate_cents"]
    else:
        # Default rate if pricing unavailable
        default_rate = 45000  # $450/night
        current = req.check_in
        while current < req.check_out:
            nightly_rates.append({"date": current.isoformat(), "rate_cents": default_rate})
            subtotal_cents += default_rate
            current += timedelta(days=1)
    
    # Calculate fees
    fees = []
    fees_total = 0
    for fee in fee_data.get("fees", []):
        fee_amount = fee["amount_cents"]
        if fee.get("per") == "PER_NIGHT":
            fee_amount *= nights
        elif fee.get("per") == "PER_GUEST":
            fee_amount *= total_guests
        fees.append({"kind": fee["kind"], "label": fee.get("label", fee["kind"]), "cents": fee_amount})
        fees_total += fee_amount
    
    # Calculate taxes (simplified: 12% occupancy tax)
    tax_rate = 0.12
    taxable = subtotal_cents + fees_total
    taxes_total = round(taxable * tax_rate)
    taxes = [{"kind": "occupancy", "label": f"Occupancy Tax ({int(tax_rate * 100)}%)", "rate": tax_rate, "cents": taxes_total}]
    
    total_cents = subtotal_cents + fees_total + taxes_total
    currency = pricing.get("currency", "USD") if pricing else "USD"
    
    # Generate UBR
    ubr = generate_ubr(req.property_id, req.check_in.isoformat(), req.check_out.isoformat(), req.guest_id)
    guest_name = f"{req.guests.primary.first_name} {req.guests.primary.last_name}"
    
    # 6. Create booking record + inventory block (single transaction)
    booking_id = str(uuid.uuid4())
    
    with get_db_cursor() as (cur, conn):
        try:
            cur.execute("""
                INSERT INTO booking 
                    (id, ubr, property_id, guest_id, guest_name, guest_email, guest_phone,
                     check_in, check_out, nights, guests_adults, guests_children, guests_infants, guests_pets,
                     payment_intent_id, customer_id, currency, total_cents, status,
                     idempotency_key, confirmed_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'CONFIRMED', %s, %s)
            """, (
                booking_id, ubr, req.property_id, req.guest_id,
                guest_name, req.guests.primary.email, req.guests.primary.phone,
                req.check_in, req.check_out, nights,
                req.guests.adults, req.guests.children, req.guests.infants, req.guests.pets,
                req.payment_method_id, req.customer_id, currency, total_cents,
                req.idempotency_key, now,
            ))
            
            # Price snapshot
            cur.execute("""
                INSERT INTO booking_price
                    (booking_id, currency, nightly_rates, subtotal_cents, fees, fees_total_cents,
                     taxes, taxes_total_cents, discounts, discounts_total_cents, total_cents)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, '[]', 0, %s)
            """, (
                booking_id, currency, json.dumps(nightly_rates), subtotal_cents,
                json.dumps(fees), fees_total, json.dumps(taxes), taxes_total, total_cents,
            ))
            
            # Inventory block
            cur.execute("""
                INSERT INTO inventory_block
                    (booking_id, property_id, check_in, check_out, status,
                     guest_first_name, guest_last_name, guest_email, guest_phone,
                     guests, source)
                VALUES (%s, %s, %s, %s, 'CONFIRMED', %s, %s, %s, %s, %s, 'WANDER')
            """, (
                booking_id, req.property_id, req.check_in, req.check_out,
                req.guests.primary.first_name, req.guests.primary.last_name,
                req.guests.primary.email, req.guests.primary.phone, total_guests,
            ))
            
            conn.commit()
        except Exception as e:
            # Release dates on failure
            await _release_dates(req.property_id, req.check_in, req.check_out)
            if "exclude" in str(e).lower() or "overlap" in str(e).lower():
                raise HTTPException(status_code=409, detail={"code": "DATES_UNAVAILABLE", "message": "Double booking prevented by constraint"})
            raise
    
    # 7. Create invoice + payment
    invoice_items = [
        {"item_type": "NIGHTLY", "description": f"{nights} nights", "cents": subtotal_cents},
    ]
    for fee in fees:
        invoice_items.append({"item_type": fee["kind"].upper(), "description": fee["label"], "cents": fee["cents"]})
    invoice_items.append({"item_type": "TAX", "description": "Occupancy Tax", "cents": taxes_total})
    
    invoice = await _create_invoice(booking_id, req.customer_id, invoice_items, currency)
    if invoice:
        await _pay_invoice(invoice["id"], req.payment_method_id, total_cents)
    
    # 8. Create PMS reservation
    pms_result = await _create_pms_reservation(
        req.property_id, req.check_in, req.check_out,
        guest_name, req.guests.primary.email, total_guests,
    )
    
    pms_confirmation_id = None
    if pms_result:
        pms_confirmation_id = pms_result.get("confirmation_code")
        with get_db_cursor() as (cur, conn):
            cur.execute(
                "UPDATE booking SET pms_confirmation_id = %s WHERE id = %s",
                (pms_confirmation_id, booking_id)
            )
            conn.commit()
    else:
        # PMS failed: auto-refund and release
        logger.warning(f"PMS reservation failed for booking {booking_id}, initiating auto-refund")
        await _refund_booking(booking_id, total_cents, "PMS reservation failed")
        await _release_dates(req.property_id, req.check_in, req.check_out)
        
        with get_db_cursor() as (cur, conn):
            cur.execute("UPDATE booking SET status = 'CANCELLED', cancelled_at = NOW() WHERE id = %s", (booking_id,))
            cur.execute("UPDATE inventory_block SET status = 'CANCELLED' WHERE booking_id = %s", (booking_id,))
            conn.commit()
        
        emit("booking.failed", "booking", booking_id, context={
            "step": "pms_reservation", "error": "PMS unavailable",
        })
        raise HTTPException(status_code=503, detail={"code": "PMS_UNAVAILABLE", "message": "Could not create PMS reservation. Payment reversed."})
    
    # 9. Emit events
    emit("booking.confirmed", "booking", booking_id, context={
        "ubr": ubr, "property_id": req.property_id, "total_cents": total_cents,
        "pms_confirmation_id": pms_confirmation_id,
    })
    
    return {
        "id": booking_id,
        "ubr": ubr,
        "status": "CONFIRMED",
        "property_id": req.property_id,
        "check_in": req.check_in.isoformat(),
        "check_out": req.check_out.isoformat(),
        "nights": nights,
        "guests": {
            "adults": req.guests.adults,
            "children": req.guests.children,
            "total": total_guests,
        },
        "price": {
            "currency": currency,
            "subtotal_cents": subtotal_cents,
            "fees_total_cents": fees_total,
            "taxes_total_cents": taxes_total,
            "discounts_total_cents": 0,
            "total_cents": total_cents,
        },
        "pms_confirmation_id": pms_confirmation_id,
        "payment_intent_id": req.payment_method_id,
        "confirmed_at": now.isoformat(),
    }


@app.get("/bookings/{booking_id}")
async def get_booking(booking_id: str):
    """Retrieve booking details with price breakdown."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM booking WHERE id = %s", (booking_id,))
        booking = cur.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Booking not found"})
        
        cur.execute("SELECT * FROM booking_price WHERE booking_id = %s", (booking_id,))
        price = cur.fetchone()
    
    return _format_booking(booking, price)


@app.get("/bookings/ubr/{ubr}")
async def get_booking_by_ubr(ubr: str):
    """Retrieve booking by Universal Booking Reference."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM booking WHERE ubr = %s", (ubr,))
        booking = cur.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Booking not found"})
        
        cur.execute("SELECT * FROM booking_price WHERE booking_id = %s", (booking['id'],))
        price = cur.fetchone()
    
    return _format_booking(booking, price)


@app.get("/bookings")
async def list_bookings(
    guest_id: Optional[str] = None,
    property_id: Optional[str] = None,
    status: Optional[str] = None,
    check_in_from: Optional[date] = None,
    check_in_to: Optional[date] = None,
    limit: int = Query(20, le=100),
    cursor: Optional[str] = None,
):
    """List bookings with filters."""
    conditions = []
    params = []
    
    if guest_id:
        conditions.append("guest_id = %s")
        params.append(guest_id)
    if property_id:
        conditions.append("property_id = %s")
        params.append(property_id)
    if status:
        conditions.append("status = %s")
        params.append(status)
    if check_in_from:
        conditions.append("check_in >= %s")
        params.append(check_in_from)
    if check_in_to:
        conditions.append("check_in <= %s")
        params.append(check_in_to)
    if cursor:
        conditions.append("id > %s")
        params.append(cursor)
    
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    params.append(limit + 1)
    
    with get_db_cursor() as (cur, conn):
        cur.execute(f"SELECT * FROM booking {where} ORDER BY created_at DESC LIMIT %s", params)
        rows = cur.fetchall()
    
    has_more = len(rows) > limit
    data = rows[:limit]
    
    return {
        "data": [
            {
                "id": str(b['id']),
                "ubr": b['ubr'],
                "property_id": str(b['property_id']),
                "status": b['status'],
                "check_in": b['check_in'].isoformat(),
                "check_out": b['check_out'].isoformat(),
                "nights": b['nights'],
                "total_cents": b['total_cents'],
                "guest_name": b['guest_name'],
                "created_at": b['created_at'].isoformat(),
            }
            for b in data
        ],
        "next_cursor": str(data[-1]['id']) if has_more else None,
    }


@app.post("/bookings/{booking_id}/cancel")
async def cancel_booking(booking_id: str, req: CancelBookingRequest):
    """Cancel a booking. Handles refund calculation."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM booking WHERE id = %s", (booking_id,))
        booking = cur.fetchone()
        if not booking:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Booking not found"})
        
        if booking['status'] == 'CANCELLED':
            raise HTTPException(status_code=400, detail={"code": "ALREADY_CANCELLED", "message": "Booking already cancelled"})
    
    # Calculate refund based on cancellation policy
    days_until_checkin = (booking['check_in'] - date.today()).days
    total = booking['total_cents']
    
    # Simplified cancellation policy tiers
    if days_until_checkin >= 7:
        refund_percent = 100
    elif days_until_checkin >= 1:
        refund_percent = 50
    else:
        refund_percent = 0
    
    refund_amount = round(total * refund_percent / 100)
    penalty_cents = total - refund_amount
    
    if req.dry_run:
        return {
            "dry_run": True,
            "cancellation": {
                "refund_amount_cents": refund_amount,
                "penalty_cents": penalty_cents,
                "refund_percent": refund_percent,
                "policy_name": "MODERATE",
            }
        }
    
    # Process cancellation
    cancellation_data = {
        "reason": req.reason,
        "actor": req.actor,
        "refund_amount_cents": refund_amount,
        "penalty_cents": penalty_cents,
        "refund_percent": refund_percent,
    }
    
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            UPDATE booking
            SET status = 'CANCELLED', cancelled_at = NOW(), cancellation = %s
            WHERE id = %s
        """, (json.dumps(cancellation_data), booking_id))
        
        cur.execute("UPDATE inventory_block SET status = 'CANCELLED' WHERE booking_id = %s", (booking_id,))
        conn.commit()
    
    # Release availability
    await _release_dates(str(booking['property_id']), booking['check_in'], booking['check_out'])
    
    # Process refund if applicable
    if refund_amount > 0:
        await _refund_booking(booking_id, refund_amount, req.reason)
    
    emit("booking.cancelled", "booking", booking_id, context={
        "reason": req.reason, "refund_amount_cents": refund_amount,
    })
    
    return {
        "id": booking_id,
        "status": "CANCELLED",
        "cancellation": cancellation_data,
    }


def _format_booking(booking: dict, price: dict) -> dict:
    """Format booking + price for API response."""
    result = {
        "id": str(booking['id']),
        "ubr": booking['ubr'],
        "status": booking['status'],
        "property_id": str(booking['property_id']),
        "check_in": booking['check_in'].isoformat(),
        "check_out": booking['check_out'].isoformat(),
        "nights": booking['nights'],
        "guest": {
            "id": str(booking['guest_id']),
            "name": booking['guest_name'],
            "email": booking['guest_email'],
        },
        "guests": {
            "adults": booking['guests_adults'],
            "children": booking['guests_children'],
            "pets": booking['guests_pets'],
        },
        "pms_confirmation_id": booking['pms_confirmation_id'],
        "confirmed_at": booking['confirmed_at'].isoformat() if booking['confirmed_at'] else None,
        "cancelled_at": booking['cancelled_at'].isoformat() if booking['cancelled_at'] else None,
    }
    
    if price:
        result["price"] = {
            "currency": price['currency'],
            "nightly_rates": price['nightly_rates'],
            "subtotal_cents": price['subtotal_cents'],
            "fees": price['fees'],
            "fees_total_cents": price['fees_total_cents'],
            "taxes": price['taxes'],
            "taxes_total_cents": price['taxes_total_cents'],
            "discounts": price.get('discounts', []),
            "discounts_total_cents": price.get('discounts_total_cents', 0),
            "total_cents": price['total_cents'],
        }
    
    return result
