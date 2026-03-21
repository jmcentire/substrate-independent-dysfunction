"""
Pricing Service — Fast nightly rate lookups.

Base URL: /api/v1/pricing
Source of Truth: Nightly rate cache (not authoritative).

Endpoints:
  GET  /rate     - Get nightly rates for a unit and date range
  GET  /calendar - Get rates for a unit across a month
  POST /bulk     - Get aggregate rates for multiple units
  POST /set      - Set nightly rates (called by Sync / Pricing Intelligence)
"""

from datetime import date, datetime
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.shared.events import emit_event
from services.pricing.store import PricingStore


app = FastAPI(title="Pricing Service", version="1.0.0")

_store: Optional[PricingStore] = None


def get_store() -> PricingStore:
    global _store
    if _store is None:
        _store = PricingStore(Database(), get_cache())
    return _store


def set_store(store: PricingStore):
    global _store
    _store = store


def parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


# --- Request Models ---

class RateEntry(BaseModel):
    date: str
    rate_cents: int


class SetRatesRequest(BaseModel):
    unit_id: str
    currency: str = "USD"
    rates: List[RateEntry]


class BulkRequest(BaseModel):
    unit_ids: List[str]
    start_date: str
    end_date: str


# --- Endpoints ---

@app.get("/api/v1/pricing/rate")
def get_rate(
    unit_id: str = Query(...),
    start_date: str = Query(...),
    end_date: Optional[str] = Query(None)
):
    """Get nightly rates for a unit and date range."""
    try:
        sd = parse_date(start_date)
        ed = parse_date(end_date) if end_date else sd
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_INPUT", "message": "Invalid date format"}})

    if ed < sd:
        raise HTTPException(400, detail={"error": {"code": "INVALID_INPUT", "message": "end_date before start_date"}})

    store = get_store()
    currency, rates, missing = store.get_rate(unit_id, sd, ed)

    return {
        "unit_id": unit_id,
        "currency": currency,
        "rates": rates,
        "missing_dates": missing
    }


@app.get("/api/v1/pricing/calendar")
def get_calendar(
    unit_id: str = Query(...),
    month: str = Query(...)
):
    """Get nightly rates for a unit across a month."""
    store = get_store()
    currency, rates, missing_days = store.get_calendar(unit_id, month)

    return {
        "unit_id": unit_id,
        "month": month,
        "currency": currency,
        "rates": rates,
        "missing_days": missing_days
    }


@app.post("/api/v1/pricing/bulk")
def bulk_rates(request: BulkRequest):
    """Get aggregate rates for multiple units."""
    if len(request.unit_ids) > 500:
        raise HTTPException(400, detail={"error": {"code": "INVALID_INPUT", "message": "Max 500 units"}})

    try:
        sd = parse_date(request.start_date)
        ed = parse_date(request.end_date)
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_INPUT", "message": "Invalid date format"}})

    store = get_store()
    results = store.bulk_rates(request.unit_ids, sd, ed)
    return {"rates": results}


@app.post("/api/v1/pricing/set")
def set_rates(request: SetRatesRequest):
    """Set nightly rates for a unit."""
    store = get_store()
    count = store.set_rates(
        request.unit_id,
        request.currency,
        [r.model_dump() for r in request.rates]
    )

    # Emit event
    emit_event(
        "pricing.rates_updated",
        "unit",
        request.unit_id,
        context={"dates": [r.date for r in request.rates]}
    )

    return {"status": "updated", "count": count}


@app.get("/health")
def health():
    return {"status": "ok", "service": "pricing"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
