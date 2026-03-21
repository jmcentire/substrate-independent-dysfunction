# === Pricing Database Schema & Repository (pricing_schema) v1 ===
#  Dependencies: shared_foundation
# PostgreSQL schema and data access layer for pricing. Defines five tables within the `pricing` schema namespace: rate_plans, rate_overrides, fees, taxes, and los_discounts. Provides a repository factory that accepts a database pool and returns a fully-mockable interface with CRUD operations for each table plus a critical resolveRatesForDateRange query method. All monetary values are stored as integers in minor currency units (cents). Date ranges use half-open intervals [checkIn, checkOut). Schema DDL and TypeScript domain types are kept in separate files, synchronized via contract tests. PostgreSQL stores authoritative rate configurations; Redis cache is managed by sibling components.

# Module invariants:
#   - All monetary values (baseRate, nightlyRate, fee amounts, tax amounts, subtotals, totals) are stored and computed as non-negative integers in minor currency units (cents). No floating-point arithmetic is used for money.
#   - All date ranges use half-open intervals [start, end) where start < end. A stay of N nights has checkIn and checkOut exactly N days apart.
#   - The EXCLUDE constraint on pricing.rate_overrides guarantees that no two overrides for the same rate_plan_id have overlapping [date_start, date_end) ranges.
#   - All tables have UUID v4 primary keys, created_at TIMESTAMPTZ DEFAULT NOW(), and updated_at TIMESTAMPTZ maintained by a BEFORE UPDATE trigger.
#   - Currency codes are ISO 4217 three-letter uppercase codes. All monetary entities for a single unit must use the same currency (enforced by application logic and validated in resolveRatesForDateRange).
#   - The resolveRatesForDateRange computation order is fixed: nightly sum → LOS discount → fees → taxes → grand total. This order is part of the contract and must be consistent across implementations.
#   - Only the single best (highest discount percentage) active LOS discount where minNights <= numNights is applied. If no LOS discount qualifies, no discount is applied.
#   - Percentage fees and tax rates are stored in basis points (0-10000 where 10000 = 100%). Integer division uses banker's rounding (round half to even).
#   - Taxes are computed on (discountedSubtotal + sum of taxable fee amounts). Non-taxable fees are excluded from the tax base.
#   - The repository interface is stateless and fully mockable. All state resides in the PostgreSQL database. Multiple repository instances sharing a pool are safe for concurrent use.
#   - Schema DDL lives in a separate migration file from TypeScript types. Contract tests verify synchronization between the two.
#   - All tables live within the PostgreSQL `pricing` schema namespace (database-per-service via schemas within a shared instance).
#   - Delete operations on rate_plans cascade to rate_overrides via ON DELETE CASCADE. No other cascades exist — fees, taxes, and los_discounts are independently deletable.

CentsAmount = primitive  # Branded integer type representing monetary value in minor currency units (e.g. cents for USD, pence for GBP). Underlying storage is a 64-bit integer. Never use floating-point for money. The brand tag prevents accidental mixing with plain integers.

CurrencyCode = primitive  # ISO 4217 three-letter currency code stored as TEXT. Examples: USD, EUR, GBP, JPY. Validated via regex on input.

DiscountPercent = primitive  # Branded integer representing a discount percentage in basis points (0-10000, where 10000 = 100%). Stored as integer to avoid floating-point issues.

TaxRateBasisPoints = primitive  # Branded integer representing a tax rate in basis points (0-10000, where 10000 = 100%). Stored as integer to avoid floating-point issues.

class FeeType(Enum):
    """Discriminated union tag for fee calculation method. Determines whether the fee amount is a flat CentsAmount or a percentage of the subtotal."""
    FLAT = "FLAT"
    PERCENTAGE = "PERCENTAGE"

class TaxType(Enum):
    """Enumeration of supported tax types applied to rental units."""
    OCCUPANCY = "OCCUPANCY"
    SALES = "SALES"
    VAT = "VAT"
    TOURISM = "TOURISM"
    LOCAL = "LOCAL"
    STATE = "STATE"
    FEDERAL = "FEDERAL"
    CUSTOM = "CUSTOM"

class PricingErrorKind(Enum):
    """Discriminated union tag for all pricing error types."""
    RATE_NOT_FOUND = "RATE_NOT_FOUND"
    CURRENCY_MISMATCH = "CURRENCY_MISMATCH"
    DATE_RANGE_INVALID = "DATE_RANGE_INVALID"
    OVERLAP_CONFLICT = "OVERLAP_CONFLICT"
    UNIT_NOT_FOUND = "UNIT_NOT_FOUND"
    RATE_PLAN_NOT_FOUND = "RATE_PLAN_NOT_FOUND"
    FEE_NOT_FOUND = "FEE_NOT_FOUND"
    TAX_NOT_FOUND = "TAX_NOT_FOUND"
    LOS_DISCOUNT_NOT_FOUND = "LOS_DISCOUNT_NOT_FOUND"
    DATABASE_ERROR = "DATABASE_ERROR"
    TRANSACTION_ERROR = "TRANSACTION_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"

class PricingError:
    """Structured error type for all pricing repository operations. Uses a discriminated union pattern via the `kind` field. Additional context provided in `message` and optional `details` map."""
    kind: PricingErrorKind                   # required, The discriminated union tag identifying the error category.
    message: str                             # required, Human-readable error description.
    details: dict = {}                       # optional, Additional structured context (e.g. conflicting date ranges, mismatched currencies).
    cause: str = None                        # optional, Underlying database or system error message, if any.

class RatePlan:
    """Entity type for the `pricing.rate_plans` table. Represents the base rate configuration for a rental unit. One unit may have multiple rate plans (e.g. standard, premium). Maps to camelCase TS properties with explicit column-mapping helpers."""
    id: str                                  # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID v4 primary key.
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), FK to the rental unit. UUID v4.
    name: str                                # required, length(1..255), Human-readable rate plan name (e.g. 'Standard', 'Peak Season').
    baseRate: CentsAmount                    # required, range(0..), Default nightly rate in minor currency units. Must be >= 0.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), ISO 4217 currency code for this rate plan.
    isActive: bool                           # required, Soft-delete / activation flag.
    createdAt: str = None                    # optional, ISO 8601 timestamp, set by database trigger on INSERT.
    updatedAt: str = None                    # optional, ISO 8601 timestamp, set by database trigger on INSERT and UPDATE.

class RatePlanCreateInput:
    """Input type for creating a new rate plan. Excludes auto-generated fields (id, createdAt, updatedAt)."""
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), FK to the rental unit. UUID v4.
    name: str                                # required, length(1..255)
    baseRate: CentsAmount                    # required, range(0..)
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$)
    isActive: bool = true                    # optional

class RatePlanUpdateInput:
    """Input type for updating an existing rate plan. All fields optional except id."""
    name: str = None                         # optional, length(1..255)
    baseRate: CentsAmount = None             # optional, range(0..)
    currency: CurrencyCode = None            # optional, regex(^[A-Z]{3}$)
    isActive: bool = None                    # optional

class RateOverride:
    """Entity type for the `pricing.rate_overrides` table. Date-specific nightly rate override for a rate plan. Uses half-open interval [dateStart, dateEnd). An EXCLUDE constraint prevents overlapping date ranges for the same rate plan."""
    id: str                                  # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID v4 primary key.
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), FK to pricing.rate_plans(id).
    dateStart: str                           # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive start date of override period. ISO 8601 date (YYYY-MM-DD).
    dateEnd: str                             # required, regex(^\d{4}-\d{2}-\d{2}$), Exclusive end date of override period. ISO 8601 date (YYYY-MM-DD). Must be > dateStart.
    nightlyRate: CentsAmount                 # required, range(0..), Override nightly rate in minor currency units.
    label: str = None                        # optional, Optional human-readable label (e.g. 'Holiday Premium', 'Last Minute Deal').
    createdAt: str = None                    # optional
    updatedAt: str = None                    # optional

class RateOverrideCreateInput:
    """Input for creating a rate override. Excludes auto-generated fields."""
    ratePlanId: str                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    dateStart: str                           # required, regex(^\d{4}-\d{2}-\d{2}$)
    dateEnd: str                             # required, regex(^\d{4}-\d{2}-\d{2}$)
    nightlyRate: CentsAmount                 # required, range(0..)
    label: str = None                        # optional

class RateOverrideUpdateInput:
    """Input for updating an existing rate override. All fields optional."""
    dateStart: str = None                    # optional, regex(^\d{4}-\d{2}-\d{2}$)
    dateEnd: str = None                      # optional, regex(^\d{4}-\d{2}-\d{2}$)
    nightlyRate: CentsAmount = None          # optional, range(0..)
    label: str = None                        # optional

class Fee:
    """Entity type for the `pricing.fees` table. Represents a fee applied to a rental unit. Can be either a flat amount or a percentage of the nightly subtotal. When feeType=FLAT, amount is CentsAmount. When feeType=PERCENTAGE, amount is basis points (0-10000)."""
    id: str                                  # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    name: str                                # required, length(1..255), Human-readable fee name (e.g. 'Cleaning Fee', 'Service Fee').
    feeType: FeeType                         # required, FLAT for absolute CentsAmount, PERCENTAGE for basis points of subtotal.
    amount: int                              # required, range(0..), Fee amount: CentsAmount when FLAT, basis points (0-10000) when PERCENTAGE.
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$), Currency code. Must match the rate plan currency for the same unit.
    isTaxable: bool                          # required, Whether this fee is subject to taxes.
    isActive: bool                           # required
    createdAt: str = None                    # optional
    updatedAt: str = None                    # optional

class FeeCreateInput:
    """Input for creating a fee."""
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    name: str                                # required, length(1..255)
    feeType: FeeType                         # required
    amount: int                              # required, range(0..)
    currency: CurrencyCode                   # required, regex(^[A-Z]{3}$)
    isTaxable: bool = false                  # optional
    isActive: bool = true                    # optional

class FeeUpdateInput:
    """Input for updating an existing fee."""
    name: str = None                         # optional, length(1..255)
    feeType: FeeType = None                  # optional
    amount: int = None                       # optional, range(0..)
    currency: CurrencyCode = None            # optional, regex(^[A-Z]{3}$)
    isTaxable: bool = None                   # optional
    isActive: bool = None                    # optional

class Tax:
    """Entity type for the `pricing.taxes` table. Represents a tax applied to a rental unit. Rate is stored in basis points."""
    id: str                                  # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    taxType: TaxType                         # required, Classification of the tax.
    name: str                                # required, length(1..255), Human-readable tax name.
    rate: TaxRateBasisPoints                 # required, range(0..10000), Tax rate in basis points (0-10000).
    isActive: bool                           # required
    createdAt: str = None                    # optional
    updatedAt: str = None                    # optional

class TaxCreateInput:
    """Input for creating a tax."""
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    taxType: TaxType                         # required
    name: str                                # required, length(1..255)
    rate: TaxRateBasisPoints                 # required, range(0..10000)
    isActive: bool = true                    # optional

class TaxUpdateInput:
    """Input for updating an existing tax."""
    taxType: TaxType = None                  # optional
    name: str = None                         # optional, length(1..255)
    rate: TaxRateBasisPoints = None          # optional, range(0..10000)
    isActive: bool = None                    # optional

class LosDiscount:
    """Entity type for the `pricing.los_discounts` table. Length-of-stay discount: if a booking is >= minNights, apply discountPct to the nightly subtotal. Only the single best (highest discount) matching LOS discount is applied."""
    id: str                                  # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    minNights: int                           # required, range(1..), Minimum number of nights for discount eligibility.
    discountPct: DiscountPercent             # required, range(0..10000), Discount in basis points (0-10000).
    isActive: bool                           # required
    createdAt: str = None                    # optional
    updatedAt: str = None                    # optional

class LosDiscountCreateInput:
    """Input for creating a length-of-stay discount."""
    unitId: str                              # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    minNights: int                           # required, range(1..)
    discountPct: DiscountPercent             # required, range(0..10000)
    isActive: bool = true                    # optional

class LosDiscountUpdateInput:
    """Input for updating an existing length-of-stay discount."""
    minNights: int = None                    # optional, range(1..)
    discountPct: DiscountPercent = None      # optional, range(0..10000)
    isActive: bool = None                    # optional

class NightlyBreakdownEntry:
    """Per-night rate detail within a resolved rate. Shows the date, the applicable rate, and the source (base vs override)."""
    date: str                                # required, regex(^\d{4}-\d{2}-\d{2}$), The night date (ISO 8601 YYYY-MM-DD). In a half-open [checkIn, checkOut) interval, this is the night starting on this date.
    rate: CentsAmount                        # required, range(0..), Nightly rate in minor currency units for this date.
    source: str                              # required, Either 'BASE' or the UUID of the rate_override that provided this rate.
    ratePlanId: str                          # required, The rate plan ID from which this rate was resolved.

class ResolvedFee:
    """A fee resolved to an absolute CentsAmount for a specific booking context."""
    feeId: str                               # required, The source fee record ID.
    name: str                                # required
    feeType: FeeType                         # required
    originalAmount: int                      # required, Original amount from the fee record (cents or basis points).
    resolvedAmount: CentsAmount              # required, Computed fee in minor currency units for this booking.
    isTaxable: bool                          # required

class ResolvedTax:
    """A tax resolved to an absolute CentsAmount for a specific booking context."""
    taxId: str                               # required, The source tax record ID.
    name: str                                # required
    taxType: TaxType                         # required
    rateBasisPoints: TaxRateBasisPoints      # required, The tax rate in basis points.
    taxableAmount: CentsAmount               # required, The base amount on which this tax was computed.
    taxAmount: CentsAmount                   # required, Computed tax in minor currency units.

class ResolvedLosDiscount:
    """The applied length-of-stay discount, if any."""
    losDiscountId: str                       # required
    minNights: int                           # required
    discountPctBasisPoints: DiscountPercent  # required
    discountAmount: CentsAmount              # required, Absolute discount amount in minor currency units.

OptionalResolvedLosDiscount = Any | None

NightlyBreakdownList = list[NightlyBreakdownEntry]
# Ordered list of nightly breakdown entries, one per night in [checkIn, checkOut).

ResolvedFeeList = list[ResolvedFee]
# List of resolved fees for the booking.

ResolvedTaxList = list[ResolvedTax]
# List of resolved taxes for the booking.

class ResolvedRate:
    """Complete rate resolution output for a unit and date range. Includes per-night breakdown, subtotals, fees, taxes, LOS discount, and grand total. All amounts in minor currency units. The computation order is: (1) sum nightly rates -> nightlySubtotal, (2) apply LOS discount -> discountedSubtotal, (3) compute fees on discountedSubtotal -> feesTotal, (4) compute taxes on (discountedSubtotal + taxable fees) -> taxesTotal, (5) grandTotal = discountedSubtotal + feesTotal + taxesTotal."""
    unitId: str                              # required
    ratePlanId: str                          # required, The primary rate plan used for resolution.
    checkIn: str                             # required, Inclusive check-in date (YYYY-MM-DD).
    checkOut: str                            # required, Exclusive check-out date (YYYY-MM-DD).
    numNights: int                           # required, Number of nights = checkOut - checkIn in days.
    currency: CurrencyCode                   # required
    nightlyBreakdown: NightlyBreakdownList   # required, Per-night rate details. Length must equal numNights.
    nightlySubtotal: CentsAmount             # required, Sum of all nightly rates before LOS discount.
    losDiscount: OptionalResolvedLosDiscount = None # optional, Applied LOS discount, if stay qualifies.
    discountedSubtotal: CentsAmount          # required, nightlySubtotal - losDiscount.discountAmount (or nightlySubtotal if no LOS discount).
    fees: ResolvedFeeList                    # required
    feesTotal: CentsAmount                   # required, Sum of all resolved fee amounts.
    taxes: ResolvedTaxList                   # required
    taxesTotal: CentsAmount                  # required, Sum of all resolved tax amounts.
    grandTotal: CentsAmount                  # required, discountedSubtotal + feesTotal + taxesTotal.
    resolvedAt: str                          # required, ISO 8601 timestamp when this resolution was computed.

RatePlanList = list[RatePlan]
# List of rate plan entities.

RateOverrideList = list[RateOverride]
# List of rate override entities.

FeeList = list[Fee]
# List of fee entities.

TaxList = list[Tax]
# List of tax entities.

LosDiscountList = list[LosDiscount]
# List of LOS discount entities.

RatePlanResult = RatePlan | PricingError

RatePlanListResult = RatePlanList | PricingError

RateOverrideResult = RateOverride | PricingError

RateOverrideListResult = RateOverrideList | PricingError

FeeResult = Fee | PricingError

FeeListResult = FeeList | PricingError

TaxResult = Tax | PricingError

TaxListResult = TaxList | PricingError

LosDiscountResult = LosDiscount | PricingError

LosDiscountListResult = LosDiscountList | PricingError

ResolvedRateResult = ResolvedRate | PricingError

DeleteResult = bool | PricingError

TransactionResult = any | PricingError

def createPricingRepository(
    pool: any,
) -> any:
    """
    Factory function that accepts a database pool and returns a PricingRepository interface. The returned repository is fully mockable — test components can provide an in-memory implementation conforming to the same interface. All repository methods operate within the `pricing` PostgreSQL schema namespace.

    Preconditions:
      - pool is a valid, connected DatabasePool instance
      - The `pricing` schema and all five tables exist (migration has been run)
      - The btree_gist extension is enabled for EXCLUDE constraints

    Postconditions:
      - Returns a PricingRepository object with all CRUD methods and resolveRatesForDateRange
      - The returned repository is stateless — all state lives in the database
      - Multiple repository instances sharing the same pool are safe for concurrent use

    Errors:
      - pool_not_connected (PricingError): The database pool is not connected or has been closed.
          kind: DATABASE_ERROR
      - schema_not_found (PricingError): The `pricing` schema does not exist in the database.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def createRatePlan(
    input: RatePlanCreateInput,
) -> RatePlanResult:
    """
    Inserts a new rate plan into pricing.rate_plans. Generates a UUID v4 for the id. Sets createdAt and updatedAt via database triggers.

    Preconditions:
      - input.unitId references a valid rental unit
      - input.currency is a valid ISO 4217 code
      - input.baseRate >= 0

    Postconditions:
      - A new row exists in pricing.rate_plans with a generated UUID
      - createdAt and updatedAt are set to the current timestamp
      - The returned RatePlan reflects the inserted row

    Errors:
      - validation_error (PricingError): Input fails validation (e.g. empty name, negative baseRate, invalid currency).
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database constraint violation or connection error.
          kind: DATABASE_ERROR

    Side effects: Inserts a row into pricing.rate_plans
    Idempotent: no
    """
    ...

def getRatePlan(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> RatePlanResult:
    """
    Retrieves a single rate plan by its UUID.

    Postconditions:
      - If found, returns the RatePlan matching the given id
      - If not found, returns PricingError with kind=RATE_PLAN_NOT_FOUND

    Errors:
      - not_found (PricingError): No rate plan exists with the given id.
          kind: RATE_PLAN_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def listRatePlansByUnit(
    unitId: str,               # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    activeOnly: bool = true,
) -> RatePlanListResult:
    """
    Lists all rate plans for a given unit, optionally filtering by isActive status.

    Postconditions:
      - Returns a list of rate plans for the unit (may be empty)
      - If activeOnly=true, all returned plans have isActive=true
      - Results are ordered by createdAt ascending

    Errors:
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def updateRatePlan(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    input: RatePlanUpdateInput,
) -> RatePlanResult:
    """
    Updates an existing rate plan. Only provided fields are updated (partial update). updatedAt is refreshed by database trigger.

    Preconditions:
      - At least one field in input is provided

    Postconditions:
      - The rate plan row is updated with the provided fields
      - updatedAt is refreshed to the current timestamp
      - Returns the full updated RatePlan

    Errors:
      - not_found (PricingError): No rate plan exists with the given id.
          kind: RATE_PLAN_NOT_FOUND
      - validation_error (PricingError): Provided fields fail validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database constraint violation or connection error.
          kind: DATABASE_ERROR

    Side effects: Updates a row in pricing.rate_plans
    Idempotent: yes
    """
    ...

def deleteRatePlan(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> DeleteResult:
    """
    Deletes a rate plan by id. Also cascades to delete associated rate_overrides via ON DELETE CASCADE.

    Postconditions:
      - If found, the rate plan and all its rate overrides are deleted
      - Returns true if a row was deleted, PricingError if not found

    Errors:
      - not_found (PricingError): No rate plan exists with the given id.
          kind: RATE_PLAN_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Deletes row(s) from pricing.rate_plans and cascaded pricing.rate_overrides
    Idempotent: yes
    """
    ...

def createRateOverride(
    input: RateOverrideCreateInput,
) -> RateOverrideResult:
    """
    Inserts a new rate override into pricing.rate_overrides. The EXCLUDE constraint enforces non-overlapping date ranges per rate_plan_id. Uses half-open interval [dateStart, dateEnd).

    Preconditions:
      - input.ratePlanId references an existing rate plan
      - input.dateStart < input.dateEnd
      - The [dateStart, dateEnd) range does not overlap any existing override for the same rate plan

    Postconditions:
      - A new row exists in pricing.rate_overrides
      - The EXCLUDE constraint guarantees no overlapping overrides for the same rate plan
      - Returns the inserted RateOverride

    Errors:
      - rate_plan_not_found (PricingError): The referenced rate plan does not exist.
          kind: RATE_PLAN_NOT_FOUND
      - date_range_invalid (PricingError): dateStart >= dateEnd.
          kind: DATE_RANGE_INVALID
      - overlap_conflict (PricingError): The date range overlaps an existing override for the same rate plan (EXCLUDE constraint violation).
          kind: OVERLAP_CONFLICT
      - validation_error (PricingError): Input fails validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Inserts a row into pricing.rate_overrides
    Idempotent: no
    """
    ...

def getRateOverride(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> RateOverrideResult:
    """
    Retrieves a single rate override by its UUID.

    Postconditions:
      - Returns the RateOverride if found, or PricingError with kind=RATE_NOT_FOUND

    Errors:
      - not_found (PricingError): No rate override exists with the given id.
          kind: RATE_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def listRateOverridesByRatePlan(
    ratePlanId: str,           # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    dateRangeStart: str = None, # regex(^\d{4}-\d{2}-\d{2}$)
    dateRangeEnd: str = None,  # regex(^\d{4}-\d{2}-\d{2}$)
) -> RateOverrideListResult:
    """
    Lists all rate overrides for a given rate plan, ordered by dateStart ascending.

    Preconditions:
      - If dateRangeStart and dateRangeEnd are both provided, dateRangeStart < dateRangeEnd

    Postconditions:
      - Returns overrides ordered by dateStart ascending
      - If date range filters are provided, only overlapping overrides are returned

    Errors:
      - date_range_invalid (PricingError): dateRangeStart >= dateRangeEnd when both are provided.
          kind: DATE_RANGE_INVALID
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def updateRateOverride(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    input: RateOverrideUpdateInput,
) -> RateOverrideResult:
    """
    Updates an existing rate override. Partial update — only provided fields are changed. Re-validates EXCLUDE constraint if date range changes.

    Preconditions:
      - At least one field in input is provided

    Postconditions:
      - The override row is updated
      - EXCLUDE constraint still holds after update
      - updatedAt is refreshed

    Errors:
      - not_found (PricingError): No rate override exists with the given id.
          kind: RATE_NOT_FOUND
      - date_range_invalid (PricingError): Resulting dateStart >= dateEnd after update.
          kind: DATE_RANGE_INVALID
      - overlap_conflict (PricingError): Updated date range overlaps another override for the same rate plan.
          kind: OVERLAP_CONFLICT
      - validation_error (PricingError): Provided fields fail validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Updates a row in pricing.rate_overrides
    Idempotent: yes
    """
    ...

def deleteRateOverride(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> DeleteResult:
    """
    Deletes a rate override by id.

    Postconditions:
      - Returns true if deleted, PricingError if not found

    Errors:
      - not_found (PricingError): No rate override exists with the given id.
          kind: RATE_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Deletes a row from pricing.rate_overrides
    Idempotent: yes
    """
    ...

def createFee(
    input: FeeCreateInput,
) -> FeeResult:
    """
    Inserts a new fee into pricing.fees.

    Preconditions:
      - input.unitId references a valid rental unit
      - If feeType=PERCENTAGE, amount must be 0-10000 (basis points)

    Postconditions:
      - A new row exists in pricing.fees
      - Returns the inserted Fee

    Errors:
      - validation_error (PricingError): Input fails validation (e.g. PERCENTAGE amount > 10000).
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database constraint violation or connection error.
          kind: DATABASE_ERROR

    Side effects: Inserts a row into pricing.fees
    Idempotent: no
    """
    ...

def getFee(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> FeeResult:
    """
    Retrieves a single fee by its UUID.

    Postconditions:
      - Returns the Fee if found, or PricingError with kind=FEE_NOT_FOUND

    Errors:
      - not_found (PricingError): No fee exists with the given id.
          kind: FEE_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def listFeesByUnit(
    unitId: str,               # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    activeOnly: bool = true,
) -> FeeListResult:
    """
    Lists all fees for a given unit, optionally filtering by isActive.

    Postconditions:
      - Returns fees for the unit ordered by createdAt ascending

    Errors:
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def updateFee(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    input: FeeUpdateInput,
) -> FeeResult:
    """
    Updates an existing fee. Partial update.

    Preconditions:
      - At least one field in input is provided

    Postconditions:
      - Returns the updated Fee

    Errors:
      - not_found (PricingError): No fee exists with the given id.
          kind: FEE_NOT_FOUND
      - validation_error (PricingError): Provided fields fail validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Updates a row in pricing.fees
    Idempotent: yes
    """
    ...

def deleteFee(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> DeleteResult:
    """
    Deletes a fee by id.

    Postconditions:
      - Returns true if deleted

    Errors:
      - not_found (PricingError): No fee exists with the given id.
          kind: FEE_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Deletes a row from pricing.fees
    Idempotent: yes
    """
    ...

def createTax(
    input: TaxCreateInput,
) -> TaxResult:
    """
    Inserts a new tax into pricing.taxes.

    Preconditions:
      - input.unitId references a valid rental unit
      - input.rate is in range 0-10000 basis points

    Postconditions:
      - A new row exists in pricing.taxes
      - Returns the inserted Tax

    Errors:
      - validation_error (PricingError): Input fails validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database constraint violation or connection error.
          kind: DATABASE_ERROR

    Side effects: Inserts a row into pricing.taxes
    Idempotent: no
    """
    ...

def getTax(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> TaxResult:
    """
    Retrieves a single tax by its UUID.

    Postconditions:
      - Returns the Tax if found, or PricingError with kind=TAX_NOT_FOUND

    Errors:
      - not_found (PricingError): No tax exists with the given id.
          kind: TAX_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def listTaxesByUnit(
    unitId: str,               # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    activeOnly: bool = true,
) -> TaxListResult:
    """
    Lists all taxes for a given unit, optionally filtering by isActive.

    Postconditions:
      - Returns taxes for the unit ordered by createdAt ascending

    Errors:
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def updateTax(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    input: TaxUpdateInput,
) -> TaxResult:
    """
    Updates an existing tax. Partial update.

    Preconditions:
      - At least one field in input is provided

    Postconditions:
      - Returns the updated Tax

    Errors:
      - not_found (PricingError): No tax exists with the given id.
          kind: TAX_NOT_FOUND
      - validation_error (PricingError): Provided fields fail validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Updates a row in pricing.taxes
    Idempotent: yes
    """
    ...

def deleteTax(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> DeleteResult:
    """
    Deletes a tax by id.

    Postconditions:
      - Returns true if deleted

    Errors:
      - not_found (PricingError): No tax exists with the given id.
          kind: TAX_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Deletes a row from pricing.taxes
    Idempotent: yes
    """
    ...

def createLosDiscount(
    input: LosDiscountCreateInput,
) -> LosDiscountResult:
    """
    Inserts a new length-of-stay discount into pricing.los_discounts.

    Preconditions:
      - input.unitId references a valid rental unit
      - input.minNights >= 1
      - input.discountPct in range 0-10000

    Postconditions:
      - A new row exists in pricing.los_discounts
      - Returns the inserted LosDiscount

    Errors:
      - validation_error (PricingError): Input fails validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database constraint violation or connection error.
          kind: DATABASE_ERROR

    Side effects: Inserts a row into pricing.los_discounts
    Idempotent: no
    """
    ...

def getLosDiscount(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> LosDiscountResult:
    """
    Retrieves a single LOS discount by its UUID.

    Postconditions:
      - Returns the LosDiscount if found, or PricingError with kind=LOS_DISCOUNT_NOT_FOUND

    Errors:
      - not_found (PricingError): No LOS discount exists with the given id.
          kind: LOS_DISCOUNT_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def listLosDiscountsByUnit(
    unitId: str,               # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    activeOnly: bool = true,
) -> LosDiscountListResult:
    """
    Lists all LOS discounts for a given unit, optionally filtering by isActive. Ordered by minNights ascending.

    Postconditions:
      - Returns LOS discounts ordered by minNights ascending

    Errors:
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def updateLosDiscount(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    input: LosDiscountUpdateInput,
) -> LosDiscountResult:
    """
    Updates an existing LOS discount. Partial update.

    Preconditions:
      - At least one field in input is provided

    Postconditions:
      - Returns the updated LosDiscount

    Errors:
      - not_found (PricingError): No LOS discount exists with the given id.
          kind: LOS_DISCOUNT_NOT_FOUND
      - validation_error (PricingError): Provided fields fail validation.
          kind: VALIDATION_ERROR
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Updates a row in pricing.los_discounts
    Idempotent: yes
    """
    ...

def deleteLosDiscount(
    id: str,                   # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
) -> DeleteResult:
    """
    Deletes a LOS discount by id.

    Postconditions:
      - Returns true if deleted

    Errors:
      - not_found (PricingError): No LOS discount exists with the given id.
          kind: LOS_DISCOUNT_NOT_FOUND
      - database_error (PricingError): Database connection error.
          kind: DATABASE_ERROR

    Side effects: Deletes a row from pricing.los_discounts
    Idempotent: yes
    """
    ...

def resolveRatesForDateRange(
    unitId: str,               # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    checkIn: str,              # regex(^\d{4}-\d{2}-\d{2}$)
    checkOut: str,             # regex(^\d{4}-\d{2}-\d{2}$)
) -> ResolvedRateResult:
    """
    The critical query method. Given a unit and a half-open date range [checkIn, checkOut), resolves the complete pricing breakdown. Algorithm: (1) Find the active rate plan for the unit (if multiple, use the first active one by createdAt). (2) For each night in [checkIn, checkOut), determine the nightly rate: use rate_override if one covers that date, otherwise use the rate plan's baseRate. (3) Sum nightly rates to get nightlySubtotal. (4) Find the best applicable LOS discount (highest discountPct where minNights <= numNights) and compute discountedSubtotal. (5) Compute all active fees for the unit against discountedSubtotal. (6) Compute all active taxes against (discountedSubtotal + taxable fee amounts). (7) Return the complete ResolvedRate with per-night breakdown. All monetary calculations use integer arithmetic with banker's rounding for divisions.

    Preconditions:
      - checkIn < checkOut (half-open interval must contain at least one night)
      - checkIn is a valid calendar date
      - checkOut is a valid calendar date
      - The date range is reasonable (implementation may enforce a max range, e.g. 365 nights)

    Postconditions:
      - nightlyBreakdown has exactly (checkOut - checkIn) entries, one per night
      - nightlySubtotal == sum of all nightlyBreakdown[i].rate
      - discountedSubtotal == nightlySubtotal - (losDiscount.discountAmount or 0)
      - discountedSubtotal >= 0
      - feesTotal == sum of all fees[i].resolvedAmount
      - taxesTotal == sum of all taxes[i].taxAmount
      - grandTotal == discountedSubtotal + feesTotal + taxesTotal
      - All amounts are non-negative integers
      - currency matches the rate plan's currency
      - resolvedAt is set to the current timestamp

    Errors:
      - rate_not_found (PricingError): No active rate plan exists for the given unitId.
          kind: RATE_NOT_FOUND
      - unit_not_found (PricingError): The unitId does not correspond to any known unit.
          kind: UNIT_NOT_FOUND
      - date_range_invalid (PricingError): checkIn >= checkOut, or dates are not valid calendar dates.
          kind: DATE_RANGE_INVALID
      - currency_mismatch (PricingError): Fees or taxes for the unit have a different currency than the rate plan (should not happen with proper data, but validated defensively).
          kind: CURRENCY_MISMATCH
      - database_error (PricingError): Database connection error during the multi-table query.
          kind: DATABASE_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def withTransaction(
    callback: any,
) -> TransactionResult:
    """
    Executes a callback within a database transaction. Acquires a client from the pool, begins a transaction, executes the callback passing a transaction-scoped repository, and commits on success or rolls back on error. The transaction-scoped repository has the same interface but all operations execute within the transaction. Useful for atomic multi-table operations (e.g. creating a rate plan with overrides and fees atomically).

    Preconditions:
      - The database pool has available connections
      - callback is an async function accepting a PricingRepository

    Postconditions:
      - If callback returns a success Result, the transaction is committed
      - If callback returns an error Result, the transaction is rolled back
      - If callback throws, the transaction is rolled back and a TRANSACTION_ERROR is returned
      - The transaction-scoped repository is not usable after withTransaction returns

    Errors:
      - transaction_begin_failed (PricingError): Cannot acquire a connection or begin a transaction.
          kind: TRANSACTION_ERROR
      - transaction_commit_failed (PricingError): The COMMIT statement fails (e.g. serialization failure).
          kind: TRANSACTION_ERROR
      - callback_threw (PricingError): The callback function threw an unhandled exception.
          kind: TRANSACTION_ERROR
      - database_error (PricingError): Underlying database error during transaction management.
          kind: DATABASE_ERROR

    Side effects: Acquires and releases a database connection, May commit or rollback a database transaction
    Idempotent: no
    """
    ...

def runMigration(
    pool: any,
) -> DeleteResult:
    """
    Executes the schema DDL migration for the pricing schema. Creates the `pricing` schema namespace, enables btree_gist extension, and creates all five tables with constraints, indexes, triggers, and audit columns. Idempotent — uses IF NOT EXISTS and is safe to run multiple times.

    Preconditions:
      - pool is connected with sufficient privileges to CREATE SCHEMA, CREATE TABLE, CREATE EXTENSION

    Postconditions:
      - The `pricing` schema exists
      - The btree_gist extension is enabled
      - All five tables exist with proper columns, constraints, and indexes
      - Trigger functions for updated_at are installed
      - Returns true on success

    Errors:
      - insufficient_privileges (PricingError): The pool connection lacks CREATE privileges.
          kind: DATABASE_ERROR
      - database_error (PricingError): DDL execution fails.
          kind: DATABASE_ERROR

    Side effects: Creates or updates the pricing schema DDL in PostgreSQL
    Idempotent: yes
    """
    ...
