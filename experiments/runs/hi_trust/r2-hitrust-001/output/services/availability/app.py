"""
Availability Service — Fast availability lookups using bitmask cache.

Base URL: /api/v1/availability
Source of Truth: This service is a cache — PMS is always authoritative.

Endpoints:
  GET  /check          - Check if unit is available for date range
  GET  /next-available  - Find next available window of N nights
  GET  /search          - Find available units in region(s)
  POST /acquire         - Block a date range
  POST /release         - Release a blocked date range
"""

from datetime import date, datetime
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from services.shared.db import Database
from services.shared.cache import MemoryCache, get_cache
from services.shared.events import emit_event
from services.availability.store import AvailabilityStore


app = FastAPI(title="Availability Service", version="1.0.0")

# Lazily initialized store
_store: Optional[AvailabilityStore] = None


def get_store() -> AvailabilityStore:
    global _store
    if _store is None:
        _store = AvailabilityStore(Database(), get_cache())
    return _store


def set_store(store: AvailabilityStore):
    global _store
    _store = store


def parse_date(d: str) -> date:
    return datetime.strptime(d, "%Y-%m-%d").date()


# --- Request/Response Models ---

class AcquireRequest(BaseModel):
    unit_id: str
    start_date: str
    end_date: str


class ReleaseRequest(BaseModel):
    unit_id: str
    start_date: str
    end_date: str


# --- Endpoints ---

@app.get("/api/v1/availability/check")
def check_availability(
    unit_id: str = Query(...),
    start_date: str = Query(...),
    end_date: str = Query(...)
):
    """Check if a unit is available for a date range."""
    try:
        sd = parse_date(start_date)
        ed = parse_date(end_date)
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "Invalid date format"}})

    if ed < sd:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "end_date < start_date"}})

    store = get_store()
    available = store.check(unit_id, sd, ed)
    return {"available": available}


@app.get("/api/v1/availability/next-available")
def next_available(
    unit_id: str = Query(...),
    nights: int = Query(...),
    after: Optional[str] = Query(None),
    limit_months: int = Query(12)
):
    """Find the next available window of N contiguous nights."""
    after_date = parse_date(after) if after else date.today()
    store = get_store()
    found, start_date = store.next_available(unit_id, nights, after_date, limit_months)
    return {"found": found, "start_date": start_date}


@app.get("/api/v1/availability/search")
def search_availability(
    start_date: str = Query(...),
    end_date: str = Query(...),
    region: Optional[str] = Query(None),
    regions: Optional[str] = Query(None)  # comma-separated
):
    """Find available units in one or more regions."""
    try:
        sd = parse_date(start_date)
        ed = parse_date(end_date)
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "Invalid date format"}})

    store = get_store()
    all_unit_ids = []

    if region:
        all_unit_ids = store.search(region, sd, ed)
    elif regions:
        region_list = [r.strip() for r in regions.split(",")]
        seen = set()
        for r in region_list:
            for uid in store.search(r, sd, ed):
                if uid not in seen:
                    all_unit_ids.append(uid)
                    seen.add(uid)

    return {"unit_ids": all_unit_ids}


@app.post("/api/v1/availability/acquire")
def acquire_dates(request: AcquireRequest):
    """Block a date range. Fails if any date already blocked."""
    try:
        sd = parse_date(request.start_date)
        ed = parse_date(request.end_date)
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "Invalid date format"}})

    if ed < sd:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "end_date < start_date"}})

    store = get_store()
    success, status = store.acquire(request.unit_id, sd, ed)

    if not success:
        raise HTTPException(409, detail={"error": {"code": "CONFLICT", "message": "Dates already blocked"}})

    # Emit event
    emit_event(
        "availability.changed",
        "unit",
        request.unit_id,
        context={
            "start_date": request.start_date,
            "end_date": request.end_date,
            "blocked": True
        }
    )

    return {"status": "acquired"}


@app.post("/api/v1/availability/release")
def release_dates(request: ReleaseRequest):
    """Unblock a previously acquired date range."""
    try:
        sd = parse_date(request.start_date)
        ed = parse_date(request.end_date)
    except ValueError:
        raise HTTPException(400, detail={"error": {"code": "INVALID_DATES", "message": "Invalid date format"}})

    store = get_store()
    status = store.release(request.unit_id, sd, ed)

    emit_event(
        "availability.changed",
        "unit",
        request.unit_id,
        context={
            "start_date": request.start_date,
            "end_date": request.end_date,
            "blocked": False
        }
    )

    return {"status": "released"}


@app.get("/health")
def health():
    return {"status": "ok", "service": "availability"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
