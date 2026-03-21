# Technology Context

## Stack

- **Runtime:** Deno 2.x (TypeScript preferred) — but any language acceptable
- **Language:** TypeScript (preferred), Python acceptable
- **Database:** PostgreSQL 14+ with streaming replication
- **Cache:** Redis Cluster (3 masters, 3 replicas, Sentinel for failover)
- **Message Bus:** Kafka (32 partitions for events.raw, variable for subsystems)
- **Object Storage:** R2 (for documents, images)
- **Geo:** PostGIS extension for location queries
- **Connection Pooling:** pgBouncer in transaction mode

## Conventions

- **Dates:** YYYY-MM-DD (date-only, no time) for availability/pricing; ISO8601 UTC for timestamps
- **Money:** Cents (integers), Banker's rounding (round half to even), currency per property
- **IDs:** UUIDs for entities, content-addressable SHA256 for events
- **Idempotency:** All mutation endpoints require client-generated UUID idempotency_key
- **Errors:** Structured `{error: {code: "...", message: "...", details: {}}}`, 4xx client / 5xx server
- **Events:** Fire-and-forget to Event Service SDK, audit trail for all state changes

## Architecture Principles

1. **"User at checkout with credit card. 3 seconds."** — Everything flows from this constraint
2. **PostgreSQL is source of truth** — Redis/caches are non-authoritative
3. **PMS is authoritative** — For BRANDED properties, external PMS always wins
4. **Synchronous critical path** — No sagas, no distributed transactions for booking flow
5. **Simple over complex** — Straightforward patterns over distributed system gymnastics

## Existing Test Patterns

The existing codebase uses:
- **Deno testing:** `Deno.test()` with `@std/assert` (assertEquals, assertExists, etc.)
- **Effect-TS:** Functional effect system for dependency injection and error handling
- **Zod/Schema:** Runtime type validation for API contracts
- **Smoke tests:** Integration tests hitting service HTTP endpoints
- **Unit tests:** Effect.provide() for mock dependency injection
