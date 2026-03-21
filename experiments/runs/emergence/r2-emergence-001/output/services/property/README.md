# Property Service

Service 3 of 7 — Property and unit management with 4-tier override hierarchy.

## Overview

The Property Service manages properties and units within the Wander platform. It implements a 4-tier settings override hierarchy:

```
platform defaults → brand overrides → property overrides → unit overrides
```

When resolving settings for a unit, each tier's settings are merged on top of the previous, with more specific tiers taking precedence.

## Technology Stack

- **Runtime**: Deno (TypeScript)
- **Database**: PostgreSQL
- **Cache**: Redis
- **Framework**: Oak (HTTP)

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /properties/:property_id | Get property details |
| GET | /properties/:property_id/units | List units for a property |
| GET | /units/:unit_id | Get unit details with resolved settings |
| POST | /properties | Create a new property |
| PUT | /properties/:property_id | Update property |
| POST | /properties/:property_id/units | Create a unit |
| PUT | /units/:unit_id | Update unit |
| GET | /health | Health check |

## 4-Tier Override Hierarchy

1. **Platform defaults**: Base settings that apply to all properties/units
2. **Brand overrides**: Settings specific to a brand (e.g., Wander, partner brands)
3. **Property overrides**: Settings specific to a property
4. **Unit overrides**: Settings specific to a unit

Resolution merges settings from each tier, with more specific tiers winning.

## Setup

### Prerequisites

- Deno 1.38+
- PostgreSQL 15+
- Redis 7+

### Environment Variables

```env
DATABASE_URL=postgresql://localhost:5432/wander_property
REDIS_URL=redis://localhost:6379
PORT=8003
```

### Database Setup

```bash
psql -f db/migrations/001_initial.sql
psql -f db/seeds/platform_defaults.sql
```

### Running

```bash
deno run --allow-net --allow-env --allow-read src/main.ts
```

### Testing

```bash
deno test --allow-net --allow-env --allow-read
```

## Project Structure

```
services/property/
├── src/
│   ├── main.ts              # Entry point
│   ├── config.ts            # Configuration
│   ├── router.ts            # Route definitions
│   ├── handlers/
│   │   ├── property_handler.ts
│   │   └── unit_handler.ts
│   ├── services/
│   │   ├── property_service.ts
│   │   ├── unit_service.ts
│   │   └── settings_resolver.ts
│   ├── repositories/
│   │   ├── property_repository.ts
│   │   ├── unit_repository.ts
│   │   ├── brand_repository.ts
│   │   └── platform_repository.ts
│   ├── cache/
│   │   └── redis_cache.ts
│   ├── models/
│   │   └── types.ts
│   └── middleware/
│       └── error_handler.ts
├── db/
│   ├── migrations/
│   │   └── 001_initial.sql
│   └── seeds/
│       └── platform_defaults.sql
├── tests/
│   ├── unit/
│   │   ├── settings_resolver_test.ts
│   │   ├── property_service_test.ts
│   │   └── unit_service_test.ts
│   └── integration/
│       └── api_test.ts
├── deno.json
└── README.md
```
