# Pricing Service

Service 2 of the Wander Backend Services platform. Provides rate calculation, pricing lookups, and quote generation with a hash-sharded Redis cache.

## Architecture

- **Runtime**: Deno (TypeScript)
- **Database**: PostgreSQL (persistent rate storage)
- **Cache**: Redis (hash-sharded by unit_id)
- **Framework**: Oak (HTTP)
- **Validation**: Zod

## API Endpoints

### GET /pricing/:unit_id?start=YYYY-MM-DD&end=YYYY-MM-DD

Get daily pricing for a unit within a date range.

**Response:**
```json
{
  "unitId": "uuid",
  "startDate": "2024-01-01",
  "endDate": "2024-01-05",
  "prices": [
    {
      "date": "2024-01-01",
      "price": 20000,
      "isWeekend": false,
      "seasonalMultiplier": 1.0,
      "minimumStay": 1,
      "currency": "USD"
    }
  ]
}
```

### POST /pricing/quote

Generate a price quote for a potential booking.

**Request:**
```json
{
  "unitId": "uuid",
  "checkIn": "2024-01-08",
  "checkOut": "2024-01-12",
  "guestCount": 2
}
```

**Response:**
```json
{
  "unitId": "uuid",
  "checkIn": "2024-01-08",
  "checkOut": "2024-01-12",
  "nights": 4,
  "dailyPrices": [...],
  "subtotal": 80000,
  "taxes": 9600,
  "fees": 9900,
  "total": 99500,
  "currency": "USD",
  "minimumStayMet": true,
  "availabilityConfirmed": true,
  "quoteExpiresAt": "2024-01-08T01:00:00.000Z"
}
```

### PUT /pricing/:unit_id/rates

Update rates for a unit.

**Request:**
```json
{
  "rates": [
    {
      "date": "2024-07-01",
      "baseRate": 25000,
      "weekendRate": 30000,
      "seasonalMultiplier": 1.5,
      "minimumStay": 3,
      "currency": "USD"
    }
  ]
}
```

### GET /health

Health check endpoint.

### GET /health/ready

Readiness probe.

## Hash-Sharded Cache

The cache uses FNV-1a hashing on `unit_id` to distribute rate data across multiple Redis shards. This provides:

- **Consistent mapping**: Same unit always goes to the same shard
- **Even distribution**: FNV-1a produces well-distributed hashes
- **Independent scaling**: Shards can be added/removed
- **Isolation**: Hot units don't affect other shards

## Pricing Logic

- **Base rate**: Default nightly rate in cents
- **Weekend rate**: Applied for Friday and Saturday nights (if set)
- **Seasonal multiplier**: Multiplied against the applicable rate (e.g., 1.5 for peak)
- **Minimum stay**: Maximum across all nights in the range must be met
- **Taxes**: 12% lodging tax (configurable)
- **Fees**: $75 cleaning fee + 3% service fee (configurable)
- **Default rate**: $150/night when no rate is configured

## Running

```bash
# Development
deno task dev

# Production
deno task start

# Tests
deno task test
deno task test:unit
deno task test:integration
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8020 | HTTP port |
| PG_HOST | localhost | PostgreSQL host |
| PG_PORT | 5432 | PostgreSQL port |
| PG_USER | pricing | PostgreSQL user |
| PG_PASSWORD | pricing | PostgreSQL password |
| PG_DATABASE | pricing | PostgreSQL database |
| PG_POOL_SIZE | 10 | Connection pool size |
| REDIS_NODES | localhost:6379 | Comma-separated Redis nodes (host:port) |
| REDIS_TTL_SECONDS | 3600 | Cache TTL in seconds |
| AVAILABILITY_SERVICE_URL | http://localhost:8010 | Availability service URL |
| LOG_LEVEL | INFO | Log level |

## Integration with Other Services

- **Availability Service (Service 1)**: Checked during quote generation to confirm dates are available
- **Booking Service (Service 3)**: Calls this service to get quotes during booking creation
