"""
Payments Service API
Stripe orchestration and financial ledger mirror.
Stripe is the ledger — we maintain read-optimized mirror.
Immutable payout snapshots with SHA256 hash chain.

Endpoints:
  POST /invoices             - Create invoice
  GET  /invoices/{id}        - Get invoice
  POST /invoices/{id}/pay    - Record payment
  POST /invoices/{id}/void   - Void invoice
  POST /refunds              - Process refund
  GET  /refunds/{id}         - Get refund
  POST /payouts/run          - Run payout cycle
  GET  /payouts/{id}         - Get payout snapshot
  GET  /payouts/verify-chain - Verify hash chain integrity
  GET  /reconciliation/status - Reconciliation status
"""
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional, List
import hashlib
import json
import uuid
import logging

from shared.database import get_db_cursor, init_schema
from shared.events import emit

logger = logging.getLogger("payments")

app = FastAPI(title="Payments Service", version="1.0.0")


# --- Models ---

class InvoiceItemInput(BaseModel):
    item_type: str
    description: str
    cents: int
    quantity: int = 1


class CreateInvoiceRequest(BaseModel):
    booking_id: str
    customer_id: str
    items: List[InvoiceItemInput]
    currency: str = "USD"
    due_date: Optional[date] = None
    platform_fee_rate: float = 0.15


class PayInvoiceRequest(BaseModel):
    stripe_payment_intent_id: str
    amount_cents: int


class RefundRequest(BaseModel):
    booking_id: str
    amount_cents: int
    reason: Optional[str] = None
    note: Optional[str] = None


class PayoutRunRequest(BaseModel):
    cycle: str  # YYYY-Wnn
    owner_ids: Optional[List[str]] = None
    dry_run: bool = False


class ResolveExceptionRequest(BaseModel):
    resolution: str
    note: Optional[str] = None


# --- Hash Chain ---

def compute_payout_hash(owner_id: str, bookings: list, totals: dict, prev_hash: str = None) -> str:
    """SHA256 hash for payout snapshot. Exclude mutable fields."""
    content = json.dumps({
        "owner_id": owner_id,
        "bookings": sorted(bookings, key=lambda b: b.get("booking_id", "")),
        "totals": dict(sorted(totals.items())),
    }, sort_keys=True)
    
    if prev_hash:
        content = prev_hash + content
    
    return hashlib.sha256(content.encode()).hexdigest()


# --- Invoice Number ---

def generate_invoice_number() -> str:
    """Generate a human-readable invoice number."""
    return f"INV-{uuid.uuid4().hex[:8].upper()}"


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/payments/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "payments"}


@app.post("/invoices")
async def create_invoice(req: CreateInvoiceRequest):
    """Create an invoice for a booking."""
    subtotal_cents = sum(item.cents * item.quantity for item in req.items)
    # Separate tax items from non-tax
    tax_items = [i for i in req.items if i.item_type.upper() in ('TAX', 'OCCUPANCY_TAX', 'SALES_TAX')]
    tax_cents = sum(i.cents * i.quantity for i in tax_items)
    total_cents = subtotal_cents
    due_cents = total_cents
    platform_fee_cents = round(subtotal_cents * req.platform_fee_rate)
    invoice_number = generate_invoice_number()
    
    with get_db_cursor() as (cur, conn):
        # Check for existing invoice
        cur.execute("SELECT id FROM invoice WHERE booking_id = %s", (req.booking_id,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail={"code": "INVOICE_EXISTS", "message": "Invoice already exists for this booking"})
        
        cur.execute("""
            INSERT INTO invoice (invoice_number, booking_id, customer_id, status, currency,
                subtotal_cents, tax_cents, total_cents, paid_cents, refunded_cents, due_cents,
                platform_fee_cents, platform_fee_rate, due_date)
            VALUES (%s, %s, %s, 'OPEN', %s, %s, %s, %s, 0, 0, %s, %s, %s, %s)
            RETURNING id, created_at
        """, (
            invoice_number, req.booking_id, req.customer_id, req.currency,
            subtotal_cents, tax_cents, total_cents, due_cents,
            platform_fee_cents, req.platform_fee_rate, req.due_date,
        ))
        inv = cur.fetchone()
        
        # Insert line items
        for item in req.items:
            cur.execute("""
                INSERT INTO invoice_item (invoice_id, item_type, description, cents, quantity)
                VALUES (%s, %s, %s, %s, %s)
            """, (inv['id'], item.item_type, item.description, item.cents, item.quantity))
        
        conn.commit()
    
    emit("payment.invoice_created", "invoice", str(inv['id']), context={
        "booking_id": req.booking_id, "total_cents": total_cents,
    })
    
    return {
        "id": str(inv['id']),
        "invoice_number": invoice_number,
        "status": "OPEN",
        "subtotal_cents": subtotal_cents,
        "tax_cents": tax_cents,
        "total_cents": total_cents,
        "due_cents": due_cents,
        "created_at": inv['created_at'].isoformat(),
    }


@app.get("/invoices/{invoice_id}")
async def get_invoice(invoice_id: str):
    """Get invoice details with line items."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM invoice WHERE id = %s", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})
        
        cur.execute("SELECT * FROM invoice_item WHERE invoice_id = %s", (invoice_id,))
        items = cur.fetchall()
    
    return {
        "id": str(inv['id']),
        "invoice_number": inv['invoice_number'],
        "booking_id": str(inv['booking_id']),
        "customer_id": inv['customer_id'],
        "status": inv['status'],
        "currency": inv['currency'],
        "items": [
            {"item_type": i['item_type'], "description": i['description'],
             "cents": i['cents'], "quantity": i['quantity']}
            for i in items
        ],
        "subtotal_cents": inv['subtotal_cents'],
        "tax_cents": inv['tax_cents'],
        "total_cents": inv['total_cents'],
        "paid_cents": inv['paid_cents'],
        "refunded_cents": inv['refunded_cents'],
        "due_cents": inv['due_cents'],
        "paid_at": inv['paid_at'].isoformat() if inv['paid_at'] else None,
        "created_at": inv['created_at'].isoformat(),
    }


@app.post("/invoices/{invoice_id}/pay")
async def pay_invoice(invoice_id: str, req: PayInvoiceRequest):
    """Record a payment against an invoice."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM invoice WHERE id = %s FOR UPDATE", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})
        
        # Record payment
        cur.execute("""
            INSERT INTO payment (invoice_id, stripe_payment_intent_id, amount_cents, status, completed_at)
            VALUES (%s, %s, %s, 'SUCCEEDED', NOW())
        """, (invoice_id, req.stripe_payment_intent_id, req.amount_cents))
        
        new_paid = inv['paid_cents'] + req.amount_cents
        new_due = inv['total_cents'] - new_paid + inv['refunded_cents']
        new_status = 'PAID' if new_due <= 0 else 'PARTIALLY_PAID'
        
        cur.execute("""
            UPDATE invoice
            SET paid_cents = %s, due_cents = %s, status = %s, paid_at = NOW(),
                stripe_payment_intent_id = %s, updated_at = NOW()
            WHERE id = %s
        """, (new_paid, max(0, new_due), new_status, req.stripe_payment_intent_id, invoice_id))
        
        conn.commit()
    
    emit("payment.succeeded", "invoice", str(invoice_id), context={
        "amount_cents": req.amount_cents,
    })
    
    return {"status": new_status, "paid_cents": new_paid, "due_cents": max(0, new_due)}


@app.post("/invoices/{invoice_id}/void")
async def void_invoice(invoice_id: str):
    """Void an invoice."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT status FROM invoice WHERE id = %s", (invoice_id,))
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Invoice not found"})
        
        if inv['status'] in ('PAID', 'REFUNDED'):
            raise HTTPException(status_code=400, detail={"code": "INVALID_STATE", "message": "Cannot void paid or refunded invoice"})
        
        cur.execute(
            "UPDATE invoice SET status = 'VOID', voided_at = NOW(), updated_at = NOW() WHERE id = %s",
            (invoice_id,)
        )
        conn.commit()
    
    return {"status": "VOID", "voided_at": datetime.now(timezone.utc).isoformat()}


@app.post("/refunds")
async def create_refund(req: RefundRequest):
    """Process a refund. Amount must not exceed paid - already_refunded."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM invoice WHERE booking_id = %s", (req.booking_id,))
        inv = cur.fetchone()
        if not inv:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "No invoice found for booking"})
        
        max_refundable = inv['paid_cents'] - inv['refunded_cents']
        if req.amount_cents > max_refundable:
            raise HTTPException(status_code=400, detail={
                "code": "EXCEEDS_PAID",
                "message": f"Refund {req.amount_cents} exceeds max refundable {max_refundable}",
            })
        
        # Create refund record (in production, call Stripe API here)
        stripe_refund_id = f"re_{uuid.uuid4().hex[:24]}"
        
        cur.execute("""
            INSERT INTO refund (invoice_id, booking_id, amount_cents, reason, note, status, stripe_refund_id, completed_at)
            VALUES (%s, %s, %s, %s, %s, 'SUCCEEDED', %s, NOW())
            RETURNING id, created_at
        """, (inv['id'], req.booking_id, req.amount_cents, req.reason, req.note, stripe_refund_id))
        ref = cur.fetchone()
        
        # Update invoice
        new_refunded = inv['refunded_cents'] + req.amount_cents
        new_status = 'REFUNDED' if new_refunded >= inv['paid_cents'] else inv['status']
        cur.execute(
            "UPDATE invoice SET refunded_cents = %s, status = %s, updated_at = NOW() WHERE id = %s",
            (new_refunded, new_status, inv['id'])
        )
        
        conn.commit()
    
    emit("payment.refunded", "refund", str(ref['id']), context={
        "invoice_id": str(inv['id']), "amount_cents": req.amount_cents,
    })
    
    return {
        "id": str(ref['id']),
        "invoice_id": str(inv['id']),
        "amount_cents": req.amount_cents,
        "status": "SUCCEEDED",
        "stripe_refund_id": stripe_refund_id,
        "created_at": ref['created_at'].isoformat(),
    }


@app.get("/refunds/{refund_id}")
async def get_refund(refund_id: str):
    """Get refund details."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM refund WHERE id = %s", (refund_id,))
        ref = cur.fetchone()
    
    if not ref:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Refund not found"})
    
    return {
        "id": str(ref['id']),
        "invoice_id": str(ref['invoice_id']),
        "booking_id": str(ref['booking_id']),
        "amount_cents": ref['amount_cents'],
        "reason": ref['reason'],
        "status": ref['status'],
        "stripe_refund_id": ref['stripe_refund_id'],
        "created_at": ref['created_at'].isoformat(),
        "completed_at": ref['completed_at'].isoformat() if ref['completed_at'] else None,
    }


@app.post("/payouts/run")
async def run_payout(req: PayoutRunRequest):
    """Run payout cycle. Creates immutable, hash-chained snapshots."""
    with get_db_cursor() as (cur, conn):
        # Get bookings for this cycle (simplified: all paid invoices)
        query = "SELECT * FROM invoice WHERE status = 'PAID'"
        params = []
        if req.owner_ids:
            # In production, join with property ownership
            pass
        
        cur.execute(query, params)
        invoices = cur.fetchall()
        
        if not invoices:
            return {"cycle": req.cycle, "payout_snapshots": [], "total_payout_cents": 0}
        
        # Group by owner (simplified: use customer_id as proxy)
        owner_groups = {}
        for inv in invoices:
            owner = inv['customer_id']
            if owner not in owner_groups:
                owner_groups[owner] = []
            owner_groups[owner].append(inv)
        
        snapshots = []
        for owner_id, owner_invoices in owner_groups.items():
            bookings = []
            gross_cents = 0
            platform_fee_cents = 0
            
            for inv in owner_invoices:
                bookings.append({
                    "booking_id": str(inv['booking_id']),
                    "gross_cents": inv['total_cents'],
                    "platform_fee_cents": inv['platform_fee_cents'],
                })
                gross_cents += inv['total_cents']
                platform_fee_cents += inv['platform_fee_cents']
            
            total_cents = gross_cents - platform_fee_cents
            
            totals = {
                "gross_cents": gross_cents,
                "platform_fee_cents": platform_fee_cents,
                "total_cents": total_cents,
            }
            
            # Get previous hash
            cur.execute(
                "SELECT content_hash FROM payout_snapshot WHERE owner_id = %s ORDER BY created_at DESC LIMIT 1",
                (owner_id,)
            )
            prev_row = cur.fetchone()
            prev_hash = prev_row['content_hash'] if prev_row else None
            
            content_hash = compute_payout_hash(owner_id, bookings, totals, prev_hash)
            
            if not req.dry_run:
                cur.execute("""
                    INSERT INTO payout_snapshot
                        (owner_id, cycle, bookings, booking_count, gross_cents, platform_fee_cents,
                         total_cents, content_hash, prev_hash, status)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'PENDING')
                    RETURNING id
                """, (
                    owner_id, req.cycle, json.dumps(bookings), len(bookings),
                    gross_cents, platform_fee_cents, total_cents,
                    content_hash, prev_hash,
                ))
                snap = cur.fetchone()
                snap_id = str(snap['id'])
            else:
                snap_id = "dry_run"
            
            snapshots.append({
                "id": snap_id,
                "owner_id": owner_id,
                "booking_count": len(bookings),
                "total_cents": total_cents,
                "content_hash": content_hash,
                "status": "DRY_RUN" if req.dry_run else "PENDING",
            })
        
        if not req.dry_run:
            conn.commit()
    
    total_payout = sum(s['total_cents'] for s in snapshots)
    
    return {
        "cycle": req.cycle,
        "payout_snapshots": snapshots,
        "total_payout_cents": total_payout,
    }


@app.get("/payouts/{payout_id}")
async def get_payout(payout_id: str):
    """Get payout snapshot details."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM payout_snapshot WHERE id = %s", (payout_id,))
        snap = cur.fetchone()
    
    if not snap:
        raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Payout not found"})
    
    return {
        "id": str(snap['id']),
        "owner_id": str(snap['owner_id']),
        "cycle": snap['cycle'],
        "bookings": snap['bookings'],
        "booking_count": snap['booking_count'],
        "gross_cents": snap['gross_cents'],
        "platform_fee_cents": snap['platform_fee_cents'],
        "total_cents": snap['total_cents'],
        "content_hash": snap['content_hash'],
        "prev_hash": snap['prev_hash'],
        "status": snap['status'],
        "created_at": snap['created_at'].isoformat(),
    }


@app.get("/payouts/verify-chain")
async def verify_chain(owner_id: str = Query(...)):
    """Verify the hash chain integrity for an owner's payouts."""
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM payout_snapshot WHERE owner_id = %s ORDER BY created_at",
            (owner_id,)
        )
        snapshots = cur.fetchall()
    
    if not snapshots:
        return {"valid": True, "snapshots_checked": 0}
    
    prev_hash = None
    for snap in snapshots:
        expected_hash = compute_payout_hash(
            str(snap['owner_id']),
            snap['bookings'],
            {
                "gross_cents": snap['gross_cents'],
                "platform_fee_cents": snap['platform_fee_cents'],
                "total_cents": snap['total_cents'],
            },
            prev_hash,
        )
        
        if expected_hash != snap['content_hash']:
            return {
                "valid": False,
                "broken_at": str(snap['id']),
                "expected_hash": expected_hash,
                "actual_hash": snap['content_hash'],
            }
        
        prev_hash = snap['content_hash']
    
    return {"valid": True, "snapshots_checked": len(snapshots)}


@app.get("/reconciliation/status")
async def reconciliation_status():
    """Get reconciliation status."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT COUNT(*) as cnt FROM reconciliation_exception WHERE status = 'PENDING'")
        pending = cur.fetchone()['cnt']
    
    return {
        "last_run": None,
        "status": "ok" if pending == 0 else "exceptions_pending",
        "exceptions_pending": pending,
        "drift_amount_cents": 0,
    }


@app.get("/reconciliation/exceptions")
async def list_exceptions(status: str = Query("PENDING")):
    """List reconciliation exceptions."""
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "SELECT * FROM reconciliation_exception WHERE status = %s ORDER BY created_at DESC",
            (status,)
        )
        exceptions = cur.fetchall()
    
    return {
        "exceptions": [
            {
                "id": str(e['id']),
                "exception_type": e['exception_type'],
                "stripe_id": e['stripe_id'],
                "expected_cents": e['expected_cents'],
                "actual_cents": e['actual_cents'],
                "status": e['status'],
                "created_at": e['created_at'].isoformat(),
            }
            for e in exceptions
        ],
        "total": len(exceptions),
    }


@app.post("/reconciliation/exceptions/{exception_id}/resolve")
async def resolve_exception(exception_id: str, req: ResolveExceptionRequest):
    """Resolve a reconciliation exception."""
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE reconciliation_exception SET status = 'RESOLVED', resolution = %s, resolved_at = NOW() WHERE id = %s",
            (req.resolution, exception_id)
        )
        conn.commit()
    
    return {"status": "resolved"}
