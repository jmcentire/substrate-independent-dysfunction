"""
Contract tests for pricing_cache_engine — Hash-Sharded Price Cache.

Tests verify the contract behavior of shardKey, cacheRates, getCachedRates,
and invalidateUnit using a FakeRedis in-memory mock. No external services required.

Run with: pytest contract_test.py -v
"""
import json
import re
import uuid
from datetime import date, timedelta
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# ---------------------------------------------------------------------------
# Import the component under test
# ---------------------------------------------------------------------------
from src.pricing_cache_engine import (
    PricingCacheEngine,
    CacheEngineConfig,
    CacheOptions,
    DateRange,
    NightlyRate,
    MoneyAmount,
    FeeDetail,
    LosDiscount,
    CacheHit,
    CacheMiss,
)


# ---------------------------------------------------------------------------
# Test Data Factories
# ---------------------------------------------------------------------------
VALID_UNIT_ID = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
VALID_UNIT_ID_2 = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"


def make_money(amount_cents: int = 10000, currency: str = "USD") -> MoneyAmount:
    """Factory for MoneyAmount with sensible defaults."""
    return MoneyAmount(amount_cents=amount_cents, currency=currency)


def make_fee(
    fee_code: str = "cleaning_fee",
    label: str = "Cleaning Fee",
    amount_cents: int = 1500,
    currency: str = "USD",
    is_tax: bool = False,
) -> FeeDetail:
    """Factory for FeeDetail."""
    return FeeDetail(
        fee_code=fee_code,
        label=label,
        amount=make_money(amount_cents, currency),
        is_tax=is_tax,
    )


def make_los_discount(
    min_nights: int = 7,
    discount_percent: int = 10,
    discount_amount_cents: int = 1000,
    currency: str = "USD",
) -> LosDiscount:
    """Factory for LosDiscount."""
    return LosDiscount(
        min_nights=min_nights,
        discount_percent=discount_percent,
        discount_amount=make_money(discount_amount_cents, currency),
    )


def make_nightly_rate(
    rate_date: str = "2025-07-01",
    base_rate_cents: int = 10000,
    seasonal_rate_cents: int = 10000,
    adjusted_rate_cents: int = 10000,
    total_cents: int = 10000,
    currency: str = "USD",
    fees_and_taxes: Optional[List[FeeDetail]] = None,
    los_discount: Optional[LosDiscount] = None,
) -> NightlyRate:
    """Factory for NightlyRate with sensible defaults."""
    return NightlyRate(
        date=rate_date,
        base_rate=make_money(base_rate_cents, currency),
        seasonal_rate=make_money(seasonal_rate_cents, currency),
        adjusted_rate=make_money(adjusted_rate_cents, currency),
        los_discount=los_discount,
        fees_and_taxes=fees_and_taxes if fees_and_taxes is not None else [],
        total_cents=total_cents,
        currency=currency,
    )


def make_rates_for_range(
    check_in: str, check_out: str, base_cents: int = 10000, currency: str = "USD"
) -> List[NightlyRate]:
    """Create a list of NightlyRate objects for each date in [check_in, check_out)."""
    start = date.fromisoformat(check_in)
    end = date.fromisoformat(check_out)
    rates = []
    current = start
    while current < end:
        rates.append(
            make_nightly_rate(
                rate_date=current.isoformat(),
                base_rate_cents=base_cents,
                total_cents=base_cents,
                currency=currency,
            )
        )
        current += timedelta(days=1)
    return rates


def make_date_range(check_in: str = "2025-07-01", check_out: str = "2025-07-04") -> DateRange:
    """Factory for DateRange."""
    return DateRange(check_in=check_in, check_out=check_out)


def default_config(shard_count: int = 64, ttl: int = 3600, schema_version: int = 1) -> CacheEngineConfig:
    """Factory for CacheEngineConfig."""
    return CacheEngineConfig(
        shard_count=shard_count,
        default_ttl_seconds=ttl,
        current_schema_version=schema_version,
    )


# ---------------------------------------------------------------------------
# FakeRedis — In-memory Redis mock with hash and TTL support
# ---------------------------------------------------------------------------
class FakeRedis:
    """Minimal in-memory Redis mock supporting hset, hmget, expire, delete."""

    def __init__(self):
        self.store: Dict[str, Dict[str, str]] = {}  # key -> {field: value}
        self.ttls: Dict[str, int] = {}
        self.calls: Dict[str, List[Any]] = {
            "hset": [],
            "hmget": [],
            "expire": [],
            "delete": [],
        }

    def hset(self, key: str, mapping: Dict[str, str] = None, **kwargs) -> int:
        self.calls["hset"].append((key, mapping or kwargs))
        if key not in self.store:
            self.store[key] = {}
        data = mapping if mapping else kwargs
        self.store[key].update(data)
        return len(data)

    def hmget(self, key: str, *fields) -> List[Optional[str]]:
        # Handle fields passed as a list or as *args
        if len(fields) == 1 and isinstance(fields[0], (list, tuple)):
            fields = fields[0]
        self.calls["hmget"].append((key, list(fields)))
        hash_data = self.store.get(key, {})
        return [hash_data.get(f) for f in fields]

    def expire(self, key: str, seconds: int) -> bool:
        self.calls["expire"].append((key, seconds))
        self.ttls[key] = seconds
        return key in self.store

    def delete(self, *keys) -> int:
        self.calls["delete"].append(list(keys))
        count = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                count += 1
            self.ttls.pop(k, None)
        return count

    def get_raw_hash(self, key: str) -> Optional[Dict[str, str]]:
        """Test helper: inspect raw stored data."""
        return self.store.get(key)


class ErrorRedis:
    """Redis mock that raises configurable exceptions on specific operations."""

    def __init__(self, error_class=ConnectionError, error_on=None):
        self.error_class = error_class
        self.error_on = error_on or ["hset", "hmget", "expire", "delete"]
        self.calls: Dict[str, List] = {
            "hset": [], "hmget": [], "expire": [], "delete": []
        }

    def _maybe_raise(self, op: str):
        if op in self.error_on:
            raise self.error_class(f"Simulated {self.error_class.__name__} on {op}")

    def hset(self, key, mapping=None, **kwargs):
        self.calls["hset"].append(key)
        self._maybe_raise("hset")

    def hmget(self, key, *fields):
        self.calls["hmget"].append(key)
        self._maybe_raise("hmget")

    def expire(self, key, seconds):
        self.calls["expire"].append(key)
        self._maybe_raise("expire")

    def delete(self, *keys):
        self.calls["delete"].append(keys)
        self._maybe_raise("delete")


# ---------------------------------------------------------------------------
# shardKey Tests
# ---------------------------------------------------------------------------
class TestShardKey:
    """Tests for shardKey — pure deterministic shard key computation."""

    def setup_method(self):
        self.config = default_config(shard_count=64)
        self.engine = PricingCacheEngine(config=self.config, redis_client=FakeRedis())

    def test_shard_key_happy_path_format(self):
        """shardKey returns a key matching 'pricing:{N}:{unit_id}'."""
        key = self.engine.shard_key(VALID_UNIT_ID)
        pattern = rf"^pricing:\d+:{re.escape(VALID_UNIT_ID)}$"
        assert re.match(pattern, key), f"Key '{key}' does not match expected pattern"

    def test_shard_key_shard_number_in_range(self):
        """Shard number N is in [0, shard_count)."""
        key = self.engine.shard_key(VALID_UNIT_ID)
        shard_num = int(key.split(":")[1])
        assert 0 <= shard_num < 64, f"Shard number {shard_num} out of range [0, 64)"

    def test_shard_key_deterministic(self):
        """Same unit_id always produces the same key (100 iterations)."""
        results = {self.engine.shard_key(VALID_UNIT_ID) for _ in range(100)}
        assert len(results) == 1, "shardKey is not deterministic — got multiple results"

    def test_shard_key_different_units_may_differ(self):
        """Different unit_ids produce different shard numbers (statistical check)."""
        shard_numbers = set()
        for i in range(20):
            uid = str(uuid.uuid4())
            key = self.engine.shard_key(uid)
            shard_numbers.add(int(key.split(":")[1]))
        assert len(shard_numbers) > 1, (
            "Expected multiple distinct shard numbers across 20 random UUIDs"
        )

    def test_shard_key_shard_count_1(self):
        """With shard_count=1, all unit_ids map to shard 0."""
        engine = PricingCacheEngine(
            config=default_config(shard_count=1), redis_client=FakeRedis()
        )
        key = engine.shard_key(VALID_UNIT_ID)
        shard_num = int(key.split(":")[1])
        assert shard_num == 0, f"Expected shard 0 with shard_count=1, got {shard_num}"

    def test_shard_key_shard_count_max(self):
        """With shard_count=4096 (max), shard number is in [0, 4096)."""
        engine = PricingCacheEngine(
            config=default_config(shard_count=4096), redis_client=FakeRedis()
        )
        key = engine.shard_key(VALID_UNIT_ID)
        shard_num = int(key.split(":")[1])
        assert 0 <= shard_num < 4096, f"Shard {shard_num} out of [0, 4096)"

    def test_shard_key_format_invariant_multiple_uuids(self):
        """All generated keys match the pattern ^pricing:\\d+:[0-9a-f-]+$."""
        for _ in range(50):
            uid = str(uuid.uuid4())
            key = self.engine.shard_key(uid)
            assert re.match(r"^pricing:\d+:[0-9a-f-]+$", key), (
                f"Key '{key}' violates format invariant"
            )
            shard_num = int(key.split(":")[1])
            assert 0 <= shard_num < 64


# ---------------------------------------------------------------------------
# cacheRates Tests
# ---------------------------------------------------------------------------
class TestCacheRates:
    """Tests for cacheRates — writing nightly rates to Redis."""

    def setup_method(self):
        self.fake_redis = FakeRedis()
        self.config = default_config()
        self.engine = PricingCacheEngine(
            config=self.config, redis_client=self.fake_redis
        )

    def test_cache_single_night_round_trip(self):
        """Cache a single night and retrieve it as a CacheHit."""
        rate = make_nightly_rate("2025-07-01", base_rate_cents=10000)
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True, "Expected CacheHit"
        assert len(result.data) == 1
        assert result.data[0].date == "2025-07-01"
        assert result.data[0].base_rate.amount_cents == 10000

    def test_cache_multi_night_round_trip(self):
        """Cache 3 nights and verify all are returned sorted by date."""
        rates = make_rates_for_range("2025-07-01", "2025-07-04", base_cents=15000)
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result.hit is True, "Expected CacheHit for all 3 nights"
        assert len(result.data) == 3
        dates = [r.date for r in result.data]
        assert dates == ["2025-07-01", "2025-07-02", "2025-07-03"]
        for r in result.data:
            assert r.base_rate.amount_cents == 15000

    def test_cache_with_custom_ttl(self):
        """Custom TTL from CacheOptions is used for EXPIRE."""
        rate = make_nightly_rate("2025-07-01")
        options = CacheOptions(ttl_seconds=7200)
        self.engine.cache_rates(VALID_UNIT_ID, [rate], options=options)

        shard_key = self.engine.shard_key(VALID_UNIT_ID)
        assert len(self.fake_redis.calls["expire"]) >= 1
        # Find the expire call for our key
        expire_ttls = [t for k, t in self.fake_redis.calls["expire"] if k == shard_key]
        assert 7200 in expire_ttls, f"Expected TTL 7200, got {expire_ttls}"

    def test_cache_with_ttl_zero_no_immediate_expiry(self):
        """TTL of 0 means no expiry; data remains retrievable."""
        rate = make_nightly_rate("2025-07-01")
        options = CacheOptions(ttl_seconds=0)
        self.engine.cache_rates(VALID_UNIT_ID, [rate], options=options)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True, "Data should be retrievable with TTL=0"

    def test_cache_preserves_fees_and_taxes(self):
        """Fees and taxes round-trip through cache correctly."""
        fees = [
            make_fee("cleaning_fee", "Cleaning Fee", 1500, "USD", False),
            make_fee("occupancy_tax", "Occupancy Tax", 800, "USD", True),
        ]
        rate = make_nightly_rate(
            "2025-07-01", base_rate_cents=10000, total_cents=12300, fees_and_taxes=fees
        )
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        retrieved = result.data[0]
        assert len(retrieved.fees_and_taxes) == 2
        assert retrieved.fees_and_taxes[0].fee_code == "cleaning_fee"
        assert retrieved.fees_and_taxes[0].amount.amount_cents == 1500
        assert retrieved.fees_and_taxes[0].is_tax is False
        assert retrieved.fees_and_taxes[1].fee_code == "occupancy_tax"
        assert retrieved.fees_and_taxes[1].amount.amount_cents == 800
        assert retrieved.fees_and_taxes[1].is_tax is True

    def test_cache_preserves_los_discount(self):
        """LOS discount round-trips through cache correctly."""
        discount = make_los_discount(min_nights=7, discount_percent=10, discount_amount_cents=1000)
        rate = make_nightly_rate(
            "2025-07-01", base_rate_cents=10000, total_cents=9000, los_discount=discount
        )
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        d = result.data[0].los_discount
        assert d is not None, "Expected LOS discount to be present"
        assert d.min_nights == 7
        assert d.discount_percent == 10
        assert d.discount_amount.amount_cents == 1000

    def test_cache_preserves_null_los_discount(self):
        """Null LOS discount round-trips as None."""
        rate = make_nightly_rate("2025-07-01", los_discount=None)
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].los_discount is None

    def test_cache_preserves_empty_fees_list(self):
        """Empty fees_and_taxes list round-trips as empty list."""
        rate = make_nightly_rate("2025-07-01", fees_and_taxes=[])
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].fees_and_taxes == []

    def test_cache_duplicate_dates_last_write_wins(self):
        """Duplicate dates in rates list: last entry wins."""
        rate1 = make_nightly_rate("2025-07-01", base_rate_cents=10000, total_cents=10000)
        rate2 = make_nightly_rate("2025-07-01", base_rate_cents=20000, total_cents=20000)
        self.engine.cache_rates(VALID_UNIT_ID, [rate1, rate2])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].base_rate.amount_cents == 20000, (
            "Expected last-write-wins for duplicate date"
        )

    def test_cache_rates_redis_connection_failure_swallowed(self):
        """cacheRates swallows Redis connection errors."""
        error_redis = ErrorRedis(ConnectionError, error_on=["hset"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)
        rate = make_nightly_rate("2025-07-01")
        # Should NOT raise
        result = engine.cache_rates(VALID_UNIT_ID, [rate])
        assert result is None, "cacheRates should return None even on error"

    def test_cache_rates_redis_timeout_swallowed(self):
        """cacheRates swallows Redis timeout errors."""
        error_redis = ErrorRedis(TimeoutError, error_on=["hset"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)
        rate = make_nightly_rate("2025-07-01")
        result = engine.cache_rates(VALID_UNIT_ID, [rate])
        assert result is None

    def test_cache_rates_schema_version_stamped(self):
        """Cached payloads contain _v field matching current_schema_version."""
        rate = make_nightly_rate("2025-07-01")
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        shard_key = self.engine.shard_key(VALID_UNIT_ID)
        raw_hash = self.fake_redis.get_raw_hash(shard_key)
        assert raw_hash is not None, "Expected data in Redis"
        for field_name, raw_value in raw_hash.items():
            parsed = json.loads(raw_value)
            assert parsed.get("_v") == 1, (
                f"Expected _v=1 in serialized payload, got {parsed.get('_v')}"
            )

    def test_cache_rates_preserves_currency_eur(self):
        """Currency field preserved through round-trip for EUR."""
        rate = make_nightly_rate("2025-07-01", currency="EUR")
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].currency == "EUR"
        assert result.data[0].base_rate.currency == "EUR"

    def test_cache_rates_jpy_zero_decimal_currency(self):
        """JPY (zero-decimal) amount_cents represents yen and round-trips exactly."""
        rate = make_nightly_rate("2025-07-01", base_rate_cents=15000, total_cents=15000, currency="JPY")
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].currency == "JPY"
        assert result.data[0].base_rate.amount_cents == 15000

    def test_cache_rates_monetary_values_exact_integers(self):
        """All monetary values round-trip as exact integers (no floating-point drift)."""
        rate = make_nightly_rate(
            "2025-07-01",
            base_rate_cents=15999,
            seasonal_rate_cents=17999,
            adjusted_rate_cents=16499,
            total_cents=18299,
        )
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        r = result.data[0]
        assert r.base_rate.amount_cents == 15999
        assert isinstance(r.base_rate.amount_cents, int)
        assert r.seasonal_rate.amount_cents == 17999
        assert isinstance(r.seasonal_rate.amount_cents, int)
        assert r.adjusted_rate.amount_cents == 16499
        assert isinstance(r.adjusted_rate.amount_cents, int)
        assert r.total_cents == 18299
        assert isinstance(r.total_cents, int)


# ---------------------------------------------------------------------------
# getCachedRates Tests
# ---------------------------------------------------------------------------
class TestGetCachedRates:
    """Tests for getCachedRates — reading cached rates from Redis."""

    def setup_method(self):
        self.fake_redis = FakeRedis()
        self.config = default_config()
        self.engine = PricingCacheEngine(
            config=self.config, redis_client=self.fake_redis
        )

    def test_cache_hit_all_dates_present(self):
        """CacheHit when all dates in range are cached and valid."""
        rates = make_rates_for_range("2025-07-01", "2025-07-04")
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result.hit is True
        assert len(result.data) == 3
        assert result.data[0].date == "2025-07-01"
        assert result.data[2].date == "2025-07-03"

    def test_single_night_stay(self):
        """CacheHit for single-night stay (1 date in range)."""
        rates = make_rates_for_range("2025-07-01", "2025-07-02")
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert len(result.data) == 1
        assert result.data[0].date == "2025-07-01"

    def test_complete_miss_empty_redis(self):
        """CacheMiss(reason='miss') when Redis has no data for the unit."""
        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result.hit is False
        assert result.reason == "miss"

    def test_partial_miss_some_dates_missing(self):
        """CacheMiss(reason='miss') when some dates are cached but not all."""
        # Cache only 2 of 3 needed dates
        rate1 = make_nightly_rate("2025-07-01")
        rate3 = make_nightly_rate("2025-07-03")
        self.engine.cache_rates(VALID_UNIT_ID, [rate1, rate3])

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result.hit is False, "Partial hit should be treated as miss"
        assert result.reason == "miss"

    def test_schema_version_mismatch_returns_miss_and_deletes(self):
        """Schema version mismatch → CacheMiss(reason='miss') + DEL of hash key."""
        # Write with schema version 1
        rate = make_nightly_rate("2025-07-01")
        self.engine.cache_rates(VALID_UNIT_ID, [rate])

        # Create engine expecting schema version 2
        engine_v2 = PricingCacheEngine(
            config=default_config(schema_version=2), redis_client=self.fake_redis
        )
        result = engine_v2.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is False
        assert result.reason == "miss", (
            "Schema version mismatch should return reason='miss'"
        )
        # Verify DEL was called
        shard_key = engine_v2.shard_key(VALID_UNIT_ID)
        del_calls = self.fake_redis.calls["delete"]
        deleted_keys = [k for call_keys in del_calls for k in call_keys]
        assert shard_key in deleted_keys, (
            "Expected auto-invalidation (DEL) of hash key on schema mismatch"
        )

    def test_deserialization_failure_returns_error_and_deletes(self):
        """Corrupt JSON → CacheMiss(reason='error') + DEL of hash key."""
        # Manually store corrupt JSON
        shard_key = self.engine.shard_key(VALID_UNIT_ID)
        self.fake_redis.hset(shard_key, mapping={"2025-07-01": "{corrupt_json"})

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is False
        assert result.reason == "error", (
            "Corrupt JSON should return reason='error'"
        )
        del_calls = self.fake_redis.calls["delete"]
        deleted_keys = [k for call_keys in del_calls for k in call_keys]
        assert shard_key in deleted_keys, (
            "Expected auto-invalidation (DEL) on deserialization failure"
        )

    def test_redis_connection_failure_returns_error(self):
        """Redis connection failure → CacheMiss(reason='error')."""
        error_redis = ErrorRedis(ConnectionError, error_on=["hmget"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)

        result = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is False
        assert result.reason == "error"

    def test_redis_timeout_returns_error(self):
        """Redis timeout → CacheMiss(reason='error')."""
        error_redis = ErrorRedis(TimeoutError, error_on=["hmget"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)

        result = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is False
        assert result.reason == "error"

    def test_invalid_date_range_check_in_after_check_out(self):
        """check_in > check_out → CacheMiss(reason='error'), no Redis call."""
        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-04", "2025-07-01")
        )
        assert result.hit is False
        assert result.reason == "error"
        assert len(self.fake_redis.calls["hmget"]) == 0, (
            "Should not call Redis with invalid date range"
        )

    def test_invalid_date_range_same_day(self):
        """check_in == check_out → CacheMiss(reason='error')."""
        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-01")
        )
        assert result.hit is False
        assert result.reason == "error"

    def test_invalid_date_range_exceeds_730_days(self):
        """Date range > 730 days → CacheMiss(reason='error')."""
        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2023-01-01", "2025-12-31")
        )
        assert result.hit is False
        assert result.reason == "error"

    def test_data_sorted_by_date_regardless_of_insertion_order(self):
        """CacheHit.data is sorted by date ascending even if cached out of order."""
        # Cache in reverse order
        rates = [
            make_nightly_rate("2025-07-03"),
            make_nightly_rate("2025-07-01"),
            make_nightly_rate("2025-07-02"),
        ]
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result.hit is True
        dates = [r.date for r in result.data]
        assert dates == ["2025-07-01", "2025-07-02", "2025-07-03"], (
            f"Expected sorted dates, got {dates}"
        )

    def test_cache_hit_only_when_all_dates_present(self):
        """Invariant: CacheHit ONLY when ALL dates in range are present."""
        # Cache 3 of 4 dates (missing 2025-07-03)
        rates = [
            make_nightly_rate("2025-07-01"),
            make_nightly_rate("2025-07-02"),
            make_nightly_rate("2025-07-04"),
        ]
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        result = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-05")
        )
        assert result.hit is False, "Missing 2025-07-03 should cause full miss"
        assert result.reason == "miss"


# ---------------------------------------------------------------------------
# invalidateUnit Tests
# ---------------------------------------------------------------------------
class TestInvalidateUnit:
    """Tests for invalidateUnit — deleting cached rates for a unit."""

    def setup_method(self):
        self.fake_redis = FakeRedis()
        self.config = default_config()
        self.engine = PricingCacheEngine(
            config=self.config, redis_client=self.fake_redis
        )

    def test_invalidate_removes_cached_data(self):
        """After invalidateUnit, getCachedRates returns CacheMiss."""
        rates = make_rates_for_range("2025-07-01", "2025-07-04")
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        # Verify data is cached
        result_before = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result_before.hit is True, "Setup: data should be cached"

        # Invalidate
        ret = self.engine.invalidate_unit(VALID_UNIT_ID)
        assert ret is None, "invalidateUnit should return None"

        # Verify data is gone
        result_after = self.engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-04")
        )
        assert result_after.hit is False
        assert result_after.reason == "miss"

    def test_invalidate_idempotent_nonexistent_key(self):
        """invalidateUnit on non-existent key succeeds silently."""
        ret = self.engine.invalidate_unit(VALID_UNIT_ID)
        assert ret is None

    def test_invalidate_double_call_idempotent(self):
        """Calling invalidateUnit twice succeeds both times."""
        rates = make_rates_for_range("2025-07-01", "2025-07-02")
        self.engine.cache_rates(VALID_UNIT_ID, rates)

        ret1 = self.engine.invalidate_unit(VALID_UNIT_ID)
        ret2 = self.engine.invalidate_unit(VALID_UNIT_ID)
        assert ret1 is None
        assert ret2 is None

    def test_invalidate_redis_connection_failure_swallowed(self):
        """invalidateUnit swallows Redis connection errors."""
        error_redis = ErrorRedis(ConnectionError, error_on=["delete"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)
        ret = engine.invalidate_unit(VALID_UNIT_ID)
        assert ret is None

    def test_invalidate_redis_timeout_swallowed(self):
        """invalidateUnit swallows Redis timeout errors."""
        error_redis = ErrorRedis(TimeoutError, error_on=["delete"])
        engine = PricingCacheEngine(config=self.config, redis_client=error_redis)
        ret = engine.invalidate_unit(VALID_UNIT_ID)
        assert ret is None


# ---------------------------------------------------------------------------
# Cross-cutting Invariant Tests
# ---------------------------------------------------------------------------
class TestCrossCuttingInvariants:
    """Tests for invariants that span multiple functions."""

    def setup_method(self):
        self.config = default_config()

    def test_no_public_method_throws_on_redis_errors(self):
        """No public method ever raises; errors are swallowed or CacheMiss."""
        for error_cls in [ConnectionError, TimeoutError, OSError, RuntimeError]:
            error_redis = ErrorRedis(error_cls)
            engine = PricingCacheEngine(config=self.config, redis_client=error_redis)

            # cacheRates should not throw
            rate = make_nightly_rate("2025-07-01")
            ret = engine.cache_rates(VALID_UNIT_ID, [rate])
            assert ret is None, (
                f"cacheRates should not throw on {error_cls.__name__}"
            )

            # getCachedRates should not throw
            result = engine.get_cached_rates(
                VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
            )
            assert result.hit is False, (
                f"getCachedRates should return CacheMiss on {error_cls.__name__}"
            )
            assert result.reason == "error"

            # invalidateUnit should not throw
            ret = engine.invalidate_unit(VALID_UNIT_ID)
            assert ret is None, (
                f"invalidateUnit should not throw on {error_cls.__name__}"
            )

    def test_round_trip_full_rate_object(self):
        """Complete NightlyRate with all fields populated round-trips correctly."""
        fake_redis = FakeRedis()
        engine = PricingCacheEngine(config=self.config, redis_client=fake_redis)

        fees = [
            make_fee("cleaning_fee", "Cleaning Fee", 2500, "USD", False),
            make_fee("occupancy_tax", "Occupancy Tax", 1200, "USD", True),
            make_fee("resort_fee", "Resort Fee", 500, "USD", False),
        ]
        discount = make_los_discount(
            min_nights=7, discount_percent=15, discount_amount_cents=2250
        )
        rate = make_nightly_rate(
            rate_date="2025-07-01",
            base_rate_cents=15000,
            seasonal_rate_cents=17000,
            adjusted_rate_cents=16500,
            total_cents=18450,
            currency="USD",
            fees_and_taxes=fees,
            los_discount=discount,
        )
        engine.cache_rates(VALID_UNIT_ID, [rate])

        result = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        r = result.data[0]
        assert r.date == "2025-07-01"
        assert r.base_rate.amount_cents == 15000
        assert r.base_rate.currency == "USD"
        assert r.seasonal_rate.amount_cents == 17000
        assert r.adjusted_rate.amount_cents == 16500
        assert r.total_cents == 18450
        assert r.currency == "USD"
        assert len(r.fees_and_taxes) == 3
        assert r.los_discount is not None
        assert r.los_discount.min_nights == 7
        assert r.los_discount.discount_percent == 15
        assert r.los_discount.discount_amount.amount_cents == 2250

    def test_invalidation_then_re_cache(self):
        """After invalidation, re-caching and reading works correctly."""
        fake_redis = FakeRedis()
        engine = PricingCacheEngine(config=self.config, redis_client=fake_redis)

        # Cache original data
        rate_v1 = make_nightly_rate("2025-07-01", base_rate_cents=10000, total_cents=10000)
        engine.cache_rates(VALID_UNIT_ID, [rate_v1])

        # Invalidate
        engine.invalidate_unit(VALID_UNIT_ID)

        # Re-cache with new data
        rate_v2 = make_nightly_rate("2025-07-01", base_rate_cents=20000, total_cents=20000)
        engine.cache_rates(VALID_UNIT_ID, [rate_v2])

        result = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        assert result.hit is True
        assert result.data[0].base_rate.amount_cents == 20000, (
            "After invalidation + re-cache, new data should be returned"
        )

    def test_different_units_isolated(self):
        """Cached data for different units is isolated."""
        fake_redis = FakeRedis()
        engine = PricingCacheEngine(config=self.config, redis_client=fake_redis)

        rate1 = make_nightly_rate("2025-07-01", base_rate_cents=10000, total_cents=10000)
        rate2 = make_nightly_rate("2025-07-01", base_rate_cents=20000, total_cents=20000)

        engine.cache_rates(VALID_UNIT_ID, [rate1])
        engine.cache_rates(VALID_UNIT_ID_2, [rate2])

        result1 = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        result2 = engine.get_cached_rates(
            VALID_UNIT_ID_2, make_date_range("2025-07-01", "2025-07-02")
        )

        assert result1.hit is True
        assert result2.hit is True
        assert result1.data[0].base_rate.amount_cents == 10000
        assert result2.data[0].base_rate.amount_cents == 20000

    def test_invalidate_one_unit_does_not_affect_another(self):
        """Invalidating one unit does not clear another unit's cache."""
        fake_redis = FakeRedis()
        engine = PricingCacheEngine(config=self.config, redis_client=fake_redis)

        rate1 = make_nightly_rate("2025-07-01", base_rate_cents=10000, total_cents=10000)
        rate2 = make_nightly_rate("2025-07-01", base_rate_cents=20000, total_cents=20000)

        engine.cache_rates(VALID_UNIT_ID, [rate1])
        engine.cache_rates(VALID_UNIT_ID_2, [rate2])

        # Invalidate only unit 1
        engine.invalidate_unit(VALID_UNIT_ID)

        result1 = engine.get_cached_rates(
            VALID_UNIT_ID, make_date_range("2025-07-01", "2025-07-02")
        )
        result2 = engine.get_cached_rates(
            VALID_UNIT_ID_2, make_date_range("2025-07-01", "2025-07-02")
        )

        assert result1.hit is False, "Unit 1 should be invalidated"
        assert result2.hit is True, "Unit 2 should still be cached"
        assert result2.data[0].base_rate.amount_cents == 20000
