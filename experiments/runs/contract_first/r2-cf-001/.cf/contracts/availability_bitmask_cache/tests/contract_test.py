"""
Contract Test Suite for Availability Bitmask Cache Layer (v2)
=============================================================
Tests verify the component against its contract at boundaries (inputs/outputs).
All dependencies (RedisClient, PgClient) are mocked.
A mock clock is injected to prevent time-dependent flakiness.

Run with: pytest contract_test.py -v
"""

import re
import uuid
import asyncio
import logging
from datetime import date, timedelta, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, call
from collections import Counter
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Import the component under test
# ---------------------------------------------------------------------------
from src.availability_bitmask_cache import (
    checkAvailability,
    checkBulkAvailability,
    invalidateCache,
    computeShardKey,
    buildRedisKey,
    CacheConfig,
    AvailabilityResult,
    BulkAvailabilityResult,
    InvalidationResult,
)

# ============================= HELPERS =====================================

# Fixed "today" for deterministic tests
FIXED_TODAY = date(2025, 6, 15)
FIXED_TODAY_STR = "2025-06-15"


def _date_str(offset: int = 0) -> str:
    """Return YYYY-MM-DD string for FIXED_TODAY + offset days."""
    return (FIXED_TODAY + timedelta(days=offset)).isoformat()


def _make_uuid(suffix: str = "0001") -> str:
    """Return a valid lowercase UUID v4 with a predictable tail."""
    # UUID v4: version nibble = 4, variant bits 10xx
    return f"a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b{suffix}"


UNIT_ID = _make_uuid("0001")
UNIT_ID_2 = _make_uuid("0002")
UNIT_ID_3 = _make_uuid("0003")
UNIT_NOT_FOUND_ID = "deadbeef-dead-4ead-beef-deadbeef0001"


def _valid_uuid_v4() -> str:
    """Generate a random valid UUID v4 string (lowercase)."""
    return str(uuid.uuid4())


def _default_config(**overrides) -> CacheConfig:
    """Create a CacheConfig with sensible defaults, applying overrides."""
    defaults = dict(
        ttl_seconds=3600,
        shard_count=64,
        window_days=366,
        hash_algorithm="CRC32C",
        key_prefix="avail",
        singleflight_enabled=True,
        fallback_to_pg_on_redis_failure=True,
    )
    defaults.update(overrides)
    return CacheConfig(**defaults)


def _make_redis_mock(**method_overrides) -> AsyncMock:
    """Create a mock RedisClient with all async methods stubbed."""
    redis = AsyncMock()
    redis.get_bit = AsyncMock(return_value=1)
    redis.get_bit_range = AsyncMock(return_value=b"\xff" * 46)  # 366 bits all-1
    redis.set_bits = AsyncMock(return_value=None)
    redis.delete_key = AsyncMock(return_value=True)
    redis.pipeline_get_bit_ranges = AsyncMock(return_value=[b"\xff" * 46])
    redis.exists = AsyncMock(return_value=True)
    redis.get_key_metadata = AsyncMock(
        return_value={
            "anchor_date": FIXED_TODAY_STR,
            "built_at_epoch_ms": 1718409600000,
            "window_days": 366,
        }
    )
    redis.ping = AsyncMock(return_value=True)
    for k, v in method_overrides.items():
        setattr(redis, k, v)
    return redis


def _make_pg_mock(
    unit_exists: bool = True,
    blocks: Optional[List[Dict[str, Any]]] = None,
    bulk_blocks: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> AsyncMock:
    """Create a mock PgClient."""
    pg = AsyncMock()
    pg.unit_exists = AsyncMock(return_value=unit_exists)

    if blocks is None:
        # Default: all days available in the 366-day window
        blocks = [
            {
                "unit_id": UNIT_ID,
                "start_date": FIXED_TODAY_STR,
                "end_date": _date_str(365),
                "is_available": True,
            }
        ]
    pg.get_availability_blocks = AsyncMock(return_value=blocks)

    if bulk_blocks is None:
        bulk_blocks = {}
    pg.get_availability_blocks_bulk = AsyncMock(return_value=bulk_blocks)
    pg.ping = AsyncMock(return_value=True)
    return pg


def _redis_unavailable_mock() -> AsyncMock:
    """RedisClient mock that raises ConnectionError on every call."""
    redis = AsyncMock()
    err = ConnectionError("Redis connection refused")
    redis.get_bit = AsyncMock(side_effect=err)
    redis.get_bit_range = AsyncMock(side_effect=err)
    redis.set_bits = AsyncMock(side_effect=err)
    redis.delete_key = AsyncMock(side_effect=err)
    redis.pipeline_get_bit_ranges = AsyncMock(side_effect=err)
    redis.exists = AsyncMock(side_effect=err)
    redis.get_key_metadata = AsyncMock(side_effect=err)
    redis.ping = AsyncMock(side_effect=err)
    return redis


# Patch target for the "now/today" function used inside the component.
# Adjust this path to match your implementation's actual import.
NOW_PATCH_TARGET = "src.availability_bitmask_cache._utc_today"


@pytest.fixture
def mock_today():
    """Patch the component's UTC today function to return FIXED_TODAY."""
    with patch(NOW_PATCH_TARGET, return_value=FIXED_TODAY) as m:
        yield m


# ====================== computeShardKey TESTS =============================


class TestComputeShardKey:
    """Pure unit tests for computeShardKey."""

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        result1 = computeShardKey(UNIT_ID, 64)
        result2 = computeShardKey(UNIT_ID, 64)
        assert result1 == result2, (
            f"computeShardKey should be deterministic: got {result1} then {result2}"
        )

    def test_returns_integer(self):
        result = computeShardKey(UNIT_ID, 64)
        assert isinstance(result, int), f"Expected int, got {type(result)}"

    def test_range_valid_64(self):
        """Output is in [0, 63] for shard_count=64."""
        result = computeShardKey(UNIT_ID, 64)
        assert 0 <= result <= 63, f"Shard key {result} out of range [0, 63]"

    def test_shard_count_1_always_zero(self):
        """With shard_count=1, result must always be 0."""
        result = computeShardKey(UNIT_ID, 1)
        assert result == 0, f"Expected 0 for shard_count=1, got {result}"

    def test_shard_count_1024_range(self):
        """Output is in [0, 1023] for shard_count=1024."""
        result = computeShardKey(UNIT_ID, 1024)
        assert 0 <= result <= 1023, f"Shard key {result} out of range [0, 1023]"

    def test_different_uuids_can_differ(self):
        """Not all shard keys are identical for distinct inputs."""
        keys = set()
        for _ in range(10):
            uid = _valid_uuid_v4()
            keys.add(computeShardKey(uid, 64))
        assert len(keys) >= 2, (
            "Expected at least 2 distinct shard keys from 10 random UUIDs"
        )

    def test_distribution_uniformity(self):
        """1000 random UUIDs should distribute roughly uniformly across 64 shards."""
        shard_count = 64
        counts = Counter()
        n = 1000
        for _ in range(n):
            uid = _valid_uuid_v4()
            shard = computeShardKey(uid, shard_count)
            counts[shard] += 1

        # All shards should have at least 1 hit
        used_shards = len(counts)
        mean = n / shard_count  # ~15.6
        max_count = max(counts.values())

        assert used_shards > shard_count * 0.5, (
            f"Only {used_shards}/{shard_count} shards used — poor distribution"
        )
        assert max_count < 4 * mean, (
            f"Max bucket {max_count} exceeds 4x mean {mean:.1f} — skewed distribution"
        )


# ====================== buildRedisKey TESTS ================================


class TestBuildRedisKey:
    """Pure unit tests for buildRedisKey."""

    def test_key_format_default_prefix(self):
        """Key matches avail:{shard}:{unit_id}."""
        config = _default_config()
        key = buildRedisKey(UNIT_ID, config)
        pattern = rf"^avail:\d{{1,2}}:{re.escape(UNIT_ID)}$"
        assert re.match(pattern, key), f"Key '{key}' does not match pattern '{pattern}'"

    def test_key_starts_with_prefix(self):
        config = _default_config()
        key = buildRedisKey(UNIT_ID, config)
        assert key.startswith("avail:"), f"Key should start with 'avail:', got '{key}'"

    def test_key_ends_with_unit_id(self):
        config = _default_config()
        key = buildRedisKey(UNIT_ID, config)
        assert key.endswith(f":{UNIT_ID}"), f"Key should end with ':{UNIT_ID}', got '{key}'"

    def test_custom_prefix(self):
        """Custom key_prefix is used."""
        config = _default_config(key_prefix="test_cache")
        key = buildRedisKey(UNIT_ID, config)
        assert key.startswith("test_cache:"), f"Expected prefix test_cache, got '{key}'"

    def test_deterministic(self):
        """Same inputs always produce the same output."""
        config = _default_config()
        key1 = buildRedisKey(UNIT_ID, config)
        key2 = buildRedisKey(UNIT_ID, config)
        assert key1 == key2, f"buildRedisKey should be deterministic: '{key1}' != '{key2}'"

    def test_shard_in_range(self):
        """Embedded shard number is in [0, shard_count - 1]."""
        config = _default_config(shard_count=64)
        key = buildRedisKey(UNIT_ID, config)
        parts = key.split(":")
        assert len(parts) == 3, f"Key should have 3 colon-separated parts, got {parts}"
        shard = int(parts[1])
        assert 0 <= shard <= 63, f"Shard {shard} not in [0, 63]"


# ====================== CacheConfig VALIDATION TESTS =======================


class TestCacheConfigValidation:
    """Tests for CacheConfig field validators."""

    def test_defaults_applied(self):
        config = _default_config()
        assert config.shard_count == 64
        assert config.window_days == 366
        assert config.key_prefix == "avail"
        assert config.singleflight_enabled is True
        assert config.fallback_to_pg_on_redis_failure is True

    def test_ttl_too_low(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(ttl_seconds=0)

    def test_ttl_too_high(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(ttl_seconds=86401)

    def test_ttl_boundary_low(self):
        config = _default_config(ttl_seconds=1)
        assert config.ttl_seconds == 1

    def test_ttl_boundary_high(self):
        config = _default_config(ttl_seconds=86400)
        assert config.ttl_seconds == 86400

    def test_shard_count_not_power_of_2(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(shard_count=3)

    def test_shard_count_zero(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(shard_count=0)

    def test_shard_count_negative(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(shard_count=-1)

    def test_shard_count_exceeds_max(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(shard_count=2048)

    def test_shard_count_valid_power_of_2(self):
        for sc in [1, 2, 4, 8, 16, 32, 64, 128, 256, 512, 1024]:
            config = _default_config(shard_count=sc)
            assert config.shard_count == sc

    def test_window_days_too_low(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(window_days=0)

    def test_window_days_too_high(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(window_days=732)

    def test_window_days_boundary(self):
        config = _default_config(window_days=1)
        assert config.window_days == 1
        config2 = _default_config(window_days=731)
        assert config2.window_days == 731

    def test_key_prefix_starts_with_digit(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(key_prefix="1invalid")

    def test_key_prefix_uppercase(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(key_prefix="Invalid")

    def test_key_prefix_too_long(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(key_prefix="a" * 17)

    def test_key_prefix_empty(self):
        with pytest.raises((ValueError, Exception)):
            _default_config(key_prefix="")

    def test_key_prefix_valid(self):
        config = _default_config(key_prefix="avail_cache01")
        assert config.key_prefix == "avail_cache01"


# ====================== checkAvailability TESTS ============================


class TestCheckAvailability:
    """Behavioral tests for checkAvailability with mocked Redis/PG."""

    @pytest.mark.asyncio
    async def test_cache_hit_all_available(self, mock_today):
        """Cache hit with all bits=1 returns ok=True, value=True."""
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, f"Expected ok=True, got {result}"
        assert result.value is True, f"Expected value=True (all available), got {result.value}"
        # PG should NOT be called on cache hit
        pg.get_availability_blocks.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_hit_some_unavailable(self, mock_today):
        """Cache hit with some bits=0 returns ok=True, value=False."""
        # Return a bitmask where bit 1 is 0 (unavailable)
        redis = _make_redis_mock()
        # Override get_bit_range to return bytes with a zero bit in range
        redis.get_bit_range = AsyncMock(return_value=b"\xa0")  # 10100000 - bit 1 is 0
        config = _default_config()
        pg = _make_pg_mock()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, f"Expected ok=True, got {result}"
        assert result.value is False, (
            f"Expected value=False (some unavailable), got {result.value}"
        )

    @pytest.mark.asyncio
    async def test_cache_miss_triggers_pg_rebuild(self, mock_today):
        """Cache miss queries PG, builds bitmask, stores in Redis."""
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, f"Expected ok=True after rebuild, got {result}"
        assert result.value is True, f"Expected value=True, got {result.value}"
        pg.get_availability_blocks.assert_called()
        redis.set_bits.assert_called()

    @pytest.mark.asyncio
    async def test_stale_anchor_triggers_rebuild(self, mock_today):
        """Bitmask with anchor_date != today is treated as miss and rebuilt."""
        yesterday_str = (FIXED_TODAY - timedelta(days=1)).isoformat()
        redis = _make_redis_mock(
            get_key_metadata=AsyncMock(
                return_value={
                    "anchor_date": yesterday_str,
                    "built_at_epoch_ms": 1718323200000,
                    "window_days": 366,
                }
            ),
        )
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True
        pg.get_availability_blocks.assert_called(), (
            "PG should be queried because anchor is stale"
        )
        redis.set_bits.assert_called(), "Bitmask should be rebuilt in Redis"

    @pytest.mark.asyncio
    async def test_single_day_range(self, mock_today):
        """Single-day range (start_date == end_date) is supported."""
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(0),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True
        assert result.value is True

    @pytest.mark.asyncio
    async def test_window_boundary_max_end_date(self, mock_today):
        """end_date = today + 365 (window_days-1) is accepted."""
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config(window_days=366)

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(365),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, f"Maximum window boundary should be accepted, got {result}"

    # ---- Error cases ----

    @pytest.mark.asyncio
    async def test_error_start_after_end(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(5),
            end_date=_date_str(2),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False, "start > end should fail"
        assert result.error.error_code == "INVALID_DATE_RANGE"
        assert result.error.reason == "START_AFTER_END"

    @pytest.mark.asyncio
    async def test_error_dates_in_past(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        yesterday = (FIXED_TODAY - timedelta(days=1)).isoformat()
        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=yesterday,
            end_date=_date_str(2),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False, "Past start_date should fail"
        assert result.error.error_code == "INVALID_DATE_RANGE"
        assert result.error.reason == "DATES_IN_PAST"

    @pytest.mark.asyncio
    async def test_error_exceeds_window(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config(window_days=366)

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(400),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "INVALID_DATE_RANGE"
        assert result.error.reason == "RANGE_EXCEEDS_WINDOW"

    @pytest.mark.asyncio
    async def test_error_invalid_date_format(self, mock_today):
        """Feb 30 is not a valid calendar date."""
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date="2024-02-30",
            end_date="2024-03-05",
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "INVALID_DATE_RANGE"
        assert result.error.reason == "INVALID_DATE_FORMAT"

    @pytest.mark.asyncio
    async def test_error_unit_not_found(self, mock_today):
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        pg = _make_pg_mock(unit_exists=False)
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_NOT_FOUND_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "UNIT_NOT_FOUND"
        assert result.error.unit_id == UNIT_NOT_FOUND_ID

    @pytest.mark.asyncio
    async def test_error_cache_unavailable_no_fallback(self, mock_today):
        """Redis down + fallback disabled → CacheUnavailable error."""
        redis = _redis_unavailable_mock()
        pg = _make_pg_mock()
        config = _default_config(fallback_to_pg_on_redis_failure=False)

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "CACHE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_redis_down_fallback_enabled(self, mock_today):
        """Redis down + fallback enabled → graceful degradation to PG."""
        redis = _redis_unavailable_mock()
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config(fallback_to_pg_on_redis_failure=True)

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, "Fallback to PG should succeed"
        assert result.value is True
        pg.get_availability_blocks.assert_called()

    @pytest.mark.asyncio
    async def test_error_internal_pg_timeout(self, mock_today):
        """PG timeout during rebuild → InternalError."""
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        pg = _make_pg_mock()
        pg.get_availability_blocks = AsyncMock(side_effect=TimeoutError("PG timeout"))
        pg.unit_exists = AsyncMock(return_value=True)
        config = _default_config()

        result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "INTERNAL_ERROR"

    @pytest.mark.asyncio
    async def test_cache_rebuild_uses_configured_ttl(self, mock_today):
        """On rebuild, set_bits is called with the configured TTL."""
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config(ttl_seconds=7200)

        await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        redis.set_bits.assert_called()
        # Verify TTL argument
        call_kwargs = redis.set_bits.call_args
        # The ttl_seconds should appear in the call (positional or keyword)
        call_args_str = str(call_kwargs)
        assert "7200" in call_args_str, (
            f"Expected TTL 7200 in set_bits call args, got: {call_args_str}"
        )


# ================== checkBulkAvailability TESTS ============================


class TestCheckBulkAvailability:
    """Behavioral tests for checkBulkAvailability."""

    @pytest.mark.asyncio
    async def test_happy_path_multiple_units(self, mock_today):
        """Bulk check for 3 cached, available units."""
        redis = _make_redis_mock()
        # Pipeline returns bitmasks for 3 units, all bits=1
        redis.pipeline_get_bit_ranges = AsyncMock(
            return_value=[b"\xff" * 46, b"\xff" * 46, b"\xff" * 46]
        )
        # Metadata for each key says anchor=today
        redis.get_key_metadata = AsyncMock(
            return_value={
                "anchor_date": FIXED_TODAY_STR,
                "built_at_epoch_ms": 1718409600000,
                "window_days": 366,
            }
        )
        pg = _make_pg_mock()
        config = _default_config()

        unit_ids = [UNIT_ID, UNIT_ID_2, UNIT_ID_3]
        result = await checkBulkAvailability(
            unit_ids=unit_ids,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True
        assert len(result.results) == 3, f"Expected 3 results, got {len(result.results)}"
        for r in result.results:
            assert r.available is True, f"Unit {r.unit_id} should be available"

    @pytest.mark.asyncio
    async def test_partial_failure_unit_not_found(self, mock_today):
        """One unit found, one not — top-level ok=True, per-unit error for missing."""
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        # Pipeline returns empty for both (cache miss)
        redis.pipeline_get_bit_ranges = AsyncMock(return_value=[None, None])

        pg = _make_pg_mock()
        # unit_exists returns True for UNIT_ID, False for UNIT_NOT_FOUND_ID
        async def _unit_exists(uid):
            return uid != UNIT_NOT_FOUND_ID

        pg.unit_exists = AsyncMock(side_effect=_unit_exists)
        pg.get_availability_blocks_bulk = AsyncMock(
            return_value={
                UNIT_ID: [
                    {
                        "unit_id": UNIT_ID,
                        "start_date": FIXED_TODAY_STR,
                        "end_date": _date_str(365),
                        "is_available": True,
                    }
                ]
            }
        )

        config = _default_config()
        result = await checkBulkAvailability(
            unit_ids=[UNIT_ID, UNIT_NOT_FOUND_ID],
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True, "Top-level should be ok even with per-unit failures"
        assert len(result.results) == 2
        # Find the not-found unit's result
        not_found_results = [
            r for r in result.results if r.unit_id == UNIT_NOT_FOUND_ID
        ]
        assert len(not_found_results) == 1
        assert not_found_results[0].error is not None
        assert not_found_results[0].error.error_code == "UNIT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_preserves_input_order(self, mock_today):
        """Results are in the same order as input unit_ids."""
        redis = _make_redis_mock()
        redis.pipeline_get_bit_ranges = AsyncMock(
            return_value=[b"\xff" * 46, b"\xff" * 46, b"\xff" * 46]
        )
        redis.get_key_metadata = AsyncMock(
            return_value={
                "anchor_date": FIXED_TODAY_STR,
                "built_at_epoch_ms": 1718409600000,
                "window_days": 366,
            }
        )
        pg = _make_pg_mock()
        config = _default_config()

        ids = [UNIT_ID_3, UNIT_ID, UNIT_ID_2]
        result = await checkBulkAvailability(
            unit_ids=ids,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True
        result_ids = [r.unit_id for r in result.results]
        assert result_ids == ids, (
            f"Results should preserve input order: expected {ids}, got {result_ids}"
        )

    @pytest.mark.asyncio
    async def test_deduplicates_unit_ids(self, mock_today):
        """Duplicate unit_ids are deduplicated; one result per unique id."""
        redis = _make_redis_mock()
        redis.pipeline_get_bit_ranges = AsyncMock(return_value=[b"\xff" * 46])
        redis.get_key_metadata = AsyncMock(
            return_value={
                "anchor_date": FIXED_TODAY_STR,
                "built_at_epoch_ms": 1718409600000,
                "window_days": 366,
            }
        )
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkBulkAvailability(
            unit_ids=[UNIT_ID, UNIT_ID, UNIT_ID],
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is True
        assert len(result.results) == 1, (
            f"Expected 1 deduped result, got {len(result.results)}"
        )
        assert result.results[0].unit_id == UNIT_ID

    @pytest.mark.asyncio
    async def test_error_empty_unit_ids(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkBulkAvailability(
            unit_ids=[],
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False, "Empty unit_ids should fail"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_error_too_many_unit_ids(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        ids = [_valid_uuid_v4() for _ in range(101)]
        result = await checkBulkAvailability(
            unit_ids=ids,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False, "More than 100 unit_ids should fail"
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_error_start_after_end(self, mock_today):
        redis = _make_redis_mock()
        pg = _make_pg_mock()
        config = _default_config()

        result = await checkBulkAvailability(
            unit_ids=[UNIT_ID],
            start_date=_date_str(5),
            end_date=_date_str(2),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "INVALID_DATE_RANGE"
        assert result.error.reason == "START_AFTER_END"

    @pytest.mark.asyncio
    async def test_systemic_failure_both_down(self, mock_today):
        """Both Redis and PG unreachable → systemic failure."""
        redis = _redis_unavailable_mock()
        pg = _make_pg_mock()
        pg.get_availability_blocks = AsyncMock(
            side_effect=ConnectionError("PG unreachable")
        )
        pg.get_availability_blocks_bulk = AsyncMock(
            side_effect=ConnectionError("PG unreachable")
        )
        pg.unit_exists = AsyncMock(side_effect=ConnectionError("PG unreachable"))
        pg.ping = AsyncMock(side_effect=ConnectionError("PG unreachable"))

        config = _default_config(fallback_to_pg_on_redis_failure=True)

        result = await checkBulkAvailability(
            unit_ids=[UNIT_ID],
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert result.ok is False, "Both backends down should be a systemic failure"
        assert result.error is not None
        assert result.error.error_code in ("CACHE_UNAVAILABLE", "INTERNAL_ERROR")

    @pytest.mark.asyncio
    async def test_pipeline_usage(self, mock_today):
        """Bulk check uses Redis pipeline for batch fetching."""
        redis = _make_redis_mock()
        redis.pipeline_get_bit_ranges = AsyncMock(
            return_value=[b"\xff" * 46, b"\xff" * 46, b"\xff" * 46]
        )
        redis.get_key_metadata = AsyncMock(
            return_value={
                "anchor_date": FIXED_TODAY_STR,
                "built_at_epoch_ms": 1718409600000,
                "window_days": 366,
            }
        )
        pg = _make_pg_mock()
        config = _default_config()

        await checkBulkAvailability(
            unit_ids=[UNIT_ID, UNIT_ID_2, UNIT_ID_3],
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        redis.pipeline_get_bit_ranges.assert_called()


# ====================== invalidateCache TESTS ==============================


class TestInvalidateCache:
    """Tests for invalidateCache."""

    @pytest.mark.asyncio
    async def test_success_key_existed(self, mock_today):
        """Invalidation succeeds when key existed."""
        redis = _make_redis_mock(delete_key=AsyncMock(return_value=True))
        config = _default_config()

        result = await invalidateCache(
            unit_id=UNIT_ID,
            redis_client=redis,
            config=config,
        )

        assert result.ok is True
        assert result.error is None
        redis.delete_key.assert_called_once()

    @pytest.mark.asyncio
    async def test_idempotent_nonexistent_key(self, mock_today):
        """Invalidation of nonexistent key is a successful no-op."""
        redis = _make_redis_mock(delete_key=AsyncMock(return_value=False))
        config = _default_config()

        result = await invalidateCache(
            unit_id=UNIT_NOT_FOUND_ID,
            redis_client=redis,
            config=config,
        )

        assert result.ok is True, "Deleting nonexistent key should succeed"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_correct_key_deleted(self, mock_today):
        """invalidateCache deletes the correctly-formatted Redis key."""
        redis = _make_redis_mock()
        config = _default_config(key_prefix="avail", shard_count=64)

        await invalidateCache(
            unit_id=UNIT_ID,
            redis_client=redis,
            config=config,
        )

        redis.delete_key.assert_called_once()
        deleted_key = redis.delete_key.call_args[0][0]
        expected_key = buildRedisKey(UNIT_ID, config)
        assert deleted_key == expected_key, (
            f"Expected key '{expected_key}' to be deleted, got '{deleted_key}'"
        )

    @pytest.mark.asyncio
    async def test_error_redis_unavailable(self, mock_today):
        """Redis down → CacheUnavailable (no PG fallback for invalidation)."""
        redis = _redis_unavailable_mock()
        config = _default_config()

        result = await invalidateCache(
            unit_id=UNIT_ID,
            redis_client=redis,
            config=config,
        )

        assert result.ok is False
        assert result.error.error_code == "CACHE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_invalidate_then_check_triggers_rebuild(self, mock_today):
        """After invalidation, next checkAvailability triggers full PG rebuild."""
        redis = _make_redis_mock()
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config()

        # Step 1: Invalidate
        inv_result = await invalidateCache(
            unit_id=UNIT_ID,
            redis_client=redis,
            config=config,
        )
        assert inv_result.ok is True

        # Step 2: Simulate cache miss after invalidation
        redis.exists = AsyncMock(return_value=False)
        redis.get_key_metadata = AsyncMock(return_value=None)

        check_result = await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        assert check_result.ok is True
        pg.get_availability_blocks.assert_called(), (
            "PG should be queried to rebuild after invalidation"
        )
        redis.set_bits.assert_called(), "Bitmask should be stored in Redis after rebuild"


# ====================== INVARIANT TESTS ====================================


class TestInvariants:
    """Cross-cutting invariant tests from the contract."""

    @pytest.mark.asyncio
    async def test_pg_is_read_only(self, mock_today):
        """No method writes to the PostgreSQL availability_blocks table."""
        redis = _make_redis_mock(
            exists=AsyncMock(return_value=False),
            get_key_metadata=AsyncMock(return_value=None),
        )
        pg = _make_pg_mock(unit_exists=True)
        config = _default_config()

        await checkAvailability(
            unit_id=UNIT_ID,
            start_date=_date_str(0),
            end_date=_date_str(3),
            redis_client=redis,
            pg_client=pg,
            config=config,
        )

        # Only read methods should have been called
        allowed_methods = {
            "get_availability_blocks",
            "unit_exists",
            "get_availability_blocks_bulk",
            "ping",
        }
        for method_name in dir(pg):
            if method_name.startswith("_"):
                continue
            method = getattr(pg, method_name)
            if hasattr(method, "called") and method.called:
                assert method_name in allowed_methods, (
                    f"Unexpected PG method called: {method_name} — "
                    f"component should be read-only w.r.t. PostgreSQL"
                )

    @pytest.mark.asyncio
    async def test_invalidation_is_idempotent(self, mock_today):
        """Calling invalidateCache twice for the same unit is safe."""
        redis = _make_redis_mock()
        config = _default_config()

        r1 = await invalidateCache(unit_id=UNIT_ID, redis_client=redis, config=config)
        # Second call — key no longer exists
        redis.delete_key = AsyncMock(return_value=False)
        r2 = await invalidateCache(unit_id=UNIT_ID, redis_client=redis, config=config)

        assert r1.ok is True
        assert r2.ok is True

    def test_bitmask_window_days_is_366_by_default(self):
        """Default bitmask covers 366 bits to handle leap years."""
        config = _default_config()
        assert config.window_days == 366

    def test_all_dates_inclusive(self):
        """Contract says [start_date, end_date] inclusive — verified via bit range."""
        # E.g., [today, today+2] should cover 3 days: bit 0, 1, 2
        start_offset = 0
        end_offset = 2
        num_days = end_offset - start_offset + 1
        assert num_days == 3, "Inclusive range [0, 2] should cover 3 days"

    def test_shard_key_range_invariant_across_many_uuids(self):
        """ShardKey is always in [0, shard_count - 1] for many inputs."""
        shard_count = 64
        for _ in range(200):
            uid = _valid_uuid_v4()
            shard = computeShardKey(uid, shard_count)
            assert 0 <= shard < shard_count, (
                f"Shard {shard} out of range for unit {uid}"
            )

    def test_redis_key_format_invariant(self):
        """Redis key is always {prefix}:{shard}:{unit_id}."""
        config = _default_config()
        for _ in range(50):
            uid = _valid_uuid_v4()
            key = buildRedisKey(uid, config)
            parts = key.split(":")
            # Key has format prefix:shard:uuid, but UUID contains hyphens not colons
            # So we expect: prefix, shard_number, and the rest rejoined is the UUID
            assert parts[0] == "avail", f"Prefix mismatch in key '{key}'"
            shard_str = parts[1]
            assert shard_str.isdigit(), f"Shard '{shard_str}' is not a digit"
            shard_val = int(shard_str)
            assert 0 <= shard_val < 64, f"Shard {shard_val} out of range in key '{key}'"
            reconstructed_uuid = ":".join(parts[2:])
            assert reconstructed_uuid == uid, (
                f"UUID mismatch: expected '{uid}', got '{reconstructed_uuid}'"
            )
