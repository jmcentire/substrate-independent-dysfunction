# Event Service

Kafka-based event bus for inter-service communication with content-addressable event IDs.

## Features

- **Content-addressable event IDs**: `event_id = SHA-256(canonical JSON of event payload)` for automatic deduplication
- **Idempotent publishing**: Same payload always produces the same event ID; duplicates are silently accepted
- **Event store**: PostgreSQL-backed persistence for event history
- **Event streaming**: Kafka-based pub/sub (in-memory mock for development)
- **Webhook subscriptions**: Register HTTP endpoints to receive events by type
- **Schema validation**: Validates events against defined event type schemas

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | /events | Publish a new event |
| GET | /events/:event_id | Get event by ID |
| GET | /events?type=&since=&limit= | Query events by type/time range |
| POST | /events/subscribe | Subscribe to event types (webhook) |
| GET | /health | Health check |

## Event Types

- `availability.updated`
- `pricing.updated`
- `booking.created`
- `booking.confirmed`
- `booking.cancelled`
- `property.updated`
- `sync.completed`
- `payment.processed`

## Content-Addressable IDs

Event IDs are computed as `SHA-256(canonical_json(payload))` where canonical JSON means:
- Keys sorted alphabetically (deep)
- No extra whitespace
- Deterministic serialization

This ensures:
- Same event content always gets the same ID
- No duplicate events in the store
- Natural deduplication across publishers

## Running

```bash
# Development
deno run --allow-net --allow-env --allow-read services/event/src/main.ts

# Tests
deno test --allow-net --allow-env --allow-read services/event/
```

## Configuration

Environment variables:

| Variable | Default | Description |
|----------|---------|-------------|
| PORT | 8004 | HTTP server port |
| DATABASE_URL | postgresql://localhost:5432/wander_events | PostgreSQL connection |
| KAFKA_BROKERS | localhost:9092 | Kafka broker addresses |
| KAFKA_TOPIC_PREFIX | wander. | Prefix for Kafka topics |
| USE_MOCK_KAFKA | true | Use in-memory event bus |
| USE_MOCK_DB | true | Use in-memory event store |

## Database Schema

See `schema.sql` for the full PostgreSQL schema.

## Docker

```bash
docker-compose up
```
