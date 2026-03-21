# === Availability HTTP API & Server (availability_api) v1 ===
# Oak HTTP server exposing availability REST endpoints. GET /availability/:unit_id?start=&end= checks availability for a date range. PUT /availability/:unit_id updates availability (block/unblock dates). GET /availability/bulk?unit_ids=&start=&end= bulk-checks up to 100 units. GET /health returns server health. Uses Zod schemas as single source of truth for validation and TypeScript types. Date-only strings (YYYY-MM-DD) interpreted as UTC with half-open intervals [start, end). Consistent error envelope {error: {code, message, details?}}. PUT returns 200 (idempotent). Health returns 503 during shutdown for load balancer draining. Dependency injection via AvailabilityService interface for testability.

# Module invariants:
#   - All dates are YYYY-MM-DD strings interpreted as UTC midnight; no time or timezone components.
#   - All date ranges are half-open intervals: [start, end) where start is inclusive and end is exclusive.
#   - start must be strictly before end in all date range inputs.
#   - All error responses use the ApiErrorEnvelope shape: {error: {code: string, message: string, details?: unknown}}.
#   - PUT /availability/:unit_id always returns HTTP 200 on success (idempotent state set), never 201.
#   - GET /health returns HTTP 200 with status 'ok' when healthy and HTTP 503 with status 'shutting_down' during graceful shutdown.
#   - Bulk queries are capped at 100 unit_ids maximum.
#   - Unknown unit_ids in bulk queries return {available: false} rather than causing a 404 error.
#   - Zod schemas in schemas.ts are the single source of truth for both runtime validation and TypeScript types (via z.infer<>).
#   - UnitId is a branded UUID v4 string; all unit_id parameters must be valid UUID v4.
#   - BlockType must be one of: OWNER_BLOCK, MAINTENANCE, BOOKING, SEASONAL_CLOSURE, OTHER.
#   - The router registers GET /availability/bulk before GET /availability/:unit_id to prevent path parameter collision.
#   - The middleware stack order is: error handler (outermost) → request logger → router (innermost).
#   - Graceful shutdown waits up to shutdown_timeout_ms for in-flight requests before force-closing.
#   - All response types (AvailabilityCheckResult, AvailabilityUpdateResult, BulkAvailabilityResult, HealthStatus) are Readonly.
#   - validateRequest never throws; all validation failures are returned as structured ValidationFailure values.

UnitId = primitive  # Branded UUID v4 string identifying a rentable unit. Must match UUID v4 format [0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}.

DateString = primitive  # Date-only string in YYYY-MM-DD format, interpreted as UTC midnight. Must match regex ^\d{4}-\d{2}-\d{2}$ and represent a valid calendar date.

class BlockType(Enum):
    """The reason a date range is blocked for a unit."""
    OWNER_BLOCK = "OWNER_BLOCK"
    MAINTENANCE = "MAINTENANCE"
    BOOKING = "BOOKING"
    SEASONAL_CLOSURE = "SEASONAL_CLOSURE"
    OTHER = "OTHER"

class HttpMethod(Enum):
    """HTTP methods used by this API."""
    GET = "GET"
    PUT = "PUT"

HttpStatusCode = primitive  # HTTP numeric status code (e.g. 200, 400, 404, 500, 503).

class DateRange:
    """A half-open date interval [start, end). start is inclusive, end is exclusive. Both are YYYY-MM-DD date strings in UTC."""
    start: DateString                        # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Inclusive start date of the range.
    end: DateString                          # required, regex(^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$), Exclusive end date of the range. Must be strictly after start.

class ErrorDetail:
    """Structured error detail within the API error envelope."""
    code: str                                # required, Machine-readable error code, e.g. INVALID_DATE_FORMAT, UNIT_NOT_FOUND, VALIDATION_ERROR, INTERNAL_ERROR, BULK_LIMIT_EXCEEDED.
    message: str                             # required, Human-readable error description.
    details: any = None                      # optional, Optional additional structured details (e.g. Zod issue array, field-level errors).

class ApiErrorEnvelope:
    """Consistent JSON error response envelope. All error responses from this API use this shape. Readonly."""
    error: ErrorDetail                       # required, The structured error detail object.

class AvailabilityCheckParams:
    """Validated parameters for GET /availability/:unit_id?start=&end=. Derived from Zod AvailabilityCheckParamsSchema via z.infer<>."""
    unit_id: UnitId                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID of the unit to check.
    start: DateString                        # required, Inclusive start of the date range to check.
    end: DateString                          # required, Exclusive end of the date range to check.

class AvailabilityCheckResult:
    """Response body for GET /availability/:unit_id. Readonly. Indicates whether the unit is fully available across the requested [start, end) range."""
    available: bool                          # required, True if the unit is available for every date in [start, end).
    unit_id: UnitId                          # required, The unit that was checked.
    start: DateString                        # required, Inclusive start date that was checked.
    end: DateString                          # required, Exclusive end date that was checked.

class AvailabilityUpdateParams:
    """Validated path parameters for PUT /availability/:unit_id. Derived from Zod AvailabilityUpdateParamsSchema via z.infer<>."""
    unit_id: UnitId                          # required, regex(^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$), UUID of the unit to update.

class AvailabilityUpdateBody:
    """Validated request body for PUT /availability/:unit_id. Derived from Zod AvailabilityUpdateBodySchema via z.infer<>."""
    start: DateString                        # required, Inclusive start date of the range to update.
    end: DateString                          # required, Exclusive end date of the range to update.
    block_type: BlockType                    # required, The reason for blocking. Required even when unblocking, to identify which block record to remove.
    blocked: bool                            # required, True to mark the range as blocked/unavailable, false to unblock/mark available.

class AvailabilityUpdateResult:
    """Response body for PUT /availability/:unit_id. Readonly. Always returns HTTP 200 because PUT is idempotent (state set, not created)."""
    success: bool                            # required, True if the availability state was successfully applied.

class BulkAvailabilityQueryParams:
    """Validated query parameters for GET /availability/bulk?unit_ids=id1,id2&start=&end=. Derived from Zod BulkAvailabilityQuerySchema via z.infer<>."""
    unit_ids: UnitIdList                     # required, length(1..100), Comma-separated list of unit UUIDs, parsed into an array. Maximum 100 entries.
    start: DateString                        # required, Inclusive start date of the range to check.
    end: DateString                          # required, Exclusive end date of the range to check.

UnitIdList = list[UnitId]
# A list of UnitId values. Maximum 100 items for bulk queries.

class BulkAvailabilityEntry:
    """A single unit's availability result within a bulk response. Readonly."""
    unit_id: UnitId                          # required, The unit that was checked.
    available: bool                          # required, True if the unit is available for every date in [start, end).

BulkAvailabilityEntryList = list[BulkAvailabilityEntry]
# List of per-unit availability entries in a bulk response.

class BulkAvailabilityResult:
    """Response body for GET /availability/bulk. Readonly."""
    results: BulkAvailabilityEntryList       # required, One entry per requested unit_id, in the same order as the request.

class HealthStatus:
    """Response body for GET /health. Readonly. Returns HTTP 200 when healthy, HTTP 503 during graceful shutdown to support load balancer draining."""
    status: str                              # required, Either 'ok' or 'shutting_down'.
    uptime_seconds: float                    # required, Server uptime in seconds since start.
    version: str                             # required, Application version string.

class ServerConfig:
    """Configuration for server startup. Passed to createServer factory."""
    port: int                                # required, range(1..65535), TCP port to listen on.
    hostname: str                            # required, Hostname/IP to bind to.
    shutdown_timeout_ms: int                 # required, range(0..300000), Maximum milliseconds to wait for in-flight requests during graceful shutdown.
    availability_service: AvailabilityServiceInterface # required, Injected service dependency implementing the AvailabilityService interface.

class AvailabilityServiceInterface:
    """Interface (TypeScript interface) for the availability domain service. Injected into the router factory function to decouple HTTP layer from business logic. Enables testing route handlers without mocking Oak internals."""
    checkAvailability: str                   # required, Async method: (unitId: UnitId, start: DateString, end: DateString) => Promise<AvailabilityCheckResult>. Throws UnitNotFoundError if unit does not exist.
    updateAvailability: str                  # required, Async method: (unitId: UnitId, body: AvailabilityUpdateBody) => Promise<AvailabilityUpdateResult>. Throws UnitNotFoundError if unit does not exist.
    bulkCheckAvailability: str               # required, Async method: (unitIds: UnitId[], start: DateString, end: DateString) => Promise<BulkAvailabilityResult>. Unknown unit_ids are returned with available: false rather than throwing.

class ServerHandle:
    """Handle returned by createServer. Provides start/stop lifecycle methods."""
    start: str                               # required, Async method: () => Promise<void>. Starts listening on the configured port. Resolves once the server is accepting connections.
    stop: str                                # required, Async method: () => Promise<void>. Initiates graceful shutdown: sets health to 503, stops accepting new connections, waits for in-flight requests up to shutdown_timeout_ms, then closes. Uses AbortController internally.

ValidationResult = ValidationSuccess | ValidationFailure

class ValidationSuccess:
    """Successful validation result from validateRequest. Contains the parsed and typed data."""
    success: bool                            # required, Always true for successful validation.
    data: any                                # required, The validated and typed data matching the Zod schema.

class ValidationFailure:
    """Failed validation result from validateRequest. Contains a structured ApiErrorEnvelope ready for 400 response."""
    success: bool                            # required, Always false for failed validation.
    error_response: ApiErrorEnvelope         # required, Structured error envelope with code VALIDATION_ERROR, message, and Zod issues in details.

class RequestLogEntry:
    """Structured log entry emitted by the request logger middleware."""
    method: str                              # required, HTTP method of the request.
    path: str                                # required, Request path.
    status: int                              # required, HTTP response status code.
    duration_ms: float                       # required, Request-response duration in milliseconds.
    timestamp: str                           # required, ISO 8601 timestamp of the request.

def validateRequest(
    schema: any,
    data: any,
) -> ValidationResult:
    """
    Generic request validation helper that uses Zod .safeParse() on input data against a given schema. Returns either the successfully parsed typed data or a structured ApiErrorEnvelope suitable for a 400 response. This is the single entry point for all request validation in schemas.ts.

    Preconditions:
      - schema must be a valid Zod schema with a .safeParse() method.
      - data must not be undefined (null is acceptable as Zod will report it as an error).

    Postconditions:
      - If success is true, data conforms to the schema's inferred TypeScript type.
      - If success is false, error_response.error.code is 'VALIDATION_ERROR'.
      - If success is false, error_response.error.details contains the Zod issues array.
      - Function never throws; all validation failures are returned as ValidationFailure.

    Side effects: none
    Idempotent: yes
    """
    ...

def handleCheckAvailability(
    unit_id: str,
    start: str,
    end: str,
) -> AvailabilityCheckResult:
    """
    Route handler for GET /availability/:unit_id?start=&end=. Validates path param (unit_id as UUID v4) and query params (start, end as YYYY-MM-DD). Calls AvailabilityService.checkAvailability. Returns 200 with AvailabilityCheckResult on success. Date range is interpreted as half-open interval [start, end).

    Preconditions:
      - Server is running and accepting requests (not in shutdown state).
      - AvailabilityService has been injected into the router.

    Postconditions:
      - Response status is 200 with AvailabilityCheckResult body on success.
      - Response unit_id, start, end echo back the validated input values.
      - available is true only if every date in [start, end) is unblocked.

    Errors:
      - invalid_unit_id (ApiErrorEnvelope): unit_id path parameter is not a valid UUID v4 string.
          http_status: 400
          code: VALIDATION_ERROR
      - invalid_date_format (ApiErrorEnvelope): start or end query parameter is missing, not a valid YYYY-MM-DD string, or does not represent a valid calendar date.
          http_status: 400
          code: INVALID_DATE_FORMAT
      - end_not_after_start (ApiErrorEnvelope): end date is less than or equal to start date.
          http_status: 400
          code: VALIDATION_ERROR
          message: end must be strictly after start.
      - unit_not_found (ApiErrorEnvelope): The unit_id does not correspond to any known unit in the system.
          http_status: 404
          code: UNIT_NOT_FOUND
      - internal_error (ApiErrorEnvelope): An unexpected error occurs in the service layer (database timeout, Redis failure, etc.).
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def handleUpdateAvailability(
    unit_id: str,
    body: any,
) -> AvailabilityUpdateResult:
    """
    Route handler for PUT /availability/:unit_id. Validates path param (unit_id as UUID v4) and JSON body (start, end, block_type, blocked). Calls AvailabilityService.updateAvailability. Returns 200 with AvailabilityUpdateResult. PUT is idempotent: setting the same state twice produces the same result. Never returns 201.

    Preconditions:
      - Server is running and accepting requests (not in shutdown state).
      - AvailabilityService has been injected into the router.
      - Request Content-Type is application/json.

    Postconditions:
      - Response status is 200 with {success: true} on success.
      - The availability state for the unit's [start, end) range reflects the requested blocked/unblocked state.
      - Bitmask cache for the affected unit has been invalidated.
      - Repeated identical PUTs produce the same 200 response (idempotent).

    Errors:
      - invalid_unit_id (ApiErrorEnvelope): unit_id path parameter is not a valid UUID v4 string.
          http_status: 400
          code: VALIDATION_ERROR
      - invalid_body (ApiErrorEnvelope): Request body is missing, not valid JSON, or fails Zod schema validation (missing fields, wrong types, invalid date format, invalid block_type).
          http_status: 400
          code: VALIDATION_ERROR
      - invalid_date_format (ApiErrorEnvelope): start or end in the body is not a valid YYYY-MM-DD string or not a valid calendar date.
          http_status: 400
          code: INVALID_DATE_FORMAT
      - end_not_after_start (ApiErrorEnvelope): end date is less than or equal to start date in the body.
          http_status: 400
          code: VALIDATION_ERROR
          message: end must be strictly after start.
      - invalid_block_type (ApiErrorEnvelope): block_type is not one of the valid BlockType enum values.
          http_status: 400
          code: VALIDATION_ERROR
          message: block_type must be one of: OWNER_BLOCK, MAINTENANCE, BOOKING, SEASONAL_CLOSURE, OTHER.
      - unit_not_found (ApiErrorEnvelope): The unit_id does not correspond to any known unit in the system.
          http_status: 404
          code: UNIT_NOT_FOUND
      - internal_error (ApiErrorEnvelope): An unexpected error occurs in the service layer.
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def handleBulkCheckAvailability(
    unit_ids: str,
    start: str,
    end: str,
) -> BulkAvailabilityResult:
    """
    Route handler for GET /availability/bulk?unit_ids=id1,id2&start=&end=. Validates query params: unit_ids as comma-separated UUID v4 list (max 100), start and end as YYYY-MM-DD. Calls AvailabilityService.bulkCheckAvailability. Returns 200 with BulkAvailabilityResult. Unknown unit_ids are returned with available: false (not 404).

    Preconditions:
      - Server is running and accepting requests (not in shutdown state).
      - AvailabilityService has been injected into the router.

    Postconditions:
      - Response status is 200 with BulkAvailabilityResult body.
      - results array has exactly one entry per unique requested unit_id.
      - results are in the same order as the input unit_ids.
      - Unknown unit_ids appear in results with available: false.
      - Duplicate unit_ids in input are deduplicated in results.

    Errors:
      - missing_unit_ids (ApiErrorEnvelope): unit_ids query parameter is missing or empty.
          http_status: 400
          code: VALIDATION_ERROR
          message: unit_ids query parameter is required.
      - invalid_unit_id_format (ApiErrorEnvelope): One or more values in the comma-separated unit_ids list is not a valid UUID v4.
          http_status: 400
          code: VALIDATION_ERROR
      - bulk_limit_exceeded (ApiErrorEnvelope): More than 100 unit_ids are provided in the query parameter.
          http_status: 400
          code: BULK_LIMIT_EXCEEDED
          message: Maximum 100 unit_ids per bulk request.
      - invalid_date_format (ApiErrorEnvelope): start or end query parameter is missing, not a valid YYYY-MM-DD string, or not a valid calendar date.
          http_status: 400
          code: INVALID_DATE_FORMAT
      - end_not_after_start (ApiErrorEnvelope): end date is less than or equal to start date.
          http_status: 400
          code: VALIDATION_ERROR
          message: end must be strictly after start.
      - internal_error (ApiErrorEnvelope): An unexpected error occurs in the service layer.
          http_status: 500
          code: INTERNAL_ERROR

    Side effects: none
    Idempotent: yes
    """
    ...

def handleHealthCheck() -> HealthStatus:
    """
    Route handler for GET /health. Returns HealthStatus with current server state. Returns HTTP 200 with status 'ok' when healthy. Returns HTTP 503 with status 'shutting_down' during graceful shutdown to enable load balancer draining.

    Postconditions:
      - If server is healthy, response status is 200 and body.status is 'ok'.
      - If server is shutting down, response status is 503 and body.status is 'shutting_down'.
      - uptime_seconds is a non-negative number reflecting time since server start.
      - version is a non-empty string.

    Side effects: none
    Idempotent: yes
    """
    ...

def createRouter(
    service: AvailabilityServiceInterface,
) -> any:
    """
    Factory function in routes.ts that creates an Oak Router with all availability routes and the health endpoint. Accepts an AvailabilityServiceInterface via dependency injection, enabling testability without mocking Oak internals. Registers: GET /availability/bulk, GET /availability/:unit_id, PUT /availability/:unit_id, GET /health. The /bulk route is registered before /:unit_id to avoid path parameter collision.

    Preconditions:
      - service must implement all three methods of AvailabilityServiceInterface.
      - service methods must return Promises that resolve to the documented types.

    Postconditions:
      - Returns an Oak Router instance with all four routes registered.
      - GET /availability/bulk is registered before GET /availability/:unit_id to prevent path collision.
      - Each route handler validates inputs before calling the service.
      - Each route handler catches service errors and maps them to appropriate HTTP status codes and ApiErrorEnvelope responses.

    Side effects: none
    Idempotent: yes
    """
    ...

def createServer(
    config: ServerConfig,
) -> ServerHandle:
    """
    Server lifecycle factory in server.ts. Creates and configures the Oak Application with middleware stack (error handler → request logger → router) and returns a ServerHandle with start() and stop() methods. Uses AbortController internally for graceful shutdown. Registers SIGINT and SIGTERM signal handlers that call stop().

    Preconditions:
      - config.port is a valid port number (1-65535).
      - config.availability_service implements AvailabilityServiceInterface.
      - No other server is already listening on the same port.

    Postconditions:
      - Returns a ServerHandle with start() and stop() async methods.
      - start() resolves once the server is accepting connections on config.port.
      - stop() triggers graceful shutdown: health returns 503, new connections are rejected, in-flight requests drain up to shutdown_timeout_ms, then the server closes.
      - SIGINT and SIGTERM signal handlers are registered to call stop().
      - Middleware is applied in order: error handler (outermost) → request logger → router (innermost).

    Errors:
      - port_in_use (str): The configured port is already bound by another process.
          message: Failed to start server: port already in use.
      - invalid_config (str): ServerConfig fails validation (e.g. port out of range, missing service).
          message: Invalid server configuration.

    Side effects: Binds a TCP listener on the configured port and hostname., Registers process signal handlers for SIGINT and SIGTERM., Emits structured request log entries to stdout for each handled request.
    Idempotent: no
    """
    ...
