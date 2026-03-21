"""
Sync Service — Bidirectional PMS synchronization.

Base URL: /api/v1/sync
Source of Truth: Owns sync state.

Features:
- Two-layer: Connectors (transport) + Adapters (translation)
- Three subsystems: Property, Availability, Pricing
- 8 PMS adapters: Guesty, Hostaway, Streamline, Escapia, OwnerRez, Lodgify, RentalsUnited, TrackPMS
- Token bucket rate limiting per credential
- Debounced availability updates (5s coalesce)
- Webhook deduplication (SHA256 payload hash)

Endpoints:
  POST /webhook/{pms_type}                  - Receive PMS webhook
  POST /sync/push                           - Outbound sync
  POST /sync/pull                           - Inbound sync
  GET  /sync/jobs/{job_id}                  - Get job status
  POST /sync/reconcile                      - Start reconciliation
  GET  /sync/status                         - Sync health
  GET  /sync/rate-limits                    - Rate limit state
  POST /sync/pms/quote                      - Get PMS quote (internal)
  POST /sync/pms/reservation               - Create PMS reservation (internal)
  DELETE /sync/pms/reservation/{id}         - Cancel PMS reservation (internal)
"""

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Optional, List, Dict, Any

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.shared.events import emit_event
from services.sync.store import SyncStore
from services.sync.adapters import get_adapter, ADAPTERS


app = FastAPI(title="Sync Service", version="1.0.0")

_store: Optional[SyncStore] = None


def get_store() -> SyncStore:
    global _store
    if _store is None:
        _store = SyncStore(Database(), get_cache())
    return _store


def set_store(store: SyncStore):
    global _store
    _store = store


# --- Request Models ---

class SyncPushRequest(BaseModel):
    property_id: str
    subsystems: List[str] = ["availability", "pricing", "property"]
    date_range: Optional[Dict[str, str]] = None


class SyncPullRequest(BaseModel):
    property_id: str
    subsystems: List[str] = ["availability", "pricing", "property"]
    full: bool = False


class ReconcileRequest(BaseModel):
    property_ids: Optional[List[str]] = None
    subsystems: List[str] = ["availability", "pricing", "property"]


class PmsQuoteRequest(BaseModel):
    property_id: str
    check_in: str
    check_out: str
    guests: int = 1


class PmsReservationRequest(BaseModel):
    property_id: str
    check_in: str
    check_out: str
    guest: Dict[str, Any]
    quote_id: Optional[str] = None
    booking_id: Optional[str] = None


# --- Endpoints ---

@app.post("/api/v1/sync/webhook/{pms_type}")
def receive_webhook(pms_type: str, payload: Dict[str, Any] = {}):
    """Receive and acknowledge a PMS webhook. Must respond within 500ms."""
    if pms_type not in ADAPTERS:
        raise HTTPException(400, detail={"error": {"code": "UNKNOWN_PMS", "message": f"Unknown PMS: {pms_type}"}})

    store = get_store()
    result = store.receive_webhook(pms_type, payload)

    if not result.get("deduplicated"):
        emit_event(
            f"sync.webhook_received",
            "sync",
            result["event_id"],
            context={"pms_type": pms_type}
        )

    return result


@app.post("/api/v1/sync/push")
def sync_push(request: SyncPushRequest):
    """Push changes to PMS (outbound sync for OPERATED properties)."""
    store = get_store()
    job = store.create_sync_job("push", request.property_id, subsystems=request.subsystems)

    emit_event(
        "sync.property_pushed",
        "property",
        request.property_id,
        context={"job_id": job["job_id"]}
    )

    # In production, this would enqueue actual sync work
    store.complete_job(job["job_id"], "COMPLETED")

    return {"job_id": job["job_id"], "status": "QUEUED", "subsystem_jobs": job["subsystem_jobs"]}


@app.post("/api/v1/sync/pull")
def sync_pull(request: SyncPullRequest):
    """Pull changes from PMS (inbound sync for BRANDED properties)."""
    store = get_store()
    job = store.create_sync_job("pull", request.property_id, subsystems=request.subsystems)

    emit_event(
        "sync.property_pulled",
        "property",
        request.property_id,
        context={"job_id": job["job_id"]}
    )

    store.complete_job(job["job_id"], "COMPLETED")

    return {"job_id": job["job_id"], "status": "QUEUED", "subsystem_jobs": job["subsystem_jobs"]}


@app.get("/api/v1/sync/jobs/{job_id}")
def get_job(job_id: str):
    """Get sync job status."""
    store = get_store()
    job = store.get_job(job_id)
    if not job:
        raise HTTPException(404, detail={"error": {"code": "NOT_FOUND"}})
    return job


@app.post("/api/v1/sync/reconcile")
def start_reconciliation(request: ReconcileRequest):
    """Start a reconciliation job."""
    store = get_store()
    job = store.create_sync_job(
        "reconcile", None,
        subsystems=request.subsystems
    )
    store.complete_job(job["job_id"], "COMPLETED")

    return {
        "job_id": job["job_id"],
        "status": "QUEUED",
        "property_count": len(request.property_ids) if request.property_ids else 0,
    }


@app.get("/api/v1/sync/status")
def sync_status():
    """Get overall sync health."""
    store = get_store()
    return store.get_sync_status()


@app.get("/api/v1/sync/rate-limits")
def rate_limits():
    """Get current rate limit state by partner."""
    store = get_store()
    limits = store.get_rate_limits()
    return {"limits": limits}


@app.post("/api/v1/sync/pms/quote")
def pms_quote(request: PmsQuoteRequest):
    """Get a quote from the PMS (internal, called by Booking Service).
    
    In production, this would call the actual PMS API via the connector layer.
    For now, returns a simulated quote.
    """
    store = get_store()

    # Simulate PMS quote (in production, would call PMS API)
    import time as _time
    from datetime import datetime, timedelta

    check_in = datetime.strptime(request.check_in, "%Y-%m-%d")
    check_out = datetime.strptime(request.check_out, "%Y-%m-%d")
    nights = (check_out - check_in).days

    rates = []
    for i in range(nights):
        d = check_in + timedelta(days=i)
        rates.append({"date": d.strftime("%Y-%m-%d"), "rate_cents": 45000})

    fees = [{"kind": "CLEANING", "label": "Cleaning Fee", "amount_cents": 15000}]
    total = sum(r["rate_cents"] for r in rates) + sum(f["amount_cents"] for f in fees)

    result = store.cache_quote(
        request.property_id, "simulated",
        request.check_in, request.check_out,
        rates, fees, total
    )

    return result


@app.post("/api/v1/sync/pms/reservation", status_code=201)
def create_pms_reservation(request: PmsReservationRequest):
    """Create a reservation in the PMS (internal, called by Booking Service).
    
    In production, would call PMS API. Returns simulated confirmation.
    """
    store = get_store()

    # Simulate PMS reservation creation
    import uuid as _uuid
    external_id = f"PMS-{str(_uuid.uuid4())[:8].upper()}"
    confirmation_code = f"WDR-{str(_uuid.uuid4())[:6].upper()}"

    result = store.create_reservation_mapping(
        request.booking_id or "",
        request.property_id,
        "simulated",
        external_id,
        confirmation_code
    )

    emit_event(
        "sync.booking_received",
        "booking",
        request.booking_id or "",
        context={
            "property_id": request.property_id,
            "external_id": external_id,
        }
    )

    return result


@app.delete("/api/v1/sync/pms/reservation/{external_id}")
def cancel_pms_reservation(external_id: str):
    """Cancel a PMS reservation (internal, called by Booking Service)."""
    # In production, would call PMS API to cancel
    return {"external_id": external_id, "cancelled": True}


@app.get("/health")
def health():
    return {"status": "ok", "service": "sync"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8005)
