"""
Booking Service — Synchronous booking orchestrator (<3s flow).

Base URL: /api/v1/bookings
Source of Truth: Owns booking state, UBR generation, reservation lifecycle.

Flow: Authorize payment → Verify PMS → Create PMS reservation → Capture → Finalize
If PMS fails after charge: auto-refund + release availability.

Endpoints:
  POST /bookings              - Create booking with payment
  GET  /bookings/{id}         - Get booking details
  GET  /bookings/ubr/{ubr}    - Get booking by UBR
  GET  /bookings              - List bookings
  POST /bookings/{id}/cancel  - Cancel booking
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.booking.store import BookingStore
from services.booking.orchestrator import BookingOrchestrator


app = FastAPI(title="Booking Service", version="1.0.0")

_orchestrator: Optional[BookingOrchestrator] = None
_store: Optional[BookingStore] = None


def get_store() -> BookingStore:
    global _store
    if _store is None:
        _store = BookingStore(Database(), get_cache())
    return _store


def set_store(store: BookingStore):
    global _store
    _store = store


def get_orchestrator() -> BookingOrchestrator:
    global _orchestrator
    if _orchestrator is None:
        raise RuntimeError("Orchestrator not initialized. Call set_orchestrator() first.")
    return _orchestrator


def set_orchestrator(orch: BookingOrchestrator):
    global _orchestrator
    _orchestrator = orch


# --- Request Models ---

class GuestPrimary(BaseModel):
    first_name: str = ""
    last_name: str = ""
    email: str = ""
    phone: Optional[str] = None


class GuestsInfo(BaseModel):
    adults: int = 1
    children: int = 0
    infants: int = 0
    pets: int = 0
    primary: Optional[GuestPrimary] = None


class CreateBookingRequest(BaseModel):
    idempotency_key: str
    property_id: str
    check_in: str
    check_out: str
    guest_id: str
    guests: GuestsInfo
    payment_method_id: str
    customer_id: str
    source_channel: Optional[str] = None
    promo_code: Optional[str] = None


class CancelBookingRequest(BaseModel):
    reason: str
    actor: Optional[str] = None
    dry_run: bool = False


# --- Endpoints ---

@app.post("/api/v1/bookings", status_code=201)
def create_booking(request: CreateBookingRequest):
    """Create a new booking with payment."""
    orch = get_orchestrator()

    req_dict = request.model_dump()
    # Flatten guests for orchestrator
    guests = req_dict.pop("guests", {})
    req_dict["guests"] = guests

    result, status_code = orch.create_booking(req_dict)

    if status_code >= 400:
        raise HTTPException(status_code, detail=result)

    return result


@app.get("/api/v1/bookings/{booking_id}")
def get_booking(booking_id: str):
    """Get booking details."""
    store = get_store()
    booking = store.get_booking(booking_id)
    if not booking:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "Booking not found"}})
    return booking


@app.get("/api/v1/bookings/ubr/{ubr}")
def get_booking_by_ubr(ubr: str):
    """Get booking by Universal Booking Reference."""
    store = get_store()
    booking = store.get_booking_by_ubr(ubr)
    if not booking:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": "Booking not found"}})
    return booking


@app.get("/api/v1/bookings")
def list_bookings(
    guest_id: Optional[str] = Query(None),
    property_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    cursor: Optional[str] = Query(None)
):
    """List bookings with filters."""
    store = get_store()
    filters = {}
    if guest_id:
        filters["guest_id"] = guest_id
    if property_id:
        filters["property_id"] = property_id
    if status:
        filters["status"] = status

    bookings, next_cursor = store.list_bookings(filters, limit, cursor)
    return {"data": bookings, "next_cursor": next_cursor}


@app.post("/api/v1/bookings/{booking_id}/cancel")
def cancel_booking(booking_id: str, request: CancelBookingRequest):
    """Cancel a booking with refund calculation."""
    orch = get_orchestrator()
    result, status_code = orch.cancel_booking(
        booking_id, request.reason, request.actor, request.dry_run
    )

    if status_code >= 400:
        raise HTTPException(status_code, detail=result)

    return result


@app.get("/health")
def health():
    return {"status": "ok", "service": "booking"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8007)
