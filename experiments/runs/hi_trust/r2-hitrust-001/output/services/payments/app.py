"""
Payments Service — Stripe orchestration and financial ledger mirror.

Base URL: /api/v1/payments
Source of Truth: Stripe (this is a mirror).

Features:
- Invoice-centric: invoices → payments → refunds → payouts
- Immutable payout snapshots with SHA256 hash chain
- Nightly reconciliation detects drift

Endpoints:
  POST /invoices                - Create invoice
  GET  /invoices/{id}           - Get invoice
  POST /invoices/{id}/pay       - Record payment
  POST /invoices/{id}/void      - Void invoice
  POST /refunds                 - Create refund
  GET  /refunds/{id}            - Get refund
  POST /payouts/run             - Create payout run
  GET  /payouts/{id}            - Get payout snapshot
  GET  /payouts/verify-chain    - Verify hash chain
  GET  /owners/{id}/payouts     - List owner payouts
  GET  /reconciliation/status   - Reconciliation status
  GET  /reconciliation/exceptions - List exceptions
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.shared.events import emit_event
from services.payments.store import PaymentsStore


app = FastAPI(title="Payments Service", version="1.0.0")

_store: Optional[PaymentsStore] = None


def get_store() -> PaymentsStore:
    global _store
    if _store is None:
        _store = PaymentsStore(Database(), get_cache())
    return _store


def set_store(store: PaymentsStore):
    global _store
    _store = store


# --- Request Models ---

class InvoiceItemRequest(BaseModel):
    type: str
    description: str
    cents: int


class CreateInvoiceRequest(BaseModel):
    booking_id: str
    customer_id: str
    items: List[InvoiceItemRequest]
    due_date: Optional[str] = None
    currency: str = "USD"


class PayInvoiceRequest(BaseModel):
    stripe_payment_intent_id: str
    amount_cents: int


class VoidInvoiceRequest(BaseModel):
    reason: str


class CreateRefundRequest(BaseModel):
    booking_id: str
    amount_cents: int
    reason: Optional[str] = None
    note: Optional[str] = None


class BookingPayout(BaseModel):
    booking_id: str
    gross_cents: int
    platform_fee_cents: int
    adjustments_cents: int = 0


class PayoutRunRequest(BaseModel):
    cycle: str
    owner_id: str
    bookings: List[BookingPayout]
    dry_run: bool = False


# --- Endpoints ---

@app.post("/api/v1/payments/invoices", status_code=201)
def create_invoice(request: CreateInvoiceRequest):
    """Create an invoice with line items."""
    store = get_store()
    result = store.create_invoice(
        request.booking_id,
        request.customer_id,
        [item.model_dump() for item in request.items],
        request.due_date,
        request.currency
    )

    emit_event(
        "payment.invoice_created",
        "invoice",
        result["id"],
        context={"booking_id": request.booking_id, "total_cents": result["total_cents"]}
    )

    return result


@app.get("/api/v1/payments/invoices/{invoice_id}")
def get_invoice(invoice_id: str):
    """Get invoice details."""
    store = get_store()
    invoice = store.get_invoice(invoice_id)
    if not invoice:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
    return invoice


@app.post("/api/v1/payments/invoices/{invoice_id}/pay")
def pay_invoice(invoice_id: str, request: PayInvoiceRequest):
    """Record a payment against an invoice."""
    store = get_store()
    result = store.pay_invoice(invoice_id, request.stripe_payment_intent_id, request.amount_cents)

    if "error" in result:
        if result["error"] == "NOT_FOUND":
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
        raise HTTPException(400, detail={"error": {"code": result["error"]}})

    emit_event(
        "payment.succeeded",
        "invoice",
        invoice_id,
        context={"amount_cents": request.amount_cents}
    )

    return result


@app.post("/api/v1/payments/invoices/{invoice_id}/void")
def void_invoice(invoice_id: str, request: VoidInvoiceRequest):
    """Void an unpaid invoice."""
    store = get_store()
    result = store.void_invoice(invoice_id, request.reason)

    if "error" in result:
        if result["error"] == "NOT_FOUND":
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
        raise HTTPException(400, detail={"error": {"code": result["error"]}})

    return result


@app.post("/api/v1/payments/refunds", status_code=201)
def create_refund(request: CreateRefundRequest):
    """Create a refund against a booking's invoice."""
    store = get_store()
    result = store.create_refund(
        request.booking_id, request.amount_cents,
        request.reason, request.note
    )

    if "error" in result:
        if result["error"] == "NOT_FOUND":
            raise HTTPException(404, detail={"error": {"code": "NOT_FOUND", "message": result.get("message")}})
        if result["error"] == "EXCEEDS_PAID":
            raise HTTPException(400, detail={"error": {"code": "EXCEEDS_PAID", "message": result.get("message")}})
        raise HTTPException(400, detail={"error": {"code": result["error"]}})

    emit_event(
        "payment.refunded",
        "refund",
        result["id"],
        context={"invoice_id": result["invoice_id"], "amount_cents": request.amount_cents}
    )

    return result


@app.get("/api/v1/payments/refunds/{refund_id}")
def get_refund(refund_id: str):
    """Get refund details."""
    store = get_store()
    refund = store.get_refund(refund_id)
    if not refund:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
    return refund


@app.post("/api/v1/payments/payouts/run")
def create_payout_run(request: PayoutRunRequest):
    """Create a payout run for an owner."""
    store = get_store()
    result = store.create_payout(
        request.owner_id,
        request.cycle,
        [b.model_dump() for b in request.bookings],
        request.dry_run
    )

    if not request.dry_run:
        emit_event(
            "payment.payout_created",
            "payout",
            result["id"],
            context={"owner_id": request.owner_id, "total_cents": result["total_cents"]}
        )

    return result


@app.get("/api/v1/payments/payouts/{payout_id}")
def get_payout(payout_id: str):
    """Get payout snapshot."""
    store = get_store()
    payout = store.get_payout(payout_id)
    if not payout:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
    return payout


@app.get("/api/v1/payments/payouts/verify-chain")
def verify_hash_chain(owner_id: str = Query(...)):
    """Verify the hash chain integrity for an owner's payouts."""
    store = get_store()
    return store.verify_hash_chain(owner_id)


@app.get("/api/v1/payments/owners/{owner_id}/payouts")
def list_owner_payouts(
    owner_id: str,
    status: Optional[str] = Query(None),
    limit: int = Query(50),
    cursor: Optional[str] = Query(None)
):
    """List payouts for an owner."""
    store = get_store()
    payouts, next_cursor = store.get_owner_payouts(owner_id, status, limit, cursor)
    return {"owner_id": owner_id, "payouts": payouts, "next_cursor": next_cursor}


@app.get("/api/v1/payments/reconciliation/status")
def reconciliation_status():
    """Get reconciliation status."""
    store = get_store()
    return store.get_reconciliation_status()


@app.get("/api/v1/payments/reconciliation/exceptions")
def reconciliation_exceptions():
    """List pending reconciliation exceptions."""
    store = get_store()
    exceptions = store.get_reconciliation_exceptions()
    return {"exceptions": exceptions, "total": len(exceptions)}


@app.get("/health")
def health():
    return {"status": "ok", "service": "payments"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8006)
