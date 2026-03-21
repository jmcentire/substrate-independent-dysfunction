# === Availability Bitmask Cache Layer (availability_bitmask_cache) v2 ===
# Builds and maintains Redis-cached bitmasks from PostgreSQL availability_blocks data. On cache miss: queries all availability blocks for a unit within a 366-day window (to handle leap years), constructs a bitmask (1=available, 0=blocked), stores in Redis with configurable TTL. On availability update: invalidates the cached bitmask (lazy rebuild on next read). Key format: avail:{CRC32C(unit_id) % 64}:{unit_id}. Internal bitmask is a raw Redis bit string of 366 bits anchored to today's UTC date (bit 0 = today, bit 365 = today + 365 days). Anchor date staleness is detected on reads: if the cached bitmask's anchor date differs from today's UTC date, the cache entry is treated as a miss and rebuilt. Singleflight pattern prevents thundering herd on concurrent cache misses for the same unit. Redis unavailability triggers graceful degradation to direct PostgreSQL queries (logged as warning). Exposes checkAvailability, checkBulkAvailability, and invalidateCache.

# Module invariants:
#   - The bitmask for a unit is always exactly window_days bits long (default 366 bits). Bit 0 corresponds to today's UTC date; bit N corresponds to today + N days.
#   - A bit value of 1 means the unit is available on that date; a bit value of 0 means the unit is blocked on that date.
#   - 'Today' is always defined as the current date at UTC midnight (00:00:00Z). All date arithmetic uses UTC.
#   - The anchor_date stored in BitmaskMetadata must equal today's UTC date for the cached bitmask to be considered valid. If anchor_date != today UTC on a read, the entry is stale and treated as a cache miss.
#   - The Redis key for a unit is always: {key_prefix}:{CRC32C(unit_id) % shard_count}:{unit_id}. This format is immutable for a given CacheConfig.
#   - CRC32C (Castagnoli) is the only supported hash algorithm. The hash is computed over the raw UTF-8 bytes of the unit_id string including hyphens.
#   - Shard count must be a power of 2 to ensure uniform distribution via modulo operation on CRC32C output.
#   - PostgreSQL availability_blocks table is the source of truth. Redis is a derived, read-optimized cache. Any conflict is resolved by rebuilding from PostgreSQL.
#   - All dates in input/output are inclusive on both ends. The range [2024-06-01, 2024-06-03] includes June 1, 2, and 3.
#   - Invalidation is always safe and idempotent. Deleting a non-existent key is a successful no-op.
#   - The singleflight pattern ensures that at most one PostgreSQL query is in-flight at any time for a given unit_id, regardless of concurrent callers.
#   - When Redis is unavailable and fallback_to_pg_on_redis_failure is true, the system operates in degraded mode: all reads go directly to PostgreSQL and no caching occurs. A warning-level log is emitted per request.
#   - Bulk operations preserve input order in results and deduplicate unit_ids. Each unique unit_id appears exactly once in the results.
#   - No method in this component writes to or mutates the PostgreSQL availability_blocks table. This component is read-only with respect to PostgreSQL.

UnitId = primitive  # Branded UUID v4 string identifying a bookable unit. Must conform to lowercase UUID v4 format (8-4-4-4-12 hex digits with hyphens). Acts as a nominal/branded type to prevent accidental interchange with other string identifiers.

DateString = primitive  # Branded ISO 8601 date string in YYYY-MM-DD format, UTC-anchored. No time component. Must represent a valid calendar date. Acts as a nominal/branded type to prevent accidental interchange with other strings.

ShardKey = primitive  # Branded integer in the range [0, 63] inclusive, derived from CRC32C(unit_id) % shardCount. Used as the hash slot in the Redis key format: avail:{shard}:{unit_id}. Constrains key distribution to a fixed number of Redis hash slots for predictable cluster behavior.

class HashAlgorithm(Enum):
    """Supported hash algorithms for shard key derivation."""
    CRC32C = "CRC32C"

class CacheConfig:
    """Configuration for the Availability Bitmask Cache Layer. Immutable after construction."""
    ttl_seconds: int                         # required, range(1 <= value <= 86400), Time-to-live in seconds for cached bitmask entries in Redis. After expiry, next read triggers a lazy rebuild from PostgreSQL.
    shard_count: int = 64                    # optional, range(1 <= value <= 1024), custom((value & (value - 1)) == 0), Number of shard buckets for key distribution. CRC32C(unit_id) % shard_count produces the ShardKey. Must be a power of 2 for uniform distribution.
    window_days: int = 366                   # optional, range(1 <= value <= 731), Total number of bits in the bitmask representing the availability window starting from today's UTC date. Default 366 to handle leap years (bit 0 = today, bit 365 = today + 365 days).
    hash_algorithm: HashAlgorithm = CRC32C   # optional, Hash algorithm used to derive the shard key from unit_id. Currently only CRC32C is supported.
    key_prefix: str = avail                  # optional, regex(^[a-z][a-z0-9_]{0,15}$), Prefix for all Redis keys produced by this cache layer. Key format: {key_prefix}:{shard}:{unit_id}.
    singleflight_enabled: bool = true        # optional, When true, concurrent cache misses for the same unit_id are coalesced into a single PostgreSQL query (singleflight/request-collapsing pattern) to prevent thundering herd.
    fallback_to_pg_on_redis_failure: bool = true # optional, When true, Redis unavailability triggers graceful degradation to direct PostgreSQL queries. A warning-level log is emitted. When false, CacheUnavailable error is returned.

class ErrorCode(Enum):
    """Exhaustive enumeration of error codes produced by the Availability Bitmask Cache Layer."""
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    UNIT_NOT_FOUND = "UNIT_NOT_FOUND"
    CACHE_UNAVAILABLE = "CACHE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"

class InvalidDateRange:
    """Error returned when the requested date range violates constraints: start > end, range exceeds window_days, or dates are in the past (before today UTC)."""
    error_code: ErrorCode                    # required, Always INVALID_DATE_RANGE.
    message: str                             # required, Human-readable description of the date range violation.
    start_date: DateString                   # required, The start date that was requested.
    end_date: DateString                     # required, The end date that was requested.
    reason: DateRangeViolation               # required, Specific reason the date range is invalid.

class DateRangeViolation(Enum):
    """Specific reasons a date range can be invalid."""
    START_AFTER_END = "START_AFTER_END"
    RANGE_EXCEEDS_WINDOW = "RANGE_EXCEEDS_WINDOW"
    DATES_IN_PAST = "DATES_IN_PAST"
    INVALID_DATE_FORMAT = "INVALID_DATE_FORMAT"

class UnitNotFound:
    """Error returned when the unit_id does not exist in the PostgreSQL availability_blocks table. Only raised during cache rebuild (not on cache hit)."""
    error_code: ErrorCode                    # required, Always UNIT_NOT_FOUND.
    message: str                             # required, Human-readable message.
    unit_id: UnitId                          # required, The unit_id that was not found.

class CacheUnavailable:
    """Error returned when Redis is unavailable AND fallback_to_pg_on_redis_failure is false. If fallback is enabled, this error is never surfaced to callers (the system degrades gracefully)."""
    error_code: ErrorCode                    # required, Always CACHE_UNAVAILABLE.
    message: str                             # required, Human-readable message including Redis connection details (sanitized).
    redis_error: str = None                  # optional, Underlying Redis client error message.

class InternalError:
    """Catch-all error for unexpected failures (e.g., corrupt bitmask, PostgreSQL query timeout, singleflight panic)."""
    error_code: ErrorCode                    # required, Always INTERNAL_ERROR.
    message: str                             # required, Human-readable message.
    cause: str = None                        # optional, Stringified root cause for logging. Never exposed to external callers.

AvailabilityError = InvalidDateRange | UnitNotFound | CacheUnavailable | InternalError

class AvailabilityResult:
    """Result wrapper for checkAvailability. Discriminated on 'ok' field: if ok=true, value contains the boolean availability; if ok=false, error contains the AvailabilityError."""
    ok: bool                                 # required, True if the operation succeeded, false if an error occurred.
    value: bool = false                      # optional, True if the entire date range [startDate, endDate] is available (all bits = 1). Only meaningful when ok=true.
    error: AvailabilityError = None          # optional, The error that occurred. Only present when ok=false.

class BulkUnitResult:
    """Per-unit result within a bulk availability check. Supports per-unit error granularity: a unit may be available (true), unavailable (false), or errored (null with error populated)."""
    unit_id: UnitId                          # required, The unit this result pertains to.
    available: bool = None                   # optional, True if the entire date range is available for this unit. Null/false if an error occurred for this specific unit.
    error: AvailabilityError = None          # optional, Per-unit error if this unit's lookup failed (e.g., UnitNotFound). Null if lookup succeeded.

BulkUnitResultList = list[BulkUnitResult]
# List of per-unit results from a bulk availability check.

class BulkAvailabilityResult:
    """Result wrapper for checkBulkAvailability. On success, results contains a list of per-unit availability outcomes (including per-unit errors). A top-level error indicates a systemic failure affecting all units (e.g., both Redis and PostgreSQL down)."""
    ok: bool                                 # required, True if the bulk operation completed (individual units may still have errors). False only on systemic failure.
    results: BulkUnitResultList = None       # optional, Per-unit availability results. One entry per requested unit_id, in the same order as the input. Only present when ok=true.
    error: AvailabilityError = None          # optional, Systemic error affecting all units. Only present when ok=false.

class InvalidationResult:
    """Result wrapper for invalidateCache. On success, value is None (void). On failure, error describes what went wrong."""
    ok: bool                                 # required, True if the invalidation succeeded (or key did not exist). False if an error occurred.
    error: AvailabilityError = None          # optional, The error that occurred. Only present when ok=false.

UnitIdList = list[UnitId]
# List of UnitId values for bulk operations.

class RedisClient:
    """Dependency injection abstraction for the Redis client. Consumers inject an implementation conforming to this interface. Supports single-key and pipelined bit operations. All methods are async. Implementations must be safe for concurrent use."""
    get_bit: str                             # required, Async method: (key: str, offset: int) → int. Returns the bit value (0 or 1) at the given offset in the string stored at key.
    get_bit_range: str                       # required, Async method: (key: str, start_offset: int, end_offset: int) → bytes. Returns the raw bytes covering bits [start_offset, end_offset]. Caller must mask boundary bits.
    set_bits: str                            # required, Async method: (key: str, bit_map: dict[int, int], ttl_seconds: int) → None. Sets multiple bits atomically via MULTI/EXEC pipeline, then sets TTL.
    delete_key: str                          # required, Async method: (key: str) → bool. Deletes the key. Returns true if key existed, false otherwise.
    pipeline_get_bit_ranges: str             # required, Async method: (commands: list[tuple[str, int, int]]) → list[bytes]. Executes multiple GETRANGE commands in a single Redis pipeline for bulk operations.
    exists: str                              # required, Async method: (key: str) → bool. Returns true if key exists in Redis.
    get_key_metadata: str                    # required, Async method: (key: str) → dict. Returns metadata including TTL remaining and anchor_date stored alongside the bitmask.
    ping: str                                # required, Async method: () → bool. Health check. Returns true if Redis is reachable.

class AvailabilityBlock:
    """A single availability block record from PostgreSQL. Represents a contiguous date range during which a unit is either available or blocked."""
    unit_id: UnitId                          # required, The unit this block belongs to.
    start_date: DateString                   # required, Inclusive start date of the block.
    end_date: DateString                     # required, Inclusive end date of the block.
    is_available: bool                       # required, True if the unit is available during this block, false if blocked.

AvailabilityBlockList = list[AvailabilityBlock]
# List of availability blocks returned from PostgreSQL queries.

class PgClient:
    """Dependency injection abstraction for the PostgreSQL client. Consumers inject an implementation conforming to this interface. All methods are async. Used as the source of truth for availability data."""
    get_availability_blocks: str             # required, Async method: (unit_id: UnitId, range_start: DateString, range_end: DateString) → AvailabilityBlockList. Returns all availability_blocks rows overlapping the given date range for the specified unit. Returns empty list if unit_id has no blocks (caller must distinguish from unit-not-found via unit_exists).
    unit_exists: str                         # required, Async method: (unit_id: UnitId) → bool. Returns true if the unit_id exists in the units table.
    get_availability_blocks_bulk: str        # required, Async method: (unit_ids: list[UnitId], range_start: DateString, range_end: DateString) → dict[UnitId, AvailabilityBlockList]. Batch query returning availability blocks grouped by unit_id.
    ping: str                                # required, Async method: () → bool. Health check. Returns true if PostgreSQL is reachable.

class BitmaskMetadata:
    """Internal metadata stored alongside each cached bitmask in Redis (as a separate hash field or encoded prefix). Used for anchor date staleness detection."""
    anchor_date: DateString                  # required, The UTC date representing bit 0 of the bitmask. If this differs from today's UTC date on a read, the cache entry is stale and treated as a miss.
    built_at_epoch_ms: int                   # required, Unix epoch milliseconds when the bitmask was constructed. Used for observability and debugging.
    window_days: int                         # required, Number of bits in the bitmask. Must match CacheConfig.window_days.

def checkAvailability(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    start_date: DateString,    # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
    end_date: DateString,      # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
) -> AvailabilityResult:
    """
    Checks whether a single unit is fully available for an inclusive date range [startDate, endDate]. On cache hit with a valid (non-stale) bitmask: performs O(1) bitwise AND check across the relevant bit range. On cache miss or stale anchor date: queries PostgreSQL for all availability_blocks within the 366-day window, constructs the bitmask, stores in Redis with TTL, then checks the requested range. Singleflight pattern coalesces concurrent misses for the same unit_id. If Redis is unavailable and fallback_to_pg_on_redis_failure is true, queries PostgreSQL directly (logged as warning). Single-day queries (startDate == endDate) are supported.

    Preconditions:
      - unit_id is a valid UUID v4 string.
      - start_date and end_date are valid ISO 8601 YYYY-MM-DD date strings representing real calendar dates.
      - start_date <= end_date.
      - start_date >= today (UTC). 'Today' is defined as the current date at UTC midnight.
      - end_date <= today + (window_days - 1) days.
      - At least one of RedisClient or PgClient is reachable.

    Postconditions:
      - If ok=true, value is true if and only if every date in [start_date, end_date] inclusive has bit=1 in the bitmask (or equivalent PostgreSQL check on fallback).
      - If ok=true, value is false if any date in [start_date, end_date] has bit=0.
      - If the bitmask was rebuilt from PostgreSQL, it is now cached in Redis with the configured TTL (unless Redis is unavailable).
      - If the cached bitmask had a stale anchor_date (not equal to today UTC), it was treated as a miss and rebuilt.
      - If singleflight was active, only one PostgreSQL query was issued for concurrent callers requesting the same unit_id.

    Errors:
      - invalid_date_range_start_after_end (InvalidDateRange): start_date > end_date
          reason: START_AFTER_END
      - invalid_date_range_past_dates (InvalidDateRange): start_date < today (UTC)
          reason: DATES_IN_PAST
      - invalid_date_range_exceeds_window (InvalidDateRange): end_date > today + (window_days - 1) days
          reason: RANGE_EXCEEDS_WINDOW
      - invalid_date_format (InvalidDateRange): start_date or end_date does not parse to a valid calendar date (e.g., 2024-02-30)
          reason: INVALID_DATE_FORMAT
      - unit_not_found (UnitNotFound): unit_id does not exist in PostgreSQL units table (checked during cache rebuild only)
      - cache_unavailable (CacheUnavailable): Redis is unreachable AND fallback_to_pg_on_redis_failure is false
      - internal_error (InternalError): Unexpected failure: corrupt bitmask, PostgreSQL timeout, singleflight panic, or both Redis and PostgreSQL are unreachable

    Side effects: On cache miss: issues a SELECT query to PostgreSQL availability_blocks table., On cache miss: writes the rebuilt bitmask and metadata to Redis with configured TTL., On stale anchor date detection: deletes stale key and rebuilds (equivalent to miss)., On Redis failure with fallback enabled: emits a warning-level log., Singleflight: may block concurrent callers for the same unit_id until the first caller's rebuild completes.
    Idempotent: yes
    """
    ...

def checkBulkAvailability(
    unit_ids: UnitIdList,      # length(1 <= len <= 100)
    start_date: DateString,    # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
    end_date: DateString,      # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
) -> BulkAvailabilityResult:
    """
    Checks availability for multiple units across the same inclusive date range [startDate, endDate]. Uses a Redis pipeline to fetch bitmasks for all requested units in a single round-trip. Units with cache misses are batch-queried from PostgreSQL. Returns per-unit results with per-unit error granularity: individual units may fail (e.g., UnitNotFound) without failing the entire batch. A top-level error (ok=false) indicates systemic failure affecting all units.

    Preconditions:
      - All unit_ids are valid UUID v4 strings.
      - start_date and end_date are valid ISO 8601 YYYY-MM-DD date strings representing real calendar dates.
      - start_date <= end_date.
      - start_date >= today (UTC).
      - end_date <= today + (window_days - 1) days.
      - unit_ids list is non-empty and contains at most 100 entries.
      - At least one of RedisClient or PgClient is reachable.

    Postconditions:
      - If ok=true, results contains exactly one BulkUnitResult per unique unit_id from the input, in input order (deduped).
      - For each BulkUnitResult where error is absent: available=true iff every date in [start_date, end_date] is available for that unit.
      - For each BulkUnitResult where error is present: the error describes why that specific unit's lookup failed.
      - All cache misses triggered by this call have been rebuilt and stored in Redis (unless Redis is unavailable).
      - Stale bitmasks (anchor_date != today UTC) are rebuilt during this operation.

    Errors:
      - invalid_date_range_start_after_end (InvalidDateRange): start_date > end_date
          reason: START_AFTER_END
      - invalid_date_range_past_dates (InvalidDateRange): start_date < today (UTC)
          reason: DATES_IN_PAST
      - invalid_date_range_exceeds_window (InvalidDateRange): end_date > today + (window_days - 1) days
          reason: RANGE_EXCEEDS_WINDOW
      - invalid_date_format (InvalidDateRange): start_date or end_date does not parse to a valid calendar date
          reason: INVALID_DATE_FORMAT
      - empty_unit_ids (InvalidDateRange): unit_ids list is empty
          reason: INVALID_DATE_FORMAT
      - too_many_unit_ids (InvalidDateRange): unit_ids list contains more than 100 entries
          reason: RANGE_EXCEEDS_WINDOW
      - systemic_cache_unavailable (CacheUnavailable): Redis is unreachable AND PostgreSQL is unreachable
      - internal_error (InternalError): Unexpected systemic failure affecting all units

    Side effects: On cache misses: issues batch SELECT queries to PostgreSQL availability_blocks table., On cache misses: writes rebuilt bitmasks and metadata to Redis via pipeline with configured TTL., On stale anchor date detection for any unit: deletes and rebuilds those entries., On Redis failure with fallback enabled: emits a warning-level log and falls back to PostgreSQL for all units., Singleflight: concurrent misses for the same unit_id within this batch (and across concurrent calls) are coalesced.
    Idempotent: yes
    """
    ...

def invalidateCache(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> InvalidationResult:
    """
    Deletes the cached bitmask and metadata for a single unit from Redis. The next read for this unit will trigger a lazy rebuild from PostgreSQL. This method is called when availability data is updated (PUT /availability/:unit_id). Invalidation is idempotent: deleting a non-existent key is a successful no-op.

    Preconditions:
      - unit_id is a valid UUID v4 string.

    Postconditions:
      - If ok=true, no Redis key exists for this unit_id in the cache (key avail:{CRC32C(unit_id) % shard_count}:{unit_id} has been deleted or did not exist).
      - If ok=true, the next call to checkAvailability or checkBulkAvailability for this unit_id will trigger a full rebuild from PostgreSQL.
      - If ok=false with CacheUnavailable, the Redis key may still exist (invalidation failed). Caller should retry or rely on TTL expiry.

    Errors:
      - cache_unavailable (CacheUnavailable): Redis is unreachable (no fallback for invalidation — must reach Redis to delete the key)
      - internal_error (InternalError): Unexpected failure during key deletion (e.g., Redis protocol error)

    Side effects: Deletes the Redis key avail:{CRC32C(unit_id) % shard_count}:{unit_id} if it exists., May cancel any in-flight singleflight rebuild for this unit_id (implementation-defined).
    Idempotent: yes
    """
    ...

def computeShardKey(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    shard_count: int,          # range(1 <= value <= 1024), custom((value & (value - 1)) == 0)
) -> ShardKey:
    """
    Pure function that computes the ShardKey for a given unit_id. Applies the configured hash algorithm (CRC32C) to the unit_id string and returns the result modulo shard_count. Exposed for testing and observability; not part of the public API.

    Preconditions:
      - unit_id is a valid UUID v4 string.
      - shard_count is a positive power of 2.

    Postconditions:
      - Returned ShardKey is in the range [0, shard_count - 1].
      - The function is pure: same inputs always produce the same output.
      - Distribution across shard keys is approximately uniform for random UUID v4 inputs.

    Side effects: none
    Idempotent: yes
    """
    ...

def buildRedisKey(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    config: CacheConfig,
) -> str:
    """
    Pure function that constructs the full Redis key for a given unit_id. Format: {key_prefix}:{shard_key}:{unit_id}. Exposed for testing and observability; not part of the public API.

    Preconditions:
      - unit_id is a valid UUID v4 string.
      - config is a valid CacheConfig.

    Postconditions:
      - Returned string matches pattern: {config.key_prefix}:{N}:{unit_id} where N is an integer in [0, config.shard_count - 1].
      - The function is pure: same inputs always produce the same output.

    Side effects: none
    Idempotent: yes
    """
    ...
