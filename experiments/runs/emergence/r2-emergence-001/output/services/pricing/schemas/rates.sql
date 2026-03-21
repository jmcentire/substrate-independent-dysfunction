-- Pricing Service: Rate storage schema
-- This matches the migration in src/db/postgres.ts

CREATE TABLE IF NOT EXISTS rates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id UUID NOT NULL,
    date DATE NOT NULL,
    base_rate INTEGER NOT NULL,           -- Price in cents
    weekend_rate INTEGER,                  -- Weekend price in cents (nullable)
    seasonal_multiplier NUMERIC(5,3) NOT NULL DEFAULT 1.000,
    minimum_stay INTEGER NOT NULL DEFAULT 1,
    currency VARCHAR(3) NOT NULL DEFAULT 'USD',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(unit_id, date)
);

-- Index for fast lookups by unit + date range
CREATE INDEX IF NOT EXISTS idx_rates_unit_date
ON rates (unit_id, date);

CREATE INDEX IF NOT EXISTS idx_rates_unit_date_range
ON rates (unit_id, date ASC);

-- Trigger to auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_rates_updated_at
    BEFORE UPDATE ON rates
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();
