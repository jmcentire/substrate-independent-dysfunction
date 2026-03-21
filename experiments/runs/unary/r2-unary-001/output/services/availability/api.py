"""
Availability Service API
Fast availability lookups using bitmask-per-month storage.
Non-authoritative cache — PMS is always source of truth.

Endpoints:
  GET  /check          - Is unit X available for dates Y-Z?
  GET  /next-available  - Find next N-day window
  GET  /search          - Find available units in region
  POST /acquire         - Block dates (fails if conflict)
  POST /release         - Unblock dates
"""
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from datetime import date, timedelta
from typing import Optional, List
import calendar
import logging

from .bitmask import (
    date_range_to_masks, check_conflict, apply_block, remove_block,
    compute_contiguous_metadata, get_days_in_month, is_available
)
from shared.database import get_db_cursor, get_redis, init_schema
from shared.events import emit

logger = logging.getLogger("availability")

app = FastAPI(title="Availability Service", version="1.0.0")

SCHEMA_SQL = open("services/availability/schema.sql").read() if __name__ != "__main__" else ""


def _init_schema():
    try:
        with open("services/availability/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Models ---

class AcquireRequest(BaseModel):
    unit_id: str
    start_date: date
    end_date: date


class ReleaseRequest(BaseModel):
    unit_id: str
    start_date: date
    end_date: date


# --- Redis Cache ---

def _redis_get_mask(r, unit_id: str, year_month: str) -> Optional[int]:
    """Get bitmask from Redis cache."""
    if r is None:
        return None
    val = r.get(f"avail:{unit_id}:{year_month}")
    return int(val) if val is not None else None


def _redis_set_mask(r, unit_id: str, year_month: str, mask: int):
    """Set bitmask in Redis cache."""
    if r is None:
        return
    try:
        r.set(f"avail:{unit_id}:{year_month}", mask, ex=3600)  # 1hr TTL
    except Exception:
        pass


def _redis_invalidate(r, unit_id: str, year_month: str):
    """Invalidate Redis cache for a unit-month."""
    if r is None:
        return
    try:
        r.delete(f"avail:{unit_id}:{year_month}")
    except Exception:
        pass


# --- DB Operations ---

def _db_get_mask(cur, unit_id: str, year_month: str) -> int:
    """Get bitmask from PostgreSQL. Returns 0 (all available) if not found."""
    cur.execute(
        "SELECT days FROM availability WHERE unit_id = %s AND year_month = %s",
        (unit_id, year_month)
    )
    row = cur.fetchone()
    return row['days'] if row else 0


def _get_mask(cur, unit_id: str, year_month: str) -> int:
    """Get bitmask from cache or DB."""
    r = get_redis()
    cached = _redis_get_mask(r, unit_id, year_month)
    if cached is not None:
        return cached
    
    mask = _db_get_mask(cur, unit_id, year_month)
    _redis_set_mask(r, unit_id, year_month, mask)
    return mask


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "availability"}


@app.get("/check")
async def check_availability(
    unit_id: str = Query(..., description="Unit identifier"),
    start_date: date = Query(..., description="First night (inclusive)"),
    end_date: date = Query(..., description="Last night (inclusive)")
):
    """Check if a unit is available for a date range. O(1) bitmask check."""
    if end_date < start_date:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATES", "message": "end_date must be >= start_date"})
    
    masks = date_range_to_masks(start_date, end_date)
    
    with get_db_cursor() as (cur, conn):
        for ym, request_mask in masks.items():
            existing_mask = _get_mask(cur, unit_id, ym)
            if check_conflict(existing_mask, request_mask):
                return {"available": False}
    
    return {"available": True}


@app.get("/next-available")
async def next_available(
    unit_id: str = Query(..., description="Unit identifier"),
    nights: int = Query(..., description="Contiguous nights needed"),
    after: Optional[date] = Query(None, description="Start search from this date"),
    limit_months: int = Query(12, description="Months to search")
):
    """Find the next available window of N contiguous nights."""
    search_start = after or date.today()
    
    with get_db_cursor() as (cur, conn):
        current = search_start
        consecutive_free = 0
        window_start = None
        
        end_search = date(
            search_start.year + (search_start.month + limit_months - 1) // 12,
            (search_start.month + limit_months - 1) % 12 + 1,
            1
        )
        
        while current < end_search:
            ym = current.strftime('%Y-%m')
            mask = _get_mask(cur, unit_id, ym)
            
            days_in_month = get_days_in_month(ym)
            start_day = current.day if current.strftime('%Y-%m') == ym else 1
            
            for d in range(start_day, days_in_month + 1):
                if is_available(mask, d):
                    if consecutive_free == 0:
                        window_start = date(int(ym[:4]), int(ym[5:7]), d)
                    consecutive_free += 1
                    if consecutive_free >= nights:
                        return {"found": True, "start_date": window_start.isoformat()}
                else:
                    consecutive_free = 0
                    window_start = None
            
            # Move to next month
            year, month = int(ym[:4]), int(ym[5:7])
            if month == 12:
                current = date(year + 1, 1, 1)
            else:
                current = date(year, month + 1, 1)
    
    return {"found": False, "start_date": None}


@app.get("/search")
async def search_available(
    region: Optional[str] = Query(None, description="Region code"),
    regions: Optional[str] = Query(None, description="Comma-separated region codes"),
    start_date: date = Query(..., description="First night (inclusive)"),
    end_date: date = Query(..., description="Last night (inclusive)")
):
    """Find available units in one or more regions. Returns unit IDs only."""
    if not region and not regions:
        raise HTTPException(status_code=400, detail={"code": "INVALID_INPUT", "message": "Provide region or regions"})
    
    region_list = [region] if region else regions.split(",")
    masks = date_range_to_masks(start_date, end_date)
    
    with get_db_cursor() as (cur, conn):
        # Get unit IDs in regions
        placeholders = ','.join(['%s'] * len(region_list))
        cur.execute(
            f"SELECT unit_id FROM unit_region WHERE region IN ({placeholders})",
            region_list
        )
        unit_ids = [str(row['unit_id']) for row in cur.fetchall()]
        
        available_units = []
        for uid in unit_ids:
            is_avail = True
            for ym, request_mask in masks.items():
                existing_mask = _get_mask(cur, uid, ym)
                if check_conflict(existing_mask, request_mask):
                    is_avail = False
                    break
            if is_avail:
                available_units.append(uid)
    
    return {"unit_ids": available_units}


@app.post("/acquire")
async def acquire_dates(req: AcquireRequest):
    """Block a date range. Fails if any date already blocked (409 CONFLICT)."""
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATES", "message": "end_date must be >= start_date"})
    
    masks = date_range_to_masks(req.start_date, req.end_date)
    r = get_redis()
    
    with get_db_cursor() as (cur, conn):
        # Check all months for conflicts, then apply (within transaction)
        for ym, request_mask in masks.items():
            cur.execute(
                "SELECT days FROM availability WHERE unit_id = %s AND year_month = %s FOR UPDATE",
                (req.unit_id, ym)
            )
            row = cur.fetchone()
            existing_mask = row['days'] if row else 0
            
            if check_conflict(existing_mask, request_mask):
                raise HTTPException(status_code=409, detail={"code": "CONFLICT", "message": "Dates already blocked"})
        
        # Apply blocks
        for ym, request_mask in masks.items():
            cur.execute(
                "SELECT days FROM availability WHERE unit_id = %s AND year_month = %s",
                (req.unit_id, ym)
            )
            row = cur.fetchone()
            existing_mask = row['days'] if row else 0
            new_mask = apply_block(existing_mask, request_mask)
            
            days_in_month = get_days_in_month(ym)
            sf, ef, mf = compute_contiguous_metadata(new_mask, days_in_month)
            
            cur.execute("""
                INSERT INTO availability (unit_id, year_month, days, start_free, end_free, max_free)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (unit_id, year_month) DO UPDATE
                SET days = %s, start_free = %s, end_free = %s, max_free = %s
            """, (req.unit_id, ym, new_mask, sf, ef, mf, new_mask, sf, ef, mf))
            
            _redis_invalidate(r, req.unit_id, ym)
        
        conn.commit()
    
    # Emit event
    emit("availability.changed", "unit", req.unit_id, context={
        "start_date": req.start_date.isoformat(),
        "end_date": req.end_date.isoformat(),
        "blocked": True,
    })
    
    return {"status": "acquired"}


@app.post("/release")
async def release_dates(req: ReleaseRequest):
    """Unblock a previously acquired date range."""
    if req.end_date < req.start_date:
        raise HTTPException(status_code=400, detail={"code": "INVALID_DATES", "message": "end_date must be >= start_date"})
    
    masks = date_range_to_masks(req.start_date, req.end_date)
    r = get_redis()
    
    with get_db_cursor() as (cur, conn):
        for ym, request_mask in masks.items():
            cur.execute(
                "SELECT days FROM availability WHERE unit_id = %s AND year_month = %s FOR UPDATE",
                (req.unit_id, ym)
            )
            row = cur.fetchone()
            if not row:
                continue
            
            existing_mask = row['days']
            new_mask = remove_block(existing_mask, request_mask)
            
            days_in_month = get_days_in_month(ym)
            sf, ef, mf = compute_contiguous_metadata(new_mask, days_in_month)
            
            cur.execute("""
                UPDATE availability
                SET days = %s, start_free = %s, end_free = %s, max_free = %s
                WHERE unit_id = %s AND year_month = %s
            """, (new_mask, sf, ef, mf, req.unit_id, ym))
            
            _redis_invalidate(r, req.unit_id, ym)
        
        conn.commit()
    
    emit("availability.changed", "unit", req.unit_id, context={
        "start_date": req.start_date.isoformat(),
        "end_date": req.end_date.isoformat(),
        "blocked": False,
    })
    
    return {"status": "released"}
