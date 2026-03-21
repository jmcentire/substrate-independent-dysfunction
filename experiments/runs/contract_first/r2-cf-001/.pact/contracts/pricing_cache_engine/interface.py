# === Hash-Sharded Price Cache (pricing_cache_engine) v1 ===
#  Dependencies: shared_foundation
# Redis caching layer for pricing data, hash-sharded by unit_id. Implements the Redis-side operations of a cache-aside pattern: stores nightly rates as Redis Hashes keyed by date, sharded by consistent hash of unit_id for even distribution across configurable shard count. All monetary values use integer-cents representation. JSON-serialized payloads include a schema version field (_v) for forward compatibility. Redis errors are caught and surfaced as discriminated cache-miss results — never thrown — making all write operations fire-and-forget safe. The engine owns ONLY Redis operations; cache-aside orchestration (check cache → fallback to DB → populate) lives in a higher-level service, keeping the engine testable with only a Redis mock.

# Module invariants:
#   - All cached data is stored as JSON with a '_v' schema version field; entries with _v != current_schema_version are treated as stale on read and auto-invalidated
#   - All monetary values in cache payloads are integer cents (no floating-point); the MoneyAmount.amount_cents field is always a non-negative integer
#   - All date strings in cache keys and NightlyRate.date fields are ISO 8601 YYYY-MM-DD format in UTC
#   - shardKey is a pure deterministic function: same unit_id always produces the same Redis key regardless of system state
#   - The shard key format is always 'pricing:{N}:{unit_id}' where N = hash(unit_id) % shard_count and N is in [0, shard_count)
#   - No public method of the engine ever throws or rejects a Promise; all Redis errors are caught, logged, and either swallowed (writes) or surfaced as CacheMiss(reason='error') (reads)
#   - The engine performs ONLY Redis operations; it never accesses PostgreSQL or any other data store directly
#   - Cache-aside orchestration (check cache → fallback to DB → populate cache) is NOT the responsibility of this engine
#   - All NightlyRate entries within a single cacheRates call must share the same currency; cross-currency mixing within a unit's cache is undefined behavior
#   - getCachedRates returns CacheHit only when ALL dates in the requested range are present and valid; partial hits are treated as full misses
#   - Corrupt or version-mismatched cache entries trigger automatic async invalidation (DEL) of the entire unit hash key to prevent serving stale data
#   - The Redis hash key is the shard key; hash fields within it are ISO date strings; hash field values are JSON SerializedCachePayload strings
#   - shard_count is read once at engine construction from PRICING_CACHE_SHARD_COUNT env var (default 64) and is immutable for the engine's lifetime
#   - default_ttl_seconds is read once at engine construction from PRICING_CACHE_TTL_SECONDS env var (default 3600) and can be overridden per-call via CacheOptions

UnitId = primitive  # Branded UUID v4 string identifying a rental unit. Re-exported from shared_foundation for local type resolution.

class Currency(Enum):
    """ISO 4217 currency code. Extensible enum; only codes actually used in the system are listed."""
    USD = "USD"
    EUR = "EUR"
    GBP = "GBP"
    CAD = "CAD"
    AUD = "AUD"
    JPY = "JPY"
    MXN = "MXN"

class MoneyAmount:
    """Monetary value represented as integer cents to avoid floating-point precision issues. For zero-decimal currencies (e.g. JPY), amount_cents represents the smallest unit."""
    amount_cents: int                        # required, range(value >= 0), Amount in the smallest currency unit (cents for USD/EUR/GBP, yen for JPY, etc.).
    currency: Currency                       # required, ISO 4217 currency code for this amount.

class FeeDetail:
    """A single named fee or tax line item applied to a nightly rate."""
    fee_code: str                            # required, regex(^[a-z][a-z0-9_]{1,63}$), Machine-readable fee identifier, e.g. 'cleaning_fee', 'occupancy_tax', 'resort_fee'.
    label: str                               # required, Human-readable label for display, e.g. 'Cleaning Fee'.
    amount: MoneyAmount                      # required, Fee amount in integer cents.
    is_tax: bool                             # required, True if this line item is a tax rather than a fee.

FeeDetailList = list[FeeDetail]
# Ordered list of fee/tax line items.

class LosDiscount:
    """Length-of-stay discount that was applied or is applicable to this rate."""
    min_nights: int                          # required, range(value >= 2), Minimum number of consecutive nights required to qualify for this discount.
    discount_percent: int                    # required, range(value >= 1 && value <= 99), Discount percentage as integer (e.g. 10 means 10%). Applied to adjusted rate before fees/taxes.
    discount_amount: MoneyAmount             # required, Computed discount amount in cents for this specific night.

OptionalLosDiscount = LosDiscount | None

class NightlyRate:
    """Complete rate breakdown for a single night. All monetary fields use integer-cents representation. Dates are ISO 8601 date strings (YYYY-MM-DD) in UTC."""
    date: str                                # required, regex(^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$), The calendar date this rate applies to, in ISO 8601 format YYYY-MM-DD (UTC).
    base_rate: MoneyAmount                   # required, Base nightly rate before any adjustments, seasonal pricing, or discounts.
    seasonal_rate: MoneyAmount               # required, Rate after seasonal adjustment. Equals base_rate if no seasonal adjustment applies.
    adjusted_rate: MoneyAmount               # required, Rate after dynamic pricing adjustments (demand, day-of-week, etc.). Applied on top of seasonal_rate.
    los_discount: OptionalLosDiscount = None # optional, Length-of-stay discount details if applicable, or null.
    fees_and_taxes: FeeDetailList            # required, Itemized fees and taxes applicable to this night.
    total_cents: int                         # required, range(value >= 0), Final total for this night in cents: adjusted_rate - los_discount + sum(fees_and_taxes). Must be >= 0.
    currency: Currency                       # required, Currency for all monetary fields in this rate record. Must match all nested MoneyAmount currencies.

NightlyRateList = list[NightlyRate]
# Ordered list of nightly rates, sorted by date ascending.

class DateRange:
    """Inclusive date range for querying cached rates. Both dates are ISO 8601 YYYY-MM-DD strings in UTC."""
    check_in: str                            # required, regex(^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$), Start date (inclusive) in ISO 8601 YYYY-MM-DD format.
    check_out: str                           # required, regex(^\d{4}-(?:0[1-9]|1[0-2])-(?:0[1-9]|[12]\d|3[01])$), End date (exclusive — last night is check_out minus 1 day) in ISO 8601 YYYY-MM-DD format.

class CacheOptions:
    """Optional configuration overrides for a cache write operation."""
    ttl_seconds: int = 3600                  # optional, range(value >= 0), TTL in seconds for the cached data. Overrides the default from PRICING_CACHE_TTL_SECONDS env var. 0 means no expiry.
    schema_version: int = 1                  # optional, range(value >= 1), Schema version to stamp on serialized payloads. Defaults to current engine schema version (1).

class CacheMissReason(Enum):
    """Discriminator for why a cache lookup did not return data."""
    MISS = "miss"
    ERROR = "error"

class CacheHit:
    """Discriminated union variant: cache hit. Contains the cached data."""
    hit: bool                                # required, custom(value == true), Always true for a cache hit.
    data: NightlyRateList                    # required, The cached nightly rates for the requested date range, sorted by date ascending.

class CacheMiss:
    """Discriminated union variant: cache miss or error. No data returned."""
    hit: bool                                # required, custom(value == false), Always false for a cache miss/error.
    reason: CacheMissReason                  # required, Why the cache did not return data: 'miss' for key not found, 'error' for Redis/deserialization failure.

CacheResult = CacheHit | CacheMiss

class CacheEngineConfig:
    """Configuration for the pricing cache engine, resolved from environment variables at construction time."""
    shard_count: int                         # required, range(value >= 1 && value <= 4096), Number of hash shards for distributing unit keys across Redis key space. From PRICING_CACHE_SHARD_COUNT env var.
    default_ttl_seconds: int                 # required, range(value >= 0), Default TTL in seconds for cached rate data. From PRICING_CACHE_TTL_SECONDS env var. 0 means no expiry.
    current_schema_version: int              # required, range(value >= 1), Current schema version for serialization. Cache entries with a different _v are treated as stale and auto-invalidated.

class SerializedCachePayload:
    """Internal JSON structure stored in Redis hash fields. Each field in the hash corresponds to one date, and the value is a JSON string matching this schema. Not exposed in the public API but documented for serialization contract."""
    _v: int                                  # required, range(value >= 1), Schema version number. Used for forward-compatible deserialization. Entries with _v != current_schema_version are auto-invalidated.
    rate: NightlyRate                        # required, The serialized nightly rate data for a single date.

def shardKey(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> str:
    """
    Pure function that computes the Redis key for a given unit_id using consistent hashing. Returns a key in the format 'pricing:{hash(unitId) % shardCount}:{unitId}'. The hash function is deterministic (CRC32 or equivalent) so the same unitId always maps to the same shard. This function performs no I/O and never throws.

    Preconditions:
      - unit_id is a valid UUID v4 string
      - Engine has been constructed with a valid CacheEngineConfig (shard_count >= 1)

    Postconditions:
      - Returned string matches pattern 'pricing:{N}:{unit_id}' where N is an integer in [0, shard_count)
      - Same unit_id always produces the same key (deterministic)
      - Key is a valid Redis key string (no embedded nulls)

    Side effects: none
    Idempotent: yes
    """
    ...

def cacheRates(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    rates: NightlyRateList,    # length(length >= 1 && length <= 730)
    options: CacheOptions = None,
) -> None:
    """
    Writes nightly rates for a unit into the Redis hash. Each rate is stored as a separate hash field keyed by its ISO date string, with the value being a JSON-serialized SerializedCachePayload (including _v schema version). Uses HSET for the batch write followed by EXPIRE to set/reset TTL. Fire-and-forget safe: all Redis and serialization errors are caught, logged, and swallowed — this function never throws.

    Preconditions:
      - unit_id is a valid UUID v4 string
      - rates list is non-empty and contains no more than 730 entries
      - All NightlyRate entries have consistent currency fields
      - All monetary amounts within each NightlyRate use the same currency as the rate's currency field
      - RedisClientPort has been injected and is callable (connection may be down; errors are swallowed)

    Postconditions:
      - On success: Redis hash at shardKey(unit_id) contains one field per rate date, each with a valid JSON SerializedCachePayload
      - On success: Redis hash TTL is set/reset to the effective TTL (from options or config default)
      - On Redis/serialization error: error is logged, no exception propagates, cache state is unchanged or partially written
      - Function always resolves (never rejects the Promise)

    Errors:
      - redis_connection_failure (None): RedisClientPort is unreachable or returns a connection error on HSET or EXPIRE
          behavior: Error is logged at WARN level. Function resolves void. Cache may be partially written.
      - serialization_failure (None): A NightlyRate object cannot be serialized to JSON (e.g. circular reference, BigInt without serializer)
          behavior: Error is logged at ERROR level. Function resolves void. No data written for the failed entry.
      - redis_timeout (None): Redis operation exceeds the client timeout threshold
          behavior: Error is logged at WARN level. Function resolves void. Cache may be partially written.

    Side effects: Writes to Redis: HSET on the shard key with date-keyed fields, Writes to Redis: EXPIRE on the shard key to set/reset TTL, Logs errors at WARN or ERROR level on failure
    Idempotent: yes
    """
    ...

def getCachedRates(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    date_range: DateRange,
) -> CacheResult:
    """
    Reads cached nightly rates for a unit within a date range from Redis. Uses HMGET to fetch all date fields in the range from the unit's hash key. Returns a discriminated CacheResult union: CacheHit with data if ALL requested dates are present and deserializable with matching schema version, or CacheMiss with reason 'miss' if any date is missing, or CacheMiss with reason 'error' on Redis/deserialization failure. Corrupt or version-mismatched entries trigger automatic invalidation (DEL) of the entire hash key. Never throws.

    Preconditions:
      - unit_id is a valid UUID v4 string
      - date_range.check_in < date_range.check_out (start before end)
      - Date range spans at most 730 days
      - RedisClientPort has been injected and is callable

    Postconditions:
      - Returns CacheHit if and only if ALL dates in [check_in, check_out) have valid, version-matched entries in Redis
      - CacheHit.data is sorted by date ascending and contains exactly (check_out - check_in) entries in days
      - Returns CacheMiss(reason='miss') if any date in the range has no cached entry or has a schema version mismatch
      - Returns CacheMiss(reason='error') if Redis is unreachable, times out, or returns malformed data
      - On schema version mismatch or deserialization failure: the entire hash key is asynchronously DEL'd (auto-invalidation)
      - Function always resolves (never rejects the Promise)

    Errors:
      - redis_connection_failure (CacheMiss): RedisClientPort is unreachable or returns a connection error on HMGET
          reason: error
          behavior: Error logged at WARN level. Returns CacheMiss with reason 'error'.
      - redis_timeout (CacheMiss): Redis HMGET operation exceeds client timeout threshold
          reason: error
          behavior: Error logged at WARN level. Returns CacheMiss with reason 'error'.
      - deserialization_failure (CacheMiss): One or more cached JSON payloads cannot be parsed or fail structural validation
          reason: error
          behavior: Error logged at ERROR level. Triggers async DEL of the hash key. Returns CacheMiss with reason 'error'.
      - schema_version_mismatch (CacheMiss): One or more cached payloads have _v != current_schema_version
          reason: miss
          behavior: Stale data treated as miss. Triggers async DEL of the hash key. Returns CacheMiss with reason 'miss'.
      - partial_cache_miss (CacheMiss): Some dates in the range are cached but others are not (HMGET returns nil for some fields)
          reason: miss
          behavior: Partial data is discarded. Returns CacheMiss with reason 'miss'. No auto-invalidation triggered.
      - invalid_date_range (CacheMiss): check_in >= check_out or date range exceeds 730 days
          reason: error
          behavior: Validation error logged at ERROR level. Returns CacheMiss with reason 'error'. No Redis call made.

    Side effects: Reads from Redis: HMGET on the shard key for each date in the range, May write to Redis: DEL on the shard key if deserialization fails or schema version mismatches (async, fire-and-forget), Logs errors at WARN or ERROR level on failure
    Idempotent: yes
    """
    ...

def invalidateUnit(
    unit_id: UnitId,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> None:
    """
    Deletes the entire Redis hash key for a unit, removing all cached nightly rates for that unit across all dates. Idempotent: succeeds silently if the key does not exist. Fire-and-forget safe: all Redis errors are caught, logged, and swallowed — this function never throws.

    Preconditions:
      - unit_id is a valid UUID v4 string
      - RedisClientPort has been injected and is callable (connection may be down; errors are swallowed)

    Postconditions:
      - On success: Redis key at shardKey(unit_id) no longer exists
      - On Redis error: error is logged, no exception propagates
      - Idempotent: calling with a non-existent key is a no-op success
      - Function always resolves (never rejects the Promise)

    Errors:
      - redis_connection_failure (None): RedisClientPort is unreachable or returns a connection error on DEL
          behavior: Error is logged at WARN level. Function resolves void. Key may still exist in Redis.
      - redis_timeout (None): Redis DEL operation exceeds client timeout threshold
          behavior: Error is logged at WARN level. Function resolves void. Key may still exist in Redis.

    Side effects: Writes to Redis: DEL on the shard key, Logs errors at WARN level on failure
    Idempotent: yes
    """
    ...
