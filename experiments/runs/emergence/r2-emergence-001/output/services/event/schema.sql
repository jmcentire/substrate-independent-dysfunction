-- Event Service Schema
-- PostgreSQL schema for event store with content-addressable IDs

CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Events table: stores all published events
CREATE TABLE IF NOT EXISTS events (
    event_id        VARCHAR(64) PRIMARY KEY,  -- SHA-256 hex hash
    event_type      VARCHAR(100) NOT NULL,
    source          VARCHAR(255) NOT NULL,
    payload         JSONB NOT NULL,
    metadata        JSONB DEFAULT '{}',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    version         INTEGER NOT NULL DEFAULT 1
);

-- Index for querying by event type and time
CREATE INDEX IF NOT EXISTS idx_events_type_created
    ON events (event_type, created_at DESC);

-- Index for time-range queries
CREATE INDEX IF NOT EXISTS idx_events_created_at
    ON events (created_at DESC);

-- Webhook subscriptions table
CREATE TABLE IF NOT EXISTS subscriptions (
    subscription_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type      VARCHAR(100) NOT NULL,
    webhook_url     VARCHAR(2048) NOT NULL,
    secret          VARCHAR(255),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_delivery   TIMESTAMPTZ,
    failure_count   INTEGER NOT NULL DEFAULT 0
);

-- Index for looking up subscriptions by event type
CREATE INDEX IF NOT EXISTS idx_subscriptions_event_type
    ON subscriptions (event_type) WHERE active = TRUE;

-- Unique constraint: one webhook URL per event type
CREATE UNIQUE INDEX IF NOT EXISTS idx_subscriptions_unique_url_type
    ON subscriptions (event_type, webhook_url) WHERE active = TRUE;

-- Dead letter queue for failed deliveries
CREATE TABLE IF NOT EXISTS dead_letter_queue (
    id              BIGSERIAL PRIMARY KEY,
    event_id        VARCHAR(64) NOT NULL REFERENCES events(event_id),
    subscription_id UUID NOT NULL REFERENCES subscriptions(subscription_id),
    error_message   TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_dlq_event_id
    ON dead_letter_queue (event_id);
