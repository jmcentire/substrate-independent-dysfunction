-- Pricing Service Database Schema

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Daily rates table: one row per unit per date
CREATE TABLE IF NOT EXISTS daily_rates (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  unit_id VARCHAR(64) NOT NULL,
  date DATE NOT NULL,
  base_rate INTEGER NOT NULL,           -- in cents
  currency VARCHAR(3) NOT NULL DEFAULT 'USD',
  rate_type VARCHAR(20) NOT NULL DEFAULT 'standard',
  seasonal_multiplier NUMERIC(4,2) NOT NULL DEFAULT 1.00,
  weekend_multiplier NUMERIC(4,2) NOT NULL DEFAULT 1.00,
  min_stay INTEGER NOT NULL DEFAULT 1,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(unit_id, date)
);

CREATE INDEX idx_daily_rates_unit_date ON daily_rates(unit_id, date);
CREATE INDEX idx_daily_rates_unit_id ON daily_rates(unit_id);

-- Length-of-stay discounts
CREATE TABLE IF NOT EXISTS los_discounts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  unit_id VARCHAR(64) NOT NULL,
  min_nights INTEGER NOT NULL,
  discount_percent NUMERIC(5,2) NOT NULL,  -- 0.00 - 100.00
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE(unit_id, min_nights)
);

CREATE INDEX idx_los_discounts_unit ON los_discounts(unit_id);

-- Quote history for audit trail
CREATE TABLE IF NOT EXISTS quotes (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  unit_id VARCHAR(64) NOT NULL,
  start_date DATE NOT NULL,
  end_date DATE NOT NULL,
  guests INTEGER NOT NULL DEFAULT 1,
  subtotal INTEGER NOT NULL,
  los_discount INTEGER NOT NULL DEFAULT 0,
  cleaning_fee INTEGER NOT NULL DEFAULT 0,
  service_fee INTEGER NOT NULL DEFAULT 0,
  taxes INTEGER NOT NULL DEFAULT 0,
  grand_total INTEGER NOT NULL,
  currency VARCHAR(3) NOT NULL DEFAULT 'USD',
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_quotes_unit ON quotes(unit_id);
CREATE INDEX idx_quotes_expires ON quotes(expires_at);
