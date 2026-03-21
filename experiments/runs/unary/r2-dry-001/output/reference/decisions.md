# Architectural Decisions

These 16 decisions were made during a prior decomposition phase to resolve
ambiguities in the architecture specification. They represent binding
engineering decisions that implementations should respect.

## 1. Redis Sharding Strategy

**Decision:** Use Redis Cluster with 3 master nodes (9 total with replicas).
Partition using CRC16 of unit_id modulo 16384 (Redis Cluster default).
Region-sharding for Availability means logical grouping via key prefix
(`avail:{region}:{unit_id}:*`) but still distributed across cluster.

## 2. Kafka Partition Key Strategy

**Decision:** Use Java's `String.hashCode()` of entity_id modulo partition
count for deterministic routing. Start with 32 partitions for events.raw, 16
for audit/log/analytics, 8 for alerts/metrics. Hot partitions accepted —
ordering per entity is the goal.

## 3. PMS Adapter Interface Enforcement

**Decision:** Use dataclasses with runtime type checking for adapter outputs.
Define canonical structs (PropertyCore, AvailabilityBlock, etc.) as frozen
dataclasses. Adapters must return these types; decorator validates at call time.

## 4. Booking Service PMS Verification Protocol

**Decision:** PMS verification = 2-step: (1) GET availability for dates (timeout:
5s), (2) POST quote (timeout: 5s). 'Verified' means: availability returns
`available=true` AND quote matches cached price within 5% tolerance. Total
budget: 10s, parallelized with payment auth. On timeout: treat as unavailable,
cancel payment auth, return 409. On price mismatch >5%: return 422 with new
price.

## 5. Date/Timezone Handling

**Decision:** Canonical internal format: YYYY-MM-DD (date-only, no time) for
availability/pricing, stored as PostgreSQL DATE. Time-aware events use ISO8601
with UTC. Adapter layer converts PMS local time to UTC using property.timezone.
If PMS doesn't provide timezone, assume UTC and log warning.

## 6. Money Representation

**Decision:** Always store in cents (integers). Banker's rounding (round half to
even) for all calculations. Proration distributes remainder cents to first N
items. Currency conversion: never in backend — always bill in property's native
currency. UI handles display conversion.

## 7. External Reference Validation

**Decision:** Define regex patterns per ref kind:
- `stripe_charge: ^ch_[a-zA-Z0-9]{24}$`
- `stripe_payout: ^po_[a-zA-Z0-9]{24}$`
- `ubr: ^UBR-[A-Za-z0-9]{43,}$`
- `pms_confirmation: ^[A-Z0-9-]{5,20}$` (lenient)

Validate on creation, reject invalid refs. GIN index on external_refs JSONB.

## 8. Sync-to-Availability Boundary

**Decision:** Sync directly calls Availability Service APIs (/acquire, /release,
/apply_external_blocks). After successful update, Sync emits
`sync.availability_pulled` event for observability. Availability emits
`availability.changed` event when data changes. Two separate event streams.

## 9. PMS Down During Booking

**Decision:** Booking flow timeouts: (1) Auth payment 5s, (2) Verify PMS
availability 5s, (3) Create PMS reservation 10s, (4) Capture payment 5s.
If step 3 fails: cancel payment auth, return 503. No retries in booking flow.
Background reconciliation job every 5 minutes detects orphaned auths, auto-
cancels after 30 minutes.

## 10. Dual-Write Consistency

**Decision:** Write to new tables first, then old. On conflict: log error, emit
alert, continue. Comparison job every 15 minutes. After 30 days of zero
discrepancies, flip feature flag to stop dual-write.

## 11. Rate Limit Scope

**Decision:** Rate limits are per `(pms_type, credential_id)` pair. Token bucket
per credential. Config stored in pms_connection table. Aggregator view shows
per-partner totals for monitoring, but enforcement is per-credential.

## 12. Database Connection Pooling

**Decision:** pgBouncer in transaction pooling mode. Pool size per service:
min=5, max=20, timeout=30s. PostgreSQL max_connections=200. Read replicas for
heavy read services (Property, Availability search).

## 13. Kafka Consumer Scaling

**Decision:** Auto-scale consumers when lag > 10,000 messages for 5 consecutive
minutes. Max consumers per group = partition count. Start with 1 consumer per
subsystem per partition. Kubernetes HPA with custom metric.

## 14. Redis Failover Strategy

**Decision:** Redis Cluster with Sentinel (30s detection, 60s failover). On
cache miss or Redis error: read from PostgreSQL, return result, skip cache write.
No pre-warming on startup; lazy load via read traffic.

## 15. Bookability Logic

**Decision:** Two-phase check: (1) Property Service owns structural bookability
(`is_bookable` flag: live, not archived, has complete data). (2) Booking Service
owns transactional bookability (dates available, min stay met, max occupancy OK).

## 16. Financial Audit Hash Chain

**Decision:** SHA256 hash algorithm. Hash input: `JSON.stringify(sorted keys)`
of `{owner_id, period_start, period_end, booking_ids (sorted), totals (sorted
keys), bookings (sorted by booking_id)}`. Exclude mutable fields (id,
created_at, stripe_transfer_id, paid_at, status). Include prev_hash in current
hash. Expose `GET /payouts/verify-chain` endpoint.

---

## Human-Answered Questions

### Tax Calculation Strategy

Booking service pulls tax data from Property service. Booking passes that to
Payment service which handles all calculations and Stripe interactions. Booking
doesn't calculate — Payment calculates the invoice using prices from Pricing,
dates/config from Booking, and data from Property. Once Booking has the invoice
back, it checks with PMSes via Sync. If prices are close, honor what we told the
guest. If different by too much, alert the user that the price changed and get
approval.

### Property Override Conflict Resolution (ASK Policy)

Message goes to Event service which handles routing. For now, Slack notification
is sufficient.

### Kafka/Redis Operational Maturity

Production experience with both. Proceed with full design as specified.
