# === Availability Service Tests (availability_tests) v1 ===
# Unit tests for bitmask operations (edge cases: single day, full year, boundary dates, leap years). Integration tests for cache layer (cache miss → DB query → cache fill → cache hit path, invalidation). API contract tests validating all endpoint request/response shapes. Tests run with mocked DB and Redis (no external services required). Minimum: 1 test per public function, contract tests for every endpoint. Organized as three test modules (bitmask_test.ts, cache_test.ts, api_contract_test.ts) plus shared test_helpers.ts under services/availability/tests/.

# Module invariants:
#   - All dates are represented as ISO 8601 'YYYY-MM-DD' strings in UTC
#   - unit_id values are always UUID v4 strings matching regex ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$
#   - BigInt bitmasks are serialized as lowercase hex strings prefixed with '0x' for Redis/cache storage
#   - Bitmask bit index 0 corresponds to January 1 of the given year, index 364 to December 31 (non-leap), index 365 to December 31 (leap year)
#   - Leap year Feb 29 is always bit index 59 (0-indexed day-of-year)
#   - Cache keys follow the format 'availability:{unit_id}:{year}' where year is a 4-digit integer
#   - All error responses from API endpoints conform to the ErrorResponse shape: { error: string, message: string }
#   - InMemoryDb and InMemoryCache mocks are behaviorally equivalent to production DbClient and CacheClient interfaces for the subset of operations used by the availability service
#   - Every public function in the availability service has at least one corresponding test
#   - Every REST endpoint has contract tests covering success and all documented error codes
#   - No test requires external services (PostgreSQL, Redis) — all I/O is mocked via InMemoryDb and InMemoryCache
#   - Hex serialization round-trip: for any bitmask B, deserializeBitmask(serializeBitmask(B)) === B

IsoDateString = primitive  # ISO 8601 date string in 'YYYY-MM-DD' format, UTC. Must match regex ^\d{4}-\d{2}-\d{2}$ and represent a valid calendar date.

UuidString = primitive  # UUID v4 string identifier for a unit. Must match regex ^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$.

HexBitmaskString = primitive  # BigInt bitmask serialized as a lowercase hex string prefixed with '0x'. E.g. '0x1a2b3c'. Used for Redis cache storage.

Year = primitive  # 4-digit integer representing a calendar year (e.g. 2024).

BitIndex = primitive  # Non-negative integer representing a 0-based day-of-year index in a bitmask. Range: 0..365 (0..364 for non-leap years, 0..365 for leap years).

class DateRange:
    """A contiguous date range defined by inclusive start and end dates."""
    start: IsoDateString                     # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive start date of the range.
    end: IsoDateString                       # required, regex(^\d{4}-\d{2}-\d{2}$), Inclusive end date of the range.

class AvailabilityRecord:
    """A single availability record as stored in PostgreSQL. Represents one unit's availability bitmask for one year."""
    unit_id: UuidString                      # required, The unit this record belongs to.
    year: Year                               # required, The calendar year this bitmask covers.
    bitmask: HexBitmaskString                # required, The availability bitmask for the year, serialized as hex.
    updated_at: IsoDateString                # required, Timestamp of last update (date portion).

class AvailabilityStatus(Enum):
    """Whether a given date or range is available or blocked."""
    AVAILABLE = "AVAILABLE"
    BLOCKED = "BLOCKED"
    PARTIAL = "PARTIAL"

class AvailabilityCheckResult:
    """Result of checking availability for a unit over a date range."""
    unit_id: UuidString                      # required, The unit checked.
    start: IsoDateString                     # required, Start date queried.
    end: IsoDateString                       # required, End date queried.
    status: AvailabilityStatus               # required, Overall availability status for the range.
    available_dates: IsoDateStringList       # required, List of available dates within the range.
    blocked_dates: IsoDateStringList         # required, List of blocked dates within the range.

IsoDateStringList = list[IsoDateString]
# A list of ISO 8601 date strings.

class BulkAvailabilityResult:
    """Result of a bulk availability check for multiple units."""
    results: AvailabilityCheckResultList     # required, List of per-unit availability check results.

AvailabilityCheckResultList = list[AvailabilityCheckResult]
# List of availability check results, one per unit.

UuidStringList = list[UuidString]
# List of unit UUID strings.

class UpdateAvailabilityRequest:
    """Request body for PUT /availability/:unit_id. Sets specified dates as available or blocked."""
    dates: IsoDateStringList                 # required, List of dates to update.
    status: AvailabilityStatus               # required, custom(status in ['AVAILABLE', 'BLOCKED']), The status to set for the given dates. Must be AVAILABLE or BLOCKED (not PARTIAL).

class UpdateAvailabilityResponse:
    """Response body for a successful PUT /availability/:unit_id."""
    unit_id: UuidString                      # required, The unit that was updated.
    updated_dates: IsoDateStringList         # required, The dates that were modified.
    new_status: AvailabilityStatus           # required, The status that was applied.

class ErrorResponse:
    """Standard error response shape returned by all API endpoints on failure."""
    error: str                               # required, Machine-readable error code (e.g. 'NOT_FOUND', 'INVALID_REQUEST', 'VALIDATION_ERROR').
    message: str                             # required, Human-readable error description.

class HttpStatusCode(Enum):
    """HTTP status codes used by the availability API endpoints."""
    200 = "200"
    400 = "400"
    404 = "404"
    422 = "422"
    500 = "500"

CacheKey = primitive  # Cache key string in format 'availability:{unit_id}:{year}'.

class DbClient:
    """Interface for database operations. Production uses PostgreSQL; tests use InMemoryDb mock."""
    getAvailability: str                     # required, Function signature: (unit_id: UuidString, year: Year) => Promise<AvailabilityRecord | null>
    setAvailability: str                     # required, Function signature: (unit_id: UuidString, year: Year, bitmask: HexBitmaskString) => Promise<AvailabilityRecord>
    getMultipleAvailability: str             # required, Function signature: (unit_ids: UuidString[], year: Year) => Promise<AvailabilityRecord[]>

class CacheClient:
    """Interface for cache operations. Production uses Redis; tests use InMemoryCache (Map-based) mock."""
    get: str                                 # required, Function signature: (key: CacheKey) => Promise<HexBitmaskString | null>
    set: str                                 # required, Function signature: (key: CacheKey, value: HexBitmaskString, ttl?: number) => Promise<void>
    del: str                                 # required, Function signature: (key: CacheKey) => Promise<void>

class InMemoryDb:
    """In-memory mock implementation of DbClient backed by an array of AvailabilityRecords. Used in cache_test.ts and api_contract_test.ts."""
    records: AvailabilityRecordList          # required, Internal storage array of availability records.
    queryCount: int                          # required, Spy counter tracking number of getAvailability calls.
    writeCount: int                          # required, Spy counter tracking number of setAvailability calls.

AvailabilityRecordList = list[AvailabilityRecord]
# List of availability records.

class InMemoryCache:
    """In-memory mock implementation of CacheClient backed by a Map<string, string>. Used in cache_test.ts and api_contract_test.ts."""
    store: dict                              # required, Internal Map storing CacheKey → HexBitmaskString entries.
    getCount: int                            # required, Spy counter tracking number of get calls.
    setCount: int                            # required, Spy counter tracking number of set calls.
    delCount: int                            # required, Spy counter tracking number of del calls.

class TestResult:
    """Result of executing a single test case."""
    test_name: str                           # required, Fully qualified test name including module prefix.
    passed: bool                             # required, Whether the test passed.
    duration_ms: float                       # required, Execution time in milliseconds.
    error_message: str = None                # optional, Error message if the test failed; empty string if passed.

TestResultList = list[TestResult]
# List of test results.

class TestSuiteReport:
    """Aggregate report for an entire test suite run."""
    suite_name: str                          # required, Name of the test suite (e.g. 'bitmask_test', 'cache_test', 'api_contract_test').
    total: int                               # required, Total number of tests executed.
    passed: int                              # required, Number of tests that passed.
    failed: int                              # required, Number of tests that failed.
    duration_ms: float                       # required, Total execution time in milliseconds.
    results: TestResultList                  # required, Individual test results.

class BitmaskOperationName(Enum):
    """Names of public bitmask operations that must each have at least one test."""
    SETBIT = "setBit"
    CLEARBIT = "clearBit"
    GETBIT = "getBit"
    SETBITRANGE = "setBitRange"
    CLEARBITRANGE = "clearBitRange"
    CHECKBITRANGE = "checkBitRange"
    CREATEYEARMASK = "createYearMask"
    CREATELEAPYEARMASK = "createLeapYearMask"
    DATETOBITINDEX = "dateToBitIndex"
    BITINDEXTODATE = "bitIndexToDate"
    SERIALIZEBITMASK = "serializeBitmask"
    DESERIALIZEBITMASK = "deserializeBitmask"
    STITCHCROSSYEARRANGE = "stitchCrossYearRange"

class EndpointName(Enum):
    """REST endpoint identifiers that must each have contract tests."""
    GET /AVAILABILITY/:UNIT_ID = "GET /availability/:unit_id"
    PUT /AVAILABILITY/:UNIT_ID = "PUT /availability/:unit_id"
    GET /AVAILABILITY/BULK = "GET /availability/bulk"

class TestFixtures:
    """Shared fixture data used across test modules, defined in test_helpers.ts."""
    sampleUnitId: UuidString                 # required, A valid UUID v4 for use in tests.
    sampleUnitId2: UuidString                # required, A second valid UUID v4 for bulk tests.
    sampleYear: Year                         # required, A non-leap year for standard tests (e.g. 2025).
    leapYear: Year                           # required, A leap year for leap-year-specific tests (e.g. 2024).
    sampleBitmaskHex: HexBitmaskString       # required, A known bitmask hex string for round-trip tests.
    emptyBitmaskHex: HexBitmaskString        # required, Hex string representing an all-zeros bitmask ('0x0').
    fullYearBitmaskHex: HexBitmaskString     # required, Hex string representing all 365 bits set (non-leap year).
    sampleAvailabilityRecord: AvailabilityRecord # required, A pre-built availability record for mock DB seeding.

def runBitmaskTests() -> TestSuiteReport:
    """
    Executes the bitmask_test.ts suite: pure unit tests for all BigInt bitmask operations. Covers single-day set/clear, full-year mask, boundary indices (0, 364, 365), leap year bit 59 (Feb 29), cross-year range stitching, and hex string serialization round-trips. Uses factory helpers createBitmask and createDateRange for readability.

    Preconditions:
      - No external services required — all tests are pure functions on BigInt values

    Postconditions:
      - Report suite_name equals 'bitmask_test'
      - Report total >= 13 (at least one test per BitmaskOperationName variant)
      - Each BitmaskOperationName variant has at least one TestResult with matching test_name prefix
      - All tests in report.results have a non-empty test_name
      - report.passed + report.failed == report.total

    Errors:
      - test_setup_failure (TestSetupError): Factory helpers or test infrastructure fail to initialize
      - unexpected_runtime_error (RuntimeError): An uncaught exception occurs outside of a test assertion

    Side effects: none
    Idempotent: yes
    """
    ...

def runCacheTests() -> TestSuiteReport:
    """
    Executes the cache_test.ts suite: integration tests for the cache-aside lifecycle using InMemoryCache and InMemoryDb mocks with spy-based invocation counting. Tests the full path: cache miss → DB query → cache fill → cache hit → write → cache invalidation → re-fill. Verifies cache key format includes unit_id and year. Verifies BigInt↔hex serialization at the cache boundary.

    Preconditions:
      - No external services required — InMemoryDb and InMemoryCache mocks are used
      - InMemoryDb implements DbClient interface
      - InMemoryCache implements CacheClient interface

    Postconditions:
      - Report suite_name equals 'cache_test'
      - Report total >= 7 (miss→query→fill, hit, write→invalidate, re-fill, key format, hex round-trip, multiple years)
      - report.passed + report.failed == report.total
      - At least one test verifies InMemoryDb.queryCount increments on cache miss
      - At least one test verifies InMemoryCache.setCount increments on cache fill
      - At least one test verifies InMemoryCache.delCount increments on cache invalidation
      - At least one test verifies cache key matches pattern 'availability:{uuid}:{year}'

    Errors:
      - mock_initialization_failure (TestSetupError): InMemoryDb or InMemoryCache fails to initialize
      - spy_count_mismatch (AssertionError): Spy counters do not reflect expected invocation counts
      - unexpected_runtime_error (RuntimeError): An uncaught exception occurs outside of a test assertion

    Side effects: none
    Idempotent: yes
    """
    ...

def runApiContractTests() -> TestSuiteReport:
    """
    Executes the api_contract_test.ts suite: contract tests for all three REST endpoints using superoak or Oak's app.handle(). Defines Zod schemas for request/response shapes and validates against them. Covers all success and error scenarios for GET /availability/:unit_id, PUT /availability/:unit_id, and GET /availability/bulk. All error responses validated against the standard { error, message } ErrorResponse shape.

    Preconditions:
      - No external services required — app is configured with InMemoryDb and InMemoryCache
      - Zod schemas are defined for: AvailabilityCheckResult, BulkAvailabilityResult, UpdateAvailabilityResponse, ErrorResponse

    Postconditions:
      - Report suite_name equals 'api_contract_test'
      - Report total >= 10 (at least: GET single success, GET single 404, GET single 400 bad dates, GET single 422 invalid range, PUT success, PUT 400 bad body, PUT cache invalidation, GET bulk success, GET bulk empty, GET bulk 400 missing params)
      - report.passed + report.failed == report.total
      - Every EndpointName variant has at least one passing contract test
      - All error response tests validate against the ErrorResponse Zod schema
      - All success response tests validate against their respective Zod schemas

    Errors:
      - app_initialization_failure (TestSetupError): Oak app or superoak test client fails to initialize
      - schema_validation_failure (SchemaValidationError): A Zod schema parse fails on a response body that was expected to succeed
      - unexpected_status_code (AssertionError): An endpoint returns an HTTP status code not in the expected set for that test case
      - unexpected_runtime_error (RuntimeError): An uncaught exception occurs outside of a test assertion

    Side effects: none
    Idempotent: yes
    """
    ...

def runAllTests() -> TestSuiteReport:
    """
    Runs all three test suites (bitmask_test, cache_test, api_contract_test) sequentially and aggregates results into a combined report. Returns failure if any suite has failing tests.

    Preconditions:
      - No external services required

    Postconditions:
      - Report suite_name equals 'availability_tests'
      - Report total equals sum of totals from bitmask_test + cache_test + api_contract_test
      - Report passed equals sum of passed from all three suites
      - Report failed equals sum of failed from all three suites
      - report.passed + report.failed == report.total
      - report.results contains all individual TestResult entries from all three suites

    Errors:
      - suite_execution_failure (TestSuiteError): One of the sub-suites throws an unrecoverable error preventing further execution
      - unexpected_runtime_error (RuntimeError): An uncaught exception occurs in the orchestration layer

    Side effects: none
    Idempotent: yes
    """
    ...

def createBitmask(
    bitIndices: BitIndexList,
    year: Year = 2025,
) -> HexBitmaskString:
    """
    Factory helper (test_helpers.ts) that creates a BigInt bitmask with specified bit indices set. Used in bitmask_test.ts for readable test setup.

    Preconditions:
      - All bit indices are non-negative integers
      - All bit indices are <= 365 for leap years, <= 364 for non-leap years
      - year is a valid 4-digit year

    Postconditions:
      - Returned hex string starts with '0x'
      - Deserializing the returned hex string yields a BigInt with exactly the specified bits set
      - No bits outside the specified indices are set

    Errors:
      - index_out_of_range (RangeError): A bit index exceeds the maximum for the given year (364 for non-leap, 365 for leap)
      - negative_index (RangeError): A bit index is negative

    Side effects: none
    Idempotent: yes
    """
    ...

def createDateRange(
    start: IsoDateString,
    end: IsoDateString,
) -> DateRange:
    """
    Factory helper (test_helpers.ts) that creates a DateRange from two ISO date strings with validation. Used across all test modules for readable test setup.

    Preconditions:
      - start matches YYYY-MM-DD format
      - end matches YYYY-MM-DD format
      - start represents a valid calendar date
      - end represents a valid calendar date

    Postconditions:
      - Returned DateRange has start and end fields matching the inputs
      - start <= end (chronologically)

    Errors:
      - invalid_date_format (ValidationError): start or end does not match YYYY-MM-DD regex
      - invalid_calendar_date (ValidationError): start or end represents a non-existent date (e.g. 2025-02-29, 2025-13-01)
      - inverted_range (ValidationError): start is chronologically after end

    Side effects: none
    Idempotent: yes
    """
    ...

def createInMemoryDb(
    seedRecords: AvailabilityRecordList = [],
) -> InMemoryDb:
    """
    Factory helper (test_helpers.ts) that creates a fresh InMemoryDb mock pre-seeded with optional availability records. Implements the DbClient interface. Spy counters initialized to zero.

    Postconditions:
      - Returned InMemoryDb.queryCount == 0
      - Returned InMemoryDb.writeCount == 0
      - Returned InMemoryDb.records contains exactly the seedRecords entries
      - Returned object satisfies the DbClient interface

    Side effects: none
    Idempotent: yes
    """
    ...

def createInMemoryCache() -> InMemoryCache:
    """
    Factory helper (test_helpers.ts) that creates a fresh InMemoryCache mock with an empty store. Implements the CacheClient interface. Spy counters initialized to zero.

    Postconditions:
      - Returned InMemoryCache.store is empty
      - Returned InMemoryCache.getCount == 0
      - Returned InMemoryCache.setCount == 0
      - Returned InMemoryCache.delCount == 0
      - Returned object satisfies the CacheClient interface

    Side effects: none
    Idempotent: yes
    """
    ...

def createTestFixtures() -> TestFixtures:
    """
    Factory helper (test_helpers.ts) that creates a complete TestFixtures instance with deterministic sample data for use across all test modules.

    Postconditions:
      - sampleUnitId is a valid UUID v4
      - sampleUnitId2 is a valid UUID v4 different from sampleUnitId
      - sampleYear is a non-leap year
      - leapYear is a leap year
      - emptyBitmaskHex equals '0x0'
      - fullYearBitmaskHex represents 365 bits set
      - sampleAvailabilityRecord.unit_id equals sampleUnitId
      - sampleAvailabilityRecord.year equals sampleYear
      - All values are deterministic across invocations

    Side effects: none
    Idempotent: yes
    """
    ...

def assertSchemaValid(
    schema: any,
    value: any,
    context: str,
) -> None:
    """
    Helper (test_helpers.ts) that validates a value against a Zod schema and throws an AssertionError with details if validation fails. Used in api_contract_test.ts to validate response shapes.

    Preconditions:
      - schema is a valid Zod schema object
      - context is a non-empty string

    Postconditions:
      - If the function returns without throwing, value conforms to schema
      - If validation fails, an AssertionError is thrown with context and Zod error details

    Errors:
      - schema_mismatch (AssertionError): The value does not conform to the Zod schema
          includes: Zod validation errors and context string

    Side effects: none
    Idempotent: yes
    """
    ...

def assertCacheKeyFormat(
    key: str,
    expectedUnitId: UuidString,
    expectedYear: Year,
) -> None:
    """
    Helper (test_helpers.ts) that asserts a cache key string matches the expected format 'availability:{uuid}:{year}'. Used in cache_test.ts.

    Postconditions:
      - If the function returns without throwing, key equals 'availability:{expectedUnitId}:{expectedYear}'

    Errors:
      - key_format_mismatch (AssertionError): The key does not match the expected format or contains wrong unit_id/year

    Side effects: none
    Idempotent: yes
    """
    ...

def assertErrorResponse(
    responseBody: any,
    expectedHttpStatus: int,
    expectedErrorCode: str = None,
) -> None:
    """
    Helper (test_helpers.ts) that validates an HTTP response body conforms to the ErrorResponse schema ({ error: string, message: string }) and optionally checks the error code. Used in api_contract_test.ts for all error path tests.

    Postconditions:
      - responseBody has 'error' field of type string
      - responseBody has 'message' field of type string
      - If expectedErrorCode is non-empty, responseBody.error equals expectedErrorCode

    Errors:
      - missing_error_field (AssertionError): responseBody does not have an 'error' string field
      - missing_message_field (AssertionError): responseBody does not have a 'message' string field
      - wrong_error_code (AssertionError): expectedErrorCode is specified but responseBody.error does not match

    Side effects: none
    Idempotent: yes
    """
    ...
