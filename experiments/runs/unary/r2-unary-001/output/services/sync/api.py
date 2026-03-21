"""
Sync Service API
Bidirectional PMS synchronization.
Two layers: Connectors (transport) + Adapters (translation).
Three subsystems: Property, Availability, Pricing (run independently).

Endpoints:
  POST /webhook/{pms_type}       - Receive PMS webhook
  POST /sync/push                - Push changes to PMS (outbound)
  POST /sync/pull                - Pull changes from PMS (inbound)
  GET  /sync/jobs/{job_id}       - Get job status
  POST /sync/reconcile           - Full reconciliation
  GET  /sync/status              - Overall sync health
  GET  /sync/rate-limits         - Current rate limit state
  POST /sync/pms/quote           - Get PMS quote (internal, for Booking Service)
  POST /sync/pms/reservation     - Create PMS reservation (internal)
  DELETE /sync/pms/reservation/{id} - Cancel PMS reservation (internal)
"""
from fastapi import FastAPI, Query, HTTPException, Request
from pydantic import BaseModel
from datetime import date, datetime, timezone
from typing import Optional, List
import hashlib
import json
import uuid
import logging

from .adapters import get_adapter, ADAPTER_REGISTRY
from shared.database import get_db_cursor, init_schema
from shared.events import emit

logger = logging.getLogger("sync")

app = FastAPI(title="Sync Service", version="1.0.0")


# --- Models ---

class PushRequest(BaseModel):
    property_id: str
    subsystems: List[str] = ["availability", "pricing"]
    date_range: Optional[dict] = None


class PullRequest(BaseModel):
    property_id: str
    subsystems: List[str] = ["availability", "pricing", "property"]
    full: bool = False


class ReconcileRequest(BaseModel):
    property_ids: Optional[List[str]] = None
    subsystems: List[str] = ["availability", "pricing"]


class PMSQuoteRequest(BaseModel):
    property_id: str
    check_in: date
    check_out: date
    guests: int = 1


class PMSReservationRequest(BaseModel):
    property_id: str
    check_in: date
    check_out: date
    guest_name: str
    guest_email: str
    guests: int = 1
    quote_id: Optional[str] = None


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/sync/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Rate Limiter ---

def _check_rate_limit(cur, pms_type: str, credential_id: str) -> bool:
    """Token bucket rate limiter. Returns True if request is allowed."""
    cur.execute("""
        SELECT tokens, max_tokens, refill_rate, last_refill, in_backoff, backoff_until
        FROM rate_limit_state
        WHERE pms_type = %s AND credential_id = %s
        FOR UPDATE
    """, (pms_type, credential_id))
    
    row = cur.fetchone()
    if not row:
        # Create default rate limit
        cur.execute("""
            INSERT INTO rate_limit_state (pms_type, credential_id, tokens, max_tokens, refill_rate)
            VALUES (%s, %s, 60, 60, 1.0)
            ON CONFLICT DO NOTHING
        """, (pms_type, credential_id))
        return True
    
    # Check backoff
    if row['in_backoff'] and row['backoff_until']:
        if datetime.now(timezone.utc) < row['backoff_until']:
            return False
        else:
            cur.execute(
                "UPDATE rate_limit_state SET in_backoff = FALSE WHERE pms_type = %s AND credential_id = %s",
                (pms_type, credential_id)
            )
    
    # Refill tokens
    now = datetime.now(timezone.utc)
    elapsed = (now - row['last_refill']).total_seconds()
    new_tokens = min(row['max_tokens'], float(row['tokens']) + elapsed * float(row['refill_rate']))
    
    if new_tokens < 1:
        return False
    
    cur.execute("""
        UPDATE rate_limit_state SET tokens = %s, last_refill = %s
        WHERE pms_type = %s AND credential_id = %s
    """, (new_tokens - 1, now, pms_type, credential_id))
    
    return True


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "sync"}


@app.post("/webhook/{pms_type}")
async def receive_webhook(pms_type: str, request: Request):
    """Receive webhook from PMS. Acknowledge within 500ms, process async."""
    body = await request.body()
    payload_hash = hashlib.sha256(body).hexdigest()
    
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        payload = {"raw": body.decode("utf-8", errors="replace")}
    
    with get_db_cursor() as (cur, conn):
        # Dedup check
        cur.execute(
            "SELECT id FROM webhook_event WHERE pms_type = %s AND payload_hash = %s",
            (pms_type, payload_hash)
        )
        if cur.fetchone():
            return {"received": True, "event_id": payload_hash, "deduplicated": True}
        
        cur.execute("""
            INSERT INTO webhook_event (pms_type, payload_hash, payload, status)
            VALUES (%s, %s, %s, 'RECEIVED')
            RETURNING id
        """, (pms_type, payload_hash, json.dumps(payload)))
        event = cur.fetchone()
        conn.commit()
    
    emit("sync.webhook_received", "webhook", str(event['id']), context={
        "pms_type": pms_type,
    })
    
    return {"received": True, "event_id": str(event['id'])}


@app.post("/sync/push")
async def push_sync(req: PushRequest):
    """Push changes to PMS (outbound sync for OPERATED properties)."""
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            INSERT INTO sync_job (job_type, property_id, status)
            VALUES ('PUSH', %s, 'QUEUED')
            RETURNING id
        """, (req.property_id,))
        job = cur.fetchone()
        
        for sub in req.subsystems:
            cur.execute("""
                INSERT INTO sync_job_subsystem (job_id, subsystem, status)
                VALUES (%s, %s, 'QUEUED')
            """, (job['id'], sub))
        
        conn.commit()
    
    emit("sync.push_requested", "property", req.property_id, context={
        "subsystems": req.subsystems,
    })
    
    return {
        "job_id": str(job['id']),
        "status": "QUEUED",
        "subsystem_jobs": [{"subsystem": s, "status": "QUEUED"} for s in req.subsystems],
    }


@app.post("/sync/pull")
async def pull_sync(req: PullRequest):
    """Pull changes from PMS (inbound sync for BRANDED properties)."""
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            INSERT INTO sync_job (job_type, property_id, status)
            VALUES ('PULL', %s, 'QUEUED')
            RETURNING id
        """, (req.property_id,))
        job = cur.fetchone()
        
        for sub in req.subsystems:
            cur.execute("""
                INSERT INTO sync_job_subsystem (job_id, subsystem, status)
                VALUES (%s, %s, 'QUEUED')
            """, (job['id'], sub))
        
        conn.commit()
    
    emit("sync.pull_requested", "property", req.property_id, context={
        "subsystems": req.subsystems, "full": req.full,
    })
    
    return {
        "job_id": str(job['id']),
        "status": "QUEUED",
        "subsystem_jobs": [{"subsystem": s, "status": "QUEUED"} for s in req.subsystems],
    }


@app.get("/sync/jobs/{job_id}")
async def get_job(job_id: str):
    """Get sync job status."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM sync_job WHERE id = %s", (job_id,))
        job = cur.fetchone()
        if not job:
            raise HTTPException(status_code=404, detail={"code": "NOT_FOUND", "message": "Job not found"})
        
        cur.execute("SELECT * FROM sync_job_subsystem WHERE job_id = %s", (job_id,))
        subsystems = cur.fetchall()
    
    return {
        "job_id": str(job['id']),
        "job_type": job['job_type'],
        "property_id": str(job['property_id']) if job['property_id'] else None,
        "status": job['status'],
        "subsystem_jobs": [
            {
                "subsystem": s['subsystem'],
                "status": s['status'],
                "records_synced": s['records_synced'],
                "completed_at": s['completed_at'].isoformat() if s['completed_at'] else None,
            }
            for s in subsystems
        ],
        "created_at": job['created_at'].isoformat(),
        "completed_at": job['completed_at'].isoformat() if job['completed_at'] else None,
    }


@app.post("/sync/reconcile")
async def reconcile(req: ReconcileRequest):
    """Start full reconciliation job."""
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            INSERT INTO sync_job (job_type, status)
            VALUES ('RECONCILE', 'QUEUED')
            RETURNING id
        """)
        job = cur.fetchone()
        conn.commit()
    
    property_count = len(req.property_ids) if req.property_ids else 0
    
    return {
        "job_id": str(job['id']),
        "status": "QUEUED",
        "property_count": property_count,
    }


@app.get("/sync/status")
async def sync_status():
    """Overall sync health."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT COUNT(*) as cnt FROM sync_job WHERE status = 'FAILED' AND created_at > NOW() - INTERVAL '24 hours'")
        failed = cur.fetchone()['cnt']
        
        cur.execute("SELECT COUNT(*) as cnt FROM webhook_event WHERE created_at > NOW() - INTERVAL '24 hours'")
        webhooks = cur.fetchone()['cnt']
    
    adapters = {pms: {"status": "active", "last_sync": None} for pms in ADAPTER_REGISTRY.keys()}
    
    return {
        "status": "healthy" if failed == 0 else "degraded",
        "adapters": adapters,
        "webhooks_received_24h": webhooks,
        "failed_jobs_24h": failed,
    }


@app.get("/sync/rate-limits")
async def rate_limits():
    """Current rate limit state by partner."""
    with get_db_cursor() as (cur, conn):
        cur.execute("SELECT * FROM rate_limit_state")
        limits = cur.fetchall()
    
    return {
        "limits": [
            {
                "partner": f"{l['pms_type']}/{l['credential_id']}",
                "max_per_minute": l['max_tokens'],
                "current_tokens": float(l['tokens']),
                "in_backoff": l['in_backoff'],
                "backoff_until": l['backoff_until'].isoformat() if l['backoff_until'] else None,
            }
            for l in limits
        ]
    }


@app.post("/sync/pms/quote")
async def pms_quote(req: PMSQuoteRequest):
    """Get a quote from the PMS. Called by Booking Service for verification."""
    # In production: look up PMS connection, call PMS API via adapter
    # For now: return a simulated quote
    nights = (req.check_out - req.check_in).days
    rate_per_night = 45000  # Default $450/night
    
    quote_id = f"quote_{uuid.uuid4().hex[:12]}"
    total_cents = rate_per_night * nights
    
    return {
        "quote_id": quote_id,
        "pms_type": "simulated",
        "property_id": req.property_id,
        "check_in": req.check_in.isoformat(),
        "check_out": req.check_out.isoformat(),
        "nights": nights,
        "rates": [
            {"date": (req.check_in.__class__(req.check_in.year, req.check_in.month, req.check_in.day)).isoformat(),
             "rate_cents": rate_per_night}
        ],
        "fees": [],
        "total_cents": total_cents,
        "currency": "USD",
        "expires_at": datetime.now(timezone.utc).isoformat(),
    }


@app.post("/sync/pms/reservation")
async def create_pms_reservation(req: PMSReservationRequest):
    """Create a reservation in the PMS. Called by Booking Service."""
    external_id = f"EXT-{uuid.uuid4().hex[:8].upper()}"
    confirmation_code = f"GY-{uuid.uuid4().hex[:5].upper()}"
    
    with get_db_cursor() as (cur, conn):
        cur.execute("""
            INSERT INTO pms_reservation_mapping
                (property_id, pms_type, external_id, confirmation_code, status)
            VALUES (%s, 'simulated', %s, %s, 'CONFIRMED')
            RETURNING id
        """, (req.property_id, external_id, confirmation_code))
        mapping = cur.fetchone()
        conn.commit()
    
    emit("sync.reservation_created", "property", req.property_id, context={
        "external_id": external_id,
    })
    
    return {
        "external_id": external_id,
        "confirmation_code": confirmation_code,
        "status": "CONFIRMED",
    }


@app.delete("/sync/pms/reservation/{external_id}")
async def cancel_pms_reservation(external_id: str):
    """Cancel a reservation in the PMS. Called by Booking Service."""
    with get_db_cursor() as (cur, conn):
        cur.execute(
            "UPDATE pms_reservation_mapping SET status = 'CANCELLED' WHERE external_id = %s",
            (external_id,)
        )
        conn.commit()
    
    return {"external_id": external_id, "cancelled": True}
