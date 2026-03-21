-- Availability Service Schema
-- Each row represents availability for a unit in a given month
-- The bitmask field uses one bit per day (bit 0 = day 1, bit 1 = day 2, etc.)
-- 1 = available, 0 = blocked

CREATE TABLE IF NOT EXISTS availability (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id         UUID NOT NULL,
    year            INTEGER NOT NULL,
    month           INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
    bitmask         BIGINT NOT NULL DEFAULT 2147483647, -- all days available (31 bits set)
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(unit_id, year, month)
);

CREATE INDEX IF NOT EXISTS idx_availability_unit_id ON availability(unit_id);
CREATE INDEX IF NOT EXISTS idx_availability_unit_date ON availability(unit_id, year, month);

-- Availability change log for audit trail
CREATE TABLE IF NOT EXISTS availability_log (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    unit_id         UUID NOT NULL,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    change_type     VARCHAR(20) NOT NULL, -- 'block' or 'unblock'
    start_date      DATE NOT NULL,
    end_date        DATE NOT NULL,
    changed_by      VARCHAR(255)
);

CREATE INDEX IF NOT EXISTS idx_availability_log_unit ON availability_log(unit_id, changed_at DESC);
