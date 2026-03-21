"""
Pricing data store — manages nightly rate data in DB + cache.

Simple rate cache: stores pre-computed nightly rates.
Does NOT compute quotes, fees, or taxes.
"""

from datetime import date, timedelta
from services.shared.db import Database
from services.shared.cache import MemoryCache


class PricingStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pricing (
                unit_id     TEXT NOT NULL,
                date        TEXT NOT NULL,
                rate_cents  INTEGER NOT NULL,
                currency    TEXT NOT NULL DEFAULT 'USD',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (unit_id, date)
            )
        """)
        self.db.commit()

    def _cache_key(self, unit_id: str, dt: str) -> str:
        return f"price:{unit_id}:{dt}"

    def set_rates(self, unit_id: str, currency: str, rates: list[dict]) -> int:
        """Set nightly rates for a unit. Returns count of rates set."""
        count = 0
        for r in rates:
            dt = r["date"]
            rate_cents = r["rate_cents"]
            self.db.execute("""
                INSERT INTO pricing (unit_id, date, rate_cents, currency)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(unit_id, date) DO UPDATE SET
                    rate_cents = ?,
                    currency = ?,
                    updated_at = datetime('now')
            """, [unit_id, dt, rate_cents, currency, rate_cents, currency])

            # Update cache
            self.cache.set(self._cache_key(unit_id, dt), str(rate_cents), ex=3600)
            count += 1

        self.db.commit()

        # Invalidate calendar caches for affected months
        months = set()
        for r in rates:
            months.add(r["date"][:7])
        for m in months:
            self.cache.delete(f"price:cal:{unit_id}:{m}")

        return count

    def get_rate(self, unit_id: str, start_date: date, end_date: date = None
                 ) -> tuple[str, list[dict], list[str]]:
        """Get nightly rates for a date range.
        
        Returns (currency, rates, missing_dates).
        """
        if end_date is None:
            end_date = start_date

        rates = []
        missing_dates = []
        currency = "USD"

        current = start_date
        while current <= end_date:
            dt_str = current.strftime("%Y-%m-%d")
            cache_key = self._cache_key(unit_id, dt_str)
            cached = self.cache.get(cache_key)

            if cached is not None:
                rates.append({"date": dt_str, "rate_cents": int(cached)})
            else:
                row = self.db.fetchone(
                    "SELECT rate_cents, currency FROM pricing WHERE unit_id = ? AND date = ?",
                    [unit_id, dt_str]
                )
                if row:
                    rates.append({"date": dt_str, "rate_cents": row["rate_cents"]})
                    currency = row["currency"]
                    self.cache.set(cache_key, str(row["rate_cents"]), ex=3600)
                else:
                    missing_dates.append(dt_str)

            current += timedelta(days=1)

        # Get currency from any existing rate
        if rates:
            row = self.db.fetchone(
                "SELECT currency FROM pricing WHERE unit_id = ? LIMIT 1",
                [unit_id]
            )
            if row:
                currency = row["currency"]

        return currency, rates, missing_dates

    def get_calendar(self, unit_id: str, month: str) -> tuple[str, list[dict], list[int]]:
        """Get all rates for a month.
        
        Returns (currency, rates, missing_days).
        """
        import calendar as cal

        year, mon = int(month[:4]), int(month[5:7])
        days_in_month = cal.monthrange(year, mon)[1]

        # Try cache
        cal_key = f"price:cal:{unit_id}:{month}"
        cached = self.cache.get(cal_key)
        if cached:
            import json
            data = json.loads(cached)
            return data["currency"], data["rates"], data["missing_days"]

        # Query DB
        rows = self.db.fetchall(
            "SELECT date, rate_cents, currency FROM pricing WHERE unit_id = ? AND date LIKE ?",
            [unit_id, f"{month}%"]
        )

        rate_map = {}
        currency = "USD"
        for r in rows:
            day = int(r["date"][-2:])
            rate_map[day] = r["rate_cents"]
            currency = r["currency"]

        rates = []
        missing_days = []
        for d in range(1, days_in_month + 1):
            if d in rate_map:
                rates.append({"day": d, "rate_cents": rate_map[d]})
            else:
                missing_days.append(d)

        # Cache result
        import json
        self.cache.set(cal_key, json.dumps({
            "currency": currency, "rates": rates, "missing_days": missing_days
        }), ex=3600)

        return currency, rates, missing_days

    def bulk_rates(self, unit_ids: list[str], start_date: date, end_date: date) -> list[dict]:
        """Get aggregate rates for multiple units."""
        results = []
        for uid in unit_ids:
            currency, rates, missing = self.get_rate(uid, start_date, end_date)
            if missing:
                results.append({
                    "unit_id": uid,
                    "currency": currency,
                    "sum_cents": None,
                    "avg_cents": None,
                    "min_cents": None,
                    "max_cents": None,
                    "missing_dates": missing
                })
            elif rates:
                cents = [r["rate_cents"] for r in rates]
                results.append({
                    "unit_id": uid,
                    "currency": currency,
                    "sum_cents": sum(cents),
                    "avg_cents": round(sum(cents) / len(cents)),
                    "min_cents": min(cents),
                    "max_cents": max(cents),
                    "missing_dates": []
                })
            else:
                all_dates = []
                current = start_date
                while current <= end_date:
                    all_dates.append(current.strftime("%Y-%m-%d"))
                    current += timedelta(days=1)
                results.append({
                    "unit_id": uid,
                    "currency": currency,
                    "sum_cents": None,
                    "avg_cents": None,
                    "min_cents": None,
                    "max_cents": None,
                    "missing_dates": all_dates
                })
        return results
