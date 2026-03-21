# Design: r2-cf-001

*Version 1 — Auto-maintained by contract-first*

## Decomposition

- [C] **Pricing Service** (`pricing_service`)
  Rate cache service providing fast price lookups for rental units. Stores nightly rates hash-sharded by unit_id in Redis for O(1) lookups. Supports dynamic pricing with date-specific rates, seasonal rates, length-of-stay discounts, and fees/taxes computation. PostgreSQL stores authoritative rate configurations; Redis is the hot cache sharded by unit_id hash for even distribution.
  - [ ] **Pricing Database Schema & Repository** (`pricing_schema`)
    PostgreSQL schema and data access layer for pricing. Tables: rate_plans (unit_id, name, base_rate, currency), rate_overrides (rate_plan_id, date_start, date_end, nightly_rate), fees (unit_id, fee_type, amount, is_percentage), taxes (unit_id, tax_type, rate), los_discounts (unit_id, min_nights, discount_pct). Repository functions for rate resolution given a unit and date range.
  - [ ] **Hash-Sharded Price Cache** (`pricing_cache_engine`)
    Redis caching layer for pricing data, hash-sharded by unit_id. Functions: shardKey(unitId) → Redis key using consistent hash, cacheRates(unitId, rates) → void, getCachedRates(unitId, dateRange) → rates|null, invalidateUnit(unitId) → void. Implements cache-aside pattern: check Redis first, fall back to DB, populate cache on miss. Shard count configurable via env var.
  - [ ] **Pricing API Endpoints** (`pricing_api`)
    Oak HTTP router implementing pricing endpoints: GET /pricing/:unit_id/quote?start=&end=&guests= (compute total price breakdown: nightly rates, fees, taxes, discounts, total), GET /pricing/:unit_id/rates?start=&end= (raw nightly rates), PUT /pricing/:unit_id/rates (update rate configuration), POST /pricing/:unit_id/fees (add/update fees). Returns structured price breakdowns with currency and all line items.
  - [C] **Pricing Service Tests** (`pricing_tests`)
    Test suite for pricing: unit tests for rate calculation logic (seasonal overlaps, LOS discounts, fee/tax stacking), cache shard distribution tests, API contract tests, tests for cache miss/hit paths, and edge cases (zero-night stays, currency rounding, overlapping rate overrides).
