# Task

Build the Wander Backend Services platform — a microservices backend for a
vacation rental booking and property management system.

CORE CONSTRAINT: "User standing at checkout with credit card. 3 seconds."
Everything flows from this.

The full architecture specification is at reference/architecture.md.
A pre-decomposed component plan with detailed API contracts (inputs, outputs,
invariants, error modes) is at reference/decomposition.json.
Architectural decisions are at reference/decisions.md.
Database schemas and test examples are in reference/schemas/ and
reference/test_examples/.
Technology context is at reference/tech_context.md.

REQUIREMENTS:
1. Implement as many of the 7 services as you can within budget
2. Each service must implement the API endpoints from the architecture spec
3. Use appropriate data models (PostgreSQL schemas at minimum)
4. Include tests for each component
5. Services must integrate via the defined API contracts
6. Prioritize WORKING, INTEGRATED components over comprehensive coverage

SERVICES (in dependency order):
1. Availability Service — bitmask cache, O(1) availability checks
2. Pricing Service — rate cache, hash-sharded by unit_id
3. Property Service — 4-tier override hierarchy
4. Event Service — Kafka event bus, content-addressable event IDs
5. Sync Service — bidirectional PMS synchronization (8 adapters)
6. Payments Service — Stripe mirror, hash-chained payout snapshots
7. Booking Service — synchronous orchestrator (<3s flow)

Any language is acceptable. TypeScript (Deno) preferred to match existing
codebase, but Python is fine. PostgreSQL for persistence, Redis for caching.

If you have questions about requirements, architecture, or existing code,
use the ask_question tool to consult the oracle.

