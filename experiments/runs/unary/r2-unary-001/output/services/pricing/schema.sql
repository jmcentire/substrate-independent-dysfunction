-- Pricing Service Schema
-- Simple rate cache. One table. That's it.

CREATE TABLE IF NOT EXISTS pricing (
    unit_id     UUID NOT NULL,
    date        DATE NOT NULL,
    rate_cents  INTEGER NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_id, date)
);

-- Index for date range and calendar queries
CREATE INDEX IF NOT EXISTS idx_pricing_unit_date_range
    ON pricing (unit_id, date);
