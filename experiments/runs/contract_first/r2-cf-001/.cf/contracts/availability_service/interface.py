# === Availability Service (availability_service) v1 ===
#  Dependencies: availability_schema, availability_bitmask_cache, availability_api, availability_tests
# Manages unit availability using bitmask caching for O(1) date range lookups. Maintains a PostgreSQL source-of-truth table of availability records and a Redis cache of bitmasks per unit. Exposes three core service methods — checkAvailability, updateAvailability, checkBulkAvailability — each returning discriminated union Result types (success | error). The service orchestrates cache-check → DB-fallback → cache-warm reads and write → cache-invalidate → respond writes. Cache is warmed on first access (cache-aside with lazy warming) and invalidated synchronously on writes (write-through). All dates are UTC date-only strings (YYYY-MM-DD) using half-open intervals [start, end). This is the lowest-dependency service and the first one called in the booking flow. Composition wires together the schema/repository, bitmask cache, and HTTP API layers through the AvailabilityServiceInterface via dependency injection of PgClient and RedisClient. All types exported from barrel mod.ts. Interface file kept under 300 lines. Contract tests validate request/response shapes against type definitions using in-memory mocks.

# Module invariants:
#   - All dates across the service are YYYY-MM-DD strings interpreted as UTC midnight — no time or timezone components ever appear
#   - All date ranges exposed by the service use half-open intervals [start, end) where start is inclusive and end is exclusive
#   - start must be strictly before end in all date range inputs (start < end)
#   - All error responses use the ApiErrorEnvelope shape: {error: {code: ErrorCode, message: string, details?: unknown}}
#   - ErrorCode is a closed set of exactly 7 values: INVALID_DATE_RANGE, UNIT_NOT_FOUND, BLOCK_OVERLAP, CACHE_UNAVAILABLE, INTERNAL_ERROR, VALIDATION_ERROR, BULK_LIMIT_EXCEEDED
#   - Each ErrorCode maps to exactly one HTTP status code: INVALID_DATE_RANGE→400, UNIT_NOT_FOUND→404, BLOCK_OVERLAP→409, CACHE_UNAVAILABLE→503, INTERNAL_ERROR→500, VALIDATION_ERROR→400, BULK_LIMIT_EXCEEDED→400
#   - PUT /availability/:unit_id always returns HTTP 200 on success (idempotent state set), never 201
#   - Bulk queries are capped at 50 unit_ids maximum (enforced at both validation and service layers)
#   - Unknown unit_ids in bulk queries return {available: false} rather than causing a 404 error
#   - All three service methods (checkAvailability, updateAvailability, checkBulkAvailability) return discriminated union Result types — they never throw exceptions
#   - Writes invalidate the Redis bitmask cache synchronously (write-through) before responding to the caller
#   - Reads use cache-aside pattern: check Redis first, fall back to PostgreSQL on cache miss, then warm the cache (lazy warming)
#   - PostgreSQL is the source of truth for all availability data. Redis is a derived, read-optimized cache. Any conflict is resolved by rebuilding from PostgreSQL
#   - The service is safe for the booking flow's read-then-write pattern: reads see latest data after any write due to synchronous cache invalidation
#   - When Redis is unavailable and fallbackToPgOnRedisFailure is true, the system operates in degraded mode: all reads go directly to PostgreSQL and no caching occurs. A warning-level log is emitted per request
#   - Singleflight pattern ensures that at most one PostgreSQL query is in-flight at any time for a given unitId, regardless of concurrent callers
#   - GET /availability/bulk route is registered before GET /availability/:unit_id in the router to prevent path parameter collision
#   - The middleware stack order is: error handler (outermost) → request logger → router (innermost)
#   - UnitId and BlockId are branded/nominal types — they must not be used interchangeably despite both being UUID v4 strings
#   - BlockType values are: 'reserved', 'maintenance', 'owner_hold'
#   - All types are exported from a barrel mod.ts file
#   - The AvailabilityServiceInterface file is kept under 300 lines
#   - Cache invalidation is idempotent: deleting a non-existent Redis key is a successful no-op
#   - GET /health returns HTTP 200 with status 'ok' when healthy and HTTP 503 with status 'shutting_down' during graceful shutdown for load balancer draining
#   - This is the lowest-dependency service and the first one called in the booking flow (zero upstream service dependencies)

UnitId = primitive  # Branded UUID v4 string identifying a bookable rental unit. Lowercase canonical form (8-4-4-4-12 hex digits with hyphens). Nominal type — must not be interchanged with BlockId or other UUID-typed identifiers.

BlockId = primitive  # Branded UUID v4 string identifying an availability block record. Lowercase canonical form (8-4-4-4-12 hex digits with hyphens). Nominal type — must not be interchanged with UnitId.

DateString = primitive  # Branded ISO 8601 date-only string in YYYY-MM-DD format. Always interpreted as UTC midnight. Never contains a time or timezone component. Used consistently across all layers for date representation.

class BlockType(Enum):
    """The reason/type for an availability block. Union of literal strings. Used in both domain layer and HTTP API. Maps to PostgreSQL VARCHAR with CHECK constraint."""
    RESERVED = "reserved"
    MAINTENANCE = "maintenance"
    OWNER_HOLD = "owner_hold"

class ErrorCode(Enum):
    """Finite set of machine-readable error codes produced by the Availability Service. Each maps to a specific HTTP status code. Used in ApiErrorEnvelope responses."""
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    UNIT_NOT_FOUND = "UNIT_NOT_FOUND"
    BLOCK_OVERLAP = "BLOCK_OVERLAP"
    CACHE_UNAVAILABLE = "CACHE_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    BULK_LIMIT_EXCEEDED = "BULK_LIMIT_EXCEEDED"

class HttpStatusCode(Enum):
    """HTTP status codes used by the Availability Service endpoints. Each ErrorCode maps to exactly one HttpStatusCode."""
    200 = "200"
    400 = "400"
    404 = "404"
    409 = "409"
    500 = "500"
    503 = "503"

class ErrorCodeToHttpStatus:
    """Mapping from ErrorCode to HTTP status code. INVALID_DATE_RANGE→400, UNIT_NOT_FOUND→404, BLOCK_OVERLAP→409, CACHE_UNAVAILABLE→503, INTERNAL_ERROR→500, VALIDATION_ERROR→400, BULK_LIMIT_EXCEEDED→400."""
    INVALID_DATE_RANGE: int                  # required, Bad request — invalid date range.
    UNIT_NOT_FOUND: int                      # required, Not found — unit does not exist.
    BLOCK_OVERLAP: int                       # required, Conflict — block overlaps with existing block.
    CACHE_UNAVAILABLE: int                   # required, Service unavailable — Redis cache unreachable and no fallback.
    INTERNAL_ERROR: int                      # required, Internal server error — unexpected failure.
    VALIDATION_ERROR: int                    # required, Bad request — input validation failure.
    BULK_LIMIT_EXCEEDED: int                 # required, Bad request — too many unit_ids in bulk query.

class DateRange:
    """A half-open date interval [start, end) where start is inclusive and end is exclusive. Both fields are DateString (YYYY-MM-DD, UTC). start must be strictly before end."""
    start: DateString                        # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Inclusive start date of the range in YYYY-MM-DD format, UTC.
    end: DateString                          # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start), Exclusive end date of the range in YYYY-MM-DD format, UTC. Must be strictly after start.

class ErrorDetail:
    """Structured error detail within the API error envelope. Contains machine-readable code, human-readable message, and optional structured details."""
    code: ErrorCode                          # required, Machine-readable error code from the ErrorCode enum.
    message: str                             # required, Human-readable error description.
    details: any = None                      # optional, Optional additional structured details (e.g., Zod issue array, field-level validation errors).

class ApiErrorEnvelope:
    """Consistent JSON error response envelope. All error responses from the Availability Service use this shape: {error: {code, message, details?}}. Readonly."""
    error: ErrorDetail                       # required, The structured error detail object.

class ApiSuccessEnvelope:
    """Consistent JSON success response envelope wrapping the data payload."""
    data: any                                # required, The success response payload. Shape varies by endpoint.

class AvailabilityCheckRequest:
    """Validated parameters for GET /availability/:unit_id?start=&end=. Derived from Zod schema via z.infer<>. Date range is half-open [start, end)."""
    unitId: UnitId                           # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID v4 of the unit to check availability for.
    start: DateString                        # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Inclusive start date of the range to check (YYYY-MM-DD, UTC).
    end: DateString                          # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start), Exclusive end date of the range to check (YYYY-MM-DD, UTC). Must be strictly after start.

class AvailabilityCheckResponse:
    """Response body for GET /availability/:unit_id. Readonly. Indicates whether the unit is fully available across the requested [start, end) range."""
    available: bool                          # required, True if the unit is available for every date in [start, end).
    unitId: UnitId                           # required, The unit that was checked (echoed back).
    start: DateString                        # required, Inclusive start date that was checked (echoed back).
    end: DateString                          # required, Exclusive end date that was checked (echoed back).

class AvailabilityUpdateRequest:
    """Validated request body for PUT /availability/:unit_id. Sets specified date range as available or blocked. Derived from Zod schema."""
    unitId: UnitId                           # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID v4 of the unit to update (from path parameter).
    start: DateString                        # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Inclusive start date of the range to update (YYYY-MM-DD, UTC).
    end: DateString                          # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start), Exclusive end date of the range to update (YYYY-MM-DD, UTC). Must be strictly after start.
    blockType: BlockType                     # required, The reason for blocking. Required even when unblocking, to identify which block record to remove.
    blocked: bool                            # required, True to mark the range as blocked/unavailable, false to unblock/mark as available.

class AvailabilityUpdateResponse:
    """Response body for PUT /availability/:unit_id. Readonly. Always returns HTTP 200 because PUT is idempotent (state set, not created)."""
    success: bool                            # required, True if the availability state was successfully applied.
    unitId: UnitId                           # required, The unit that was updated (echoed back).
    start: DateString                        # required, Inclusive start date that was updated (echoed back).
    end: DateString                          # required, Exclusive end date that was updated (echoed back).
    blockType: BlockType                     # required, The block type that was applied or removed.
    blocked: bool                            # required, Whether the range was blocked (true) or unblocked (false).

class BulkAvailabilityCheckRequest:
    """Validated query parameters for GET /availability/bulk?unit_ids=id1,id2&start=&end=. Derived from Zod schema. Maximum 50 unit_ids."""
    unitIds: UnitIdList                      # required, length(1..50), List of unit UUIDs to check, parsed from comma-separated query parameter. Maximum 50 entries.
    start: DateString                        # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Inclusive start date of the range to check (YYYY-MM-DD, UTC).
    end: DateString                          # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start), Exclusive end date of the range to check (YYYY-MM-DD, UTC). Must be strictly after start.

UnitIdList = list[UnitId]
# A list of UnitId values. Used for bulk operations. Maximum 50 items for the bulk availability endpoint.

class BulkAvailabilityEntry:
    """A single unit's availability result within a bulk response. Readonly."""
    unitId: UnitId                           # required, The unit that was checked.
    available: bool                          # required, True if the unit is available for every date in [start, end). Unknown units return false.

BulkAvailabilityEntryList = list[BulkAvailabilityEntry]
# List of per-unit availability entries in a bulk response.

class BulkAvailabilityCheckResponse:
    """Response body for GET /availability/bulk. Readonly. Contains one entry per requested unit_id in request order."""
    results: BulkAvailabilityEntryList       # required, One entry per requested unit_id, in the same order as the request. Unknown units returned with available: false.
    start: DateString                        # required, Inclusive start date that was checked (echoed back).
    end: DateString                          # required, Exclusive end date that was checked (echoed back).

ServiceResult = ServiceSuccess | ServiceFailure

class ServiceSuccess:
    """Success variant of the ServiceResult discriminated union."""
    ok: bool                                 # required, custom(ok === true), Always true for success variant.
    data: any                                # required, The success payload. Type varies by method: AvailabilityCheckResponse, AvailabilityUpdateResponse, or BulkAvailabilityCheckResponse.

class ServiceFailure:
    """Failure variant of the ServiceResult discriminated union."""
    ok: bool                                 # required, custom(ok === false), Always false for failure variant.
    error: ServiceError                      # required, The error that occurred.

class ServiceError:
    """Structured error within a ServiceResult failure. Maps to ApiErrorEnvelope for HTTP responses."""
    code: ErrorCode                          # required, Machine-readable error code.
    message: str                             # required, Human-readable error description.
    details: any = None                      # optional, Optional additional structured details for debugging.

CheckAvailabilityResult = CheckAvailabilitySuccess | ServiceFailure

class CheckAvailabilitySuccess:
    """Success variant for checkAvailability."""
    ok: bool                                 # required, custom(ok === true), Always true.
    data: AvailabilityCheckResponse          # required, The availability check result.

UpdateAvailabilityResult = UpdateAvailabilitySuccess | ServiceFailure

class UpdateAvailabilitySuccess:
    """Success variant for updateAvailability."""
    ok: bool                                 # required, custom(ok === true), Always true.
    data: AvailabilityUpdateResponse         # required, The availability update result.

BulkCheckAvailabilityResult = BulkCheckAvailabilitySuccess | ServiceFailure

class BulkCheckAvailabilitySuccess:
    """Success variant for checkBulkAvailability."""
    ok: bool                                 # required, custom(ok === true), Always true.
    data: BulkAvailabilityCheckResponse      # required, The bulk availability check result.

class HealthStatus:
    """Response body for GET /health. Readonly. Returns HTTP 200 when healthy, HTTP 503 during graceful shutdown."""
    status: str                              # required, custom(status === 'ok' || status === 'shutting_down'), Either 'ok' or 'shutting_down'.
    uptimeSeconds: float                     # required, range(0..), Server uptime in seconds since start. Non-negative.
    version: str                             # required, length(1..), Application version string.

class ServiceConfig:
    """Configuration for the Availability Service. Passed to the service constructor or factory. Includes injected dependencies and cache/server settings."""
    pgClient: any                            # required, Injected PostgreSQL client implementing the PgClient interface from availability_bitmask_cache.
    redisClient: any                         # required, Injected Redis client implementing the RedisClient interface from availability_bitmask_cache.
    cacheTtlSeconds: int                     # required, range(1..86400), TTL in seconds for cached bitmask entries in Redis.
    cacheShardCount: int = 64                # optional, custom((value & (value - 1)) === 0 && value >= 1), Number of shard buckets for Redis key distribution. Must be a power of 2.
    cacheWindowDays: int = 366               # optional, range(1..731), Number of days in the bitmask window starting from today UTC.
    fallbackToPgOnRedisFailure: bool = true  # optional, When true, Redis unavailability triggers graceful degradation to direct PostgreSQL queries.
    bulkMaxUnitIds: int = 50                 # optional, range(1..1000), Maximum number of unit_ids allowed in a bulk availability check request.
    port: int                                # required, range(1..65535), TCP port for the HTTP server.
    hostname: str = 0.0.0.0                  # optional, Hostname/IP to bind the HTTP server to.
    shutdownTimeoutMs: int = 30000           # optional, range(0..300000), Maximum milliseconds to wait for in-flight requests during graceful shutdown.

class CacheState(Enum):
    """The state of the bitmask cache for a given unit on a particular read operation."""
    HIT = "HIT"
    MISS = "MISS"
    STALE = "STALE"
    DEGRADED = "DEGRADED"

class AvailabilityServiceInterface:
    """TypeScript interface for the Availability Service. The primary contract that all layers (HTTP handlers, tests, mocks) program against. Three async methods returning discriminated union Result types — never throws exceptions."""
    checkAvailability: str                   # required, Async method: (unitId: UnitId, start: DateString, end: DateString) => Promise<CheckAvailabilityResult>. Checks if a single unit is fully available for the half-open date range [start, end).
    updateAvailability: str                  # required, Async method: (request: AvailabilityUpdateRequest) => Promise<UpdateAvailabilityResult>. Updates availability (block/unblock dates) and invalidates cache synchronously before responding.
    checkBulkAvailability: str               # required, Async method: (unitIds: UnitId[], start: DateString, end: DateString) => Promise<BulkCheckAvailabilityResult>. Bulk checks availability for up to 50 units. Unknown units returned with available: false.

def checkAvailability(
    unitId: UnitId,            # regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$)
    start: DateString,         # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
    end: DateString,           # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start)
) -> CheckAvailabilityResult:
    """
    Checks whether a single unit is fully available for the half-open date range [start, end). Orchestrates: (1) validate inputs, (2) check Redis bitmask cache, (3) on cache miss/stale, query PostgreSQL for all blocks in the window, (4) build bitmask and warm cache, (5) perform O(1) bitwise AND check on the requested range, (6) return Result. Uses singleflight to coalesce concurrent cache misses. Falls back to direct PostgreSQL query if Redis is unavailable and fallbackToPgOnRedisFailure is true. Returns a discriminated union Result — never throws.

    Preconditions:
      - unitId is a valid UUID v4 string in lowercase canonical form
      - start and end are valid ISO 8601 YYYY-MM-DD date strings representing real calendar dates
      - start < end (half-open interval [start, end) requires strict ordering)
      - start >= today (UTC) — cannot check availability for past dates
      - end <= today + cacheWindowDays days — range must fit within the bitmask window
      - At least one of RedisClient or PgClient is reachable (or fallbackToPgOnRedisFailure is true)

    Postconditions:
      - If ok=true, data.available is true if and only if every date in [start, end) has no overlapping availability blocks (all bits = 1 in bitmask)
      - If ok=true, data.available is false if any date in [start, end) is blocked (any bit = 0 in bitmask)
      - If ok=true, data.unitId === unitId, data.start === start, data.end === end (echoed back)
      - If the bitmask was rebuilt from PostgreSQL, it is now cached in Redis with the configured TTL (unless Redis is unavailable)
      - If the cached bitmask had a stale anchor_date (not equal to today UTC), it was treated as a miss and rebuilt
      - If singleflight was active, only one PostgreSQL query was issued for concurrent callers requesting the same unitId
      - The function never throws — all errors are returned as ServiceFailure

    Errors:
      - invalid_date_range (ServiceFailure): start >= end, or start/end are not valid calendar dates
          code: INVALID_DATE_RANGE
      - dates_in_past (ServiceFailure): start < today (UTC)
          code: INVALID_DATE_RANGE
      - range_exceeds_window (ServiceFailure): end > today + cacheWindowDays days
          code: INVALID_DATE_RANGE
      - unit_not_found (ServiceFailure): unitId does not exist in PostgreSQL units table (checked during cache rebuild only)
          code: UNIT_NOT_FOUND
      - cache_unavailable (ServiceFailure): Redis is unreachable AND fallbackToPgOnRedisFailure is false
          code: CACHE_UNAVAILABLE
      - internal_error (ServiceFailure): Unexpected failure: corrupt bitmask, PostgreSQL timeout, singleflight panic, or both Redis and PostgreSQL are unreachable
          code: INTERNAL_ERROR

    Side effects: On cache miss: issues SELECT query to PostgreSQL availability_blocks table, On cache miss: writes rebuilt bitmask and metadata to Redis with configured TTL, On stale anchor date detection: deletes stale Redis key and rebuilds, On Redis failure with fallback enabled: emits a warning-level log, Singleflight: may block concurrent callers for the same unitId until the first caller's rebuild completes
    Idempotent: yes
    """
    ...

def updateAvailability(
    request: AvailabilityUpdateRequest,
) -> UpdateAvailabilityResult:
    """
    Updates availability for a unit by blocking or unblocking a half-open date range [start, end). Orchestrates: (1) validate inputs, (2) if blocked=true, create availability block(s) in PostgreSQL via repository, (3) if blocked=false, delete matching block(s) from PostgreSQL, (4) synchronously invalidate the Redis bitmask cache for the unit (write-through), (5) return Result. PUT semantics — idempotent: setting the same state twice produces the same result. Always returns HTTP 200. Returns a discriminated union Result — never throws.

    Preconditions:
      - request.unitId is a valid UUID v4 string
      - request.start and request.end are valid ISO 8601 YYYY-MM-DD date strings representing real calendar dates
      - request.start < request.end (half-open interval)
      - request.blockType is one of 'reserved', 'maintenance', 'owner_hold'
      - PgClient is reachable (writes require the source of truth)
      - RedisClient is reachable for cache invalidation (degraded mode still invalidates on best-effort)

    Postconditions:
      - If ok=true, the availability state in PostgreSQL for the unit's [start, end) range reflects the requested blocked/unblocked state
      - If ok=true and blocked=true, one or more availability_blocks rows exist covering [start, end) for the unit with the specified blockType
      - If ok=true and blocked=false, no availability_blocks rows with the specified blockType exist overlapping [start, end) for the unit
      - If ok=true, the Redis bitmask cache for the affected unitId has been invalidated (deleted). Next read triggers lazy rebuild
      - If ok=true, data.success is true and data echoes back unitId, start, end, blockType, blocked
      - Repeated identical calls produce the same ok=true result (idempotent PUT semantics)
      - The function never throws — all errors are returned as ServiceFailure

    Errors:
      - invalid_date_range (ServiceFailure): start >= end, or start/end are not valid calendar dates
          code: INVALID_DATE_RANGE
      - validation_error (ServiceFailure): Request body fails Zod schema validation (missing fields, wrong types, invalid blockType)
          code: VALIDATION_ERROR
      - unit_not_found (ServiceFailure): unitId does not exist in PostgreSQL units table
          code: UNIT_NOT_FOUND
      - block_overlap (ServiceFailure): blocked=true and the new block overlaps with an existing block for the same unit (PostgreSQL EXCLUDE constraint violation)
          code: BLOCK_OVERLAP
      - cache_unavailable (ServiceFailure): Redis is unreachable for cache invalidation (write still succeeds to PostgreSQL but cache may be stale)
          code: CACHE_UNAVAILABLE
      - internal_error (ServiceFailure): Unexpected PostgreSQL error, connection failure, or other unhandled exception
          code: INTERNAL_ERROR

    Side effects: If blocked=true: inserts one or more rows into PostgreSQL availability_blocks table, If blocked=false: deletes matching rows from PostgreSQL availability_blocks table, Synchronously invalidates (deletes) the Redis bitmask cache key for the affected unitId, On Redis invalidation failure: logs a warning but may still return success if PostgreSQL write succeeded (consistency note: cache may be stale until TTL expiry)
    Idempotent: yes
    """
    ...

def checkBulkAvailability(
    unitIds: UnitIdList,       # length(1..50)
    start: DateString,         # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$)
    end: DateString,           # regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), custom(end > start)
) -> BulkCheckAvailabilityResult:
    """
    Checks availability for multiple units across the same half-open date range [start, end). Capped at 50 unit_ids per request. Orchestrates: (1) validate inputs, (2) use Redis pipeline to fetch bitmasks for all units in a single round-trip, (3) batch-query PostgreSQL for cache misses, (4) warm cache for missed units, (5) perform bitwise checks, (6) return per-unit results. Unknown units returned with available: false (not error). Returns a discriminated union Result — never throws.

    Preconditions:
      - All unitIds are valid UUID v4 strings in lowercase canonical form
      - start and end are valid ISO 8601 YYYY-MM-DD date strings representing real calendar dates
      - start < end (half-open interval)
      - start >= today (UTC)
      - end <= today + cacheWindowDays days
      - unitIds list contains between 1 and 50 entries (inclusive)
      - At least one of RedisClient or PgClient is reachable

    Postconditions:
      - If ok=true, data.results contains exactly one BulkAvailabilityEntry per unique unitId from the input, in input order (deduped)
      - For each entry: available=true iff every date in [start, end) is available for that unit
      - Unknown unitIds appear in results with available=false
      - Duplicate unitIds in input are deduplicated in results (first occurrence order preserved)
      - All cache misses triggered by this call have been rebuilt and stored in Redis (unless Redis is unavailable)
      - data.start === start, data.end === end (echoed back)
      - The function never throws — all errors are returned as ServiceFailure

    Errors:
      - invalid_date_range (ServiceFailure): start >= end, or start/end are not valid calendar dates
          code: INVALID_DATE_RANGE
      - dates_in_past (ServiceFailure): start < today (UTC)
          code: INVALID_DATE_RANGE
      - range_exceeds_window (ServiceFailure): end > today + cacheWindowDays days
          code: INVALID_DATE_RANGE
      - bulk_limit_exceeded (ServiceFailure): unitIds list contains more than 50 entries
          code: BULK_LIMIT_EXCEEDED
      - empty_unit_ids (ServiceFailure): unitIds list is empty
          code: VALIDATION_ERROR
      - validation_error (ServiceFailure): One or more unitIds is not a valid UUID v4 string
          code: VALIDATION_ERROR
      - cache_unavailable (ServiceFailure): Redis is unreachable AND PostgreSQL is unreachable (systemic failure)
          code: CACHE_UNAVAILABLE
      - internal_error (ServiceFailure): Unexpected systemic failure affecting all units
          code: INTERNAL_ERROR

    Side effects: On cache misses: issues batch SELECT queries to PostgreSQL availability_blocks table, On cache misses: writes rebuilt bitmasks and metadata to Redis via pipeline with configured TTL, On stale anchor date detection for any unit: deletes and rebuilds those entries, On Redis failure with fallback enabled: emits a warning-level log and falls back to PostgreSQL for all units, Singleflight: concurrent misses for the same unitId within this batch and across concurrent calls are coalesced
    Idempotent: yes
    """
    ...

def createAvailabilityService(
    config: ServiceConfig,
) -> AvailabilityServiceInterface:
    """
    Factory function that creates an AvailabilityService instance implementing AvailabilityServiceInterface. Wires together the three dependency layers (schema/repository, bitmask cache, HTTP API) through dependency injection of PgClient and RedisClient. Initializes the bitmask cache with the provided configuration. Returns a fully composed service ready for injection into the HTTP router.

    Preconditions:
      - config.pgClient implements the PgClient interface
      - config.redisClient implements the RedisClient interface
      - config.cacheTtlSeconds is between 1 and 86400
      - config.cacheShardCount is a positive power of 2
      - config.port is between 1 and 65535

    Postconditions:
      - Returned object implements all three methods of AvailabilityServiceInterface
      - checkAvailability, updateAvailability, and checkBulkAvailability are bound to the provided PgClient and RedisClient
      - Bitmask cache is configured with the provided TTL, shard count, and window days
      - The service is ready for injection into createRouter and createServer

    Errors:
      - invalid_config (ServiceFailure): ServiceConfig fails validation (missing required fields, out-of-range values)
          code: INTERNAL_ERROR
      - pg_client_unreachable (ServiceFailure): Initial PgClient.ping() fails during service creation health check
          code: INTERNAL_ERROR

    Side effects: May issue a PgClient.ping() to verify database connectivity, May issue a RedisClient.ping() to verify cache connectivity
    Idempotent: yes
    """
    ...

def mapErrorCodeToHttpStatus(
    code: ErrorCode,
) -> int:
    """
    Pure utility function that maps an ErrorCode to its corresponding HTTP status code integer. Used by HTTP handlers to set the response status when returning an ApiErrorEnvelope.

    Preconditions:
      - code is a valid ErrorCode enum variant

    Postconditions:
      - Returns 400 for INVALID_DATE_RANGE, VALIDATION_ERROR, BULK_LIMIT_EXCEEDED
      - Returns 404 for UNIT_NOT_FOUND
      - Returns 409 for BLOCK_OVERLAP
      - Returns 500 for INTERNAL_ERROR
      - Returns 503 for CACHE_UNAVAILABLE
      - Every ErrorCode variant maps to exactly one HTTP status code

    Side effects: none
    Idempotent: yes
    """
    ...

def serviceResultToHttpResponse(
    result: ServiceResult,
) -> HttpResponse:
    """
    Converts a ServiceResult (discriminated union) into the appropriate HTTP response: on success, wraps data in ApiSuccessEnvelope with status 200; on failure, wraps error in ApiErrorEnvelope with the mapped HTTP status code. Used by HTTP route handlers to produce consistent responses.

    Preconditions:
      - result is a valid ServiceResult (either ServiceSuccess or ServiceFailure)

    Postconditions:
      - If result.ok is true, returned HttpResponse.status is 200 and body is the success data
      - If result.ok is false, returned HttpResponse.status is the mapped HTTP status code for result.error.code, and body is an ApiErrorEnvelope
      - The function never throws

    Side effects: none
    Idempotent: yes
    """
    ...

def handleGetAvailability(
    unitId: str,
    start: str,
    end: str,
) -> HttpResponse:
    """
    HTTP route handler for GET /availability/:unit_id?start=&end=. Validates path parameter (unit_id as UUID v4) and query parameters (start, end as YYYY-MM-DD) using Zod schemas. Calls AvailabilityServiceInterface.checkAvailability. Returns 200 with AvailabilityCheckResponse on success. Maps errors to appropriate HTTP status codes via ApiErrorEnvelope. Date range is interpreted as half-open interval [start, end).

    Preconditions:
      - Server is running and accepting requests (not in shutdown state)
      - AvailabilityServiceInterface has been injected into the router

    Postconditions:
      - Response status is 200 with AvailabilityCheckResponse body on success
      - Response echoes back unitId, start, end from validated input
      - available is true only if every date in [start, end) is unblocked
      - On validation failure, returns 400 with ApiErrorEnvelope containing code VALIDATION_ERROR or INVALID_DATE_RANGE
      - On unit not found, returns 404 with ApiErrorEnvelope containing code UNIT_NOT_FOUND
      - On internal failure, returns 500 with ApiErrorEnvelope containing code INTERNAL_ERROR

    Errors:
      - invalid_unit_id (ApiErrorEnvelope): unit_id path parameter is not a valid UUID v4 string
          code: VALIDATION_ERROR
          http_status: 400
      - invalid_date_format (ApiErrorEnvelope): start or end query parameter is missing or not a valid YYYY-MM-DD string
          code: INVALID_DATE_RANGE
          http_status: 400
      - end_not_after_start (ApiErrorEnvelope): end date is less than or equal to start date
          code: INVALID_DATE_RANGE
          http_status: 400
      - unit_not_found (ApiErrorEnvelope): The unit_id does not correspond to any known unit
          code: UNIT_NOT_FOUND
          http_status: 404
      - internal_error (ApiErrorEnvelope): Unexpected error in the service layer
          code: INTERNAL_ERROR
          http_status: 500

    Side effects: none
    Idempotent: yes
    """
    ...

def handlePutAvailability(
    unitId: str,
    body: any,
) -> HttpResponse:
    """
    HTTP route handler for PUT /availability/:unit_id. Validates path parameter (unit_id as UUID v4) and JSON body (start, end, blockType, blocked) using Zod schemas. Calls AvailabilityServiceInterface.updateAvailability. Returns 200 with AvailabilityUpdateResponse. PUT is idempotent — never returns 201. Bitmask cache for the affected unit is invalidated synchronously before responding.

    Preconditions:
      - Server is running and accepting requests (not in shutdown state)
      - AvailabilityServiceInterface has been injected into the router
      - Request Content-Type is application/json

    Postconditions:
      - Response status is 200 with AvailabilityUpdateResponse {success: true} on success
      - The availability state for the unit's [start, end) range reflects the requested blocked/unblocked state
      - Bitmask cache for the affected unit has been invalidated
      - Repeated identical PUTs produce the same 200 response (idempotent)

    Errors:
      - invalid_unit_id (ApiErrorEnvelope): unit_id path parameter is not a valid UUID v4 string
          code: VALIDATION_ERROR
          http_status: 400
      - invalid_body (ApiErrorEnvelope): Request body is missing, not valid JSON, or fails Zod schema validation
          code: VALIDATION_ERROR
          http_status: 400
      - invalid_date_range (ApiErrorEnvelope): start >= end in the request body
          code: INVALID_DATE_RANGE
          http_status: 400
      - invalid_block_type (ApiErrorEnvelope): blockType is not one of the valid BlockType enum values
          code: VALIDATION_ERROR
          http_status: 400
      - unit_not_found (ApiErrorEnvelope): The unit_id does not correspond to any known unit
          code: UNIT_NOT_FOUND
          http_status: 404
      - block_overlap (ApiErrorEnvelope): blocked=true and the new block overlaps with an existing block for the same unit
          code: BLOCK_OVERLAP
          http_status: 409
      - internal_error (ApiErrorEnvelope): Unexpected error in the service layer
          code: INTERNAL_ERROR
          http_status: 500

    Side effects: Modifies PostgreSQL availability_blocks table (insert or delete rows), Invalidates Redis bitmask cache key for the affected unitId
    Idempotent: yes
    """
    ...

def handleGetBulkAvailability(
    unitIds: str,
    start: str,
    end: str,
) -> HttpResponse:
    """
    HTTP route handler for GET /availability/bulk?unit_ids=id1,id2&start=&end=. Validates query parameters: unit_ids as comma-separated UUID v4 list (max 50), start and end as YYYY-MM-DD. Calls AvailabilityServiceInterface.checkBulkAvailability. Returns 200 with BulkAvailabilityCheckResponse. Unknown unit_ids returned with available: false (not 404). Registered before GET /availability/:unit_id to prevent path parameter collision.

    Preconditions:
      - Server is running and accepting requests (not in shutdown state)
      - AvailabilityServiceInterface has been injected into the router

    Postconditions:
      - Response status is 200 with BulkAvailabilityCheckResponse body
      - results array has exactly one entry per unique requested unit_id
      - results are in the same order as the input unit_ids (deduped)
      - Unknown unit_ids appear in results with available: false
      - data.start and data.end echo back the validated input values

    Errors:
      - missing_unit_ids (ApiErrorEnvelope): unit_ids query parameter is missing or empty
          code: VALIDATION_ERROR
          http_status: 400
      - invalid_unit_id_format (ApiErrorEnvelope): One or more values in the comma-separated unit_ids list is not a valid UUID v4
          code: VALIDATION_ERROR
          http_status: 400
      - bulk_limit_exceeded (ApiErrorEnvelope): More than 50 unit_ids are provided
          code: BULK_LIMIT_EXCEEDED
          http_status: 400
      - invalid_date_format (ApiErrorEnvelope): start or end query parameter is missing or not a valid YYYY-MM-DD string
          code: INVALID_DATE_RANGE
          http_status: 400
      - end_not_after_start (ApiErrorEnvelope): end date is less than or equal to start date
          code: INVALID_DATE_RANGE
          http_status: 400
      - internal_error (ApiErrorEnvelope): Unexpected systemic error in the service layer
          code: INTERNAL_ERROR
          http_status: 500

    Side effects: none
    Idempotent: yes
    """
    ...

def handleHealthCheck() -> HttpResponse:
    """
    HTTP route handler for GET /health. Returns HealthStatus with current server state. Returns HTTP 200 with status 'ok' when healthy. Returns HTTP 503 with status 'shutting_down' during graceful shutdown to enable load balancer draining.

    Postconditions:
      - If server is healthy, response status is 200 and body.status is 'ok'
      - If server is shutting down, response status is 503 and body.status is 'shutting_down'
      - body.uptimeSeconds is a non-negative number reflecting time since server start
      - body.version is a non-empty string

    Side effects: none
    Idempotent: yes
    """
    ...
