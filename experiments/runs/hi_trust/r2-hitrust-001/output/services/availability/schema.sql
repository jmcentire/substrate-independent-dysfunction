-- Availability Service Schema
-- Bitmask-per-month storage for O(1) availability checks

CREATE TABLE IF NOT EXISTS availability (
    unit_id     TEXT NOT NULL,
    year_month  TEXT NOT NULL,           -- 'YYYY-MM'
    days        INTEGER NOT NULL DEFAULT 0,  -- 32-bit bitmask, bit N = day N
    start_free  INTEGER NOT NULL DEFAULT 31,
    end_free    INTEGER NOT NULL DEFAULT 31,
    max_free    INTEGER NOT NULL DEFAULT 31,
    PRIMARY KEY (unit_id, year_month)
);

CREATE TABLE IF NOT EXISTS unit_region (
    unit_id  TEXT PRIMARY KEY,
    region   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_unit_region ON unit_region(region);
