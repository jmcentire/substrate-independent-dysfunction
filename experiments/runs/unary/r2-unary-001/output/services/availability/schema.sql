-- Availability Service Schema
-- Bitmask per unit-month (32-bit integer, O(1) operations)
-- 97% reduction from row-per-day approach

CREATE TABLE IF NOT EXISTS availability (
    unit_id     UUID NOT NULL,
    year_month  CHAR(7) NOT NULL,           -- 'YYYY-MM'
    days        INTEGER NOT NULL DEFAULT 0,  -- bitmask: bit N (1-31) = day N, 1=blocked
    start_free  SMALLINT NOT NULL DEFAULT 31,
    end_free    SMALLINT NOT NULL DEFAULT 31,
    max_free    SMALLINT NOT NULL DEFAULT 31,
    PRIMARY KEY (unit_id, year_month)
);

CREATE TABLE IF NOT EXISTS unit_region (
    unit_id  UUID PRIMARY KEY,
    region   VARCHAR(50) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unit_region ON unit_region(region);
