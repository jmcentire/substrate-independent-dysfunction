# Wander Backend Services

A microservices backend for vacation rental booking and property management.

**Core Constraint:** "User standing at checkout with credit card. 3 seconds."

## Architecture

7 services in dependency order:

1. **Availability Service** (port 8001) — Bitmask cache, O(1) availability checks
2. **Pricing Service** (port 8002) — Rate cache, hash-sharded by unit_id
3. **Property Service** (port 8003) — 4-tier override hierarchy
4. **Event Service** (port 8004) — Event bus, content-addressable event IDs
5. **Sync Service** (port 8005) — Bidirectional PMS synchronization
6. **Payments Service** (port 8006) — Stripe mirror, hash-chained payout snapshots
7. **Booking Service** (port 8007) — Synchronous orchestrator (<3s flow)

## Quick Start

```bash
# Run all tests (no external deps required - uses SQLite for testing)
python3 -m pytest tests/ -v

# Start individual services
python3 -m services.availability.app  # port 8001
python3 -m services.pricing.app       # port 8002
python3 -m services.property.app      # port 8003
python3 -m services.event.app         # port 8004
python3 -m services.sync.app          # port 8005
python3 -m services.payments.app      # port 8006
python3 -m services.booking.app       # port 8007
```

## Tech Stack

- **Language:** Python 3.11+ (FastAPI)
- **Database:** PostgreSQL (SQLite for testing)
- **Cache:** Redis (in-memory dict for testing)
- **Message Bus:** Kafka (in-memory queue for testing)

## Design Decisions

- PostgreSQL is source of truth; Redis is cache only
- PMS is authoritative for BRANDED properties
- Synchronous critical path (<3s booking flow)
- No sagas, no distributed transactions
- Content-addressable event IDs (SHA256) for idempotency
- Bitmask availability: 32-bit int per unit-month, O(1) checks
- 4-tier property overrides: PMS(10) < PM(20) < Wander(30) < Admin(40)
- Hash-chained payout snapshots for financial audit

## Testing

All services include unit tests that run without external dependencies.
Integration tests use in-memory implementations of PostgreSQL and Redis.

```bash
python3 -m pytest tests/ -v --tb=short
```
