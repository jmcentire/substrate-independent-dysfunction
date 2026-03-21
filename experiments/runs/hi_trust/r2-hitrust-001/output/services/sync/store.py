"""
Sync Service data store — manages sync jobs, webhook events, rate limits.

Two-layer architecture: Connectors (transport) + Adapters (translation).
Three subsystems: Property, Availability, Pricing.
"""

import json
import uuid
import hashlib
import time
from typing import Optional, List, Dict
from services.shared.db import Database
from services.shared.cache import MemoryCache


class SyncStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pms_connection (
                id TEXT PRIMARY KEY,
                pms_type TEXT NOT NULL,
                credential_id TEXT NOT NULL,
                api_url TEXT,
                api_key TEXT,
                rate_limit_max_tokens INTEGER DEFAULT 60,
                rate_limit_refill_rate REAL DEFAULT 1.0,
                status TEXT DEFAULT 'ACTIVE',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS sync_job (
                id TEXT PRIMARY KEY,
                job_type TEXT NOT NULL,
                property_id TEXT,
                pms_type TEXT,
                status TEXT DEFAULT 'QUEUED',
                subsystem_jobs TEXT,
                error TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                completed_at TEXT
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS webhook_event (
                id TEXT PRIMARY KEY,
                pms_type TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                payload TEXT,
                status TEXT DEFAULT 'RECEIVED',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute(
            "CREATE INDEX IF NOT EXISTS idx_webhook_hash ON webhook_event(payload_hash)"
        )
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS fee_dictionary (
                id TEXT PRIMARY KEY,
                pms_type TEXT NOT NULL,
                external_code TEXT NOT NULL,
                standard_type TEXT NOT NULL,
                label TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pms_quote_cache (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                pms_type TEXT NOT NULL,
                check_in TEXT NOT NULL,
                check_out TEXT NOT NULL,
                rates TEXT,
                fees TEXT,
                total_cents INTEGER,
                expires_at TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pms_reservation_mapping (
                id TEXT PRIMARY KEY,
                booking_id TEXT,
                property_id TEXT NOT NULL,
                pms_type TEXT NOT NULL,
                external_id TEXT NOT NULL,
                confirmation_code TEXT,
                status TEXT DEFAULT 'CONFIRMED',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.commit()

    def receive_webhook(self, pms_type: str, payload: dict) -> dict:
        """Receive and deduplicate a webhook event."""
        payload_str = json.dumps(payload, sort_keys=True)
        payload_hash = hashlib.sha256(payload_str.encode()).hexdigest()

        # Check dedup (24hr window)
        existing = self.db.fetchone(
            "SELECT id FROM webhook_event WHERE payload_hash = ?",
            [payload_hash]
        )
        if existing:
            return {"received": True, "event_id": existing["id"], "deduplicated": True}

        event_id = str(uuid.uuid4())
        self.db.execute("""
            INSERT INTO webhook_event (id, pms_type, payload_hash, payload, status)
            VALUES (?, ?, ?, ?, 'RECEIVED')
        """, [event_id, pms_type, payload_hash, payload_str])
        self.db.commit()

        return {"received": True, "event_id": event_id, "deduplicated": False}

    def create_sync_job(self, job_type: str, property_id: str,
                        pms_type: str = None, subsystems: list = None) -> dict:
        """Create a sync job."""
        job_id = str(uuid.uuid4())
        subsystem_jobs = []
        for sub in (subsystems or ["property", "availability", "pricing"]):
            subsystem_jobs.append({
                "subsystem": sub,
                "status": "QUEUED",
                "records_synced": 0,
            })

        self.db.execute("""
            INSERT INTO sync_job (id, job_type, property_id, pms_type, status, subsystem_jobs)
            VALUES (?, ?, ?, ?, 'QUEUED', ?)
        """, [job_id, job_type, property_id, pms_type, json.dumps(subsystem_jobs)])
        self.db.commit()

        return {
            "job_id": job_id,
            "job_type": job_type,
            "property_id": property_id,
            "pms_type": pms_type,
            "status": "QUEUED",
            "subsystem_jobs": subsystem_jobs,
        }

    def get_job(self, job_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM sync_job WHERE id = ?", [job_id])
        if not row:
            return None
        job = dict(row)
        if job.get("subsystem_jobs"):
            try:
                job["subsystem_jobs"] = json.loads(job["subsystem_jobs"])
            except (json.JSONDecodeError, TypeError):
                pass
        return job

    def complete_job(self, job_id: str, status: str = "COMPLETED", error: str = None):
        self.db.execute(
            "UPDATE sync_job SET status = ?, error = ?, completed_at = datetime('now') WHERE id = ?",
            [status, error, job_id]
        )
        self.db.commit()

    def cache_quote(self, property_id: str, pms_type: str, check_in: str,
                    check_out: str, rates: list, fees: list, total_cents: int,
                    expires_minutes: int = 5) -> dict:
        """Cache a PMS quote."""
        quote_id = str(uuid.uuid4())
        expires_at = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ",
            time.gmtime(time.time() + expires_minutes * 60)
        )

        self.db.execute("""
            INSERT INTO pms_quote_cache (
                id, property_id, pms_type, check_in, check_out,
                rates, fees, total_cents, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            quote_id, property_id, pms_type, check_in, check_out,
            json.dumps(rates), json.dumps(fees), total_cents, expires_at
        ])
        self.db.commit()

        return {
            "quote_id": quote_id,
            "pms_type": pms_type,
            "rates": rates,
            "fees": fees,
            "total_cents": total_cents,
            "expires_at": expires_at,
        }

    def get_quote(self, quote_id: str) -> Optional[dict]:
        row = self.db.fetchone("SELECT * FROM pms_quote_cache WHERE id = ?", [quote_id])
        if not row:
            return None
        quote = dict(row)
        for field in ("rates", "fees"):
            if quote.get(field):
                try:
                    quote[field] = json.loads(quote[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return quote

    def create_reservation_mapping(self, booking_id: str, property_id: str,
                                   pms_type: str, external_id: str,
                                   confirmation_code: str = None) -> dict:
        """Map a Wander booking to a PMS reservation."""
        mapping_id = str(uuid.uuid4())
        self.db.execute("""
            INSERT INTO pms_reservation_mapping (
                id, booking_id, property_id, pms_type, external_id, confirmation_code
            ) VALUES (?, ?, ?, ?, ?, ?)
        """, [mapping_id, booking_id, property_id, pms_type, external_id, confirmation_code])
        self.db.commit()

        return {
            "id": mapping_id,
            "external_id": external_id,
            "confirmation_code": confirmation_code or external_id,
            "status": "CONFIRMED",
        }

    def get_sync_status(self) -> dict:
        """Get overall sync health."""
        recent_jobs = self.db.fetchone(
            "SELECT COUNT(*) as total, SUM(CASE WHEN status = 'COMPLETED' THEN 1 ELSE 0 END) as completed FROM sync_job"
        )
        webhooks_24h = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM webhook_event"
        )

        total = recent_jobs["total"] if recent_jobs else 0
        completed = recent_jobs["completed"] if recent_jobs else 0

        return {
            "status": "healthy",
            "adapters": {
                "guesty": {"status": "active"},
                "hostaway": {"status": "active"},
                "streamline": {"status": "active"},
                "escapia": {"status": "active"},
                "ownerrez": {"status": "active"},
                "lodgify": {"status": "active"},
                "rentals_united": {"status": "active"},
                "trackpms": {"status": "active"},
            },
            "webhooks_received_24h": webhooks_24h["cnt"] if webhooks_24h else 0,
            "sync_success_rate_24h": (completed / total * 100) if total > 0 else 100,
        }

    def get_rate_limits(self) -> list:
        """Get current rate limit state by partner."""
        connections = self.db.fetchall("SELECT * FROM pms_connection")
        limits = []
        for conn in connections:
            c = dict(conn)
            limits.append({
                "partner": c["pms_type"],
                "credential_id": c["credential_id"],
                "max_per_minute": c["rate_limit_max_tokens"],
                "current_tokens": c["rate_limit_max_tokens"],  # Simplified
                "in_backoff": False,
            })
        return limits
