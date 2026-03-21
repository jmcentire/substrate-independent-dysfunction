"""
Availability data store — manages bitmask data in SQLite/PostgreSQL + cache.
"""

from datetime import date, timedelta
from services.shared.db import Database
from services.shared.cache import MemoryCache
from services.availability.bitmask import (
    date_range_to_masks, check_conflict, apply_block, release_block,
    compute_contiguous_metadata, find_next_available_window
)


class AvailabilityStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS availability (
                unit_id     TEXT NOT NULL,
                year_month  TEXT NOT NULL,
                days        INTEGER NOT NULL DEFAULT 0,
                start_free  INTEGER NOT NULL DEFAULT 31,
                end_free    INTEGER NOT NULL DEFAULT 31,
                max_free    INTEGER NOT NULL DEFAULT 31,
                PRIMARY KEY (unit_id, year_month)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS unit_region (
                unit_id  TEXT PRIMARY KEY,
                region   TEXT NOT NULL
            )
        """)
        self.db.commit()

    def register_unit(self, unit_id: str, region: str):
        """Register a unit in a region."""
        self.db.execute(
            "INSERT OR REPLACE INTO unit_region (unit_id, region) VALUES (?, ?)",
            [unit_id, region]
        )
        self.db.commit()
        self.cache.sadd(f"region:{region}", unit_id)

    def get_mask(self, unit_id: str, year_month: str) -> int:
        """Get bitmask for a unit-month. Returns 0 (all available) if not found."""
        # Try cache first
        cache_key = f"avail:{unit_id}:{year_month}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return int(cached)

        # Fall back to DB
        row = self.db.fetchone(
            "SELECT days FROM availability WHERE unit_id = ? AND year_month = ?",
            [unit_id, year_month]
        )
        mask = row["days"] if row else 0

        # Populate cache
        self.cache.set(cache_key, str(mask), ex=3600)
        return mask

    def check(self, unit_id: str, start_date: date, end_date: date) -> bool:
        """Check if a unit is available for the full date range."""
        masks = date_range_to_masks(start_date, end_date)
        for ym, request_mask in masks.items():
            existing_mask = self.get_mask(unit_id, ym)
            if check_conflict(existing_mask, request_mask):
                return False
        return True

    def acquire(self, unit_id: str, start_date: date, end_date: date) -> tuple[bool, str]:
        """Block dates. Returns (success, status_or_error)."""
        masks = date_range_to_masks(start_date, end_date)

        # Check for conflicts first
        for ym, request_mask in masks.items():
            existing_mask = self.get_mask(unit_id, ym)
            if check_conflict(existing_mask, request_mask):
                return False, "conflict"

        # Apply blocks
        for ym, request_mask in masks.items():
            existing_mask = self.get_mask(unit_id, ym)
            new_mask = apply_block(existing_mask, request_mask)
            meta = compute_contiguous_metadata(new_mask, ym)

            self.db.execute("""
                INSERT INTO availability (unit_id, year_month, days, start_free, end_free, max_free)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id, year_month) DO UPDATE SET
                    days = ?,
                    start_free = ?,
                    end_free = ?,
                    max_free = ?
            """, [
                unit_id, ym, new_mask, meta["start_free"], meta["end_free"], meta["max_free"],
                new_mask, meta["start_free"], meta["end_free"], meta["max_free"]
            ])

            # Invalidate cache
            cache_key = f"avail:{unit_id}:{ym}"
            self.cache.set(cache_key, str(new_mask), ex=3600)

        self.db.commit()
        return True, "acquired"

    def release(self, unit_id: str, start_date: date, end_date: date) -> str:
        """Release blocked dates."""
        masks = date_range_to_masks(start_date, end_date)

        for ym, release_mask in masks.items():
            existing_mask = self.get_mask(unit_id, ym)
            new_mask = release_block(existing_mask, release_mask)
            meta = compute_contiguous_metadata(new_mask, ym)

            self.db.execute("""
                INSERT INTO availability (unit_id, year_month, days, start_free, end_free, max_free)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(unit_id, year_month) DO UPDATE SET
                    days = ?,
                    start_free = ?,
                    end_free = ?,
                    max_free = ?
            """, [
                unit_id, ym, new_mask, meta["start_free"], meta["end_free"], meta["max_free"],
                new_mask, meta["start_free"], meta["end_free"], meta["max_free"]
            ])

            cache_key = f"avail:{unit_id}:{ym}"
            self.cache.set(cache_key, str(new_mask), ex=3600)

        self.db.commit()
        return "released"

    def search(self, region: str, start_date: date, end_date: date) -> list[str]:
        """Find available units in a region."""
        # Get unit IDs for region
        unit_ids = self.cache.smembers(f"region:{region}")
        if not unit_ids:
            rows = self.db.fetchall(
                "SELECT unit_id FROM unit_region WHERE region = ?",
                [region]
            )
            unit_ids = {r["unit_id"] for r in rows}
            for uid in unit_ids:
                self.cache.sadd(f"region:{region}", uid)

        available = []
        for uid in unit_ids:
            if self.check(uid, start_date, end_date):
                available.append(uid)

        return available

    def next_available(self, unit_id: str, nights: int, after: date,
                       limit_months: int = 12) -> tuple[bool, str | None]:
        """Find next available window of N contiguous nights."""
        # Load all masks for the search range
        masks = {}
        current = after
        for _ in range(limit_months):
            ym = current.strftime('%Y-%m')
            masks[ym] = self.get_mask(unit_id, ym)
            if current.month == 12:
                current = date(current.year + 1, 1, 1)
            else:
                current = date(current.year, current.month + 1, 1)

        return find_next_available_window(masks, nights, after, limit_months)
