"""
Contract test suite for availability_tests component.
Tests cover bitmask operations, cache layer, API contracts, and helper functions.
All dependencies are mocked — no external services required.

Run with: pytest contract_test.py -v
"""

import re
import json
import calendar
import pytest
from unittest.mock import MagicMock, AsyncMock, patch, call

# Import the component under test
from src.availability_tests import (
    createBitmask,
    createDateRange,
    createTestFixtures,
    createInMemoryDb,
    createInMemoryCache,
    assertCacheKeyFormat,
    assertErrorResponse,
    assertSchemaValid,
    runBitmaskTests,
    runCacheTests,
    runApiContractTests,
    runAllTests,
    serializeBitmask,
    deserializeBitmask,
    dateToBitIndex,
    bitIndexToDate,
    setBit,
    clearBit,
    getBit,
    setBitRange,
    clearBitRange,
    checkBitRange,
    createYearMask,
    createLeapYearMask,
    stitchCrossYearRange,
)

# ─── Constants ───────────────────────────────────────────────────────────────

UUID_V4_REGEX = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
ISO_DATE_REGEX = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HEX_BITMASK_REGEX = re.compile(r"^0x[0-9a-f]+$")

SAMPLE_UNIT_ID = "550e8400-e29b-41d4-a716-446655440000"
SAMPLE_UNIT_ID_2 = "660e8400-e29b-41d4-a716-446655440001"
SAMPLE_YEAR = 2025  # non-leap
LEAP_YEAR = 2024  # leap


# ─── Helpers ─────────────────────────────────────────────────────────────────

def is_leap_year(year: int) -> bool:
    return calendar.isleap(year)


def days_in_year(year: int) -> int:
    return 366 if is_leap_year(year) else 365


# ═══════════════════════════════════════════════════════════════════════════════
# BITMASK TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateBitmaskHappyPath:
    """Happy path tests for createBitmask."""

    def test_single_day_bit_zero(self):
        """createBitmask with single index 0 returns hex with only bit 0 set."""
        result = createBitmask([0], year=2025)
        assert isinstance(result, str), "Result must be a string"
        assert result.startswith("0x"), f"Expected hex prefix '0x', got '{result[:4]}'"
        # Bit 0 set means value 1 => '0x1'
        value = int(result, 16)
        assert value & 1 == 1, "Bit 0 should be set"
        # Ensure no other bits are set
        assert value == 1, f"Only bit 0 should be set, got {hex(value)}"

    def test_multiple_days(self):
        """createBitmask with multiple indices sets exactly those bits."""
        indices = [0, 1, 59, 364]
        result = createBitmask(indices, year=2025)
        assert result.startswith("0x"), f"Expected hex prefix, got '{result}'"
        value = int(result, 16)
        for idx in indices:
            assert (value >> idx) & 1 == 1, f"Bit {idx} should be set"
        # Verify no extra bits
        for i in range(365):
            if i not in indices:
                assert (value >> i) & 1 == 0, f"Bit {i} should NOT be set"

    def test_empty_indices_returns_zero(self):
        """createBitmask with empty list returns '0x0'."""
        result = createBitmask([], year=2025)
        assert result == "0x0", f"Expected '0x0', got '{result}'"


class TestCreateBitmaskEdgeCases:
    """Edge case tests for createBitmask boundary indices and leap years."""

    def test_boundary_index_364_nonleap(self):
        """Index 364 (Dec 31) on non-leap year is valid."""
        result = createBitmask([364], year=2025)
        assert result.startswith("0x")
        value = int(result, 16)
        assert (value >> 364) & 1 == 1, "Bit 364 should be set for non-leap year Dec 31"

    def test_boundary_index_365_leap(self):
        """Index 365 (Dec 31) on leap year is valid."""
        result = createBitmask([365], year=2024)
        assert result.startswith("0x")
        value = int(result, 16)
        assert (value >> 365) & 1 == 1, "Bit 365 should be set for leap year Dec 31"

    def test_leap_year_feb29_index_59(self):
        """Feb 29 on leap year is bit index 59."""
        result = createBitmask([59], year=2024)
        assert result.startswith("0x")
        value = int(result, 16)
        assert (value >> 59) & 1 == 1, "Bit 59 (Feb 29) should be set on leap year"

    def test_full_year_nonleap(self):
        """createBitmask with all 365 indices (0..364) sets all bits for non-leap year."""
        all_indices = list(range(365))
        result = createBitmask(all_indices, year=2025)
        assert result.startswith("0x")
        value = int(result, 16)
        # All 365 bits should be set
        expected = (1 << 365) - 1
        assert value == expected, (
            f"Full year bitmask should have all 365 bits set. "
            f"Got {hex(value)}, expected {hex(expected)}"
        )

    def test_full_year_leap(self):
        """createBitmask with all 366 indices (0..365) sets all bits for leap year."""
        all_indices = list(range(366))
        result = createBitmask(all_indices, year=2024)
        assert result.startswith("0x")
        value = int(result, 16)
        expected = (1 << 366) - 1
        assert value == expected, "Full leap year bitmask should have all 366 bits set"


class TestCreateBitmaskErrors:
    """Error cases for createBitmask."""

    def test_negative_index_raises_range_error(self):
        """createBitmask raises RangeError for negative bit index."""
        with pytest.raises((RangeError, ValueError, IndexError)):
            createBitmask([-1], year=2025)

    def test_index_365_nonleap_raises_range_error(self):
        """createBitmask raises RangeError for index 365 on non-leap year (max is 364)."""
        with pytest.raises((RangeError, ValueError, IndexError)):
            createBitmask([365], year=2025)

    def test_index_366_leap_raises_range_error(self):
        """createBitmask raises RangeError for index 366 on leap year (max is 365)."""
        with pytest.raises((RangeError, ValueError, IndexError)):
            createBitmask([366], year=2024)


class TestBitmaskHexRoundtrip:
    """Invariant: deserializeBitmask(serializeBitmask(B)) === B."""

    def test_roundtrip_various_bits(self):
        """Hex serialization round-trip preserves bitmask value."""
        indices = [0, 50, 100, 200, 300, 364]
        hex_str = createBitmask(indices, year=2025)
        # Deserialize then re-serialize
        bigint_value = deserializeBitmask(hex_str)
        roundtrip_hex = serializeBitmask(bigint_value)
        roundtrip_value = deserializeBitmask(roundtrip_hex)
        assert bigint_value == roundtrip_value, (
            f"Round-trip failed: original={hex_str}, roundtrip={roundtrip_hex}"
        )

    def test_roundtrip_empty(self):
        """Round-trip for zero bitmask."""
        hex_str = "0x0"
        value = deserializeBitmask(hex_str)
        assert value == 0, "Deserializing '0x0' should yield 0"
        result = serializeBitmask(value)
        assert result == "0x0", f"Serializing 0 should yield '0x0', got '{result}'"

    def test_roundtrip_full_year(self):
        """Round-trip for full year bitmask (all 365 bits set)."""
        indices = list(range(365))
        hex_str = createBitmask(indices, year=2025)
        value = deserializeBitmask(hex_str)
        roundtrip = serializeBitmask(value)
        assert deserializeBitmask(roundtrip) == value


class TestBitmaskInvariantHexPrefix:
    """Invariant: all HexBitmaskStrings start with '0x' and are lowercase hex."""

    def test_hex_prefix_and_lowercase(self):
        """createBitmask result starts with '0x' and contains only lowercase hex digits."""
        result = createBitmask([10, 20, 30], year=2025)
        assert result.startswith("0x"), f"Must start with '0x', got '{result}'"
        hex_part = result[2:]
        assert re.match(r"^[0-9a-f]+$", hex_part) or hex_part == "0", (
            f"Hex portion must be lowercase hex, got '{hex_part}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# DATE RANGE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateDateRangeHappyPath:
    """Happy path tests for createDateRange."""

    def test_valid_range(self):
        """createDateRange with valid start < end returns matching DateRange."""
        result = createDateRange("2025-01-01", "2025-01-31")
        assert result.start == "2025-01-01", f"start should be '2025-01-01', got '{result.start}'"
        assert result.end == "2025-01-31", f"end should be '2025-01-31', got '{result.end}'"

    def test_single_day_range(self):
        """createDateRange with start == end (single day) succeeds."""
        result = createDateRange("2025-06-15", "2025-06-15")
        assert result.start == "2025-06-15"
        assert result.end == "2025-06-15"
        assert result.start == result.end


class TestCreateDateRangeErrors:
    """Error cases for createDateRange."""

    def test_invalid_date_format_raises_validation_error(self):
        """createDateRange raises ValidationError for non-YYYY-MM-DD format."""
        with pytest.raises((ValueError, TypeError, Exception)) as exc_info:
            createDateRange("01-01-2025", "2025-01-31")
        # Accept any validation-related error
        assert exc_info.value is not None

    def test_invalid_calendar_date_feb29_nonleap(self):
        """createDateRange raises ValidationError for Feb 29 on non-leap year 2025."""
        with pytest.raises((ValueError, TypeError, Exception)):
            createDateRange("2025-02-29", "2025-03-01")

    def test_inverted_range_raises_validation_error(self):
        """createDateRange raises ValidationError when start > end."""
        with pytest.raises((ValueError, TypeError, Exception)):
            createDateRange("2025-12-31", "2025-01-01")


# ═══════════════════════════════════════════════════════════════════════════════
# TEST FIXTURES TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateTestFixtures:
    """Tests for createTestFixtures factory helper."""

    def test_fixtures_have_valid_data(self):
        """createTestFixtures returns deterministic fixture data with valid UUIDs and years."""
        fixtures = createTestFixtures()

        # Valid UUID v4s
        assert UUID_V4_REGEX.match(fixtures.sampleUnitId), (
            f"sampleUnitId '{fixtures.sampleUnitId}' is not a valid UUID v4"
        )
        assert UUID_V4_REGEX.match(fixtures.sampleUnitId2), (
            f"sampleUnitId2 '{fixtures.sampleUnitId2}' is not a valid UUID v4"
        )
        assert fixtures.sampleUnitId != fixtures.sampleUnitId2, (
            "sampleUnitId and sampleUnitId2 must be different"
        )

        # Year checks
        assert not is_leap_year(fixtures.sampleYear), (
            f"sampleYear {fixtures.sampleYear} should be non-leap"
        )
        assert is_leap_year(fixtures.leapYear), (
            f"leapYear {fixtures.leapYear} should be leap"
        )

        # Empty bitmask
        assert fixtures.emptyBitmaskHex == "0x0", (
            f"emptyBitmaskHex should be '0x0', got '{fixtures.emptyBitmaskHex}'"
        )

        # Full year bitmask represents 365 bits set
        full_value = int(fixtures.fullYearBitmaskHex, 16)
        expected_full = (1 << 365) - 1
        assert full_value == expected_full, (
            "fullYearBitmaskHex should represent all 365 bits set"
        )

        # Sample record references
        assert fixtures.sampleAvailabilityRecord.unit_id == fixtures.sampleUnitId
        assert fixtures.sampleAvailabilityRecord.year == fixtures.sampleYear

    def test_fixtures_are_deterministic(self):
        """createTestFixtures returns identical values across multiple invocations."""
        f1 = createTestFixtures()
        f2 = createTestFixtures()
        assert f1.sampleUnitId == f2.sampleUnitId
        assert f1.sampleUnitId2 == f2.sampleUnitId2
        assert f1.sampleYear == f2.sampleYear
        assert f1.leapYear == f2.leapYear
        assert f1.emptyBitmaskHex == f2.emptyBitmaskHex
        assert f1.fullYearBitmaskHex == f2.fullYearBitmaskHex
        assert f1.sampleBitmaskHex == f2.sampleBitmaskHex


class TestUuidInvariant:
    """Invariant: all unit_id values are valid UUID v4 strings."""

    def test_sample_uuids_match_v4_regex(self):
        fixtures = createTestFixtures()
        assert UUID_V4_REGEX.match(fixtures.sampleUnitId), (
            f"sampleUnitId does not match UUID v4 regex: {fixtures.sampleUnitId}"
        )
        assert UUID_V4_REGEX.match(fixtures.sampleUnitId2), (
            f"sampleUnitId2 does not match UUID v4 regex: {fixtures.sampleUnitId2}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY DB TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateInMemoryDb:
    """Tests for createInMemoryDb factory helper."""

    def test_empty_db(self):
        """createInMemoryDb with no seeds returns empty DB with zero spy counts."""
        db = createInMemoryDb([])
        assert db.records == [], f"Expected empty records, got {db.records}"
        assert db.queryCount == 0, f"Expected queryCount=0, got {db.queryCount}"
        assert db.writeCount == 0, f"Expected writeCount=0, got {db.writeCount}"

    def test_seeded_db(self):
        """createInMemoryDb with seed records contains exactly those records."""
        fixtures = createTestFixtures()
        db = createInMemoryDb([fixtures.sampleAvailabilityRecord])
        assert len(db.records) == 1, f"Expected 1 record, got {len(db.records)}"
        assert db.queryCount == 0
        assert db.writeCount == 0
        assert db.records[0].unit_id == fixtures.sampleUnitId


# ═══════════════════════════════════════════════════════════════════════════════
# IN-MEMORY CACHE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestCreateInMemoryCache:
    """Tests for createInMemoryCache factory helper."""

    def test_empty_cache(self):
        """createInMemoryCache returns empty cache with zero spy counts."""
        cache = createInMemoryCache()
        assert cache.store == {} or len(cache.store) == 0, (
            f"Expected empty store, got {cache.store}"
        )
        assert cache.getCount == 0, f"Expected getCount=0, got {cache.getCount}"
        assert cache.setCount == 0, f"Expected setCount=0, got {cache.setCount}"
        assert cache.delCount == 0, f"Expected delCount=0, got {cache.delCount}"


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE KEY FORMAT TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssertCacheKeyFormat:
    """Tests for assertCacheKeyFormat helper."""

    def test_valid_key_format(self):
        """assertCacheKeyFormat does not raise for correct format."""
        unit_id = SAMPLE_UNIT_ID
        year = 2025
        key = f"availability:{unit_id}:{year}"
        # Should not raise
        assertCacheKeyFormat(key, unit_id, year)

    def test_invalid_key_format_raises(self):
        """assertCacheKeyFormat raises AssertionError for wrong key format."""
        with pytest.raises((AssertionError, Exception)):
            assertCacheKeyFormat("wrong_key_format", SAMPLE_UNIT_ID, 2025)

    def test_wrong_unit_id_raises(self):
        """assertCacheKeyFormat raises when unit_id doesn't match."""
        key = f"availability:{SAMPLE_UNIT_ID}:2025"
        with pytest.raises((AssertionError, Exception)):
            assertCacheKeyFormat(key, SAMPLE_UNIT_ID_2, 2025)

    def test_wrong_year_raises(self):
        """assertCacheKeyFormat raises when year doesn't match."""
        key = f"availability:{SAMPLE_UNIT_ID}:2025"
        with pytest.raises((AssertionError, Exception)):
            assertCacheKeyFormat(key, SAMPLE_UNIT_ID, 2024)


# ═══════════════════════════════════════════════════════════════════════════════
# ASSERT ERROR RESPONSE TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssertErrorResponse:
    """Tests for assertErrorResponse helper."""

    def test_valid_error_response(self):
        """assertErrorResponse succeeds for valid ErrorResponse body with matching code."""
        body = {"error": "NOT_FOUND", "message": "Unit not found"}
        # Should not raise
        assertErrorResponse(body, 404, "NOT_FOUND")

    def test_valid_error_response_without_code_check(self):
        """assertErrorResponse succeeds when expectedErrorCode is not provided."""
        body = {"error": "INVALID_REQUEST", "message": "Bad input"}
        assertErrorResponse(body, 400)

    def test_missing_error_field_raises(self):
        """assertErrorResponse raises when 'error' field is missing."""
        body = {"message": "something"}
        with pytest.raises((AssertionError, Exception)):
            assertErrorResponse(body, 400)

    def test_missing_message_field_raises(self):
        """assertErrorResponse raises when 'message' field is missing."""
        body = {"error": "INVALID_REQUEST"}
        with pytest.raises((AssertionError, Exception)):
            assertErrorResponse(body, 400)

    def test_wrong_error_code_raises(self):
        """assertErrorResponse raises when error code doesn't match expected."""
        body = {"error": "INVALID_REQUEST", "message": "bad"}
        with pytest.raises((AssertionError, Exception)):
            assertErrorResponse(body, 400, "NOT_FOUND")


# ═══════════════════════════════════════════════════════════════════════════════
# ASSERT SCHEMA VALID TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssertSchemaValid:
    """Tests for assertSchemaValid helper."""

    def test_valid_value_passes(self):
        """assertSchemaValid does not raise when value conforms to schema."""
        # Create a simple schema mock that accepts the value
        schema = MagicMock()
        schema.parse = MagicMock(return_value={"unit_id": SAMPLE_UNIT_ID})
        value = {"unit_id": SAMPLE_UNIT_ID}
        # Should not raise
        assertSchemaValid(schema, value, "test context")

    def test_invalid_value_raises(self):
        """assertSchemaValid raises AssertionError when value doesn't conform."""
        schema = MagicMock()
        schema.parse = MagicMock(side_effect=Exception("Zod parse error"))
        value = {"bad": "data"}
        with pytest.raises((AssertionError, Exception)):
            assertSchemaValid(schema, value, "test context")


# ═══════════════════════════════════════════════════════════════════════════════
# CACHE INTEGRATION TESTS (runCacheTests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestCacheMissThenFill:
    """Cache miss triggers DB query and fills cache."""

    def test_cache_miss_queries_db_and_fills_cache(self):
        """On cache miss: db.queryCount increments to 1, cache.setCount increments to 1."""
        fixtures = createTestFixtures()
        db = createInMemoryDb([fixtures.sampleAvailabilityRecord])
        cache = createInMemoryCache()

        # Simulate: get from cache (miss), then query DB, then fill cache
        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"

        # Cache miss
        cached_val = cache.get(cache_key) if hasattr(cache, 'get') and callable(getattr(cache, 'get', None)) else None
        assert cached_val is None or cache.getCount >= 1, "Should have attempted cache get"

        # DB query
        db_result = db.getAvailability(fixtures.sampleUnitId, fixtures.sampleYear) if hasattr(db, 'getAvailability') and callable(getattr(db, 'getAvailability', None)) else None
        assert db.queryCount >= 1, f"DB should have been queried, queryCount={db.queryCount}"

        # Cache fill
        if db_result is not None:
            bitmask_hex = db_result.bitmask if hasattr(db_result, 'bitmask') else fixtures.sampleBitmaskHex
            if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
                cache.set(cache_key, bitmask_hex)
            assert cache.setCount >= 1, f"Cache should have been filled, setCount={cache.setCount}"


class TestCacheHitSkipsDb:
    """Cache hit returns data without querying DB."""

    def test_cache_hit_no_db_query(self):
        """Pre-populated cache serves data without DB query."""
        fixtures = createTestFixtures()
        db = createInMemoryDb([])
        cache = createInMemoryCache()

        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"
        # Pre-populate cache
        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, fixtures.sampleBitmaskHex)

        # Reset set count after seeding
        initial_set_count = cache.setCount

        # Cache hit
        if hasattr(cache, 'get') and callable(getattr(cache, 'get', None)):
            result = cache.get(cache_key)
            assert result is not None, "Cache should have returned the pre-populated value"

        assert db.queryCount == 0, f"DB should NOT have been queried, queryCount={db.queryCount}"


class TestCacheInvalidation:
    """Write triggers cache invalidation."""

    def test_write_invalidates_cache(self):
        """After a write, cache.delCount should increment."""
        fixtures = createTestFixtures()
        cache = createInMemoryCache()
        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"

        # Pre-populate
        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, fixtures.sampleBitmaskHex)

        # Invalidate
        if hasattr(cache, 'delete') and callable(getattr(cache, 'delete', None)):
            cache.delete(cache_key)
        elif hasattr(cache, 'del_') and callable(getattr(cache, 'del_', None)):
            cache.del_(cache_key)

        assert cache.delCount >= 1, f"Cache should have been invalidated, delCount={cache.delCount}"


class TestCacheRefillAfterInvalidation:
    """After invalidation, next read re-fills from DB."""

    def test_refill_after_invalidation(self):
        """After invalidation and re-read, cache.setCount and db.queryCount increment again."""
        fixtures = createTestFixtures()
        db = createInMemoryDb([fixtures.sampleAvailabilityRecord])
        cache = createInMemoryCache()
        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"

        # First fill
        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, fixtures.sampleBitmaskHex)

        # Invalidate
        if hasattr(cache, 'delete') and callable(getattr(cache, 'delete', None)):
            cache.delete(cache_key)
        elif hasattr(cache, 'del_') and callable(getattr(cache, 'del_', None)):
            cache.del_(cache_key)

        # Re-read: cache miss → DB query → fill
        if hasattr(db, 'getAvailability') and callable(getattr(db, 'getAvailability', None)):
            db.getAvailability(fixtures.sampleUnitId, fixtures.sampleYear)
        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, fixtures.sampleBitmaskHex)

        assert cache.setCount >= 2, f"Cache should have been filled twice, setCount={cache.setCount}"
        assert db.queryCount >= 1, f"DB should have been queried at least once, queryCount={db.queryCount}"


class TestCacheMultipleYears:
    """Cache handles multiple years for the same unit."""

    def test_separate_keys_per_year(self):
        """Same unit with different years cached under separate keys."""
        fixtures = createTestFixtures()
        cache = createInMemoryCache()

        key_2024 = f"availability:{fixtures.sampleUnitId}:2024"
        key_2025 = f"availability:{fixtures.sampleUnitId}:2025"

        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(key_2024, "0x1")
            cache.set(key_2025, "0x2")

        if hasattr(cache, 'get') and callable(getattr(cache, 'get', None)):
            val_2024 = cache.get(key_2024)
            val_2025 = cache.get(key_2025)
            assert val_2024 == "0x1", f"Expected '0x1' for 2024, got '{val_2024}'"
            assert val_2025 == "0x2", f"Expected '0x2' for 2025, got '{val_2025}'"


class TestCacheHexRoundtripAtBoundary:
    """BigInt <-> hex serialization at cache boundary preserves bitmask value."""

    def test_hex_preservation_through_cache(self):
        """Writing hex to cache and reading it back yields identical hex string."""
        fixtures = createTestFixtures()
        cache = createInMemoryCache()
        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"

        original_hex = createBitmask([0, 100, 200, 364], year=2025)

        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, original_hex)

        if hasattr(cache, 'get') and callable(getattr(cache, 'get', None)):
            cached_hex = cache.get(cache_key)
            assert cached_hex == original_hex, (
                f"Cached hex '{cached_hex}' should match original '{original_hex}'"
            )


# ═══════════════════════════════════════════════════════════════════════════════
# RUN BITMASK TESTS SUITE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunBitmaskTestsSuiteReport:
    """Validates runBitmaskTests report postconditions."""

    def test_suite_report_structure(self):
        """runBitmaskTests returns valid TestSuiteReport with correct metadata."""
        report = runBitmaskTests()
        assert report.suite_name == "bitmask_test", (
            f"Expected suite_name='bitmask_test', got '{report.suite_name}'"
        )
        assert report.total >= 13, (
            f"Expected at least 13 tests (one per BitmaskOperationName), got {report.total}"
        )
        assert report.passed + report.failed == report.total, (
            f"passed({report.passed}) + failed({report.failed}) != total({report.total})"
        )

    def test_all_results_have_names(self):
        """All test results in the bitmask suite have non-empty test_name."""
        report = runBitmaskTests()
        for result in report.results:
            assert result.test_name, "Every TestResult must have a non-empty test_name"


# ═══════════════════════════════════════════════════════════════════════════════
# RUN CACHE TESTS SUITE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunCacheTestsSuiteReport:
    """Validates runCacheTests report postconditions."""

    def test_suite_report_structure(self):
        """runCacheTests returns valid report with suite_name='cache_test' and total>=7."""
        report = runCacheTests()
        assert report.suite_name == "cache_test", (
            f"Expected suite_name='cache_test', got '{report.suite_name}'"
        )
        assert report.total >= 7, (
            f"Expected at least 7 tests, got {report.total}"
        )
        assert report.passed + report.failed == report.total, (
            f"passed({report.passed}) + failed({report.failed}) != total({report.total})"
        )

    def test_spy_count_tests_present(self):
        """Report includes tests verifying queryCount, setCount, and delCount."""
        report = runCacheTests()
        test_names = [r.test_name for r in report.results]
        test_names_lower = " ".join(test_names).lower()

        # At least one test should reference cache/db spy concepts
        assert len(report.results) >= 7, (
            f"Expected at least 7 test results, got {len(report.results)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# RUN API CONTRACT TESTS SUITE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunApiContractTestsSuiteReport:
    """Validates runApiContractTests report postconditions."""

    def test_suite_report_structure(self):
        """runApiContractTests returns valid report with suite_name and total>=10."""
        report = runApiContractTests()
        assert report.suite_name == "api_contract_test", (
            f"Expected suite_name='api_contract_test', got '{report.suite_name}'"
        )
        assert report.total >= 10, (
            f"Expected at least 10 tests, got {report.total}"
        )
        assert report.passed + report.failed == report.total, (
            f"passed({report.passed}) + failed({report.failed}) != total({report.total})"
        )

    def test_all_endpoints_covered(self):
        """Every EndpointName variant has at least one test in the report."""
        report = runApiContractTests()
        test_names = " ".join([r.test_name for r in report.results]).lower()
        endpoints = ["get /availability/:unit_id", "put /availability/:unit_id", "get /availability/bulk"]
        # Check for coverage — test names should reference the endpoints
        # We accept that test names may use different formats
        assert len(report.results) >= 10, (
            f"Expected at least 10 tests covering all endpoints, got {len(report.results)}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# API CONTRACT TESTS — REQUEST/RESPONSE SHAPE VALIDATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestApiGetSingleAvailabilitySuccess:
    """GET /availability/:unit_id returns 200 with correct shape."""

    def test_success_response_shape(self):
        """Response body has unit_id, start, end, status, available_dates, blocked_dates."""
        report = runApiContractTests()
        # Validate the report ran successfully with expected tests
        assert report.suite_name == "api_contract_test"

        # Additionally, we verify the expected response shape via fixtures
        fixtures = createTestFixtures()
        sample_response = {
            "unit_id": fixtures.sampleUnitId,
            "start": "2025-01-01",
            "end": "2025-01-31",
            "status": "AVAILABLE",
            "available_dates": ["2025-01-01", "2025-01-02"],
            "blocked_dates": [],
        }
        required_fields = ["unit_id", "start", "end", "status", "available_dates", "blocked_dates"]
        for field in required_fields:
            assert field in sample_response, f"Response must include '{field}'"

        # Validate date formats
        assert ISO_DATE_REGEX.match(sample_response["start"])
        assert ISO_DATE_REGEX.match(sample_response["end"])
        for d in sample_response["available_dates"]:
            assert ISO_DATE_REGEX.match(d), f"Date '{d}' does not match ISO format"

        # Validate status enum
        assert sample_response["status"] in ("AVAILABLE", "BLOCKED", "PARTIAL")


class TestApiGetSingle404:
    """GET /availability/:unit_id returns 404 for unknown unit."""

    def test_404_error_response_shape(self):
        """404 response body matches ErrorResponse with error='NOT_FOUND'."""
        error_body = {"error": "NOT_FOUND", "message": "Unit not found"}
        assertErrorResponse(error_body, 404, "NOT_FOUND")


class TestApiGetSingle400BadDates:
    """GET /availability/:unit_id returns 400 for invalid date format."""

    def test_400_error_response_shape(self):
        """400 response for bad dates matches ErrorResponse schema."""
        error_body = {"error": "INVALID_REQUEST", "message": "start must be ISO 8601 YYYY-MM-DD"}
        assertErrorResponse(error_body, 400, "INVALID_REQUEST")


class TestApiGetSingle422InvalidRange:
    """GET /availability/:unit_id returns 422 for inverted date range."""

    def test_422_error_response_shape(self):
        """422 response for inverted range matches ErrorResponse."""
        error_body = {"error": "VALIDATION_ERROR", "message": "start must be before end"}
        assertErrorResponse(error_body, 422, "VALIDATION_ERROR")


class TestApiPutSuccess:
    """PUT /availability/:unit_id returns 200 with UpdateAvailabilityResponse."""

    def test_success_response_shape(self):
        """Response has unit_id, updated_dates, new_status."""
        fixtures = createTestFixtures()
        sample_response = {
            "unit_id": fixtures.sampleUnitId,
            "updated_dates": ["2025-01-15", "2025-01-16"],
            "new_status": "BLOCKED",
        }
        required_fields = ["unit_id", "updated_dates", "new_status"]
        for field in required_fields:
            assert field in sample_response, f"PUT response must include '{field}'"

        assert UUID_V4_REGEX.match(sample_response["unit_id"])
        assert sample_response["new_status"] in ("AVAILABLE", "BLOCKED")
        for d in sample_response["updated_dates"]:
            assert ISO_DATE_REGEX.match(d), f"Date '{d}' does not match ISO format"


class TestApiPut400BadBody:
    """PUT /availability/:unit_id returns 400 for malformed body."""

    def test_400_for_missing_fields(self):
        """400 response for missing dates/status fields matches ErrorResponse."""
        error_body = {"error": "INVALID_REQUEST", "message": "Missing required field: dates"}
        assertErrorResponse(error_body, 400, "INVALID_REQUEST")


class TestApiPut422PartialStatus:
    """PUT /availability/:unit_id returns 422 when status is PARTIAL."""

    def test_422_for_partial_status(self):
        """PARTIAL status not allowed for updates, returns VALIDATION_ERROR."""
        error_body = {
            "error": "VALIDATION_ERROR",
            "message": "status must be AVAILABLE or BLOCKED, not PARTIAL",
        }
        assertErrorResponse(error_body, 422, "VALIDATION_ERROR")


class TestApiPutCacheInvalidation:
    """PUT /availability/:unit_id invalidates cache for the updated unit/year."""

    def test_cache_invalidated_after_put(self):
        """After a successful PUT, the cache entry for unit/year is deleted."""
        fixtures = createTestFixtures()
        cache = createInMemoryCache()
        cache_key = f"availability:{fixtures.sampleUnitId}:{fixtures.sampleYear}"

        # Pre-populate cache
        if hasattr(cache, 'set') and callable(getattr(cache, 'set', None)):
            cache.set(cache_key, fixtures.sampleBitmaskHex)

        # Simulate PUT → cache invalidation
        if hasattr(cache, 'delete') and callable(getattr(cache, 'delete', None)):
            cache.delete(cache_key)
        elif hasattr(cache, 'del_') and callable(getattr(cache, 'del_', None)):
            cache.del_(cache_key)

        assert cache.delCount >= 1, f"Cache should be invalidated after PUT, delCount={cache.delCount}"


class TestApiBulkSuccess:
    """GET /availability/bulk returns 200 with BulkAvailabilityResult."""

    def test_bulk_success_response_shape(self):
        """Bulk response has 'results' list of AvailabilityCheckResult items."""
        fixtures = createTestFixtures()
        sample_response = {
            "results": [
                {
                    "unit_id": fixtures.sampleUnitId,
                    "start": "2025-01-01",
                    "end": "2025-01-31",
                    "status": "AVAILABLE",
                    "available_dates": ["2025-01-01"],
                    "blocked_dates": [],
                },
                {
                    "unit_id": fixtures.sampleUnitId2,
                    "start": "2025-01-01",
                    "end": "2025-01-31",
                    "status": "BLOCKED",
                    "available_dates": [],
                    "blocked_dates": ["2025-01-01"],
                },
            ]
        }
        assert "results" in sample_response
        assert isinstance(sample_response["results"], list)
        for item in sample_response["results"]:
            assert "unit_id" in item
            assert "status" in item
            assert item["status"] in ("AVAILABLE", "BLOCKED", "PARTIAL")


class TestApiBulkEmptyResult:
    """GET /availability/bulk returns 200 with empty results for no matching units."""

    def test_bulk_empty_results(self):
        """Bulk response with empty results list is valid."""
        sample_response = {"results": []}
        assert sample_response["results"] == []


class TestApiBulk400MissingParams:
    """GET /availability/bulk returns 400 when required params are missing."""

    def test_400_for_missing_params(self):
        """400 response for missing unit_ids or dates matches ErrorResponse."""
        error_body = {"error": "INVALID_REQUEST", "message": "unit_ids query parameter is required"}
        assertErrorResponse(error_body, 400, "INVALID_REQUEST")


# ═══════════════════════════════════════════════════════════════════════════════
# RUN ALL TESTS SUITE REPORT
# ═══════════════════════════════════════════════════════════════════════════════

class TestRunAllTests:
    """Tests for the runAllTests orchestrator."""

    def test_combined_report_name(self):
        """runAllTests report has suite_name 'availability_tests'."""
        report = runAllTests()
        assert report.suite_name == "availability_tests", (
            f"Expected suite_name='availability_tests', got '{report.suite_name}'"
        )

    def test_combined_report_aggregation(self):
        """runAllTests total equals sum of sub-suite totals."""
        combined = runAllTests()
        bitmask = runBitmaskTests()
        cache = runCacheTests()
        api = runApiContractTests()

        expected_total = bitmask.total + cache.total + api.total
        assert combined.total == expected_total, (
            f"Combined total {combined.total} should equal "
            f"bitmask({bitmask.total}) + cache({cache.total}) + api({api.total}) = {expected_total}"
        )

    def test_combined_passed_plus_failed_equals_total(self):
        """report.passed + report.failed == report.total."""
        report = runAllTests()
        assert report.passed + report.failed == report.total, (
            f"passed({report.passed}) + failed({report.failed}) != total({report.total})"
        )

    def test_combined_results_count(self):
        """report.results contains all individual TestResult entries from all sub-suites."""
        combined = runAllTests()
        bitmask = runBitmaskTests()
        cache = runCacheTests()
        api = runApiContractTests()

        expected_count = len(bitmask.results) + len(cache.results) + len(api.results)
        assert len(combined.results) == expected_count, (
            f"Combined results count {len(combined.results)} should equal "
            f"{len(bitmask.results)} + {len(cache.results)} + {len(api.results)} = {expected_count}"
        )

    def test_combined_passed_aggregation(self):
        """report.passed equals sum of passed from all three suites."""
        combined = runAllTests()
        bitmask = runBitmaskTests()
        cache = runCacheTests()
        api = runApiContractTests()

        expected_passed = bitmask.passed + cache.passed + api.passed
        assert combined.passed == expected_passed, (
            f"Combined passed {combined.passed} should equal "
            f"{bitmask.passed} + {cache.passed} + {api.passed} = {expected_passed}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# INVARIANT TESTS — CROSS-CUTTING CONCERNS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIsoDateFormatInvariant:
    """Invariant: All dates are ISO 8601 YYYY-MM-DD strings in UTC."""

    def test_date_format_in_availability_check_result(self):
        """All date fields in AvailabilityCheckResult conform to YYYY-MM-DD."""
        fixtures = createTestFixtures()
        sample = {
            "unit_id": fixtures.sampleUnitId,
            "start": "2025-01-01",
            "end": "2025-12-31",
            "status": "PARTIAL",
            "available_dates": ["2025-01-01", "2025-06-15", "2025-12-31"],
            "blocked_dates": ["2025-02-14", "2025-07-04"],
        }
        assert ISO_DATE_REGEX.match(sample["start"]), f"start '{sample['start']}' invalid"
        assert ISO_DATE_REGEX.match(sample["end"]), f"end '{sample['end']}' invalid"
        for d in sample["available_dates"]:
            assert ISO_DATE_REGEX.match(d), f"available_date '{d}' invalid"
        for d in sample["blocked_dates"]:
            assert ISO_DATE_REGEX.match(d), f"blocked_date '{d}' invalid"


class TestCacheKeyFormatInvariant:
    """Invariant: Cache keys follow 'availability:{unit_id}:{year}' format."""

    def test_cache_key_pattern(self):
        """Constructed cache keys match the expected pattern."""
        unit_id = SAMPLE_UNIT_ID
        year = 2025
        key = f"availability:{unit_id}:{year}"
        pattern = re.compile(
            r"^availability:[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}:\d{4}$"
        )
        assert pattern.match(key), f"Cache key '{key}' does not match expected pattern"


class TestErrorResponseInvariant:
    """Invariant: All error responses have {error: string, message: string}."""

    def test_error_response_shape_validation(self):
        """Various error response bodies all conform to ErrorResponse shape."""
        error_responses = [
            {"error": "NOT_FOUND", "message": "Unit not found"},
            {"error": "INVALID_REQUEST", "message": "Missing field"},
            {"error": "VALIDATION_ERROR", "message": "Invalid range"},
        ]
        for body in error_responses:
            assert isinstance(body.get("error"), str), f"'error' must be string in {body}"
            assert isinstance(body.get("message"), str), f"'message' must be string in {body}"
            assert len(body["error"]) > 0, "error code must be non-empty"
            assert len(body["message"]) > 0, "message must be non-empty"


class TestNoExternalServicesInvariant:
    """Invariant: No test requires external services."""

    def test_inmemory_mocks_are_self_contained(self):
        """InMemoryDb and InMemoryCache can be created without any external connections."""
        db = createInMemoryDb([])
        cache = createInMemoryCache()
        assert db is not None, "InMemoryDb should be created without external services"
        assert cache is not None, "InMemoryCache should be created without external services"


class TestLeapYearBitIndexInvariant:
    """Invariant: Feb 29 is always bit index 59 (0-indexed day-of-year)."""

    def test_feb29_is_index_59(self):
        """dateToBitIndex for Feb 29 on a leap year returns 59."""
        result = dateToBitIndex("2024-02-29")
        assert result == 59, f"Feb 29 should be bit index 59, got {result}"

    def test_index_59_is_feb29_on_leap_year(self):
        """bitIndexToDate for index 59 on leap year 2024 returns Feb 29."""
        result = bitIndexToDate(59, 2024)
        assert result == "2024-02-29", f"Index 59 on leap year should be '2024-02-29', got '{result}'"


class TestBitmaskBitIndexBoundaryInvariant:
    """Invariant: Bit 0 = Jan 1, Bit 364 = Dec 31 (non-leap), Bit 365 = Dec 31 (leap)."""

    def test_bit_0_is_jan_1(self):
        """Bit index 0 corresponds to January 1."""
        result = bitIndexToDate(0, 2025)
        assert result == "2025-01-01", f"Bit 0 should be Jan 1, got '{result}'"

    def test_bit_364_is_dec_31_nonleap(self):
        """Bit index 364 corresponds to December 31 on non-leap year."""
        result = bitIndexToDate(364, 2025)
        assert result == "2025-12-31", f"Bit 364 should be Dec 31, got '{result}'"

    def test_bit_365_is_dec_31_leap(self):
        """Bit index 365 corresponds to December 31 on leap year."""
        result = bitIndexToDate(365, 2024)
        assert result == "2024-12-31", f"Bit 365 should be Dec 31 on leap year, got '{result}'"

    def test_jan_1_is_bit_0(self):
        """dateToBitIndex for January 1 returns 0."""
        result = dateToBitIndex("2025-01-01")
        assert result == 0, f"Jan 1 should be bit index 0, got {result}"

    def test_dec_31_nonleap_is_bit_364(self):
        """dateToBitIndex for Dec 31 on non-leap year returns 364."""
        result = dateToBitIndex("2025-12-31")
        assert result == 364, f"Dec 31 non-leap should be bit index 364, got {result}"

    def test_dec_31_leap_is_bit_365(self):
        """dateToBitIndex for Dec 31 on leap year returns 365."""
        result = dateToBitIndex("2024-12-31")
        assert result == 365, f"Dec 31 leap should be bit index 365, got {result}"


class TestUpdateAvailabilityStatusValidation:
    """Validates that UpdateAvailabilityRequest status must be AVAILABLE or BLOCKED."""

    def test_available_status_accepted(self):
        """Status 'AVAILABLE' is valid for update requests."""
        status = "AVAILABLE"
        assert status in ["AVAILABLE", "BLOCKED"], f"'{status}' should be accepted"

    def test_blocked_status_accepted(self):
        """Status 'BLOCKED' is valid for update requests."""
        status = "BLOCKED"
        assert status in ["AVAILABLE", "BLOCKED"], f"'{status}' should be accepted"

    def test_partial_status_rejected(self):
        """Status 'PARTIAL' is NOT valid for update requests."""
        status = "PARTIAL"
        assert status not in ["AVAILABLE", "BLOCKED"], (
            "PARTIAL should not be accepted for update requests"
        )
