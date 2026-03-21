-- Sync Service Schema
-- Bidirectional PMS sync with rate limiting and webhook processing.

-- PMS connection credentials
CREATE TABLE IF NOT EXISTS pms_connection (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pms_type        VARCHAR(30) NOT NULL,
    credential_id   VARCHAR(100) NOT NULL,
    api_key         VARCHAR(500),
    api_secret      VARCHAR(500),
    base_url        VARCHAR(500),
    webhook_secret  VARCHAR(200),
    rate_limit_rpm  INTEGER DEFAULT 60,   -- Requests per minute
    status          VARCHAR(20) DEFAULT 'ACTIVE',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (pms_type, credential_id)
);

-- Sync jobs
CREATE TABLE IF NOT EXISTS sync_job (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_type        VARCHAR(20) NOT NULL,   -- PUSH, PULL, RECONCILE
    property_id     UUID,
    pms_type        VARCHAR(30),
    status          VARCHAR(20) DEFAULT 'QUEUED',  -- QUEUED, RUNNING, COMPLETED, FAILED
    error           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_sync_job_status ON sync_job(status);
CREATE INDEX IF NOT EXISTS idx_sync_job_property ON sync_job(property_id);

-- Sync job subsystems
CREATE TABLE IF NOT EXISTS sync_job_subsystem (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id      UUID NOT NULL REFERENCES sync_job(id),
    subsystem   VARCHAR(20) NOT NULL,   -- property, availability, pricing
    status      VARCHAR(20) DEFAULT 'QUEUED',
    records_synced INTEGER DEFAULT 0,
    error       TEXT,
    completed_at TIMESTAMPTZ
);

-- Webhook events (for dedup)
CREATE TABLE IF NOT EXISTS webhook_event (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pms_type        VARCHAR(30) NOT NULL,
    payload_hash    VARCHAR(64) NOT NULL,   -- SHA256 for dedup
    payload         JSONB,
    status          VARCHAR(20) DEFAULT 'RECEIVED',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (pms_type, payload_hash)
);

CREATE INDEX IF NOT EXISTS idx_webhook_hash ON webhook_event(pms_type, payload_hash);

-- Rate limit state (token bucket)
CREATE TABLE IF NOT EXISTS rate_limit_state (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pms_type        VARCHAR(30) NOT NULL,
    credential_id   VARCHAR(100) NOT NULL,
    tokens          DECIMAL(10,2) NOT NULL DEFAULT 60,
    max_tokens      INTEGER NOT NULL DEFAULT 60,
    refill_rate     DECIMAL(10,2) NOT NULL DEFAULT 1.0,  -- tokens per second
    last_refill     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    in_backoff      BOOLEAN DEFAULT FALSE,
    backoff_until   TIMESTAMPTZ,
    
    UNIQUE (pms_type, credential_id)
);

-- Fee dictionary (external fee code → standard type)
CREATE TABLE IF NOT EXISTS fee_dictionary (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pms_type        VARCHAR(30) NOT NULL,
    external_code   VARCHAR(100) NOT NULL,
    standard_type   VARCHAR(50) NOT NULL,   -- CLEANING, PET, EXTRA_GUEST, FEE_MISC
    label           VARCHAR(100),
    
    UNIQUE (pms_type, external_code)
);

-- PMS reservation mapping (Wander booking ↔ PMS reservation)
CREATE TABLE IF NOT EXISTS pms_reservation_mapping (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id          UUID,
    property_id         UUID NOT NULL,
    pms_type            VARCHAR(30) NOT NULL,
    external_id         VARCHAR(200) NOT NULL,
    confirmation_code   VARCHAR(100),
    status              VARCHAR(20) DEFAULT 'CONFIRMED',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (pms_type, external_id)
);

CREATE INDEX IF NOT EXISTS idx_pms_res_booking ON pms_reservation_mapping(booking_id);
