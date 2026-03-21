# Availability Service

Bitmask-based availability management with O(1) date-range checks.

## Overview

This service manages property/unit availability using bitmask encoding:
- Each bit represents one day of a month (bit 0 = day 1, bit 30 = day 31)
- 1 = available, 0 = blocked
- Range checks are O(1) via bitwise AND operations
- Redis caching provides fast lookups with write-through invalidation

## Architecture

```
src/
├── main.ts          # Entry point
├── config.ts        # Configuration loader
├── types.ts         # Type definitions
├── bitmask.ts       # Bitmask encoding/decoding utilities
├── cache.ts         # Redis cache layer
├── db.ts            # Database connection management
├── repository.ts    # PostgreSQL data access
├── service.ts       # Business logic
├── routes.ts        # HTTP route handlers
├── router.ts        # Oak router setup
└── server.ts        # Server initialization

tests/
├── unit/
│   ├── bitmask_test.ts     # Bitmask operation tests
│   ├── cache_test.ts       # Cache hit/miss/invalidation tests
│   ├── repository_test.ts  # Repository tests
│   └── service_test.ts     # Service logic tests
├── router_test.ts          # Route handler tests
└── integration/
    └── api_test.ts         # Full API integration tests
```

## API Endpoints

### GET /availability/:unit_id?start=YYYY-MM-DD&end=YYYY-MM-DD

Check availability for a date range.

**Response:**
```json
{
  "unitId": "uuid",
  "available": true,
  "dates": [
    { "date": "2024-03-10", "available": true },
    { "date": "2024-03-11", "available": true }
  ]
}
```

### PUT /availability/:unit_id

Update availability (block or unblock dates).

**Request:**
```json
{
  "dates": {
    "start": "2024-03-10",
    "end": "2024-03-15"
  },
  "available": false,
  "changedBy": "admin"
}
```

### POST /availability/bulk

Bulk availability check for multiple units.

**Request:**
```json
{
  "unitIds": ["uuid1", "uuid2"],
  "start": "2024-03-10",
  "end": "2024-03-15"
}
```

### GET /health

Health check endpoint.

## Running

### With Docker Compose

```bash
docker-compose up
```

### Locally (requires PostgreSQL and Redis)

```bash
export DB_HOST=localhost DB_PORT=5432 DB_NAME=availability
export DB_USER=postgres DB_PASSWORD=postgres
export REDIS_HOST=localhost REDIS_PORT=6379
deno task start
```

### Development

```bash
deno task dev   # with watch mode
```

## Testing

```bash
# All tests
deno task test

# Unit tests only
deno task test:unit

# Integration tests (requires infrastructure)
deno task test:integration
```

## Bitmask Encoding

Each month's availability is stored as a 32-bit integer:

```
Day:     1  2  3  4  5  6  7  8  ...
Bit:     0  1  2  3  4  5  6  7  ...
Value:   1  1  1  1  0  0  1  1  ... = available/blocked
```

**O(1) Range Check:**
```
mask = ((1 << width) - 1) << (startDay - 1)
available = (bitmask & mask) === mask
```

## Cache Strategy

- **Key format:** `avail:{unit_id}:{year}:{month}`
- **Value:** hex-encoded bitmask
- **Read-through:** On cache miss, load from DB and populate cache
- **Write-through:** On update, write DB first, then update cache
- **TTL:** Configurable (default 1 hour)
- **Warming:** Bulk load from DB on startup or on-demand
