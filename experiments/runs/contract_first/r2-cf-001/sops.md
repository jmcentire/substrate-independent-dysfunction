# Operating Procedures

## Tech Stack
- Language: TypeScript (Deno 2.x) preferred, Python 3.12+ acceptable
- Database: PostgreSQL for persistence
- Cache: Redis for caching layers
- Testing: Deno test (TypeScript) or pytest (Python)
- Framework: Oak (Deno) or FastAPI (Python)

## Standards
- Type annotations on all public functions
- Prefer composition over inheritance
- Each service is independently deployable
- Services communicate via HTTP REST APIs with JSON
- All dates in ISO 8601 format, UTC
- UUIDs for all entity identifiers

## Architecture Patterns
- Bitmask-based availability (O(1) date range checks)
- Hash-sharded caching by unit_id
- Content-addressable event IDs (SHA-256 of payload)
- 4-tier settings override: platform → brand → property → unit
- Hash-chained payout snapshots for audit integrity

## Verification
- All functions must have at least one test
- Tests must be runnable without external services (mock DB/Redis/Kafka)
- No task is done until its contract tests pass
- Integration boundaries must have contract tests validating request/response shapes

## Preferences
- Keep files under 300 lines
- Prefer stdlib over third-party libraries where possible
- Each service gets its own directory under services/
- Shared types/schemas should be consistent across service boundaries
