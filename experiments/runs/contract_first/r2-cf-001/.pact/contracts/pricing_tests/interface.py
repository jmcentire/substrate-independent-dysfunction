# === Pricing Service Tests (pricing_tests) v1 ===
# Test suite for the rate cache pricing service. Covers unit tests for rate calculation logic (seasonal overlaps, LOS discounts, fee/tax stacking), cache shard distribution tests, API contract tests, cache miss/hit path tests, serialization round-trip tests, and edge cases (zero-night stays, currency rounding, overlapping rate overrides). All monetary values use integer minor-unit (cents) arithmetic. The computation pipeline order is codified as: base rates → seasonal/override resolution → LOS discount → flat fees → percentage fees → taxes. Each pipeline step is a pure function, independently testable. Tests use Deno BDD style with injected mock RedisClient and RateRepository interfaces.

# Module invariants:
#   - All monetary amounts are represented as non-negative integers in minor currency units (cents). No floating-point money arithmetic is ever used.
#   - CurrencyCode is always an uppercase ISO 4217 three-letter string (e.g. 'USD', 'EUR').
#   - All entity IDs (UnitId, RateConfigId, RateOverrideId) are UUID v4 strings matching the pattern [0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}.
#   - All date values (ISODateString) conform to ISO 8601 UTC format YYYY-MM-DDTHH:mm:ssZ.
#   - The calculation pipeline is always applied in strict order: base rates → seasonal/override resolution → LOS discount → flat fees → percentage fees → taxes.
#   - Shard assignment is deterministic: the same UnitId always maps to the same shard index for a given shard count.
#   - Result types always contain exactly one of 'ok' value or 'error' value, never both and never neither.
#   - Test fixture factories always produce valid domain objects with all required fields populated by sensible defaults.
#   - Mock call counters are monotonically non-decreasing integers starting at zero.
#   - PriceBreakdown line items sum exactly to the total field — no rounding drift is permitted.

CurrencyCode = primitive  # ISO 4217 three-letter uppercase currency code. Branded string type.

ISODateString = primitive  # ISO 8601 UTC date-time string (YYYY-MM-DDTHH:mm:ssZ). Branded string type.

UnitId = primitive  # Branded UUID v4 string identifying a rental unit.

RateConfigId = primitive  # Branded UUID v4 string identifying a rate configuration record.

RateOverrideId = primitive  # Branded UUID v4 string identifying a rate override record.

class Money:
    """Monetary value represented as integer minor units (cents) with a currency code. No floating-point arithmetic."""
    amount_minor: int                        # required, range(value >= 0), Amount in minor currency units (e.g. cents). Must be >= 0 for prices.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), ISO 4217 currency code.

class PricingErrorKind(Enum):
    """Discriminator for the PricingError union type."""
    RATE_NOT_FOUND = "RATE_NOT_FOUND"
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    ZERO_NIGHT_STAY = "ZERO_NIGHT_STAY"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    OVERLAPPING_OVERRIDES_CONFLICT = "OVERLAPPING_OVERRIDES_CONFLICT"
    SHARD_UNAVAILABLE = "SHARD_UNAVAILABLE"
    CACHE_DESERIALIZATION_ERROR = "CACHE_DESERIALIZATION_ERROR"
    INVALID_UNIT_ID = "INVALID_UNIT_ID"
    INVALID_DISCOUNT_CONFIGURATION = "INVALID_DISCOUNT_CONFIGURATION"
    TAX_RATE_OUT_OF_RANGE = "TAX_RATE_OUT_OF_RANGE"
    REPOSITORY_ERROR = "REPOSITORY_ERROR"
    SCHEMA_VALIDATION_ERROR = "SCHEMA_VALIDATION_ERROR"

class PricingError:
    """Discriminated error type for all pricing operations. The 'kind' field acts as the discriminator."""
    kind: PricingErrorKind                   # required, Error discriminator.
    message: str                             # required, Human-readable error description.
    context: dict = {}                       # optional, Arbitrary key-value context data for debugging (e.g. unit_id, date range).

class ResultOk:
    """Success branch of Result<T, PricingError>. Holds the value."""
    ok: bool                                 # required, Always true for success.
    value: any                               # required, The successful result value (generic T).
    error: None                              # required, Always None for success branch.

class ResultErr:
    """Error branch of Result<T, PricingError>. Holds the error."""
    ok: bool                                 # required, Always false for error.
    value: None                              # required, Always None for error branch.
    error: PricingError                      # required, The pricing error.

PricingResult = ResultOk | ResultErr

class DateRange:
    """Inclusive check-in to exclusive check-out date range."""
    check_in: ISODateString                  # required, Check-in date (inclusive).
    check_out: ISODateString                 # required, Check-out date (exclusive). Must be after check_in.

class NightlyRate:
    """A rate applicable to a specific date or date range for a unit."""
    date: ISODateString                      # required, The night this rate applies to.
    amount: Money                            # required, Nightly rate amount in minor units.
    source: RateSource                       # required, Where this rate came from (base, seasonal, override).

class RateSource(Enum):
    """Origin tier for a resolved nightly rate, in priority order (highest wins)."""
    BASE = "BASE"
    SEASONAL = "SEASONAL"
    DATE_OVERRIDE = "DATE_OVERRIDE"
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"

class RateConfig:
    """Authoritative rate configuration for a unit, stored in PostgreSQL."""
    id: RateConfigId                         # required, Unique rate config identifier.
    unit_id: UnitId                          # required, The rental unit this config belongs to.
    base_rate: Money                         # required, Default nightly rate when no overrides apply.
    currency: CurrencyCode                   # required, Currency for all rates in this config.
    seasonal_rates: SeasonalRateList         # required, Seasonal rate adjustments.
    date_overrides: DateOverrideList         # required, Date-specific rate overrides.
    los_discounts: LOSDiscountList           # required, Length-of-stay discount tiers.
    fees: FeeList                            # required, Applicable fees (flat and percentage).
    taxes: TaxList                           # required, Applicable taxes.

class SeasonalRate:
    """A seasonal rate adjustment applicable over a date range."""
    name: str                                # required, Descriptive name (e.g. 'Summer Peak').
    start_date: ISODateString                # required, Season start (inclusive).
    end_date: ISODateString                  # required, Season end (inclusive).
    nightly_amount: Money                    # required, Nightly rate during this season.
    priority: int                            # required, range(value >= 0), Higher priority wins when seasons overlap. Must be >= 0.

SeasonalRateList = list[SeasonalRate]
# List of seasonal rate entries.

class DateOverride:
    """A date-specific rate override (highest non-manual priority)."""
    id: RateOverrideId                       # required, Override identifier.
    date: ISODateString                      # required, The specific date.
    nightly_amount: Money                    # required, Override nightly rate.
    reason: str = None                       # optional, Reason for override.

DateOverrideList = list[DateOverride]
# List of date-specific overrides.

class LOSDiscount:
    """A length-of-stay discount tier. Applied after rate resolution."""
    min_nights: int                          # required, range(value >= 1), Minimum nights (inclusive) to qualify.
    discount_percent: int                    # required, range(0 <= value <= 100), Discount percentage as integer (e.g. 20 means 20%). Range 0-100.

LOSDiscountList = list[LOSDiscount]
# List of LOS discount tiers sorted by min_nights ascending.

class FeeType(Enum):
    """Discriminator for fee calculation method."""
    FLAT = "FLAT"
    PERCENTAGE = "PERCENTAGE"

class Fee:
    """A fee applied to a stay. Flat fees are in minor units; percentage fees are basis points on the subtotal after LOS discount."""
    name: str                                # required, Fee name (e.g. 'Cleaning Fee', 'Service Fee').
    fee_type: FeeType                        # required, FLAT or PERCENTAGE.
    amount_minor: int                        # required, range(value >= 0), For FLAT: amount in minor units. For PERCENTAGE: basis points (e.g. 500 = 5.00%).
    taxable: bool                            # required, Whether this fee is included in the tax base.

FeeList = list[Fee]
# Ordered list of fees. Flat fees are applied before percentage fees per pipeline spec.

class Tax:
    """A tax applied to the taxable subtotal (accommodation + taxable fees)."""
    name: str                                # required, Tax name (e.g. 'State Sales Tax', 'Tourism Levy').
    rate_basis_points: int                   # required, range(0 <= value <= 10000), Tax rate in basis points (e.g. 1000 = 10.00%).
    applies_to_fees: bool                    # required, Whether this tax applies to taxable fees in addition to accommodation.

TaxList = list[Tax]
# List of applicable taxes.

class LineItemType(Enum):
    """Type discriminator for price breakdown line items."""
    NIGHTLY_RATE = "NIGHTLY_RATE"
    LOS_DISCOUNT = "LOS_DISCOUNT"
    FLAT_FEE = "FLAT_FEE"
    PERCENTAGE_FEE = "PERCENTAGE_FEE"
    TAX = "TAX"

class LineItem:
    """A single line item in the price breakdown."""
    type: LineItemType                       # required, Line item category.
    label: str                               # required, Human-readable label.
    amount: Money                            # required, Line item amount. Discounts are negative.
    metadata: dict = {}                      # optional, Extra context (e.g. date, source tier, rate percent).

LineItemList = list[LineItem]
# Ordered list of price breakdown line items.

NightlyRateList = list[NightlyRate]
# List of resolved nightly rates.

class PriceBreakdown:
    """Itemized price breakdown for a stay. The sum of all line_items amounts equals total exactly (no rounding drift)."""
    unit_id: UnitId                          # required, The rental unit.
    date_range: DateRange                    # required, Stay date range.
    num_nights: int                          # required, range(value >= 1), Number of nights in the stay.
    currency: CurrencyCode                   # required, Currency for all monetary amounts.
    nightly_rates: NightlyRateList           # required, Resolved rate per night with source attribution.
    accommodation_subtotal: Money            # required, Sum of nightly rates before discounts.
    los_discount_applied: bool               # required, Whether an LOS discount was applied.
    los_discount_percent: int                # required, LOS discount percentage applied (0 if none).
    accommodation_after_discount: Money      # required, Accommodation subtotal after LOS discount.
    fees_subtotal: Money                     # required, Sum of all fees.
    tax_subtotal: Money                      # required, Sum of all taxes.
    total: Money                             # required, Final total: accommodation_after_discount + fees_subtotal + tax_subtotal.
    line_items: LineItemList                 # required, Ordered, itemized breakdown. Sum of amounts equals total.

class StayRequest:
    """API request to compute pricing for a stay. Validated by Zod schema."""
    unit_id: UnitId                          # required, Rental unit to price.
    check_in: ISODateString                  # required, Check-in date.
    check_out: ISODateString                 # required, Check-out date.
    currency: CurrencyCode                   # required, Requested currency. Must match unit's configured currency.

class PriceResponse:
    """API response containing the full price breakdown. Validated by Zod schema."""
    breakdown: PriceBreakdown                # required, The full itemized price breakdown.
    cached: bool                             # required, Whether the result was served from cache.
    computed_at: ISODateString               # required, Timestamp when the price was computed.

class ShardAssignment:
    """Result of mapping a unit_id to a Redis shard."""
    unit_id: UnitId                          # required, The unit being sharded.
    shard_index: int                         # required, range(value >= 0), Zero-based shard index.
    shard_key: str                           # required, The Redis key prefix for this shard (e.g. 'rate_shard:7').

class ShardDistributionReport:
    """Report of shard distribution uniformity across a set of unit IDs."""
    shard_count: int                         # required, Total number of shards.
    unit_count: int                          # required, Total units distributed.
    counts_per_shard: list                   # required, List of int counts, one per shard.
    max_deviation_percent: float             # required, Maximum deviation from perfectly uniform distribution, as a percentage.
    is_uniform: bool                         # required, True if max_deviation_percent is within acceptable threshold.

class CachePathResult(Enum):
    """Outcome of a cache lookup operation."""
    HIT = "HIT"
    MISS_THEN_WRITEBACK = "MISS_THEN_WRITEBACK"
    MISS_REPO_ERROR = "MISS_REPO_ERROR"
    DESERIALIZATION_ERROR = "DESERIALIZATION_ERROR"
    SHARD_UNAVAILABLE = "SHARD_UNAVAILABLE"

class CacheLookupReport:
    """Diagnostic report from a cache lookup, used in cache path tests."""
    path: CachePathResult                    # required, Which code path was exercised.
    redis_get_calls: int                     # required, range(value >= 0), Number of Redis GET calls made.
    redis_set_calls: int                     # required, range(value >= 0), Number of Redis SET calls made (writebacks).
    repo_calls: int                          # required, range(value >= 0), Number of RateRepository calls made.
    ttl_seconds: int                         # required, TTL set on writeback, or -1 if no writeback.
    result: PricingResult                    # required, The pricing result from the lookup.

class MockRedisClient:
    """Mock Redis client injected in tests. Tracks call counts and allows pre-configured responses."""
    get_responses: dict                      # required, Map of key -> pre-configured response (str value or None for miss).
    get_call_count: int                      # required, Number of GET calls made.
    set_call_count: int                      # required, Number of SET calls made.
    last_set_ttl: int                        # required, TTL of last SET call, or -1 if none.
    should_fail: bool                        # required, If true, all operations throw a shard unavailable error.

class MockRateRepository:
    """Mock PostgreSQL rate repository injected in tests. Tracks call counts and returns pre-configured rate configs."""
    configs: dict                            # required, Map of unit_id -> RateConfig for lookup.
    call_count: int                          # required, Number of lookup calls made.
    should_fail: bool                        # required, If true, all operations return REPOSITORY_ERROR.

class TestFixtureOverrides:
    """Optional overrides passed to fixture factory functions (buildRate, buildStay, etc.)."""
    unit_id: UnitId = None                   # optional, Override unit ID.
    base_rate_minor: int = None              # optional, Override base rate in minor units.
    currency: CurrencyCode = None            # optional, Override currency.
    check_in: ISODateString = None           # optional, Override check-in date.
    check_out: ISODateString = None          # optional, Override check-out date.
    seasonal_rates: SeasonalRateList = None  # optional, Override seasonal rates.
    date_overrides: DateOverrideList = None  # optional, Override date-specific rates.
    los_discounts: LOSDiscountList = None    # optional, Override LOS discounts.
    fees: FeeList = None                     # optional, Override fees.
    taxes: TaxList = None                    # optional, Override taxes.

class SerializationRoundTripResult:
    """Result of a JSON serialization round-trip test."""
    original_json: str                       # required, JSON string of the original object.
    deserialized_json: str                   # required, JSON string after deserialize-then-reserialize.
    is_identical: bool                       # required, Whether the two JSON strings are semantically identical.
    type_name: str                           # required, Name of the type being round-tripped.

class SchemaValidationResult:
    """Result of validating a payload against its Zod schema."""
    valid: bool                              # required, Whether the payload passed schema validation.
    errors: list                             # required, List of validation error strings (empty if valid).
    schema_name: str                         # required, Name of the Zod schema used.
    payload_summary: str                     # required, Brief summary of the payload tested.

class TestSuiteReport:
    """Aggregate report for an entire test file execution."""
    file_path: str                           # required, Relative path of the test file (e.g. 'services/pricing/tests/rate_calculation_test.ts').
    total_tests: int                         # required, Total number of test cases.
    passed: int                              # required, Number of passed tests.
    failed: int                              # required, Number of failed tests.
    skipped: int                             # required, Number of skipped tests.
    duration_ms: float                       # required, Total execution time in milliseconds.

def resolve_nightly_rates(
    rate_config: RateConfig,
    date_range: DateRange,
) -> PricingResult:
    """
    Pure function (pipeline step 1-2): resolves nightly rates for each night in the date range using the 4-tier override system (BASE < SEASONAL < DATE_OVERRIDE < MANUAL_OVERRIDE). Higher-tier rates win. For overlapping seasonal rates, the one with higher priority wins. Tested in rate_calculation_test.ts.

    Preconditions:
      - date_range.check_out > date_range.check_in
      - rate_config.base_rate.currency matches rate_config.currency
      - All seasonal_rates and date_overrides amounts share the same currency as rate_config.currency

    Postconditions:
      - On success, result contains a NightlyRateList with exactly (check_out - check_in) entries in calendar order
      - Each NightlyRate has a source reflecting the highest-priority tier that matched
      - All returned Money amounts share the same currency as rate_config.currency

    Errors:
      - invalid_date_range (INVALID_DATE_RANGE): check_out <= check_in
      - zero_night_stay (ZERO_NIGHT_STAY): check_out == check_in (0 nights)
      - overlapping_overrides_conflict (OVERLAPPING_OVERRIDES_CONFLICT): Multiple date overrides exist for the same date with different amounts
      - currency_mismatch (CURRENCY_MISMATCH): A seasonal rate or override has a different currency than the config

    Side effects: none
    Idempotent: yes
    """
    ...

def apply_los_discount(
    accommodation_subtotal: Money,
    num_nights: int,
    los_discounts: LOSDiscountList,
) -> PricingResult:
    """
    Pure function (pipeline step 3): applies the best matching length-of-stay discount to the accommodation subtotal. Selects the discount tier with the highest min_nights that the stay qualifies for. Tested in los_discount_test.ts.

    Preconditions:
      - num_nights >= 1
      - accommodation_subtotal.amount_minor >= 0
      - los_discounts is sorted by min_nights ascending
      - All discount_percent values are 0-100

    Postconditions:
      - On success, result contains a Money value <= accommodation_subtotal
      - If no discount tier matches, returned amount equals accommodation_subtotal
      - Discount is calculated as floor(subtotal * discount_percent / 100) to avoid rounding up
      - Currency of result matches accommodation_subtotal.currency

    Errors:
      - invalid_discount_config (INVALID_DISCOUNT_CONFIGURATION): LOS discount tiers have duplicate min_nights values

    Side effects: none
    Idempotent: yes
    """
    ...

def apply_flat_fees(
    fees: FeeList,
    currency: CurrencyCode,
) -> PricingResult:
    """
    Pure function (pipeline step 4): computes and appends all flat fees. Flat fees are absolute amounts independent of accommodation total. Tested in fee_tax_test.ts.

    Preconditions:
      - All FLAT fees have amounts in the specified currency

    Postconditions:
      - On success, result contains a LineItemList of FLAT_FEE line items
      - Each line item amount matches the fee's amount_minor exactly
      - All returned amounts use the specified currency

    Errors:
      - currency_mismatch (CURRENCY_MISMATCH): A flat fee is configured in a different currency

    Side effects: none
    Idempotent: yes
    """
    ...

def apply_percentage_fees(
    fees: FeeList,
    base_amount: Money,
) -> PricingResult:
    """
    Pure function (pipeline step 5): computes percentage-based fees on the post-discount accommodation subtotal. Percentage is in basis points. Result is floor-rounded. Tested in fee_tax_test.ts.

    Preconditions:
      - base_amount.amount_minor >= 0
      - All PERCENTAGE fee amounts represent basis points (0-10000)

    Postconditions:
      - On success, result contains a LineItemList of PERCENTAGE_FEE line items
      - Each fee amount = floor(base_amount.amount_minor * fee.amount_minor / 10000)
      - All returned amounts use base_amount.currency

    Side effects: none
    Idempotent: yes
    """
    ...

def apply_taxes(
    taxes: TaxList,
    accommodation_after_discount: Money,
    taxable_fees_total: Money,
) -> PricingResult:
    """
    Pure function (pipeline step 6): computes taxes on the taxable subtotal (accommodation after discount + taxable fees). Tax rate is in basis points. Result is floor-rounded. Tested in fee_tax_test.ts.

    Preconditions:
      - accommodation_after_discount and taxable_fees_total use the same currency
      - All tax rate_basis_points are 0-10000

    Postconditions:
      - On success, result contains a LineItemList of TAX line items
      - Each tax amount = floor(taxable_base * rate_basis_points / 10000)
      - Taxes that apply_to_fees use (accommodation_after_discount + taxable_fees_total) as base
      - Taxes that do not apply_to_fees use accommodation_after_discount only as base
      - All returned amounts use accommodation_after_discount.currency

    Errors:
      - tax_rate_out_of_range (TAX_RATE_OUT_OF_RANGE): A tax rate_basis_points exceeds 10000
      - currency_mismatch (CURRENCY_MISMATCH): accommodation_after_discount and taxable_fees_total currencies differ

    Side effects: none
    Idempotent: yes
    """
    ...

def calculate_total(
    rate_config: RateConfig,
    stay_request: StayRequest,
) -> PricingResult:
    """
    Pipeline composition function: executes the full pricing pipeline (resolve_nightly_rates → apply_los_discount → apply_flat_fees → apply_percentage_fees → apply_taxes) and assembles an itemized PriceBreakdown. Tested across rate_calculation_test.ts, los_discount_test.ts, fee_tax_test.ts, and edge_cases_test.ts.

    Preconditions:
      - stay_request.check_out > stay_request.check_in
      - stay_request.currency matches rate_config.currency
      - stay_request.unit_id matches rate_config.unit_id

    Postconditions:
      - On success, result.value is a PriceBreakdown
      - PriceBreakdown.total = accommodation_after_discount + fees_subtotal + tax_subtotal
      - Sum of all line_items amounts equals total exactly (no rounding drift)
      - PriceBreakdown.nightly_rates has exactly num_nights entries
      - Pipeline steps are applied in codified order: base → seasonal/override → LOS → flat fees → % fees → taxes

    Errors:
      - invalid_date_range (INVALID_DATE_RANGE): check_out <= check_in
      - zero_night_stay (ZERO_NIGHT_STAY): check_out == check_in
      - currency_mismatch (CURRENCY_MISMATCH): stay_request.currency != rate_config.currency
      - invalid_unit_id (INVALID_UNIT_ID): stay_request.unit_id != rate_config.unit_id
      - overlapping_overrides_conflict (OVERLAPPING_OVERRIDES_CONFLICT): Conflicting date overrides detected during rate resolution
      - invalid_discount_config (INVALID_DISCOUNT_CONFIGURATION): LOS discount tiers are malformed
      - tax_rate_out_of_range (TAX_RATE_OUT_OF_RANGE): A tax rate exceeds 100%

    Side effects: none
    Idempotent: yes
    """
    ...

def compute_shard_index(
    unit_id: UnitId,
    shard_count: int,          # range(value >= 1)
) -> ShardAssignment:
    """
    Deterministic shard assignment: maps a UnitId to a shard index via consistent hashing. Tested in cache_shard_test.ts.

    Preconditions:
      - shard_count >= 1
      - unit_id is a valid UUID v4 string

    Postconditions:
      - shard_index is in range [0, shard_count)
      - Same unit_id + shard_count always produces the same shard_index (deterministic)
      - shard_key follows the pattern 'rate_shard:{shard_index}'

    Errors:
      - invalid_unit_id (INVALID_UNIT_ID): unit_id is not a valid UUID v4

    Side effects: none
    Idempotent: yes
    """
    ...

def check_shard_distribution(
    unit_ids: list,
    shard_count: int,
    max_acceptable_deviation_percent: float,
) -> ShardDistributionReport:
    """
    Test utility: distributes a set of unit IDs across shards and reports uniformity. Used in cache_shard_test.ts to verify even distribution.

    Preconditions:
      - shard_count >= 1
      - unit_ids is non-empty
      - max_acceptable_deviation_percent > 0

    Postconditions:
      - counts_per_shard has exactly shard_count entries
      - Sum of counts_per_shard equals len(unit_ids)
      - is_uniform is true iff max_deviation_percent <= max_acceptable_deviation_percent

    Side effects: none
    Idempotent: yes
    """
    ...

def lookup_price_with_cache(
    stay_request: StayRequest,
    redis_client: MockRedisClient,
    rate_repository: MockRateRepository,
    shard_count: int,
    cache_ttl_seconds: int,
) -> CacheLookupReport:
    """
    Executes the cache-aware pricing lookup: checks Redis first (hit path), falls back to PostgreSQL via RateRepository on miss, writes back to cache on miss, then returns the computed PriceBreakdown. Uses injected RedisClient and RateRepository for testability. Tested in cache_path_test.ts.

    Preconditions:
      - stay_request is a valid StayRequest
      - shard_count >= 1
      - cache_ttl_seconds > 0

    Postconditions:
      - On HIT: redis_get_calls == 1, redis_set_calls == 0, repo_calls == 0
      - On MISS_THEN_WRITEBACK: redis_get_calls == 1, redis_set_calls == 1, repo_calls == 1, ttl_seconds == cache_ttl_seconds
      - On MISS_REPO_ERROR: redis_get_calls == 1, redis_set_calls == 0, repo_calls == 1, result is ResultErr
      - On DESERIALIZATION_ERROR: redis_get_calls == 1, result is ResultErr with CACHE_DESERIALIZATION_ERROR
      - On SHARD_UNAVAILABLE: result is ResultErr with SHARD_UNAVAILABLE

    Errors:
      - shard_unavailable (SHARD_UNAVAILABLE): Redis client is unavailable or fails
      - repository_error (REPOSITORY_ERROR): RateRepository fails on cache miss fallback
      - cache_deserialization_error (CACHE_DESERIALIZATION_ERROR): Cached data fails to deserialize into valid rate structure
      - rate_not_found (RATE_NOT_FOUND): RateRepository returns no config for the unit_id
      - invalid_unit_id (INVALID_UNIT_ID): unit_id is not a valid UUID

    Side effects: Increments redis_client.get_call_count, May increment redis_client.set_call_count on cache writeback, Increments rate_repository.call_count on cache miss, Sets redis_client.last_set_ttl on cache writeback
    Idempotent: no
    """
    ...

def validate_stay_request_schema(
    payload: dict,
) -> SchemaValidationResult:
    """
    Validates a raw payload against the StayRequest Zod schema. Tested in api_contract_test.ts.

    Postconditions:
      - If valid is true, errors is empty
      - If valid is false, errors contains at least one error description
      - schema_name is 'StayRequestSchema'

    Side effects: none
    Idempotent: yes
    """
    ...

def validate_price_response_schema(
    payload: dict,
) -> SchemaValidationResult:
    """
    Validates a raw payload against the PriceResponse Zod schema. Tested in api_contract_test.ts.

    Postconditions:
      - If valid is true, errors is empty
      - If valid is false, errors contains at least one error description
      - schema_name is 'PriceResponseSchema'

    Side effects: none
    Idempotent: yes
    """
    ...

def round_trip_serialize(
    value: any,
    type_name: str,
) -> SerializationRoundTripResult:
    """
    Tests JSON serialization round-trip fidelity: serializes a typed object to JSON, deserializes it back, re-serializes, and compares. Tested in serialization_test.ts.

    Preconditions:
      - value is a valid instance of the named type

    Postconditions:
      - is_identical is true iff original_json and deserialized_json are semantically equivalent
      - type_name in result matches the input type_name

    Side effects: none
    Idempotent: yes
    """
    ...

def build_rate_config(
    overrides: TestFixtureOverrides = {},
) -> RateConfig:
    """
    Test fixture factory: creates a valid RateConfig with sensible defaults. All fields can be overridden. Used across all test files.

    Postconditions:
      - Returned RateConfig has all required fields populated
      - Default base_rate is 10000 minor units (e.g. $100.00) in USD
      - Default unit_id is a deterministic test UUID
      - All override values replace corresponding defaults
      - Returned object passes RateConfig schema validation

    Side effects: none
    Idempotent: yes
    """
    ...

def build_stay_request(
    overrides: TestFixtureOverrides = {},
) -> StayRequest:
    """
    Test fixture factory: creates a valid StayRequest with sensible defaults (3-night stay, USD). All fields can be overridden.

    Postconditions:
      - Returned StayRequest has all required fields populated
      - Default stay is 3 nights starting from a fixed test date
      - Default currency is USD
      - Default unit_id matches build_rate_config default
      - Returned object passes StayRequest schema validation

    Side effects: none
    Idempotent: yes
    """
    ...

def run_test_suite(
    file_path: str,            # regex(^services/pricing/tests/[a-z_]+_test\.ts$)
) -> TestSuiteReport:
    """
    Executes a named test file and returns aggregate results. Used for test orchestration and CI reporting.

    Preconditions:
      - file_path points to an existing test file

    Postconditions:
      - total_tests == passed + failed + skipped
      - duration_ms >= 0
      - file_path in result matches input file_path

    Errors:
      - file_not_found (REPOSITORY_ERROR): The specified test file does not exist
          detail: Test file not found at specified path

    Side effects: none
    Idempotent: yes
    """
    ...
