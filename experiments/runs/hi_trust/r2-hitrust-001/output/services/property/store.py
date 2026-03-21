"""
Property Service data store — 4-tier override hierarchy.

Override hierarchy: PMS (10) < PM (20) < Wander (30) < Admin (40)
Higher priority wins per field.
"""

import json
import uuid
from datetime import datetime
from typing import Optional
from services.shared.db import Database
from services.shared.cache import MemoryCache


# Override priority tiers
TIER_PMS = 10
TIER_PM = 20
TIER_WANDER = 30
TIER_ADMIN = 40

TIER_NAMES = {10: "PMS", 20: "PM", 30: "Wander", 40: "Admin"}
TIER_BY_NAME = {v.lower(): k for k, v in TIER_NAMES.items()}


class PropertyStore:
    def __init__(self, db: Database, cache: MemoryCache):
        self.db = db
        self.cache = cache
        self._init_schema()

    def _init_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property (
                id TEXT PRIMARY KEY,
                organization_id TEXT,
                name TEXT NOT NULL,
                description TEXT,
                property_type TEXT DEFAULT 'HOUSE',
                status TEXT DEFAULT 'ACTIVE',
                is_bookable INTEGER DEFAULT 1,
                bedrooms INTEGER DEFAULT 0,
                bathrooms REAL DEFAULT 0,
                max_guests INTEGER DEFAULT 1,
                address_line1 TEXT,
                address_line2 TEXT,
                city TEXT,
                state TEXT,
                zip TEXT,
                country TEXT DEFAULT 'US',
                latitude REAL,
                longitude REAL,
                timezone TEXT DEFAULT 'America/New_York',
                pms_type TEXT,
                external_id TEXT,
                currency TEXT DEFAULT 'USD',
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property_override (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                field TEXT NOT NULL,
                from_value TEXT,
                to_value TEXT NOT NULL,
                priority INTEGER NOT NULL,
                actor TEXT,
                note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (property_id) REFERENCES property(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property_override_conflict (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                override_id TEXT NOT NULL,
                field TEXT NOT NULL,
                old_base TEXT,
                new_base TEXT,
                status TEXT DEFAULT 'PENDING',
                resolution TEXT,
                resolved_at TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (property_id) REFERENCES property(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property_fee (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                label TEXT NOT NULL,
                amount_cents INTEGER NOT NULL,
                per TEXT DEFAULT 'PER_STAY',
                required INTEGER DEFAULT 1,
                conditions TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (property_id) REFERENCES property(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property_policy (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL UNIQUE,
                check_in_time TEXT DEFAULT '16:00',
                check_out_time TEXT DEFAULT '11:00',
                min_nights INTEGER DEFAULT 1,
                max_nights INTEGER DEFAULT 365,
                cancellation_policy TEXT DEFAULT 'FLEXIBLE',
                cancellation_tiers TEXT,
                pets_allowed INTEGER DEFAULT 0,
                smoking_allowed INTEGER DEFAULT 0,
                events_allowed INTEGER DEFAULT 0,
                house_rules TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (property_id) REFERENCES property(id)
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS property_external_id (
                id TEXT PRIMARY KEY,
                property_id TEXT NOT NULL,
                source TEXT NOT NULL,
                external_id TEXT NOT NULL,
                is_sor INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                FOREIGN KEY (property_id) REFERENCES property(id)
            )
        """)
        self.db.commit()

    def create_property(self, data: dict) -> dict:
        """Create a new property."""
        prop_id = data.get("id", str(uuid.uuid4()))
        self.db.execute("""
            INSERT INTO property (
                id, organization_id, name, description, property_type, status,
                is_bookable, bedrooms, bathrooms, max_guests,
                address_line1, city, state, zip, country,
                latitude, longitude, timezone, pms_type, external_id, currency
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            prop_id,
            data.get("organization_id"),
            data.get("name", ""),
            data.get("description"),
            data.get("property_type", "HOUSE"),
            data.get("status", "ACTIVE"),
            1 if data.get("is_bookable", True) else 0,
            data.get("bedrooms", 0),
            data.get("bathrooms", 0),
            data.get("max_guests", 1),
            data.get("address_line1"),
            data.get("city"),
            data.get("state"),
            data.get("zip"),
            data.get("country", "US"),
            data.get("latitude"),
            data.get("longitude"),
            data.get("timezone", "America/New_York"),
            data.get("pms_type"),
            data.get("external_id"),
            data.get("currency", "USD"),
        ])
        self.db.commit()
        self._invalidate_cache(prop_id)
        return self.get_property(prop_id)

    def get_property(self, property_id: str) -> Optional[dict]:
        """Get effective property data (base + overrides applied)."""
        # Check cache
        cache_key = f"property:{property_id}"
        cached = self.cache.get(cache_key)
        if cached:
            return json.loads(cached)

        # Get base data
        row = self.db.fetchone("SELECT * FROM property WHERE id = ?", [property_id])
        if not row:
            return None

        prop = dict(row)
        prop["is_bookable"] = bool(prop["is_bookable"])

        # Apply overrides (highest priority per field wins)
        overrides = self.db.fetchall(
            "SELECT field, to_value, priority FROM property_override WHERE property_id = ? ORDER BY priority DESC",
            [property_id]
        )

        applied_fields = set()
        has_overrides = len(overrides) > 0
        for ov in overrides:
            field = ov["field"]
            if field not in applied_fields and field in prop:
                # Try to maintain type
                original = prop[field]
                try:
                    if isinstance(original, int):
                        prop[field] = int(ov["to_value"])
                    elif isinstance(original, float):
                        prop[field] = float(ov["to_value"])
                    elif isinstance(original, bool):
                        prop[field] = ov["to_value"].lower() in ('true', '1')
                    else:
                        prop[field] = ov["to_value"]
                except (ValueError, TypeError):
                    prop[field] = ov["to_value"]
                applied_fields.add(field)

        # Check for conflicts
        conflicts = self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM property_override_conflict WHERE property_id = ? AND status = 'PENDING'",
            [property_id]
        )
        has_conflicts = conflicts and conflicts["cnt"] > 0

        prop["has_overrides"] = has_overrides
        prop["has_conflicts"] = has_conflicts

        # Cache result
        self.cache.set(cache_key, json.dumps(prop, default=str), ex=3600)
        return prop

    def list_properties(self, filters: dict = None, limit: int = 50, cursor: str = None) -> tuple[list, Optional[str]]:
        """List properties with optional filters."""
        query = "SELECT id FROM property WHERE 1=1"
        params = []

        if filters:
            if filters.get("organization_id"):
                query += " AND organization_id = ?"
                params.append(filters["organization_id"])
            if filters.get("city"):
                query += " AND city = ?"
                params.append(filters["city"])
            if filters.get("state"):
                query += " AND state = ?"
                params.append(filters["state"])
            if filters.get("status"):
                query += " AND status = ?"
                params.append(filters["status"])
            if filters.get("is_bookable") is not None:
                query += " AND is_bookable = ?"
                params.append(1 if filters["is_bookable"] else 0)
            if filters.get("pms_type"):
                query += " AND pms_type = ?"
                params.append(filters["pms_type"])

        if cursor:
            query += " AND id > ?"
            params.append(cursor)

        query += " ORDER BY id LIMIT ?"
        params.append(limit + 1)

        rows = self.db.fetchall(query, params)
        has_more = len(rows) > limit
        if has_more:
            rows = rows[:limit]

        properties = [self.get_property(r["id"]) for r in rows]
        properties = [p for p in properties if p is not None]

        next_cursor = rows[-1]["id"] if has_more else None
        return properties, next_cursor

    def set_override(self, property_id: str, field: str, from_value: Optional[str],
                     to_value: str, priority: int, actor: str = None, note: str = None) -> dict:
        """Set an override for a property field."""
        # Check property exists
        prop = self.db.fetchone("SELECT id FROM property WHERE id = ?", [property_id])
        if not prop:
            return {"error": "PROPERTY_NOT_FOUND"}

        # Check for existing override at same priority
        existing = self.db.fetchone(
            "SELECT id FROM property_override WHERE property_id = ? AND field = ? AND priority = ?",
            [property_id, field, priority]
        )
        if existing:
            # Update existing
            self.db.execute("""
                UPDATE property_override SET from_value = ?, to_value = ?, actor = ?, note = ?
                WHERE id = ?
            """, [from_value, to_value, actor, note, existing["id"]])
        else:
            ov_id = str(uuid.uuid4())
            self.db.execute("""
                INSERT INTO property_override (id, property_id, field, from_value, to_value, priority, actor, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, [ov_id, property_id, field, from_value, to_value, priority, actor, note])

        self.db.commit()
        self._invalidate_cache(property_id)

        return {"status": "override_set", "field": field, "priority": priority}

    def get_conflicts(self, property_id: str) -> list[dict]:
        """Get pending conflicts for a property."""
        rows = self.db.fetchall(
            "SELECT * FROM property_override_conflict WHERE property_id = ? AND status = 'PENDING'",
            [property_id]
        )
        return [dict(r) for r in rows]

    def resolve_conflict(self, property_id: str, conflict_id: str, resolution: str) -> dict:
        """Resolve a conflict. resolution: KEEP_OVERRIDE, ACCEPT_NEW, NEW_OVERRIDE."""
        conflict = self.db.fetchone(
            "SELECT * FROM property_override_conflict WHERE id = ? AND property_id = ?",
            [conflict_id, property_id]
        )
        if not conflict:
            return {"error": "NOT_FOUND"}

        if resolution == "ACCEPT_NEW":
            # Drop the override
            self.db.execute(
                "DELETE FROM property_override WHERE id = ?",
                [conflict["override_id"]]
            )
        elif resolution == "KEEP_OVERRIDE":
            # Update from_value to match new base
            self.db.execute(
                "UPDATE property_override SET from_value = ? WHERE id = ?",
                [conflict["new_base"], conflict["override_id"]]
            )

        self.db.execute(
            "UPDATE property_override_conflict SET status = 'RESOLVED', resolution = ?, resolved_at = datetime('now') WHERE id = ?",
            [resolution, conflict_id]
        )
        self.db.commit()
        self._invalidate_cache(property_id)

        return {"status": "resolved", "resolution": resolution}

    def get_fees(self, property_id: str) -> list[dict]:
        """Get fees for a property."""
        rows = self.db.fetchall(
            "SELECT * FROM property_fee WHERE property_id = ?",
            [property_id]
        )
        return [dict(r) for r in rows]

    def set_fee(self, property_id: str, fee_data: dict) -> dict:
        """Set a fee for a property."""
        fee_id = fee_data.get("id", str(uuid.uuid4()))
        self.db.execute("""
            INSERT INTO property_fee (id, property_id, kind, label, amount_cents, per, required, conditions)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                amount_cents = ?, per = ?, required = ?, label = ?
        """, [
            fee_id, property_id, fee_data["kind"], fee_data["label"],
            fee_data["amount_cents"], fee_data.get("per", "PER_STAY"),
            1 if fee_data.get("required", True) else 0,
            json.dumps(fee_data.get("conditions")) if fee_data.get("conditions") else None,
            fee_data["amount_cents"], fee_data.get("per", "PER_STAY"),
            1 if fee_data.get("required", True) else 0, fee_data["label"],
        ])
        self.db.commit()
        return {"id": fee_id, "status": "set"}

    def get_policies(self, property_id: str) -> Optional[dict]:
        """Get policies for a property."""
        row = self.db.fetchone(
            "SELECT * FROM property_policy WHERE property_id = ?",
            [property_id]
        )
        if row:
            result = dict(row)
            result["pets_allowed"] = bool(result.get("pets_allowed"))
            result["smoking_allowed"] = bool(result.get("smoking_allowed"))
            result["events_allowed"] = bool(result.get("events_allowed"))
            return result
        return None

    def set_policies(self, property_id: str, policy_data: dict) -> dict:
        """Set policies for a property."""
        policy_id = str(uuid.uuid4())
        self.db.execute("""
            INSERT INTO property_policy (
                id, property_id, check_in_time, check_out_time,
                min_nights, max_nights, cancellation_policy,
                cancellation_tiers, pets_allowed, smoking_allowed,
                events_allowed, house_rules
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(property_id) DO UPDATE SET
                check_in_time = ?, check_out_time = ?,
                min_nights = ?, max_nights = ?,
                cancellation_policy = ?, cancellation_tiers = ?,
                pets_allowed = ?, smoking_allowed = ?,
                events_allowed = ?, house_rules = ?
        """, [
            policy_id, property_id,
            policy_data.get("check_in_time", "16:00"),
            policy_data.get("check_out_time", "11:00"),
            policy_data.get("min_nights", 1),
            policy_data.get("max_nights", 365),
            policy_data.get("cancellation_policy", "FLEXIBLE"),
            json.dumps(policy_data.get("cancellation_tiers")) if policy_data.get("cancellation_tiers") else None,
            1 if policy_data.get("pets_allowed") else 0,
            1 if policy_data.get("smoking_allowed") else 0,
            1 if policy_data.get("events_allowed") else 0,
            policy_data.get("house_rules"),
            # ON CONFLICT params
            policy_data.get("check_in_time", "16:00"),
            policy_data.get("check_out_time", "11:00"),
            policy_data.get("min_nights", 1),
            policy_data.get("max_nights", 365),
            policy_data.get("cancellation_policy", "FLEXIBLE"),
            json.dumps(policy_data.get("cancellation_tiers")) if policy_data.get("cancellation_tiers") else None,
            1 if policy_data.get("pets_allowed") else 0,
            1 if policy_data.get("smoking_allowed") else 0,
            1 if policy_data.get("events_allowed") else 0,
            policy_data.get("house_rules"),
        ])
        self.db.commit()
        return {"status": "set"}

    def get_sources(self, property_id: str) -> dict:
        """Get SoR and 'also seen in' sources."""
        rows = self.db.fetchall(
            "SELECT * FROM property_external_id WHERE property_id = ?",
            [property_id]
        )
        sor = None
        also_seen_in = []
        for r in rows:
            entry = {"source": r["source"], "external_id": r["external_id"]}
            if r["is_sor"]:
                sor = entry
            else:
                also_seen_in.append(entry)

        # Fall back to property table
        if not sor:
            prop = self.db.fetchone("SELECT pms_type, external_id FROM property WHERE id = ?", [property_id])
            if prop and prop.get("pms_type"):
                sor = {"pms_type": prop["pms_type"], "external_id": prop.get("external_id")}

        return {"sor": sor, "also_seen_in": also_seen_in}

    def _invalidate_cache(self, property_id: str):
        self.cache.delete(f"property:{property_id}")
