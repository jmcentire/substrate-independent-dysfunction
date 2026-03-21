# === Pricing API Endpoints (pricing_api) v1 ===
#  Dependencies: shared_foundation
# Oak HTTP router implementing pricing endpoints for rental unit rate lookups, quote computation, rate configuration, and fee management. Thin adapter layer that validates requests via Zod schemas and delegates to a pricing engine interface via constructor injection. All monetary values use integer cents with ISO 4217 currency codes. Dates use inclusive-start (first night) / exclusive-end (checkout date) semantics. Responses use a consistent { data: T, meta? } envelope; errors use a structured { error: { code, message, details? } } envelope.

# Module invariants:
#   - All monetary amounts are represented as integer cents — no floating point values cross the API boundary
#   - start date is always inclusive (first night of stay) and end date is always exclusive (checkout date)
#   - Date range must satisfy start < end and span at most 730 days
#   - All success responses conform to ResponseEnvelope shape with data field and optional meta field
#   - All error responses conform to ErrorResponseEnvelope shape with error field containing code, message, and optional details
#   - POST /fees is idempotent when called with the same fee_type — it upserts keyed on fee_type
#   - PUT /rates returns the resolved effective configuration after applying 4-tier override merge (most-specific-wins with deep merge on nested objects)
#   - Currency is consistent within a single response — all Money objects in one response share the same currency
#   - unit_id path parameter is validated as a non-empty string on every endpoint before delegation to engine

CurrencyCode = primitive  # ISO 4217 three-letter currency code (e.g. USD, EUR, GBP). Re-exported from shared_foundation.

class Money:
    """Monetary value represented as integer cents with ISO 4217 currency. No floating point. Re-exported from shared_foundation."""
    amount_cents: int                        # required, Amount in the smallest currency unit (e.g. cents for USD). Can be negative for discounts.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), ISO 4217 three-letter currency code.

class LineItemType(Enum):
    """Discriminator for pricing line items in a quote breakdown."""
    NIGHTLY_RATE = "nightly_rate"
    CLEANING_FEE = "cleaning_fee"
    PET_FEE = "pet_fee"
    EXTRA_GUEST_FEE = "extra_guest_fee"
    CUSTOM_FEE = "custom_fee"
    TAX = "tax"
    LENGTH_OF_STAY_DISCOUNT = "length_of_stay_discount"

class NightlyRateMetadata:
    """Type-specific metadata for a nightly_rate line item."""
    date: str                                # required, regex(^\d{4}-\d{2}-\d{2}$), The specific night date (YYYY-MM-DD, inclusive).
    rate_source: RateSource                  # required, Which tier provided this rate.

class RateSource(Enum):
    """Identifies which override tier provided a rate value, from least to most specific."""
    BASE = "base"
    SEASONAL = "seasonal"
    DATE_SPECIFIC = "date_specific"
    OVERRIDE = "override"

class FeeMetadata:
    """Type-specific metadata for fee line items (cleaning_fee, pet_fee, extra_guest_fee, custom_fee)."""
    fee_type: str                            # required, The canonical fee type key used for upsert identity.
    fee_label: str                           # required, Human-readable label for the fee.
    is_percentage: bool                      # required, If true, the fee was computed as a percentage of the subtotal.
    percentage_basis_points: int = 0         # optional, The percentage in basis points (e.g. 1000 = 10%). Only meaningful when is_percentage is true.

class TaxMetadata:
    """Type-specific metadata for a tax line item."""
    tax_name: str                            # required, Name of the tax (e.g. 'State Occupancy Tax').
    rate_basis_points: int                   # required, range(0..10000), Tax rate in basis points (e.g. 1200 = 12.00%).
    jurisdiction: str = None                 # optional, Tax jurisdiction identifier.

class DiscountMetadata:
    """Type-specific metadata for a length_of_stay_discount line item."""
    min_nights: int                          # required, range(1..730), Minimum number of nights required to qualify for this discount.
    discount_basis_points: int               # required, range(0..10000), Discount percentage in basis points (e.g. 500 = 5.00%).

class PricingLineItem:
    """A single line item in a price breakdown. Uses 'type' as a discriminator field. Metadata field carries type-specific data whose shape depends on the discriminator."""
    type: LineItemType                       # required, Discriminator identifying the kind of line item.
    label: str                               # required, Human-readable description of this line item.
    amount: Money                            # required, The monetary amount for this line item. Negative for discounts.
    metadata: LineItemMetadata               # required, Type-specific metadata. Shape determined by the 'type' discriminator.

LineItemMetadata = NightlyRateMetadata | FeeMetadata | TaxMetadata | DiscountMetadata

class ResponseMeta:
    """Metadata attached to all successful responses."""
    currency: CurrencyCode                   # required, The currency used in all Money values in this response.
    generated_at: str                        # required, regex(^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z$), ISO 8601 UTC timestamp when this response was generated.

class QuoteResponse:
    """Full price quote breakdown for a stay."""
    unit_id: str                             # required, The rental unit this quote is for.
    start: str                               # required, Inclusive start date (first night, YYYY-MM-DD).
    end: str                                 # required, Exclusive end date (checkout date, YYYY-MM-DD).
    guests: int                              # required, Number of guests the quote was computed for.
    num_nights: int                          # required, Number of nights in the stay (end - start in days).
    line_items: PricingLineItemList          # required, All line items: nightly rates, fees, taxes, discounts.
    subtotal: Money                          # required, Sum of nightly rates before fees, taxes, and discounts.
    fees_total: Money                        # required, Sum of all fee line items.
    taxes_total: Money                       # required, Sum of all tax line items.
    discounts_total: Money                   # required, Sum of all discount line items (negative or zero).
    total: Money                             # required, Grand total: subtotal + fees_total + taxes_total + discounts_total.

PricingLineItemList = list[PricingLineItem]
# List of pricing line items.

class NightlyRate:
    """A single nightly rate entry returned by the rates endpoint."""
    date: str                                # required, regex(^\d{4}-\d{2}-\d{2}$), The night date (YYYY-MM-DD, inclusive).
    amount: Money                            # required, The nightly rate amount for this date.
    source: RateSource                       # required, Which override tier provided this rate.

NightlyRateList = list[NightlyRate]
# List of nightly rates.

class RatesResponse:
    """Raw nightly rates for a date range."""
    unit_id: str                             # required, The rental unit these rates are for.
    start: str                               # required, Inclusive start date (YYYY-MM-DD).
    end: str                                 # required, Exclusive end date (YYYY-MM-DD).
    rates: NightlyRateList                   # required, One entry per night in the range [start, end).

class OverrideTier(Enum):
    """The 4-tier override hierarchy for rate configuration. Most-specific-wins with deep merge."""
    BASE = "base"
    SEASONAL = "seasonal"
    DATE_SPECIFIC = "date_specific"
    OVERRIDE = "override"

class SeasonalRateRule:
    """A seasonal rate rule within rate configuration."""
    name: str                                # required, Human-readable name for the season (e.g. 'Peak Summer').
    start_month_day: str                     # required, regex(^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Season start as MM-DD (inclusive, recurring annually).
    end_month_day: str                       # required, regex(^(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Season end as MM-DD (inclusive, recurring annually).
    nightly_rate: Money                      # required, The nightly rate during this season.

SeasonalRateRuleList = list[SeasonalRateRule]
# List of seasonal rate rules.

class DateSpecificRate:
    """A date-specific rate override."""
    date: str                                # required, regex(^\d{4}-\d{2}-\d{2}$), The specific date (YYYY-MM-DD).
    nightly_rate: Money                      # required, The overridden nightly rate for this specific date.
    reason: str = None                       # optional, Optional reason for the override (e.g. 'Holiday premium').

DateSpecificRateList = list[DateSpecificRate]
# List of date-specific rate overrides.

class LengthOfStayDiscount:
    """A length-of-stay discount rule."""
    min_nights: int                          # required, range(1..730), Minimum number of nights to qualify.
    discount_basis_points: int               # required, range(1..10000), Discount percentage in basis points (e.g. 500 = 5%).
    label: str = None                        # optional, Human-readable label (e.g. 'Weekly discount').

LengthOfStayDiscountList = list[LengthOfStayDiscount]
# List of length-of-stay discount rules.

class RateConfiguration:
    """The full rate configuration for a unit, representing the merged effective config after 4-tier override resolution."""
    unit_id: str                             # required, The rental unit this configuration applies to.
    base_nightly_rate: Money                 # required, The base nightly rate (tier 1: base).
    seasonal_rules: SeasonalRateRuleList     # required, Seasonal rate rules (tier 2: seasonal).
    date_specific_rates: DateSpecificRateList # required, Date-specific rate overrides (tier 3: date_specific).
    length_of_stay_discounts: LengthOfStayDiscountList # required, Length-of-stay discount tiers.
    currency: CurrencyCode                   # required, The currency for all rates in this configuration.
    min_nights: int = 1                      # optional, range(1..730), Minimum nights for a booking.
    max_nights: int = 730                    # optional, range(1..730), Maximum nights for a booking.

class UpdateRateConfigurationRequest:
    """Request body for PUT /pricing/:unit_id/rates. All fields optional for partial update (deep merge). override_tier specifies which tier is being updated."""
    override_tier: OverrideTier              # required, Which override tier this update targets. Determines merge precedence.
    base_nightly_rate: Money = None          # optional, Updated base nightly rate.
    seasonal_rules: SeasonalRateRuleList = None # optional, Updated seasonal rate rules. Replaces all seasonal rules at this tier.
    date_specific_rates: DateSpecificRateList = None # optional, Updated date-specific overrides. Merged by date key.
    length_of_stay_discounts: LengthOfStayDiscountList = None # optional, Updated length-of-stay discount rules.
    currency: CurrencyCode = None            # optional, Currency for rate values. Must match existing configuration currency if set.
    min_nights: int = None                   # optional, range(1..730), Updated minimum nights.
    max_nights: int = None                   # optional, range(1..730), Updated maximum nights.

class FeeDefinition:
    """A fee definition for a rental unit. Keyed on fee_type for upsert semantics."""
    fee_type: str                            # required, regex(^[a-z][a-z0-9_]{0,63}$), Canonical fee type key (e.g. 'cleaning_fee', 'pet_fee', 'extra_guest_fee', or a custom string). Used as upsert key.
    label: str                               # required, length(1..256), Human-readable label for the fee.
    amount: Money                            # required, Fixed fee amount. Used when is_percentage is false.
    is_percentage: bool = false              # optional, If true, the fee is computed as a percentage of the nightly subtotal.
    percentage_basis_points: int = 0         # optional, range(0..10000), Fee percentage in basis points. Only used when is_percentage is true.
    applies_per_night: bool = false          # optional, If true, the fixed amount is charged per night rather than once.
    applies_per_guest_above: int = 0         # optional, range(0..100), Guest threshold above which this fee applies (e.g. 2 means fee applies for guest 3+). 0 means always applies.
    enabled: bool = true                     # optional, Whether this fee is currently active.

FeeDefinitionList = list[FeeDefinition]
# List of fee definitions.

class UpsertFeeRequest:
    """Request body for POST /pricing/:unit_id/fees. Upserts a fee keyed on fee_type."""
    fee_type: str                            # required, regex(^[a-z][a-z0-9_]{0,63}$), Canonical fee type key. If it already exists, the fee is updated; otherwise created.
    label: str                               # required, length(1..256), Human-readable label for the fee.
    amount: Money                            # required, Fixed fee amount.
    is_percentage: bool = false              # optional, If true, fee is a percentage of nightly subtotal.
    percentage_basis_points: int = 0         # optional, range(0..10000), Fee percentage in basis points when is_percentage is true.
    applies_per_night: bool = false          # optional, If true, charged per night.
    applies_per_guest_above: int = 0         # optional, range(0..100), Guest threshold for applicability.
    enabled: bool = true                     # optional, Whether this fee is active.

class FeesResponse:
    """Response from POST /fees containing the full current fee list after the upsert."""
    unit_id: str                             # required, The rental unit these fees belong to.
    fees: FeeDefinitionList                  # required, Complete list of all fees for this unit after the upsert operation.

class QuoteResponseEnvelope:
    """Success response envelope for GET /pricing/:unit_id/quote."""
    data: QuoteResponse                      # required, The quote data.
    meta: ResponseMeta                       # required, Response metadata including currency and generation timestamp.

class RatesResponseEnvelope:
    """Success response envelope for GET /pricing/:unit_id/rates."""
    data: RatesResponse                      # required, The rates data.
    meta: ResponseMeta                       # required, Response metadata.

class RateConfigResponseEnvelope:
    """Success response envelope for PUT /pricing/:unit_id/rates."""
    data: RateConfiguration                  # required, The resolved effective rate configuration after merge.
    meta: ResponseMeta                       # required, Response metadata.

class FeesResponseEnvelope:
    """Success response envelope for POST /pricing/:unit_id/fees."""
    data: FeesResponse                       # required, The fees data including full current fee list.
    meta: ResponseMeta                       # required, Response metadata.

class ErrorDetail:
    """Structured error detail within the error envelope. Re-exported from shared_foundation."""
    code: str                                # required, Machine-readable error code (e.g. 'VALIDATION_ERROR', 'UNIT_NOT_FOUND').
    message: str                             # required, Human-readable error message.
    details: dict = None                     # optional, Optional map of field names to arrays of validation error strings. Keys are field paths, values are lists of error messages.

class ErrorResponseEnvelope:
    """Standard error response envelope. All error responses from the API use this shape."""
    error: ErrorDetail                       # required, The error detail.

class HttpStatusCode(Enum):
    """HTTP status codes used by this API."""
    200 = "200"
    201 = "201"
    400 = "400"
    404 = "404"
    409 = "409"
    422 = "422"
    500 = "500"

class PricingEngineInterface:
    """Abstract interface for the pricing engine dependency, injected into the router via constructor. Defined here as a contract — the actual implementation is provided externally. The API router delegates all business logic to this interface."""
    compute_quote: str                       # required, Method: (unit_id, start, end, guests) -> QuoteResponse. Computes a full price breakdown.
    get_nightly_rates: str                   # required, Method: (unit_id, start, end) -> NightlyRateList. Returns raw nightly rates for a date range.
    update_rate_configuration: str           # required, Method: (unit_id, UpdateRateConfigurationRequest) -> RateConfiguration. Applies tier update and returns merged effective config.
    upsert_fee: str                          # required, Method: (unit_id, UpsertFeeRequest) -> FeesResponse. Upserts a fee and returns the full fee list.

def get_quote(
    unit_id: str,              # length(1..256)
    start: str,                # regex(^\d{4}-\d{2}-\d{2}$)
    end: str,                  # regex(^\d{4}-\d{2}-\d{2}$)
    guests: int,               # range(1..100)
) -> QuoteResponseEnvelope:
    """
    GET /pricing/:unit_id/quote?start=&end=&guests= — Computes a full price breakdown for a potential stay. Returns nightly rates, fees, taxes, discounts, and totals as structured line items. Delegates to PricingEngineInterface.compute_quote after validating request parameters via Zod schema.

    Preconditions:
      - unit_id is a non-empty string
      - start is a valid YYYY-MM-DD date string
      - end is a valid YYYY-MM-DD date string
      - start < end (start must be strictly before end)
      - Date range (end - start) must not exceed 730 days
      - guests >= 1
      - start date must not be in the past (before today in UTC)

    Postconditions:
      - Response contains exactly (end - start) nightly_rate line items, one per night
      - total.amount_cents == subtotal.amount_cents + fees_total.amount_cents + taxes_total.amount_cents + discounts_total.amount_cents
      - All Money objects in the response share the same currency
      - num_nights == number of nightly_rate line items
      - discounts_total.amount_cents <= 0
      - subtotal.amount_cents >= 0
      - meta.currency matches all Money.currency values in data

    Errors:
      - invalid_date_format (ErrorResponseEnvelope): start or end does not match YYYY-MM-DD format or is not a valid calendar date
          http_status: 400
          code: INVALID_DATE_FORMAT
      - invalid_date_range (ErrorResponseEnvelope): start >= end or date range exceeds 730 days
          http_status: 400
          code: INVALID_DATE_RANGE
      - invalid_guests (ErrorResponseEnvelope): guests parameter is missing, not an integer, or out of range [1, 100]
          http_status: 400
          code: INVALID_GUESTS
      - missing_query_params (ErrorResponseEnvelope): One or more required query parameters (start, end, guests) are missing
          http_status: 400
          code: MISSING_QUERY_PARAMS
      - unit_not_found (ErrorResponseEnvelope): No pricing configuration exists for the given unit_id
          http_status: 404
          code: UNIT_NOT_FOUND
      - rates_not_configured (ErrorResponseEnvelope): Rates are not configured for some or all dates in the requested range
          http_status: 422
          code: RATES_NOT_CONFIGURED
      - engine_error (ErrorResponseEnvelope): Pricing engine encountered an internal error during quote computation
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def get_rates(
    unit_id: str,              # length(1..256)
    start: str,                # regex(^\d{4}-\d{2}-\d{2}$)
    end: str,                  # regex(^\d{4}-\d{2}-\d{2}$)
) -> RatesResponseEnvelope:
    """
    GET /pricing/:unit_id/rates?start=&end= — Returns raw nightly rates for a date range. Each entry includes the rate amount and which override tier provided it. Delegates to PricingEngineInterface.get_nightly_rates after Zod validation.

    Preconditions:
      - unit_id is a non-empty string
      - start is a valid YYYY-MM-DD date string
      - end is a valid YYYY-MM-DD date string
      - start < end (start must be strictly before end)
      - Date range (end - start) must not exceed 730 days

    Postconditions:
      - Response contains exactly (end - start) rate entries, one per night in [start, end)
      - Rate entries are sorted by date ascending
      - All Money objects share the same currency
      - meta.currency matches all Money.currency values in data

    Errors:
      - invalid_date_format (ErrorResponseEnvelope): start or end does not match YYYY-MM-DD format or is not a valid calendar date
          http_status: 400
          code: INVALID_DATE_FORMAT
      - invalid_date_range (ErrorResponseEnvelope): start >= end or date range exceeds 730 days
          http_status: 400
          code: INVALID_DATE_RANGE
      - missing_query_params (ErrorResponseEnvelope): One or more required query parameters (start, end) are missing
          http_status: 400
          code: MISSING_QUERY_PARAMS
      - unit_not_found (ErrorResponseEnvelope): No pricing configuration exists for the given unit_id
          http_status: 404
          code: UNIT_NOT_FOUND
      - rates_not_configured (ErrorResponseEnvelope): Rates are not configured for some or all dates in the requested range
          http_status: 422
          code: RATES_NOT_CONFIGURED
      - engine_error (ErrorResponseEnvelope): Pricing engine encountered an internal error
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def update_rate_configuration(
    unit_id: str,              # length(1..256)
    body: UpdateRateConfigurationRequest,
) -> RateConfigResponseEnvelope:
    """
    PUT /pricing/:unit_id/rates — Updates rate configuration at a specified override tier. Accepts a partial configuration targeting a specific tier (base, seasonal, date_specific, or override). The pricing engine applies 4-tier merge (most-specific-wins, deep merge on nested objects) and returns the resolved effective configuration. Delegates to PricingEngineInterface.update_rate_configuration after Zod body validation.

    Preconditions:
      - unit_id is a non-empty string
      - Request body is valid JSON conforming to UpdateRateConfigurationRequest Zod schema
      - override_tier is a valid OverrideTier value
      - If currency is provided, it must match the unit's existing currency (currency change requires separate migration)
      - If both min_nights and max_nights are provided, min_nights <= max_nights
      - All Money.amount_cents values in rate fields are non-negative

    Postconditions:
      - Returned RateConfiguration reflects the merged effective config across all 4 tiers
      - The tier specified in override_tier has been updated with the provided fields
      - Authoritative configuration is persisted in PostgreSQL
      - Redis cache for this unit_id is invalidated or updated
      - All Money objects in the response share the same currency

    Errors:
      - validation_error (ErrorResponseEnvelope): Request body fails Zod schema validation (missing required fields, invalid types, constraint violations)
          http_status: 400
          code: VALIDATION_ERROR
      - invalid_body_json (ErrorResponseEnvelope): Request body is not valid JSON
          http_status: 400
          code: INVALID_JSON
      - unit_not_found (ErrorResponseEnvelope): No pricing configuration exists for the given unit_id and override_tier is not 'base' (base tier auto-creates)
          http_status: 404
          code: UNIT_NOT_FOUND
      - currency_mismatch (ErrorResponseEnvelope): Provided currency does not match the unit's existing configured currency
          http_status: 409
          code: CURRENCY_MISMATCH
      - invalid_night_range (ErrorResponseEnvelope): min_nights > max_nights after merge
          http_status: 422
          code: INVALID_NIGHT_RANGE
      - overlapping_seasons (ErrorResponseEnvelope): Seasonal rules have overlapping date ranges after merge
          http_status: 422
          code: OVERLAPPING_SEASONS
      - negative_rate (ErrorResponseEnvelope): A nightly rate amount_cents is negative
          http_status: 422
          code: NEGATIVE_RATE
      - engine_error (ErrorResponseEnvelope): Pricing engine encountered an internal error during configuration update
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: Persists updated rate configuration to PostgreSQL at the specified override tier, Invalidates or updates Redis cache for the unit_id hash shard, May trigger rate change events for downstream consumers
    Idempotent: yes
    """
    ...

def upsert_fee(
    unit_id: str,              # length(1..256)
    body: UpsertFeeRequest,
) -> FeesResponseEnvelope:
    """
    POST /pricing/:unit_id/fees — Adds or updates a fee for a rental unit. Upsert is keyed on fee_type: if a fee with the same fee_type already exists, it is replaced; otherwise a new fee is created. Returns the full current fee list after the mutation. Delegates to PricingEngineInterface.upsert_fee after Zod body validation.

    Preconditions:
      - unit_id is a non-empty string
      - Request body is valid JSON conforming to UpsertFeeRequest Zod schema
      - fee_type matches the required pattern: lowercase alphanumeric with underscores
      - If is_percentage is true, percentage_basis_points must be > 0
      - If is_percentage is false, amount.amount_cents must be >= 0
      - amount.currency must match the unit's configured currency

    Postconditions:
      - A fee with the given fee_type exists in the unit's fee list with the provided values
      - The response contains the complete list of all fees for the unit (not just the upserted one)
      - Fee list is persisted in PostgreSQL
      - Redis cache for this unit_id is invalidated or updated
      - Calling upsert_fee again with the same fee_type and same values produces the same result (idempotent)

    Errors:
      - validation_error (ErrorResponseEnvelope): Request body fails Zod schema validation
          http_status: 400
          code: VALIDATION_ERROR
      - invalid_body_json (ErrorResponseEnvelope): Request body is not valid JSON
          http_status: 400
          code: INVALID_JSON
      - unit_not_found (ErrorResponseEnvelope): No pricing configuration exists for the given unit_id
          http_status: 404
          code: UNIT_NOT_FOUND
      - currency_mismatch (ErrorResponseEnvelope): Fee amount currency does not match the unit's configured currency
          http_status: 409
          code: CURRENCY_MISMATCH
      - invalid_percentage_config (ErrorResponseEnvelope): is_percentage is true but percentage_basis_points is 0 or not provided
          http_status: 422
          code: INVALID_PERCENTAGE_CONFIG
      - max_fees_exceeded (ErrorResponseEnvelope): Adding this fee would exceed the maximum allowed number of fees per unit (50)
          http_status: 422
          code: MAX_FEES_EXCEEDED
      - engine_error (ErrorResponseEnvelope): Pricing engine encountered an internal error during fee upsert
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: Persists fee definition to PostgreSQL (insert or update keyed on unit_id + fee_type), Invalidates or updates Redis cache for the unit_id hash shard, May trigger fee change events for downstream consumers
    Idempotent: yes
    """
    ...
