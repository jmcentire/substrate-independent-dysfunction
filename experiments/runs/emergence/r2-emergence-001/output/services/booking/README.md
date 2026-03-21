# Booking Service

Synchronous booking orchestrator for the Wander Backend Services platform. This is the **critical path service** — "User standing at checkout with credit card. 3 seconds."

## Architecture

The Booking Service orchestrates the complete checkout flow:

```
Client → Booking Service → Availability Service (check)
                         → Pricing Service (quote)
                         → Availability Service (hold dates)
                         → Payments Service (charge)
                         → PostgreSQL (persist booking)
                         → Event Service (emit booking.created)
```

### Key Design Decisions

- **<3 second SLA**: Total flow must complete within 3 seconds
- **Saga Pattern**: Compensating transactions on failure
  - Payment fails → release date hold
  - Booking record fails → refund payment + release hold
- **Idempotency**: Redis-backed idempotency keys prevent double-bookings
- **Fire-and-forget events**: Event emission doesn't block the checkout flow

### Booking States

```
pending → confirmed → checked_in → checked_out
    ↘         ↘
   cancelled  cancelled
```

## API Endpoints

### POST /bookings
Create a new booking (the <3s checkout flow).

```json
{
  "property_id": "uuid",
  "unit_id": "uuid",
  "guest_id": "uuid",
  "check_in": "2024-06-01",
  "check_out": "2024-06-05",
  "guests": 2,
  "payment_method_id": "uuid",
  "idempotency_key": "optional-unique-key"
}
```

### GET /bookings/:booking_id
Get booking details.

### GET /bookings?guest_id=&property_id=&status=
List/search bookings with optional filters.

### PUT /bookings/:booking_id/cancel
Cancel a booking.

```json
{
  "reason": "Changed plans"
}
```

### PUT /bookings/:booking_id/confirm
Confirm a pending booking.

### GET /health
Health check endpoint.

## Setup

### Prerequisites
- Deno 1.38+
- PostgreSQL 15+
- Redis 7+

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8007 | Server port |
| PG_HOST | localhost | PostgreSQL host |
| PG_PORT | 5432 | PostgreSQL port |
| PG_USER | booking | PostgreSQL user |
| PG_PASSWORD | booking | PostgreSQL password |
| PG_DATABASE | booking | PostgreSQL database |
| REDIS_HOST | localhost | Redis host |
| REDIS_PORT | 6379 | Redis port |
| AVAILABILITY_SERVICE_URL | http://localhost:8001 | Availability Service URL |
| PRICING_SERVICE_URL | http://localhost:8002 | Pricing Service URL |
| PROPERTY_SERVICE_URL | http://localhost:8003 | Property Service URL |
| PAYMENTS_SERVICE_URL | http://localhost:8004 | Payments Service URL |
| EVENT_SERVICE_URL | http://localhost:8005 | Event Service URL |
| SERVICE_CALL_TIMEOUT_MS | 800 | Per-service call timeout |
| TOTAL_FLOW_TIMEOUT_MS | 3000 | Total booking flow timeout |
| IDEMPOTENCY_TTL_SECONDS | 86400 | Idempotency key TTL (24h) |

### Local Development

```bash
# Start dependencies
docker-compose up -d postgres redis

# Initialize database
psql -h localhost -p 5437 -U booking -d booking -f schema.sql

# Run the service
deno task start

# Run in watch mode
deno task dev
```

### Docker

```bash
docker-compose up
```

## Testing

```bash
# Run all tests
deno task test

# Run unit tests only
deno task test:unit

# Run integration tests only
deno task test:integration
```

### Test Structure

```
tests/
├── unit/
│   ├── validator_test.ts       # Request validation
│   ├── booking_service_test.ts # Core service logic with mocks
│   ├── idempotency_store_test.ts # Idempotency key management
│   ├── types_test.ts           # Type definitions and errors
│   └── base_client_test.ts     # HTTP client utilities
└── integration/
    └── api_test.ts             # Full HTTP API integration tests
```

## Project Structure

```
services/booking/
├── src/
│   ├── main.ts                 # Entry point
│   ├── config.ts               # Configuration
│   ├── types.ts                # Core types
│   ├── deps.ts                 # Dependency re-exports
│   ├── router.ts               # Route definitions
│   ├── server.ts               # Server setup
│   ├── clients/                # Downstream service clients
│   │   ├── base_client.ts
│   │   ├── availability_client.ts
│   │   ├── pricing_client.ts
│   │   ├── payments_client.ts
│   │   └── event_client.ts
│   ├── db/                     # Database clients
│   │   ├── client.ts
│   │   └── redis_client.ts
│   ├── handlers/               # HTTP request handlers
│   │   └── booking_handler.ts
│   ├── middleware/              # Express-style middleware
│   │   ├── error_handler.ts
│   │   └── timing.ts
│   ├── repositories/           # Data access layer
│   │   ├── booking_repository.ts
│   │   └── idempotency_store.ts
│   └── services/               # Business logic
│       ├── booking_service.ts
│       └── validator.ts
├── tests/
├── schema.sql
├── deno.json
├── Dockerfile
├── docker-compose.yml
└── README.md
```
