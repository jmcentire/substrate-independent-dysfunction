# === Pricing Service (pricing_service) v1 ===
#  Dependencies: pricing_schema, pricing_cache_engine, pricing_api, pricing_tests
# Rate cache service providing fast price lookups for rental units. Stores nightly rates hash-sharded by unit_id in Redis for O(1) lookups. Supports dynamic pricing with date-specific rates, seasonal rates, length-of-stay discounts, and fees/taxes computation. PostgreSQL stores authoritative rate configurations; Redis is the hot cache sharded by unit_id hash for even distribution. Thin orchestration facade exposing 7 public functions via a factory-created PricingService object: getQuote (cache-aside + full computation pipeline), getRates (cache-aside nightly rate lookup), updateRateConfiguration (write-through + cache invalidation), upsertFee (write-through + cache invalidation), warmCache (bulk cache population), invalidateCache (explicit cache bust), plus the createPricingService factory. Cache strategy is cache-aside reads with write-through invalidation and graceful degradation (Redis failure → DB fallback, never an error to callers). The computation pipeline follows strict order: nightly rates → subtotal → LOS discount → flat fees → percentage fees → taxes → grand total. All arithmetic in CentsAmount (integer cents), bankers rounding for percentage calculations. File organization: mod.ts (public exports, <300 lines), types.ts (all contract types), service.ts (orchestration implementation), pipeline.ts (quote computation pipeline), deps.ts (dependency interface definitions).

# Module invariants:
#   - All monetary values (rates, fees, taxes, subtotals, totals, discounts) are represented as non-negative integers in minor currency units (CentsAmount). No floating-point arithmetic is ever used for money.
#   - All date ranges use half-open intervals [start, end) where start < end. A stay of N nights has checkIn and checkOut exactly N days apart.
#   - Currency codes are ISO 4217 three-letter uppercase codes. All monetary entities within a single quote/rate lookup share the same currency. Cross-currency mixing within a single operation is an error.
#   - The computation pipeline order is fixed and immutable: (1) resolve nightly rates, (2) sum to nightlySubtotal, (3) apply single best LOS discount → discountedSubtotal, (4) add flat fees, (5) add percentage fees on discountedSubtotal, (6) compute taxes on (discountedSubtotal + taxable fees), (7) grandTotal = discountedSubtotal + feesTotal + taxesTotal.
#   - Only the single best (highest discount percentage) active LOS discount where minNights <= numNights is applied. If no LOS discount qualifies, no discount is applied and discountedSubtotal equals nightlySubtotal.
#   - Percentage fees and tax rates are stored in basis points (0-10000 where 10000 = 100%). Integer division uses banker's rounding (round half to even).
#   - Taxes are computed on (discountedSubtotal + sum of taxable fee amounts). Non-taxable fees are excluded from the tax base.
#   - No public method of the PricingService ever throws an exception. All operations return ServiceResult (discriminated union: ok with value, or err with PricingServiceError).
#   - Cache failures (Redis unavailable, timeout, deserialization error) are always silent to callers. The service falls back to PostgreSQL on any cache failure and logs the error. Cache failures never produce a ServiceResultErr.
#   - Write-through cache invalidation occurs after PostgreSQL commit on all mutation operations (updateRateConfiguration, upsertFee). If cache invalidation fails, the DB mutation is NOT rolled back — cache will eventually expire via TTL.
#   - The Redis cache is hash-sharded by unit_id using consistent hashing (CRC32 or equivalent). The shard key format is 'pricing:{N}:{unit_id}' where N = hash(unit_id) % shard_count. Same unit_id always maps to the same shard.
#   - Cache-aside read pattern: check Redis → on miss, read from PostgreSQL → populate Redis (fire-and-forget). Cache reads require ALL dates in range to be present and valid; partial hits are treated as full misses.
#   - The PricingService facade is stateless and safe for concurrent use. All state resides in PostgreSQL (authoritative) and Redis (cache). Multiple PricingService instances sharing the same pool and Redis client are safe.
#   - Dependency errors from pricing_schema and pricing_cache_engine are wrapped into PricingServiceError with appropriate kind values. Internal error types are never leaked to callers.
#   - QuoteBreakdown line item sums are exact: nightlySubtotal == sum(nightlyRates[].rate), feesTotal == sum(fees[].resolvedAmount), taxesTotal == sum(taxes[].taxAmount), grandTotal == discountedSubtotal + feesTotal + taxesTotal. No rounding drift is permitted.

CentsAmount = primitive  # Branded integer type representing monetary value in minor currency units (e.g. cents for USD). Underlying storage is a 64-bit integer. Never use floating-point for money. Re-exported from pricing_schema.

CurrencyCode = primitive  # ISO 4217 three-letter currency code stored as string. Examples: USD, EUR, GBP, JPY. Validated via regex ^[A-Z]{3}$. Re-exported from pricing_schema.

UnitId = primitive  # Branded UUID v4 string identifying a rental unit. Re-exported from shared_foundation via pricing_cache_engine. Validated via regex ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$.

ISODateString = primitive  # ISO 8601 date string in YYYY-MM-DD format. Used for check-in, check-out, and nightly rate dates.

ISOTimestampString = primitive  # ISO 8601 UTC timestamp string (YYYY-MM-DDTHH:mm:ss.sssZ). Used for resolvedAt, computedAt timestamps.

DiscountPercent = primitive  # Branded integer representing a discount percentage in basis points (0-10000, where 10000 = 100%). Re-exported from pricing_schema.

TaxRateBasisPoints = primitive  # Branded integer representing a tax rate in basis points (0-10000, where 10000 = 100%). Re-exported from pricing_schema.

class PricingServiceErrorKind(Enum):
    """Discriminated union tag for all pricing service error types. Service-level errors that wrap dependency-level errors."""
    UNIT_NOT_FOUND = "UNIT_NOT_FOUND"
    DATES_UNCOVERED = "DATES_UNCOVERED"
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    INTERNAL_ERROR = "INTERNAL_ERROR"

class PricingServiceError:
    """Structured error type for all pricing service operations. Uses a discriminated union pattern via the kind field. Wraps dependency errors into service-level errors — never leaks internal error types."""
    kind: PricingServiceErrorKind            # required, The discriminated union tag identifying the error category.
    message: str                             # required, Human-readable error description.
    details: dict = {}                       # optional, Additional structured context (e.g. unitId, date range, mismatched currencies, underlying error kind).
    cause: str = None                        # optional, Underlying dependency error message, if any. For debugging only — not exposed to callers.

class FeeType(Enum):
    """Discriminated union tag for fee calculation method. Re-exported from pricing_schema."""
    FLAT = "FLAT"
    PERCENTAGE = "PERCENTAGE"

class TaxType(Enum):
    """Enumeration of supported tax types. Re-exported from pricing_schema."""
    OCCUPANCY = "OCCUPANCY"
    SALES = "SALES"
    VAT = "VAT"
    TOURISM = "TOURISM"
    LOCAL = "LOCAL"
    STATE = "STATE"
    FEDERAL = "FEDERAL"
    CUSTOM = "CUSTOM"

class RateSource(Enum):
    """Identifies which source provided a resolved nightly rate."""
    BASE = "BASE"
    OVERRIDE = "OVERRIDE"

class NightlyRateEntry:
    """Per-night rate detail within a quote breakdown. Shows the date, the applicable rate in cents, and the source (base rate plan vs. date override)."""
    date: ISODateString                      # required, regex(^\d{4}-\d{2}-\d{2}$), The night date (YYYY-MM-DD). In a half-open [checkIn, checkOut) interval, this is the night starting on this date.
    rate: CentsAmount                        # required, range(value >= 0), Nightly rate in minor currency units for this date.
    source: RateSource                       # required, Whether this rate came from the base rate plan or a date-specific override.
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The rate plan ID from which this rate was resolved.

NightlyRateEntryList = list[NightlyRateEntry]
# Ordered list of nightly rate entries, one per night in [checkIn, checkOut). Length equals numNights.

class ResolvedFeeLineItem:
    """A fee resolved to an absolute CentsAmount for a specific booking context within the quote pipeline."""
    feeId: str                               # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The source fee record ID.
    name: str                                # required, Human-readable fee name.
    feeType: FeeType                         # required, FLAT or PERCENTAGE.
    originalAmount: int                      # required, range(value >= 0), Original amount from the fee record (cents if FLAT, basis points if PERCENTAGE).
    resolvedAmount: CentsAmount              # required, range(value >= 0), Computed fee in minor currency units for this booking.
    isTaxable: bool                          # required, Whether this fee is subject to taxes (included in the tax base).

ResolvedFeeLineItemList = list[ResolvedFeeLineItem]
# Ordered list of resolved fee line items. Flat fees appear before percentage fees per pipeline specification.

class ResolvedTaxLineItem:
    """A tax resolved to an absolute CentsAmount for a specific booking context within the quote pipeline."""
    taxId: str                               # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The source tax record ID.
    name: str                                # required, Human-readable tax name.
    taxType: TaxType                         # required, Classification of the tax.
    rateBasisPoints: TaxRateBasisPoints      # required, range(0 <= value <= 10000), The tax rate in basis points (0-10000).
    taxableAmount: CentsAmount               # required, range(value >= 0), The base amount on which this tax was computed (discountedSubtotal + taxable fee amounts).
    taxAmount: CentsAmount                   # required, range(value >= 0), Computed tax in minor currency units using bankers rounding.

ResolvedTaxLineItemList = list[ResolvedTaxLineItem]
# Ordered list of resolved tax line items.

class ResolvedLosDiscount:
    """The applied length-of-stay discount details. Present only when the stay qualifies for a discount."""
    losDiscountId: str                       # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), Source LOS discount record ID.
    minNights: int                           # required, range(value >= 1), Minimum number of nights required for this discount tier.
    discountPctBasisPoints: DiscountPercent  # required, range(0 <= value <= 10000), Discount in basis points (0-10000).
    discountAmount: CentsAmount              # required, range(value >= 0), Absolute discount amount in minor currency units. Computed via bankers rounding.

OptionalResolvedLosDiscount = ResolvedLosDiscount | None

class QuoteBreakdown:
    """Complete quote breakdown output from the pricing pipeline. Includes per-night rates, subtotals, LOS discount, fees, taxes, and grand total. All amounts in minor currency units (CentsAmount). Computation order: (1) resolve nightly rates → nightlySubtotal, (2) apply best LOS discount → discountedSubtotal, (3) add flat fees, (4) add percentage fees → feesTotal, (5) compute taxes on (discountedSubtotal + taxable fees) → taxesTotal, (6) grandTotal = discountedSubtotal + feesTotal + taxesTotal."""
    unitId: UnitId                           # required, The rental unit this quote is for.
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The primary rate plan used for resolution.
    checkIn: ISODateString                   # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive check-in date (YYYY-MM-DD).
    checkOut: ISODateString                  # required, regex(^\d{4}-\d{2}-\d{2}$), Exclusive check-out date (YYYY-MM-DD).
    numNights: int                           # required, range(value >= 1), Number of nights = checkOut - checkIn in days.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), ISO 4217 currency code for all amounts in this breakdown.
    nightlyRates: NightlyRateEntryList       # required, Per-night rate details. Length must equal numNights.
    nightlySubtotal: CentsAmount             # required, range(value >= 0), Sum of all nightly rates before LOS discount.
    losDiscount: OptionalResolvedLosDiscount = null # optional, Applied LOS discount, if stay qualifies. Null if no discount.
    discountedSubtotal: CentsAmount          # required, range(value >= 0), nightlySubtotal - losDiscount.discountAmount (or nightlySubtotal if no LOS discount). Always >= 0.
    fees: ResolvedFeeLineItemList            # required, All resolved fees. Flat fees appear before percentage fees.
    feesTotal: CentsAmount                   # required, range(value >= 0), Sum of all resolved fee amounts.
    taxableAmount: CentsAmount               # required, range(value >= 0), discountedSubtotal + sum of taxable fee amounts. Base for tax computation.
    taxes: ResolvedTaxLineItemList           # required, All resolved taxes computed on the taxable amount.
    taxesTotal: CentsAmount                  # required, range(value >= 0), Sum of all resolved tax amounts.
    grandTotal: CentsAmount                  # required, range(value >= 0), discountedSubtotal + feesTotal + taxesTotal.
    resolvedAt: ISOTimestampString           # required, ISO 8601 UTC timestamp when this quote was computed.

class QuoteOptions:
    """Optional parameters for getQuote that do not affect core pricing logic."""
    guests: int = 1                          # optional, range(1 <= value <= 100), Number of guests. May affect per-guest fees if configured.
    skipCache: bool = false                  # optional, If true, bypasses Redis cache and reads directly from PostgreSQL.
    includeInactive: bool = false            # optional, If true, includes inactive fees/taxes in the breakdown (for preview/admin use).

class RatesResult:
    """Result of a nightly rate lookup (getRates). Contains per-night rates without fee/tax computation."""
    unitId: UnitId                           # required, The rental unit.
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The rate plan used for resolution.
    startDate: ISODateString                 # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive start date (YYYY-MM-DD).
    endDate: ISODateString                   # required, regex(^\d{4}-\d{2}-\d{2}$), Exclusive end date (YYYY-MM-DD).
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), ISO 4217 currency code.
    nightlyRates: NightlyRateEntryList       # required, Per-night rate details. One entry per night in [startDate, endDate).
    cachedAt: ISOTimestampString = None      # optional, If served from cache, the timestamp when the cache entry was written. Empty if from DB.

class RateConfigurationInput:
    """Input for updateRateConfiguration. Partial update — only provided fields are applied. Delegates to pricing_schema for persistence."""
    ratePlanName: str = None                 # optional, length(1 <= length <= 255), Updated rate plan name.
    baseRate: CentsAmount = None             # optional, range(value >= 0), Updated base nightly rate in cents.
    currency: CurrencyCode = None            # optional, regex(^[A-Z]{3}$), Updated currency. Must match existing currency or all rates must be reconfigured.
    isActive: bool = None                    # optional, Activation/deactivation flag.

class UpdateResult:
    """Result of an updateRateConfiguration operation."""
    unitId: UnitId                           # required, The unit whose configuration was updated.
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The updated rate plan ID.
    updatedFields: list                      # required, List of field names that were actually updated.
    cacheInvalidated: bool                   # required, Whether the Redis cache was successfully invalidated for this unit.
    updatedAt: ISOTimestampString            # required, Timestamp of the update.

class FeeInput:
    """Input for upsertFee. Creates or updates a fee for a unit. Delegates to pricing_schema for persistence."""
    name: str                                # required, length(1 <= length <= 255), Human-readable fee name.
    feeType: FeeType                         # required, FLAT for absolute CentsAmount, PERCENTAGE for basis points of subtotal.
    amount: int                              # required, range(value >= 0), Fee amount: CentsAmount when FLAT, basis points (0-10000) when PERCENTAGE.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), Currency code. Must match the rate plan currency for the same unit.
    isTaxable: bool = false                  # optional, Whether this fee is subject to taxes.
    isActive: bool = true                    # optional, Whether this fee is currently active.
    feeId: str = None                        # optional, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), If provided, updates the existing fee with this ID. If omitted, creates a new fee.

class FeeResult:
    """Result of an upsertFee operation."""
    feeId: str                               # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), The ID of the created or updated fee.
    unitId: UnitId                           # required, The unit this fee belongs to.
    created: bool                            # required, True if a new fee was created, false if an existing fee was updated.
    cacheInvalidated: bool                   # required, Whether the Redis cache was successfully invalidated.
    updatedAt: ISOTimestampString            # required, Timestamp of the operation.

class DateRange:
    """Half-open date range [startDate, endDate) used for warmCache and rate queries."""
    startDate: ISODateString                 # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive start date (YYYY-MM-DD).
    endDate: ISODateString                   # required, regex(^\d{4}-\d{2}-\d{2}$), Exclusive end date (YYYY-MM-DD). Must be > startDate.

UnitIdList = list[UnitId]
# List of unit IDs for bulk operations.

class WarmCacheUnitResult:
    """Cache warm result for a single unit within a bulk warmCache operation."""
    unitId: UnitId                           # required, The unit ID.
    success: bool                            # required, Whether the cache was successfully populated for this unit.
    nightsCached: int                        # required, range(value >= 0), Number of nightly rate entries written to cache. 0 on failure.
    errorMessage: str = None                 # optional, Error message if success is false.

WarmCacheUnitResultList = list[WarmCacheUnitResult]
# List of per-unit cache warm results.

class WarmCacheResult:
    """Aggregate result of a bulk warmCache operation."""
    totalUnits: int                          # required, range(value >= 0), Total number of units requested.
    successCount: int                        # required, range(value >= 0), Number of units successfully cached.
    failureCount: int                        # required, range(value >= 0), Number of units that failed to cache.
    results: WarmCacheUnitResultList         # required, Per-unit results.
    durationMs: float                        # required, range(value >= 0), Total duration of the warm operation in milliseconds.

class InvalidateResult:
    """Result of an explicit cache invalidation."""
    unitId: UnitId                           # required, The unit whose cache was invalidated.
    success: bool                            # required, Whether the invalidation was successful. True even if the key did not exist (idempotent).
    keyExisted: bool                         # required, Whether a cache key actually existed and was deleted.

class DatabasePoolConfig:
    """Configuration for the PostgreSQL connection pool."""
    connectionString: str                    # required, PostgreSQL connection string.
    maxConnections: int = 10                 # optional, range(1 <= value <= 100), Maximum number of connections in the pool.

class RedisClientConfig:
    """Configuration for the Redis client used by the cache engine."""
    url: str                                 # required, Redis connection URL.
    connectTimeoutMs: int = 5000             # optional, range(value >= 100), Connection timeout in milliseconds.

class CacheConfig:
    """Cache-specific configuration options."""
    defaultTtlSeconds: int = 600             # optional, range(value >= 0), Default cache TTL in seconds (default 10 minutes).
    shardCount: int = 64                     # optional, range(1 <= value <= 4096), Number of hash shards for Redis key distribution.
    warmBatchSize: int = 50                  # optional, range(1 <= value <= 500), Number of units to warm concurrently in warmCache.

class PricingServiceConfig:
    """Top-level configuration for creating a PricingService instance via the factory function."""
    databasePool: any                        # required, A connected PostgreSQL database pool instance. Used to create the pricing_schema repository.
    redisClient: any                         # required, A connected Redis client instance. Used to create the pricing_cache_engine.
    cache: CacheConfig = {}                  # optional, Cache configuration overrides. Uses sensible defaults if not provided.
    enableCacheLogging: bool = true          # optional, Whether to log cache hit/miss/error events.

class PricingService:
    """The pricing service facade object returned by createPricingService. Exposes 6 methods: getQuote, getRates, updateRateConfiguration, upsertFee, warmCache, invalidateCache. All methods return Result types and never throw."""
    getQuote: any                            # required, Method: (unitId, checkIn, checkOut, currency, options?) -> Promise<ServiceResult<QuoteBreakdown>>.
    getRates: any                            # required, Method: (unitId, startDate, endDate, currency) -> Promise<ServiceResult<RatesResult>>.
    updateRateConfiguration: any             # required, Method: (unitId, config) -> Promise<ServiceResult<UpdateResult>>.
    upsertFee: any                           # required, Method: (unitId, fee) -> Promise<ServiceResult<FeeResult>>.
    warmCache: any                           # required, Method: (unitIds, dateRange) -> Promise<ServiceResult<WarmCacheResult>>.
    invalidateCache: any                     # required, Method: (unitId) -> Promise<ServiceResult<InvalidateResult>>.

class ServiceResultOk:
    """Success branch of the service-level Result type. Contains the value and no error."""
    ok: bool                                 # required, custom(value == true), Always true for success.
    value: any                               # required, The successful result value (generic T — QuoteBreakdown, RatesResult, UpdateResult, FeeResult, WarmCacheResult, or InvalidateResult).
    error: None                              # required, Always null for success.

class ServiceResultErr:
    """Error branch of the service-level Result type. Contains the PricingServiceError and no value."""
    ok: bool                                 # required, custom(value == false), Always false for error.
    value: None                              # required, Always null for error.
    error: PricingServiceError               # required, The pricing service error.

ServiceResult = ServiceResultOk | ServiceResultErr

def createPricingService(
    config: PricingServiceConfig,
) -> ServiceResult:
    """
    Factory function that creates and returns a PricingService facade object. Accepts a PricingServiceConfig containing a connected database pool, Redis client, and optional configuration overrides. Internally creates a pricing_schema repository and pricing_cache_engine instance, wiring them together into the orchestration service. The returned PricingService object exposes all 6 public methods. This function validates the configuration but does NOT test database/Redis connectivity — that is deferred to first use with graceful degradation.

    Preconditions:
      - config.databasePool is a valid database pool instance (may or may not be connected)
      - config.redisClient is a valid Redis client instance (may or may not be connected)
      - If config.cache is provided, shardCount >= 1 and defaultTtlSeconds >= 0

    Postconditions:
      - On success, returns ServiceResultOk with value being a PricingService object with all 6 methods
      - The returned PricingService is safe for concurrent use
      - No database or Redis calls are made during construction
      - On configuration validation failure, returns ServiceResultErr with kind=CONFIGURATION_ERROR

    Errors:
      - invalid_database_pool (PricingServiceError): config.databasePool is null, undefined, or not a valid pool interface
          kind: CONFIGURATION_ERROR
      - invalid_redis_client (PricingServiceError): config.redisClient is null, undefined, or not a valid Redis client interface
          kind: CONFIGURATION_ERROR
      - invalid_cache_config (PricingServiceError): Cache configuration values are out of valid ranges (e.g. shardCount < 1, defaultTtlSeconds < 0)
          kind: CONFIGURATION_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def getQuote(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    checkIn: ISODateString,    # regex(^\d{4}-\d{2}-\d{2}$)
    checkOut: ISODateString,   # regex(^\d{4}-\d{2}-\d{2}$)
    currency: CurrencyCode,    # regex(^[A-Z]{3}$)
    options: QuoteOptions = {},
) -> ServiceResult:
    """
    Computes a full price breakdown for a potential stay. Implements cache-aside pattern: checks Redis cache first for nightly rates, falls back to PostgreSQL via pricing_schema.resolveRatesForDateRange on miss, populates cache on miss. Then runs the computation pipeline in strict order: (1) resolve nightly rates from cache/DB, (2) sum to nightlySubtotal, (3) apply best LOS discount → discountedSubtotal, (4) add flat fees, (5) add percentage fees on discountedSubtotal → feesTotal, (6) compute taxes on (discountedSubtotal + taxable fees) → taxesTotal, (7) grandTotal = discountedSubtotal + feesTotal + taxesTotal. Each step produces typed line items for full transparency. All arithmetic in integer cents with bankers rounding. Redis failures are silently logged and result in DB fallback — never an error to callers.

    Preconditions:
      - checkIn < checkOut (half-open interval must contain at least one night)
      - checkIn and checkOut are valid calendar dates in YYYY-MM-DD format
      - The date range spans at most 730 nights
      - currency is a valid ISO 4217 code

    Postconditions:
      - On success, value is a QuoteBreakdown
      - nightlyRates has exactly (checkOut - checkIn) entries, one per night
      - nightlySubtotal == sum of all nightlyRates[i].rate
      - discountedSubtotal == nightlySubtotal - (losDiscount.discountAmount or 0)
      - discountedSubtotal >= 0
      - feesTotal == sum of all fees[i].resolvedAmount
      - taxableAmount == discountedSubtotal + sum of fees where isTaxable
      - taxesTotal == sum of all taxes[i].taxAmount
      - grandTotal == discountedSubtotal + feesTotal + taxesTotal
      - All CentsAmount values are non-negative integers
      - currency in the breakdown matches the requested currency and the rate plan's currency
      - resolvedAt is set to the current UTC timestamp
      - Redis cache failures are transparent — quote is computed from DB on cache miss/error

    Errors:
      - unit_not_found (PricingServiceError): No active rate plan exists for the given unitId
          kind: UNIT_NOT_FOUND
      - dates_uncovered (PricingServiceError): One or more nights in the date range have no rate coverage (no base rate and no override)
          kind: DATES_UNCOVERED
      - invalid_date_range (PricingServiceError): checkIn >= checkOut, dates are not valid calendar dates, or range exceeds 730 nights
          kind: INVALID_DATE_RANGE
      - currency_mismatch (PricingServiceError): Requested currency does not match the unit's configured rate plan currency
          kind: CURRENCY_MISMATCH
      - configuration_error (PricingServiceError): Rate plan, fees, or taxes have inconsistent or invalid configuration (e.g. mixed currencies within a unit)
          kind: CONFIGURATION_ERROR
      - internal_error (PricingServiceError): Unexpected database error or unrecoverable internal failure during computation
          kind: INTERNAL_ERROR

    Side effects: May write to Redis cache on cache miss (fire-and-forget, errors swallowed), Logs cache hit/miss/error events if enableCacheLogging is true
    Idempotent: yes
    """
    ...

def getRates(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    startDate: ISODateString,  # regex(^\d{4}-\d{2}-\d{2}$)
    endDate: ISODateString,    # regex(^\d{4}-\d{2}-\d{2}$)
    currency: CurrencyCode,    # regex(^[A-Z]{3}$)
) -> ServiceResult:
    """
    Retrieves raw nightly rates for a unit and date range without fee/tax computation. Implements cache-aside pattern: checks Redis cache first, falls back to PostgreSQL on miss, populates cache on miss. Returns per-night rate entries with source attribution (base vs. override). Redis failures are silently logged and result in DB fallback.

    Preconditions:
      - startDate < endDate (half-open interval)
      - startDate and endDate are valid calendar dates
      - Date range spans at most 730 nights
      - currency is a valid ISO 4217 code

    Postconditions:
      - On success, value is a RatesResult
      - nightlyRates has exactly (endDate - startDate) entries sorted by date ascending
      - Each entry has a rate >= 0 and a valid source
      - All rates use the requested currency
      - Redis cache failures are transparent — rates are fetched from DB on cache miss/error

    Errors:
      - unit_not_found (PricingServiceError): No active rate plan exists for the given unitId
          kind: UNIT_NOT_FOUND
      - dates_uncovered (PricingServiceError): One or more nights in the date range have no rate coverage
          kind: DATES_UNCOVERED
      - invalid_date_range (PricingServiceError): startDate >= endDate, dates are not valid, or range exceeds 730 nights
          kind: INVALID_DATE_RANGE
      - currency_mismatch (PricingServiceError): Requested currency does not match the unit's configured rate plan currency
          kind: CURRENCY_MISMATCH
      - internal_error (PricingServiceError): Unexpected database error or unrecoverable internal failure
          kind: INTERNAL_ERROR

    Side effects: May write to Redis cache on cache miss (fire-and-forget, errors swallowed), Logs cache hit/miss/error events if enableCacheLogging is true
    Idempotent: yes
    """
    ...

def updateRateConfiguration(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    config: RateConfigurationInput,
) -> ServiceResult:
    """
    Updates the rate configuration for a unit via write-through with cache invalidation. Delegates to pricing_schema for PostgreSQL persistence (updateRatePlan), then invalidates the Redis cache for the unit via pricing_cache_engine.invalidateUnit. The cache invalidation happens after the DB commit to ensure consistency. If cache invalidation fails, the DB update is still committed (cache will eventually expire or be warmed). Returns the updated configuration summary.

    Preconditions:
      - At least one field in config is provided (not all empty/default)
      - If config.currency is provided, it must be a valid ISO 4217 code
      - If config.baseRate is provided, it must be >= 0

    Postconditions:
      - On success, value is an UpdateResult
      - The rate plan in PostgreSQL reflects the updated fields
      - Redis cache for this unitId has been invalidated (or invalidation was attempted)
      - updatedAt reflects the time of the DB commit
      - cacheInvalidated is true if Redis invalidation succeeded, false if it failed (DB update still committed)

    Errors:
      - unit_not_found (PricingServiceError): No rate plan exists for the given unitId
          kind: UNIT_NOT_FOUND
      - currency_mismatch (PricingServiceError): Provided currency does not match the existing rate plan currency
          kind: CURRENCY_MISMATCH
      - configuration_error (PricingServiceError): Validation failure on input fields (e.g. empty name, negative baseRate)
          kind: CONFIGURATION_ERROR
      - internal_error (PricingServiceError): Database error during update
          kind: INTERNAL_ERROR

    Side effects: Updates rate plan row in PostgreSQL pricing.rate_plans, Invalidates Redis cache key for the unit (fire-and-forget on failure), Logs cache invalidation success/failure
    Idempotent: yes
    """
    ...

def upsertFee(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    fee: FeeInput,
) -> ServiceResult:
    """
    Creates or updates a fee for a rental unit via write-through with cache invalidation. If feeId is provided in the input, updates the existing fee; otherwise creates a new fee. Delegates to pricing_schema (createFee or updateFee) for PostgreSQL persistence, then invalidates the Redis cache for the unit. Cache invalidation failure does not roll back the DB change.

    Preconditions:
      - fee.name is non-empty and <= 255 characters
      - fee.amount >= 0
      - If fee.feeType is PERCENTAGE, fee.amount must be 0-10000 (basis points)
      - fee.currency must match the unit's configured rate plan currency
      - If fee.feeId is provided, it must be a valid UUID v4 referencing an existing fee

    Postconditions:
      - On success, value is a FeeResult
      - The fee exists in PostgreSQL pricing.fees with the provided values
      - created is true if a new row was inserted, false if an existing row was updated
      - Redis cache for this unitId has been invalidated (or invalidation was attempted)
      - cacheInvalidated reflects whether Redis invalidation succeeded

    Errors:
      - unit_not_found (PricingServiceError): No rate plan exists for the given unitId (fees require a rate plan context)
          kind: UNIT_NOT_FOUND
      - currency_mismatch (PricingServiceError): Fee currency does not match the unit's configured rate plan currency
          kind: CURRENCY_MISMATCH
      - configuration_error (PricingServiceError): Validation failure on fee input (e.g. empty name, negative amount, PERCENTAGE amount > 10000)
          kind: CONFIGURATION_ERROR
      - internal_error (PricingServiceError): Database error during fee creation/update
          kind: INTERNAL_ERROR

    Side effects: Inserts or updates a row in PostgreSQL pricing.fees, Invalidates Redis cache key for the unit (fire-and-forget on failure), Logs cache invalidation success/failure
    Idempotent: no
    """
    ...

def warmCache(
    unitIds: UnitIdList,       # length(1 <= length <= 10000)
    dateRange: DateRange,
) -> ServiceResult:
    """
    Bulk cache population for a list of units over a date range. For each unit, resolves rates from PostgreSQL via pricing_schema.resolveRatesForDateRange and writes them to Redis via pricing_cache_engine.cacheRates. Processes units in batches (configurable via CacheConfig.warmBatchSize) for controlled concurrency. Individual unit failures do not abort the batch — each unit's result is reported independently. This is a best-effort operation designed for pre-warming before high-traffic periods.

    Preconditions:
      - unitIds is non-empty and contains at most 10000 entries
      - dateRange.startDate < dateRange.endDate
      - Date range spans at most 730 days
      - All unit IDs are valid UUID v4 strings

    Postconditions:
      - On success, value is a WarmCacheResult
      - totalUnits == length of unitIds
      - successCount + failureCount == totalUnits
      - results list has one entry per unit in unitIds
      - For each successful unit, nightsCached == (endDate - startDate) in days
      - durationMs reflects actual wall-clock time of the operation
      - Individual unit failures (DB errors, missing rate plans) are reported per-unit, not as a top-level error

    Errors:
      - invalid_date_range (PricingServiceError): dateRange.startDate >= dateRange.endDate or range exceeds 730 days
          kind: INVALID_DATE_RANGE
      - configuration_error (PricingServiceError): unitIds is empty or exceeds 10000
          kind: CONFIGURATION_ERROR
      - internal_error (PricingServiceError): Catastrophic failure (e.g. database pool exhausted) preventing any processing
          kind: INTERNAL_ERROR

    Side effects: Reads rate configurations from PostgreSQL for each unit, Writes nightly rate data to Redis cache for each successfully resolved unit, Logs progress and per-unit errors
    Idempotent: yes
    """
    ...

def invalidateCache(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> ServiceResult:
    """
    Explicitly invalidates (deletes) the Redis cache for a specific unit. Delegates to pricing_cache_engine.invalidateUnit. Idempotent — succeeds even if the cache key does not exist. Redis failures are caught and reported in the result (success=false) but never thrown.

    Preconditions:
      - unitId is a valid UUID v4 string

    Postconditions:
      - On success, value is an InvalidateResult
      - If Redis was reachable, the cache key for the unit no longer exists
      - success is true if Redis DEL succeeded or key did not exist, false if Redis was unreachable
      - keyExisted is true only if a key was actually deleted
      - This function never returns a ServiceResultErr — Redis failures are reported via InvalidateResult.success=false

    Errors:
      - invalid_unit_id (PricingServiceError): unitId is not a valid UUID v4 string
          kind: CONFIGURATION_ERROR

    Side effects: Deletes Redis hash key for the unit's shard (if key exists), Logs cache invalidation result
    Idempotent: yes
    """
    ...
