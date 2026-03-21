"""
Pricing Service API
Fast nightly rate cache with hash-sharded Redis.
Non-authoritative — prices written by Sync Service and Pricing Intelligence.
Does NOT compute quotes/discounts/fees/taxes. Just stores nightly rates.

Endpoints:
  GET  /rate     - Get nightly rates for unit + date range
  GET  /calendar - Get rates for a unit across a month
  POST /bulk     - Get aggregate rates for multiple units
  POST /set      - Set nightly rates (called by Sync/Pricing Intelligence)
"""
from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel
from datetime import date, timedelta
from typing import Optional, List
import logging

from shared.database import get_db_cursor, get_redis, init_schema
from shared.events import emit

logger = logging.getLogger("pricing")

app = FastAPI(title="Pricing Service", version="1.0.0")

NUM_SHARDS = 3  # Hash-sharded Redis by unit_id


# --- Models ---

class RateItem(BaseModel):
    date: date
    rate_cents: int


class SetRatesRequest(BaseModel):
    unit_id: str
    currency: str = "USD"
    rates: List[RateItem]


class BulkRequest(BaseModel):
    unit_ids: List[str]
    start_date: date
    end_date: date


# --- Shard Helper ---

def _shard_key(unit_id: str) -> int:
    """Determine shard for a unit_id."""
    return hash(unit_id) % NUM_SHARDS


# --- Redis Cache ---

def _redis_get_rate(r, unit_id: str, d: date) -> Optional[int]:
    if r is None:
        return None
    val = r.get(f"price:{unit_id}:{d.isoformat()}")
    return int(val) if val is not None else None


def _redis_set_rate(r, unit_id: str, d: date, rate_cents: int):
    if r is None:
        return
    try:
        r.set(f"price:{unit_id}:{d.isoformat()}", rate_cents, ex=3600)
    except Exception:
        pass


def _redis_invalidate_rate(r, unit_id: str, d: date):
    if r is None:
        return
    try:
        r.delete(f"price:{unit_id}:{d.isoformat()}")
        # Also invalidate calendar cache
        r.delete(f"price:cal:{unit_id}:{d.strftime('%Y-%m')}")
    except Exception:
        pass


# --- DB Operations ---

def _db_get_rates(cur, unit_id: str, start: date, end: date) -> list:
    """Get rates from PostgreSQL for a date range."""
    cur.execute(
        "SELECT date, rate_cents, currency FROM pricing WHERE unit_id = %s AND date >= %s AND date <= %s ORDER BY date",
        (unit_id, start, end)
    )
    return cur.fetchall()


# --- Schema Init ---

def _init_schema():
    try:
        with open("services/pricing/schema.sql") as f:
            init_schema(f.read())
    except Exception as e:
        logger.warning(f"Schema init skipped: {e}")


# --- Endpoints ---

@app.on_event("startup")
async def startup():
    _init_schema()


@app.get("/health")
async def health():
    return {"status": "ok", "service": "pricing"}


@app.get("/rate")
async def get_rate(
    unit_id: str = Query(..., description="Unit identifier"),
    start_date: date = Query(..., description="First night (inclusive)"),
    end_date: Optional[date] = Query(None, description="Last night (inclusive)")
):
    """Get nightly rates for a unit and date range."""
    if end_date is None:
        end_date = start_date
    
    if end_date < start_date:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_INPUT", "message": "end_date must be >= start_date"
        })
    
    r = get_redis()
    rates = []
    missing_dates = []
    currency = "USD"
    
    # Try Redis first, fallback to DB
    with get_db_cursor() as (cur, conn):
        current = start_date
        while current <= end_date:
            cached = _redis_get_rate(r, unit_id, current)
            if cached is not None:
                rates.append({"date": current.isoformat(), "rate_cents": cached})
            else:
                # DB lookup
                cur.execute(
                    "SELECT rate_cents, currency FROM pricing WHERE unit_id = %s AND date = %s",
                    (unit_id, current)
                )
                row = cur.fetchone()
                if row:
                    rates.append({"date": current.isoformat(), "rate_cents": row['rate_cents']})
                    currency = row['currency']
                    _redis_set_rate(r, unit_id, current, row['rate_cents'])
                else:
                    missing_dates.append(current.isoformat())
            current += timedelta(days=1)
    
    return {
        "unit_id": unit_id,
        "currency": currency,
        "rates": rates,
        "missing_dates": missing_dates,
    }


@app.get("/calendar")
async def get_calendar(
    unit_id: str = Query(..., description="Unit identifier"),
    month: str = Query(..., description="Month (YYYY-MM)")
):
    """Get nightly rates for a unit across a month."""
    try:
        year = int(month[:4])
        mon = int(month[5:7])
    except (ValueError, IndexError):
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_INPUT", "message": "month must be YYYY-MM format"
        })
    
    import calendar as cal
    days_in_month = cal.monthrange(year, mon)[1]
    start = date(year, mon, 1)
    end = date(year, mon, days_in_month)
    
    with get_db_cursor() as (cur, conn):
        rows = _db_get_rates(cur, unit_id, start, end)
    
    rate_map = {row['date'].day: row['rate_cents'] for row in rows}
    currency = rows[0]['currency'] if rows else "USD"
    
    rates_out = []
    missing_days = []
    for d in range(1, days_in_month + 1):
        if d in rate_map:
            rates_out.append({"day": d, "rate_cents": rate_map[d]})
        else:
            missing_days.append(d)
    
    return {
        "unit_id": unit_id,
        "month": month,
        "currency": currency,
        "rates": rates_out,
        "missing_days": missing_days,
    }


@app.post("/bulk")
async def bulk_rates(req: BulkRequest):
    """Get aggregate rates for multiple units. Used by search results."""
    if len(req.unit_ids) > 500:
        raise HTTPException(status_code=400, detail={
            "code": "INVALID_INPUT", "message": "Maximum 500 units per bulk request"
        })
    
    results = []
    
    with get_db_cursor() as (cur, conn):
        # Build the complete date range
        total_dates = []
        current = req.start_date
        while current <= req.end_date:
            total_dates.append(current)
            current += timedelta(days=1)
        
        for uid in req.unit_ids:
            rows = _db_get_rates(cur, uid, req.start_date, req.end_date)
            rate_dates = {row['date'] for row in rows}
            missing = [d.isoformat() for d in total_dates if d not in rate_dates]
            
            if missing:
                # Aggregates are null if any date is missing
                results.append({
                    "unit_id": uid,
                    "currency": rows[0]['currency'] if rows else "USD",
                    "sum_cents": None,
                    "avg_cents": None,
                    "min_cents": None,
                    "max_cents": None,
                    "missing_dates": missing,
                })
            else:
                rates_list = [row['rate_cents'] for row in rows]
                results.append({
                    "unit_id": uid,
                    "currency": rows[0]['currency'] if rows else "USD",
                    "sum_cents": sum(rates_list),
                    "avg_cents": round(sum(rates_list) / len(rates_list)),
                    "min_cents": min(rates_list),
                    "max_cents": max(rates_list),
                    "missing_dates": [],
                })
    
    return {"rates": results}


@app.post("/set")
async def set_rates(req: SetRatesRequest):
    """Set nightly rates for a unit. Called by Sync Service and Pricing Intelligence."""
    r = get_redis()
    
    with get_db_cursor() as (cur, conn):
        for rate_item in req.rates:
            cur.execute("""
                INSERT INTO pricing (unit_id, date, rate_cents, currency)
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (unit_id, date) DO UPDATE
                SET rate_cents = EXCLUDED.rate_cents, 
                    currency = EXCLUDED.currency,
                    updated_at = NOW()
            """, (req.unit_id, rate_item.date, rate_item.rate_cents, req.currency))
            
            _redis_invalidate_rate(r, req.unit_id, rate_item.date)
        
        conn.commit()
    
    emit("pricing.rates_updated", "unit", req.unit_id, context={
        "dates": [r.date.isoformat() for r in req.rates],
    })
    
    return {"status": "updated", "count": len(req.rates)}
