Overview
Overview
Wander Backend Services Architecture
Overview
This document defines the backend services architecture for Wander's booking and property management platform. The architecture prioritizes simplicity over complexity, achieving industry-leading performance through straightforward patterns rather than distributed system gymnastics.


Core Principle: The user is standing at checkout with their credit card out. We have 3 seconds. Every architectural decision flows from this constraint.


________________


Services
Service
	Purpose
	Authoritative?
	Availability Service
	Fast availability lookups
	No — cache only
	Pricing Service[a][b]
	Nightly rate lookups
	No — cache only
	Booking Service
	Booking lifecycle orchestration
	Yes — owns booking state
	Property Service[c][d]
	Property data with override hierarchy
	Yes — owns Wander view of properties
	Payments Service[e][f]
	Payment processing wrapper
	No — Stripe is ledger
	Sync Service[g][h][i]
	PMS ↔ Wander synchronization
	Yes — owns sync state
	Event Service[j][k]
	Event routing and observability
	Yes — owns event log
	

________________


Architecture Diagram
┌─────────────────────────────────────────────────────────────────────────────┐
│                              EXTERNAL SYSTEMS                                │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────┐           │
│  │ Guesty  │  │Streamline│ │OwnerRez │  │ Hostaway│  │  Other  │           │
│  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘  └────┬────┘           │
│       │            │            │            │            │                 │
│       └────────────┴────────────┴────────────┴────────────┘                 │
│                                    │                                         │
│                                    ▼                                         │
│  ┌──────────────────────────────────────────────────────────────────────┐   │
│  │                         SYNC SERVICE                                  │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                │   │
│  │  │  Connectors  │  │   Adapters   │  │  Subsystems  │                │   │
│  │  │  (Transport) │→ │ (Translation)│→ │ Prop/Avail/  │                │   │
│  │  │              │  │              │  │   Pricing    │                │   │
│  │  └──────────────┘  └──────────────┘  └──────────────┘                │   │
│  └──────────────────────────────────────────────────────────────────────┘   │
│                                    │                                         │
│                                    ▼                                         │
├─────────────────────────────────────────────────────────────────────────────┤
│                              WANDER SERVICES                                 │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐                 │
│  │  PROPERTY    │     │ AVAILABILITY │     │   PRICING    │                 │
│  │  SERVICE     │     │   SERVICE    │     │   SERVICE    │                 │
│  │              │     │              │     │              │                 │
│  │ • 4-tier     │     │ • Bitmask    │     │ • Rate cache │                 │
│  │   overrides  │     │   cache      │     │ • Bulk agg   │                 │
│  │ • Conflict   │     │ • Region     │     │ • Hash shard │                 │
│  │   policies   │     │   sharded    │     │              │                 │
│  └──────┬───────┘     └──────┬───────┘     └──────┬───────┘                 │
│         │                    │                    │                          │
│         └────────────────────┼────────────────────┘                          │
│                              │                                               │
│                              ▼                                               │
│                    ┌──────────────────┐                                      │
│                    │  BOOKING SERVICE │                                      │
│                    │                  │                                      │
│                    │ • Synchronous    │                                      │
│                    │   orchestration  │                                      │
│                    │ • State machine  │                                      │
│                    │ • Policy enforce │                                      │
│                    └────────┬─────────┘                                      │
│                             │                                                │
│              ┌──────────────┼──────────────┐                                 │
│              │              │              │                                 │
│              ▼              ▼              ▼                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │   PAYMENTS   │  │    EVENT     │  │     PMS      │                       │
│  │   SERVICE    │  │   SERVICE    │  │  (via Sync)  │                       │
│  │              │  │              │  │              │                       │
│  │ • Stripe     │  │ • Kafka      │  │ • Final      │                       │
│  │   wrapper    │  │ • 5 subs     │  │   verify     │                       │
│  │ • Reconcile  │  │ • Audit log  │  │              │                       │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘

________________


Service Summaries
Availability Service
Purpose: Fast availability lookups for search and booking flows.


Key Design:


* Bitmask per unit-month (32-bit integer, O(1) operations)
* Non-authoritative — PMS is source of truth
* Region-sharded Redis for locality


What it doesn't do: Enforce policy (min stay, restrictions). That's Booking Service.


📄 Full spec: Availability Service


________________


Pricing Service
Purpose: Fast nightly rate lookups for display and booking.


Key Design:


* Simple rate cache, returns missing_dates for gaps
* Hash-sharded Redis by unit_id
* Bulk endpoint with sum/avg/min/max aggregates
* Caller decides how to handle missing data


What it doesn't do: Compute discounts, fees, taxes. That's Booking Service.


📄 Full spec: Pricing Service


________________


Booking Service
Purpose: Orchestrate the booking lifecycle from quote to completion.


Key Design:


* Synchronous flow (user at checkout = sub-3s requirement)
* Single PostgreSQL transaction + PMS verification
* State machine: CONFIRMED → COMPLETED (with CANCELLED branch)
* Owns policy enforcement (min stay, restrictions, padding)


What it doesn't do: Long-running sagas. If PMS fails after charge, we refund automatically.


📄 Full spec: Booking Service


________________


Property Service
Purpose: Manage property data with multi-source override hierarchy.


Key Design:


* 4-tier override: PMS (10) < PM (20) < Wander (30) < Admin (40)
* Conflict policies: KEEP_OVERRIDE, ACCEPT_NEW, ASK
* Materialized "effective" view for fast reads


What it doesn't do: Own PMS data. Sync Service writes the base layer.


📄 Full spec: Property Service


________________


Payments Service
Purpose: Stripe orchestration and financial ledger mirror.


Key Design:


* Stripe is the ledger — we maintain read-optimized mirror
* Invoice-centric: invoices → payments → refunds → payouts
* Immutable payout snapshots with hash chain
* Nightly reconciliation detects drift


What it doesn't do: Dispute handling (use Stripe Dashboard), complex splits.


📄 Full spec: Payments Service


________________


Sync Service
Purpose: Bidirectional synchronization between Wander and PMSs.


Key Design:


* Two-layer architecture: Connectors (transport) + Adapters (translation)
* Three subsystems: Property, Availability, Pricing (run independently)
* Delta sync: event-driven, debounced (5-sec coalesce)
* Token bucket rate limiting per partner


What it doesn't do: Business logic. It translates and routes.


📄 Full spec: Sync Service


________________


Event Service
Purpose: Event routing, audit logging, and observability infrastructure.


Key Design:


* Kafka backbone for reliable delivery
* 5 subsystems: Audit, Alerts, Logging, Monitoring, Analytics
* Content-addressable event IDs (SHA256-based)
* Append-only audit log (never delete, never update)


What it doesn't do: Business logic. It routes and records.


📄 Full spec: Event Service


________________


Key Architectural Decisions
1. Non-Authoritative Caches
Availability and Pricing are caches, not sources of truth. The PMS is authoritative.


Why: PMSs can change data outside Wander. If we treat our cache as truth, we get drift. Instead, Booking Service always verifies with PMS before confirming.
2. Synchronous Booking Flow
No sagas. No distributed transactions. Single DB transaction + PMS call.


Why: User at checkout with card out = 3 seconds max. Saga orchestration adds latency and failure modes for no benefit at our scale.


Quote → Charge Card → Book in PMS → Confirm
         │                │
         │                └── If fails: auto-refund
         └── If fails: stop, return error
3. PostgreSQL as Source of Truth[l][m][n][o]
Every service uses PostgreSQL for its source of truth. Redis is cache only.


Why:


* Transactional guarantees
* Simpler debugging
* Fewer failure modes
* If Redis dies, we're slow but correct
4. Booking Owns Policy
Availability doesn't know about min stay. Pricing doesn't know about discounts. Booking Service enforces all policy.


Why: Single place to understand and debug business rules. Caches stay simple.
5. 4-Tier Override Hierarchy
Property data has clear precedence: PMS < PM < Wander < Admin.


Why: Different stakeholders need to override different things. Clear hierarchy prevents conflicts.


________________


Data Flow Examples
Search Flow
1. User searches: "Aspen, March 15-20, 2 guests"


2. Availability.search(region="US-CO-ASPEN", dates) 
   → [unit_1, unit_2, unit_3, ...]


3. Property.filter(unit_ids, {guests >= 2})
   → [unit_1, unit_3, ...]


4. Pricing.bulk(unit_ids, dates)
   → [{unit_1: $2,250 total}, {unit_3: $1,800 total}]


5. Return sorted results with property details
Booking Flow
1. User clicks "Book Now"


2. Booking.quote(unit_id, dates, guests)
   → {nightly: $450, cleaning: $150, tax: $72, total: $2,472}


3. User enters payment, clicks "Confirm"


4. Booking.create(unit_id, dates, guests, payment_method)
   a. Availability.check(unit_id, dates) — fast cache check
   b. Availability.acquire(unit_id, dates) — block dates
   c. Payments.charge(amount, payment_method) — charge card
   d. Sync.push_booking(pms, booking) — create in PMS
   e. If PMS fails: Payments.refund(), Availability.release()
   f. Event.publish(booking.created)
   → {booking_id, status: confirmed}


Total time: < 3 seconds
Sync Flow (Delta)
1. PMS webhook: "availability changed for unit_123"


2. Sync.Connector receives, debounces (5 sec)


3. Sync.Adapter.fetch_availability(unit_123)
   → {dates: [...blocked dates...]}


4. Sync.Adapter.translate() → Wander format


5. Availability.acquire/release() — update cache


6. Event.publish(availability.synced)


Total time: < 60 seconds from PMS change

________________


Performance Targets
Metric
	Target
	Notes
	Availability check
	< 20ms p95
	Redis hit
	Pricing lookup
	< 5ms p95
	Redis hit
	Booking creation
	< 3 sec
	Full flow including PMS
	Delta sync latency
	< 60 sec
	From PMS change to Wander
	Full reconciliation
	< 2 hours
	2,000 properties
	Bulk ingest
	< 1 hour
	10,000 properties
	

________________


What This Architecture Is NOT
Not a Complete PMS
Missing (by design):


* Guest communication / messaging
* Revenue management / dynamic pricing
* Housekeeping / operations
* Analytics / BI dashboards


These are separate initiatives.
Not Event-Sourced
We use events for:


* Audit trail (append-only log)
* Cross-service communication
* Observability


We do NOT use events for:


* Rebuilding service state
* CQRS read models


Services own their current-state tables.
Not a Saga Orchestrator
We don't have distributed transactions because we don't need them. The booking flow is:


1. Fast enough to be synchronous
2. Simple enough to handle failures inline


________________


Related Documents
Document
	Purpose
	Backend Services Roadmap v2
	Implementation timeline and phases
	STR Roadmap Assessment v2
	Industry comparison and strategic position
	Individual service specs
	Detailed API and implementation
	

________________


Revision History
Version
	Date
	Changes
	2.0
	Dec 2025
	Simplified architecture, removed saga/CQRS/soft holds
	1.0
	Original
	Enterprise patterns (saga, CQRS, Lua scripts)
	

________________


Availability
Availability Service
Service Owner: TBD Base URL: /api/v1/availability Source of Truth: This service is a cache - PMS is always authoritative Migration Status: Not Started


________________


Overview
A convenience cache that answers availability questions. Not authoritative — Booking Service must verify with the source of record (PMS) before confirming any booking.


Answers three questions:


1. Is unit X available for dates Y-Z?
2. When is the next N-day window available for unit X?
3. Which units in region R are available for dates Y-Z?


Does not know why dates are blocked. Does not enforce policy. Just knows if dates are blocked.
System Dependencies
Dependency
	Role
	PostgreSQL
	Source of truth for this service's data
	Redis
	Read cache, sharded by region
	

No message queues. No search indexes. No orchestration.
Responsibilities
* Track blocked/available dates per unit (binary: blocked or not)
* Fast availability checks for booking flow
* Search filtering by date availability
Does NOT Own
Concern
	Owner
	Block types or reasons
	Booking Service
	Pricing
	Pricing Service
	Booking records
	Booking Service
	Property data
	Property Service
	PMS communication
	Sync Service
	Minimum/maximum stay
	Booking Service
	Arrival/departure restrictions
	Booking Service
	Padding/turnover days
	Booking Service (acquires the padded range)
	

________________


API Reference
GET /check
Check if a unit is available for a date range.


Request


Parameter
	Location
	Type
	Required
	Description
	unit_id
	query
	uuid
	Yes
	Unit identifier
	start_date
	query
	date
	Yes
	First night (inclusive), YYYY-MM-DD
	end_date
	query
	date
	Yes
	Last night (inclusive), YYYY-MM-DD
	

Response


{ "available": true }

Error Responses


Status
	Code
	Condition
	400
	INVALID_DATES
	end_date < start_date or dates in past
	

________________


GET /next-available
Find the next available window of N contiguous nights.


Request


Parameter
	Location
	Type
	Required
	Description
	unit_id
	query
	uuid
	Yes
	Unit identifier
	nights
	query
	int
	Yes
	Contiguous nights needed
	after
	query
	date
	No
	Start search from this date (default: today)
	limit_months
	query
	int
	No
	Months to search (default: 12)
	

Response


{ "found": true, "start_date": "2025-03-22" }

If no window found:


{ "found": false, "start_date": null }

________________


GET /search[p][q][r]
Find available units in one or more regions. Returns unit IDs only.


Request


Parameter
	Location
	Type
	Required
	Description
	region
	query
	string
	Yes*
	Region code
	regions
	query
	string[]
	Yes*
	List of region codes
	start_date
	query
	date
	Yes
	First night (inclusive)
	end_date
	query
	date
	Yes
	Last night (inclusive)
	

*Provide region or regions, not both.


Response


{ "unit_ids": ["550e8400-...", "660e8400-...", "770e8400-..."] }

________________


POST /acquire
Block a date range. Fails if any date already blocked.


Request


{
  "unit_id": "550e8400-e29b-41d4-a716-446655440000",
  "start_date": "2025-03-15",
  "end_date": "2025-03-19"
}

Field
	Type
	Required
	Description
	unit_id
	uuid
	Yes
	Unit identifier
	start_date
	date
	Yes
	First night (inclusive)
	end_date
	date
	Yes
	Last night (inclusive)
	

Response


{ "status": "acquired" }

Error Responses


Status
	Code
	Condition
	409
	CONFLICT
	Dates already blocked
	400
	INVALID_DATES
	Invalid date range
	

________________


POST /release
Unblock a previously acquired date range.


Request


{
  "unit_id": "550e8400-e29b-41d4-a716-446655440000",
  "start_date": "2025-03-15",
  "end_date": "2025-03-19"
}

Field
	Type
	Required
	Description
	unit_id
	uuid
	Yes
	Unit identifier
	start_date
	date
	Yes
	First night (inclusive)
	end_date
	date
	Yes
	Last night (inclusive)
	

Response


{ "status": "released" }

________________


Events
Event
	When
	Payload
	availability.changed
	After /acquire or /release
	{unit_id, start_date, end_date, blocked}
	

Payload fields:


Field
	Type
	Description
	unit_id
	UUID
	Unit identifier
	start_date
	DATE
	First night affected
	end_date
	DATE
	Last night affected
	blocked
	boolean
	True if dates were blocked, false if released
	

Lightweight confirmation that mutation happened. Caller is responsible for their own audit context.


________________


Data Model
Design Principle
Availability is a dumb cache that knows only whether dates are blocked or not. It does not know:


* Why dates are blocked (booking, owner block, maintenance, etc.)
* Who blocked the dates
* Any policies (min nights, check-in restrictions, etc.)


This simplicity is intentional. Booking Service owns the "why."
Bitmask Encoding
Each unit-month is a 32-bit integer. Bit N (1–31) represents day N. 1 = blocked, 0 = available. Bit 0 unused.


Days 5-7 blocked:
Bit:   0  1  2  3  4  5  6  7  8  9 ...
Value: 0  0  0  0  0  1  1  1  0  0 ...


Integer: 0b11100000 = 224
Contiguous Metadata
Each unit-month also stores:


Field
	Description
	start_free
	Free days contiguous from day 1
	end_free
	Free days contiguous to last day of month
	max_free
	Longest contiguous free run in month
	

Used for /next-available queries without scanning every bit.
PostgreSQL Schema
CREATE TABLE availability (
    unit_id     UUID NOT NULL,
    year_month  CHAR(7) NOT NULL,           -- 'YYYY-MM'
    days        BIT(32) NOT NULL DEFAULT B'00000000000000000000000000000000',
    start_free  SMALLINT NOT NULL DEFAULT 31,
    end_free    SMALLINT NOT NULL DEFAULT 31,
    max_free    SMALLINT NOT NULL DEFAULT 31,


    PRIMARY KEY (unit_id, year_month)
);


CREATE TABLE unit_region (
    unit_id  UUID PRIMARY KEY,
    region   VARCHAR(50) NOT NULL
);


CREATE INDEX idx_unit_region ON unit_region(region);
Redis Structure
# Region membership (for /search)
region:{region_code} → SET of unit_ids


# Availability bitmap (mirrors PostgreSQL)
avail:{unit_id}:{YYYY-MM} → Redis string, manipulated via BITFIELD


# Contiguous metadata (for /next-available, computed on cache miss)
avail:meta:{unit_id}:{YYYY-MM} → HASH { start_free, end_free, max_free }
Storage Comparison
Approach
	Rows for 500 units × 5 years
	Current (row per day)
	~912,500 rows
	Bitmask (row per month)
	~30,000 rows
	

97% reduction in row count.


________________


Current State Analysis
Existing Tables
AvailabilityDay (wander schema lines 4543-4581)
-- Current: 38 columns, denormalized availability + pricing data
CREATE TABLE public."AvailabilityDay" (
    id text NOT NULL,
    "calendarDayId" text NOT NULL,
    "unitId" text NOT NULL,
    "isBooked" boolean DEFAULT false NOT NULL,
    "isBlocked" boolean DEFAULT false NOT NULL,
    "nightPrice" integer DEFAULT 0 NOT NULL,           -- PRICING CONCERN
    -- ... LOS pricing columns, check-in rules, etc.
);

Issues:


* One row per day per unit = ~10M rows
* Contains pricing data that belongs in Pricing Service
* Redundant boolean flags
* text type for IDs instead of UUID
DateBlock (wander schema lines 5207-5223)
CREATE TABLE public."DateBlock" (
    id text NOT NULL,
    type public."DateBlockType" DEFAULT 'OPS' NOT NULL,
    "startDate" timestamp(3) NOT NULL,
    "endDate" timestamp(3) NOT NULL,
    "unitId" text,
    reason text,
    "bookingId" text
);

Note: Block types and reasons move to Booking Service. Availability only needs blocked/not.
What Moves to Other Services
Data
	Current Location
	New Owner
	Block type (BOOKING, OWNER, OPS)
	DateBlock.type
	Booking Service
	Block reason
	DateBlock.reason
	Booking Service
	Booking reference
	DateBlock.bookingId
	Booking Service
	Night price
	AvailabilityDay.nightPrice
	Pricing Service
	LOS pricing
	AvailabilityDay.losDay*Price
	Pricing Service
	Min nights
	AvailabilityDay.minNights
	Booking Service (policy)
	Check-in/out rules
	AvailabilityDay.canCheckIn/Out
	Booking Service (policy)
	

________________


Migration Strategy
Phase 1: Create New Tables
CREATE TABLE availability (
    unit_id     UUID NOT NULL,
    year_month  CHAR(7) NOT NULL,
    days        BIT(32) NOT NULL DEFAULT B'00000000000000000000000000000000',
    start_free  SMALLINT NOT NULL DEFAULT 31,
    end_free    SMALLINT NOT NULL DEFAULT 31,
    max_free    SMALLINT NOT NULL DEFAULT 31,
    PRIMARY KEY (unit_id, year_month)
);


CREATE TABLE unit_region (
    unit_id  UUID PRIMARY KEY,
    region   VARCHAR(50) NOT NULL
);


CREATE INDEX idx_unit_region ON unit_region(region);
Phase 2: Backfill from Existing Data
WITH blocked_days AS (
    SELECT
        "unitId"::uuid as unit_id,
        to_char(date, 'YYYY-MM') as year_month,
        EXTRACT(DAY FROM date)::int as day_num
    FROM public."AvailabilityDay"
    WHERE "isBooked" = true OR "isBlocked" = true
),
monthly_masks AS (
    SELECT
        unit_id,
        year_month,
        bit_or(1::bit(32) << day_num) as days
    FROM blocked_days
    GROUP BY unit_id, year_month
)
INSERT INTO availability (unit_id, year_month, days, start_free, end_free, max_free)
SELECT
    unit_id,
    year_month,
    COALESCE(days, B'00000000000000000000000000000000'),
    31, 31, 31  -- Contiguous metadata computed by application after insert
FROM monthly_masks;
Phase 3: Populate Redis
def populate_redis():
    for row in db.query("SELECT * FROM availability"):
        redis.set(f"avail:{row.unit_id}:{row.year_month}", row.days)
        redis.hset(f"avail:meta:{row.unit_id}:{row.year_month}", {
            "start_free": row.start_free,
            "end_free": row.end_free,
            "max_free": row.max_free
        })


    for row in db.query("SELECT * FROM unit_region"):
        redis.sadd(f"region:{row.region}", row.unit_id)
Phase 4: Dual-Write Period
1. New writes go to both old and new tables
2. Reads gradually shift to new tables (feature flag)
3. Monitor for discrepancies
Phase 5: Cutover
1. Stop writing to old tables
2. Archive old data
3. Drop old tables after retention period
Rollback Plan
1. Feature flags control read/write paths
2. Dual-write maintained for 30 days post-migration
3. Old tables retained (read-only) for 90 days
4. If issues arise, flip flag to read from old tables


________________


Implementation Details
Date-to-Bitmask Conversion
def date_range_to_masks(start_date: date, end_date: date) -> dict[str, int]:
    masks = {}
    current = start_date


    while current <= end_date:
        ym = current.strftime('%Y-%m')
        if ym not in masks:
            masks[ym] = 0
        masks[ym] |= (1 << current.day)
        current += timedelta(days=1)


    return masks


# March 15-19 → {'2025-03': 0b00000000000011111000000000000000}
Write Path (/acquire)
1. Convert date range to bitmasks (one per month)
2. Begin PostgreSQL transaction
3. For each month:
   a. SELECT ... FOR UPDATE on availability row
   b. Conflict check: (days & mask) != 0
      - If conflict: rollback, return 409
   c. Update: days = days | mask
4. Commit transaction
5. Update Redis cache
6. Emit availability.changed event
7. Return 200 { "status": "acquired" }
Read Path (/check)
1. Convert date range to bitmasks
2. For each month (parallel):
   a. GET avail:{unit_id}:{ym} from Redis
   b. If miss: query PostgreSQL, populate Redis
   c. AND with request mask
   d. If non-zero: return { "available": false }
3. All months clear: return { "available": true }

________________


SLOs
Endpoint
	p95 Latency
	Notes
	GET /check
	< 5ms
	Redis hit
	GET /check
	< 50ms
	Redis miss (DB fallback)
	GET /next-available
	< 50ms
	Scans up to 12 months
	GET /search
	< 100ms
	Up to 500 units
	POST /acquire
	< 50ms
	DB transaction
	POST /release
	< 50ms
	DB transaction
	

Metric
	Target
	Cache hit rate
	> 95%
	Reconciliation drift
	0
	

________________


Observability
Metrics
Metric
	Type
	Description
	availability_check_latency_ms
	histogram
	/check latency
	availability_search_latency_ms
	histogram
	/search latency
	availability_search_units
	histogram
	Units scanned per search
	availability_acquire_total
	counter
	Acquire attempts
	availability_acquire_conflict_total
	counter
	Conflicts
	availability_redis_hit_total
	counter
	Cache hits
	availability_redis_miss_total
	counter
	Cache misses
	availability_reconciliation_drift
	gauge
	Drift found in last reconciliation
	Alerts
Condition
	Severity
	Action
	p95 > 200ms
	Warning
	Check Redis/PostgreSQL load
	Redis unreachable
	Warning
	Falling back to PostgreSQL
	PostgreSQL unreachable
	Critical
	Service degraded
	Reconciliation drift > 0
	Warning
	Investigate, PostgreSQL wins
	

________________


Failure Patterns
Failure
	Behavior
	Redis unavailable
	Query PostgreSQL directly (slower)
	PostgreSQL unavailable
	Return 503 (service degraded)
	Redis write fails
	Invalidate cache key, continue (DB is truth)
	PostgreSQL write fails
	Abort, return error, no side effects
	

________________


Test Scenarios
test_cases:
  - name: check_available_dates
    endpoint: GET /check
    setup:
      - unit exists with no blocks
    request:
      unit_id: "{{unit_id}}"
      start_date: "2025-06-01"
      end_date: "2025-06-05"
    expected:
      status: 200
      body:
        available: true


  - name: check_blocked_dates
    endpoint: GET /check
    setup:
      - unit exists
      - dates 2025-06-01 to 2025-06-03 are blocked
    request:
      unit_id: "{{unit_id}}"
      start_date: "2025-06-01"
      end_date: "2025-06-05"
    expected:
      status: 200
      body:
        available: false


  - name: acquire_available_dates
    endpoint: POST /acquire
    setup:
      - unit exists with no blocks
    request:
      body:
        unit_id: "{{unit_id}}"
        start_date: "2025-06-01"
        end_date: "2025-06-05"
    expected:
      status: 200
      body:
        status: "acquired"


  - name: acquire_blocked_dates_fails
    endpoint: POST /acquire
    setup:
      - unit exists
      - dates 2025-06-01 to 2025-06-03 are blocked
    request:
      body:
        unit_id: "{{unit_id}}"
        start_date: "2025-06-01"
        end_date: "2025-06-05"
    expected:
      status: 409
      body:
        status: "conflict"


  - name: release_blocked_dates
    endpoint: POST /release
    setup:
      - dates 2025-06-01 to 2025-06-05 are blocked
    request:
      body:
        unit_id: "{{unit_id}}"
        start_date: "2025-06-01"
        end_date: "2025-06-05"
    expected:
      status: 200
      body:
        status: "released"


  - name: find_next_available
    endpoint: GET /next-available
    setup:
      - dates 2025-06-01 to 2025-06-10 are blocked
    request:
      unit_id: "{{unit_id}}"
      nights: 5
      after: "2025-06-01"
    expected:
      status: 200
      body:
        found: true
        start_date: "2025-06-11"


  - name: search_by_region
    endpoint: GET /search
    setup:
      - 3 units in region US-CO-ASPEN
      - 1 unit blocked for requested dates
    request:
      region: "US-CO-ASPEN"
      start_date: "2025-06-01"
      end_date: "2025-06-05"
    expected:
      status: 200
      body:
        unit_ids:
          length: 2

________________


Dependencies
Upstream (writes to Availability)
Service
	Operation
	Booking Service
	/acquire when booking confirmed
	Booking Service
	/release when booking cancelled
	Sync Service
	/acquire + /release to mirror PMS state
	Downstream (reads from Availability)
Service
	Operation
	Search
	/search to filter by dates
	Booking Service
	/check before confirming with PMS
	Pricing Service
	/check to validate quote requests
	

________________


Composition with Other Services
Availability returns unit IDs. Other services filter further:


Search Orchestrator:


1. available_ids = Availability.search(region, dates)       # Parallel
2. amenity_ids = Amenity.filter(region, filters)            # Parallel
3. price_ids = Pricing.filter(region, dates, price_range)   # Parallel


4. result_ids = intersect(available_ids, amenity_ids, price_ids)


5. Enrich, sort, paginate, return

Each service is a filter. Availability answers "what's not blocked." It doesn't know about pools, prices, or policies.


Pricing
Pricing Service
Service Owner: TBD Base URL: /api/v1/pricing Source of Truth: Nightly rate cache (not authoritative) Migration Status: Not Started


________________


Overview
A fast cache that serves pre-computed nightly rates. Not authoritative — prices are written by Sync Service and Pricing Intelligence. Does not compute discounts, fees, or taxes. Just answers: what's the nightly rate?
System Dependencies
Dependency
	Role
	PostgreSQL
	Source of truth for this service's data
	Redis
	Read cache, hash-sharded by unit_id
	Responsibilities
* Store and serve nightly rates per unit
* Cache rates in Redis (hash-sharded by unit_id)
* Fall back to PostgreSQL on cache miss
Does NOT Own
Concern
	Owner
	Fees and taxes
	Booking Service
	Quotes
	Booking Service
	LOS discounts
	Booking Service
	Dynamic pricing computation
	Pricing Intelligence Service (Data Team)
	PMS price sync
	Sync Service
	Availability
	Availability Service
	Promo codes
	Booking Service
	Custom Prices
	Stays as data channel
	Price Match Overlay
	Booking Service
	

Pricing Service is dumb. It stores numbers. It doesn't think. That's the design.


________________


API Reference
GET /rate
Get nightly rates for a unit and date range.


Request


Parameter
	Location
	Type
	Required
	Description
	unit_id
	query
	uuid
	Yes
	Unit identifier
	start_date
	query
	date
	Yes
	First night (inclusive), YYYY-MM-DD
	end_date
	query
	date
	No
	Last night (inclusive). Default: start_date
	

Response


{
  "unit_id": "550e8400-e29b-41d4-a716-446655440000",
  "currency": "USD",
  "rates": [
    { "date": "2025-03-15", "rate_cents": 45000 },
    { "date": "2025-03-16", "rate_cents": 45000 },
    { "date": "2025-03-17", "rate_cents": 52000 }
  ],
  "missing_dates": []
}

With missing data:


{
  "unit_id": "550e8400-...",
  "currency": "USD",
  "rates": [
    { "date": "2025-03-15", "rate_cents": 45000 },
    { "date": "2025-03-16", "rate_cents": 45000 }
  ],
  "missing_dates": ["2025-03-17", "2025-03-18"]
}

Caller decides how to handle missing dates (use rack rate, exclude unit, alert).


Error Responses


Status
	Code
	Condition
	400
	INVALID_INPUT
	Missing or invalid parameters
	

________________


GET /calendar
Get nightly rates for a unit across a month.


Request


Parameter
	Location
	Type
	Required
	Description
	unit_id
	query
	uuid
	Yes
	Unit identifier
	month
	query
	string
	Yes
	Month (YYYY-MM format)
	

Response


{
  "unit_id": "550e8400-...",
  "month": "2025-03",
  "currency": "USD",
  "rates": [
    { "day": 1, "rate_cents": 45000 },
    { "day": 2, "rate_cents": 45000 },
    { "day": 3, "rate_cents": 52000 }
  ],
  "missing_days": []
}

________________


POST /bulk
Get aggregate rates for multiple units. Used by search results.


Request


{
  "unit_ids": [
    "550e8400-e29b-41d4-a716-446655440000",
    "660e8400-e29b-41d4-a716-446655440001"
  ],
  "start_date": "2025-03-15",
  "end_date": "2025-03-19"
}

Field
	Type
	Required
	Description
	unit_ids
	uuid[]
	Yes
	Unit identifiers (max 500)
	start_date
	date
	Yes
	First night (inclusive)
	end_date
	date
	Yes
	Last night (inclusive)
	

Response


{
  "rates": [
    {
      "unit_id": "550e8400-...",
      "currency": "USD",
      "sum_cents": 225000,
      "avg_cents": 45000,
      "min_cents": 42000,
      "max_cents": 52000,
      "missing_dates": []
    },
    {
      "unit_id": "660e8400-...",
      "currency": "USD",
      "sum_cents": null,
      "avg_cents": null,
      "min_cents": null,
      "max_cents": null,
      "missing_dates": ["2025-03-17", "2025-03-18"]
    }
  ]
}

Aggregates are null if any date is missing. Caller filters or handles incomplete units.


________________


POST /set
Set nightly rates for a unit. Called by Sync Service and Pricing Intelligence.


Request


{
  "unit_id": "550e8400-e29b-41d4-a716-446655440000",
  "currency": "USD",
  "rates": [
    { "date": "2025-03-15", "rate_cents": 45000 },
    { "date": "2025-03-16", "rate_cents": 45000 },
    { "date": "2025-03-17", "rate_cents": 52000 }
  ]
}

Field
	Type
	Required
	Description
	unit_id
	uuid
	Yes
	Unit identifier
	currency
	string
	Yes
	Currency code (e.g., USD)
	rates
	array
	Yes
	Array of date/rate pairs
	

Response


{
  "status": "updated",
  "count": 3
}

________________


Events
Event
	When
	Payload
	pricing.rates_updated
	After /set
	{unit_id, dates}
	

Payload fields:


Field
	Type
	Description
	unit_id
	UUID
	Unit identifier
	dates
	DATE[]
	Dates that were updated
	

Lightweight confirmation that rates were written. Caller is responsible for their own audit context.


________________


Data Model
Design Principle
Pricing Service is a cache — it stores pre-computed nightly rates. It does not compute quotes, manage fees, or handle promo codes. Those responsibilities belong to Booking Service.


The schema is intentionally simple: one table for rates.
PostgreSQL Schema
CREATE TABLE pricing (
    unit_id     UUID NOT NULL,
    date        DATE NOT NULL,
    rate_cents  INT NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'USD',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_id, date)
);


-- Index for date range and calendar queries
CREATE INDEX idx_pricing_unit_date_range
    ON pricing (unit_id, date);
    INCLUDE (rate_cents);

That's it. One table.


Why so simple?


* This service is a cache, not a pricing engine
* Rates are written by Sync Service (from PMS) and Pricing Intelligence
* Booking Service computes quotes by combining rates + fees + taxes + discounts
* No need to track rate history, adjustments, or rules here
Redis Structure
Sharding: Hash-based on unit_id. All data for a unit lives on one shard.


shard = hash(unit_id) % N

Rate key:


price:{unit_id}:{YYYY-MM-DD} → rate_cents
TTL: 1 hour

Calendar key (denormalized):


price:cal:{unit_id}:{YYYY-MM} → JSON { rates: [...], missing_days: [...] }
TTL: 1 hour

Currency key:


price:currency:{unit_id} → currency code
TTL: 24 hours

________________


Current State Analysis
Existing Tables
The current monorepo has complex pricing tables that will be simplified:
PricingCalendarDay (current)
-- Current: 58 columns, daily pricing calculation cache
CREATE TABLE public."PricingCalendarDay" (
    id text NOT NULL,
    "calendarDayId" text NOT NULL,
    "nightPrice" integer NOT NULL,
    "losDay1Price" integer NOT NULL,
    "losDay2Price" integer NOT NULL,
    -- ... 50+ more columns
);

Issues:


* Too many columns for a cache
* LOS prices belong in Booking Service quote logic
* AI agent columns mixed with base pricing
PricingConfig (current)
-- Current: 46 columns, monolithic configuration
CREATE TABLE public."PricingConfig" (
    id text NOT NULL,
    "unitId" text NOT NULL,
    "basePrice" integer NOT NULL,
    -- ... 40+ feature flag columns
);

Status: Will be deprecated. Pricing configuration moves to Pricing Intelligence Service (future). Pricing Service just caches the results.
Adjustment Tables (current)
Multiple tables exist:


* PricingEarlyBirdAdjustment
* PricingLastMinuteAdjustment
* PricingLengthOfStay
* PricingMonthSeasonality
* etc.


Status: Will be deprecated. Adjustment logic moves to Pricing Intelligence Service (future). Pricing Service stores the final computed rate.[s][t]
What Moved Out
These tables/concepts are NOT part of Pricing Service:


Concept
	New Owner
	Notes
	Quote calculation
	Booking Service
	Combines rates + fees + taxes
	Fee configuration
	Property Service
	Per-property fee settings
	Promo codes
	Booking Service
	Discount application
	LOS discounts
	Booking Service
	Applied during quote
	Tax calculation
	Booking Service
	External tax service
	Adjustment rules
	Pricing Intelligence
	Computes final rate
	AI pricing
	Pricing Intelligence
	Recommends rates
	

________________


Migration Strategy
Phase 1: Create New Table
CREATE TABLE pricing (
    unit_id     UUID NOT NULL,
    date        DATE NOT NULL,
    rate_cents  INT NOT NULL,
    currency    CHAR(3) NOT NULL DEFAULT 'USD',[u][v][w][x]
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (unit_id, date)
);




-- Index for date range and calendar queries
CREATE INDEX idx_pricing_unit_date_range
    ON pricing (unit_id, date);
    INCLUDE (rate_cents);
Phase 2: Backfill from Existing Data
INSERT INTO pricing (unit_id, date, rate_cents, currency)
SELECT DISTINCT ON (u."newPkId", cd."localizedDate"::date)
    u."newPkId" AS unit_id,
    cd."localizedDate"::date AS date,
    pcd."nightPrice" AS rate_cents,
    'USD' AS currency
FROM public."PricingCalendarDay" pcd
JOIN public."CalendarDay" cd ON pcd."calendarDayId" = cd.id
JOIN public."Unit" u ON u.id = cd."unitId"
ON CONFLICT (unit_id, date) DO UPDATE
SET rate_cents = EXCLUDED.rate_cents;

Phase 3: Dual-Write
1. Update Sync Service to write to both old and new tables
2. Update Pricing Intelligence to write to both
3. Feature flag to read from new table
Phase 4: Cutover
1. Read from new table
2. Stop writing to old tables
3. Deprecate old pricing columns in AvailabilityDay
Phase 5: Cleanup
1. Archive old pricing tables
2. Remove dual-write code
3. Drop deprecated columns
Rollback Plan
1. Feature flags control read/write paths
2. Dual-write maintained during migration
3. Old tables retained (read-only) for 90 days
4. Can revert to reading from old tables instantly via feature flag


________________


Implementation Details
Hash-Based Sharding
All data for a unit lives on one shard, determined by hash(unit_id) % N.


For single-unit queries:


1. Hash unit_id to determine shard
2. Query that shard


For multi-unit queries:


1. Group unit_ids by shard
2. Fan out queries in parallel
3. Reassemble results


def bulk_lookup(unit_ids: list[str], start_date: date, end_date: date) -> dict:
    # Group by shard
    by_shard = defaultdict(list)
    for uid in unit_ids:
        shard = hash(uid) % NUM_SHARDS
        by_shard[shard].append(uid)


    # Fan out in parallel
    async def query_shard(shard: int, uids: list[str]):
        redis = get_redis_for_shard(shard)
        results = {}
        for uid in uids:
            rates = await get_rates_from_shard(redis, uid, start_date, end_date)
            results[uid] = rates
        return results


    tasks = [query_shard(s, uids) for s, uids in by_shard.items()]
    shard_results = await asyncio.gather(*tasks)


    # Reassemble
    all_results = {}
    for result in shard_results:
        all_results.update(result)


    return all_results
Write Path (/set)
1. Upsert rows in PostgreSQL:


   INSERT INTO pricing (unit_id, date, rate_cents, currency)
   VALUES (...)
   ON CONFLICT (unit_id, date) DO UPDATE
   SET rate_cents = EXCLUDED.rate_cents, currency = EXCLUDED.currency


2. Determine shard: hash(unit_id) % N


3. Invalidate Redis keys on that shard:
   - DEL price:{unit_id}:{date} for each date
   - DEL price:cal:{unit_id}:{YYYY-MM} for affected months
   - DEL price:currency:{unit_id} if currency changed


4. Emit pricing.rates_updated event


5. Return { status: "updated", count: N }

________________


SLOs
Operation
	p95 Latency
	GET /rate (single night)
	< 5ms
	GET /rate (30 nights)
	< 20ms
	GET /calendar
	< 20ms
	POST /bulk (100 units)
	< 50ms
	POST /bulk (500 units)
	< 100ms
	POST /set
	< 50ms
	

Metric
	Target
	Cache hit rate
	> 95%
	Missing rate data
	< 1% of queries
	

________________


Observability
Metrics
Metric
	Type
	Description
	pricing_rate_latency_ms
	histogram
	/rate latency
	pricing_calendar_latency_ms
	histogram
	/calendar latency
	pricing_bulk_latency_ms
	histogram
	/bulk latency
	pricing_bulk_units
	histogram
	Units per bulk request
	pricing_redis_hit_total
	counter
	Cache hits
	pricing_redis_miss_total
	counter
	Cache misses
	pricing_missing_rate_total
	counter
	Dates with no configured rate
	pricing_shard_fanout
	histogram
	Shards touched per bulk request
	Alerts
Condition
	Severity
	Action
	p95 > 100ms
	Warning
	Check Redis/PostgreSQL load
	Redis shard unreachable
	Warning
	Falling back to PostgreSQL
	PostgreSQL unreachable
	Critical
	Service degraded
	Missing rate data
	Warning
	Investigate sync pipeline
	

________________


Failure Patterns
Failure
	Behavior
	Redis shard unavailable
	Query PostgreSQL for that shard's units
	PostgreSQL unavailable
	Return 503
	Redis write fails
	Invalidate cache, continue
	Missing rate data
	Return in missing_dates, emit metric
	

________________


Test Scenarios
test_cases:
  - name: get_single_rate
    endpoint: GET /rate
    setup:
      - unit exists with rate 45000 for 2025-03-15
    request:
      query:
        unit_id: "{{unit_id}}"
        start_date: "2025-03-15"
    expected:
      status: 200
      body:
        rates:
          - date: "2025-03-15"
            rate_cents: 45000
        missing_dates: []


  - name: get_rate_with_missing
    endpoint: GET /rate
    setup:
      - unit exists with rates for 2025-03-15, 2025-03-16 only
    request:
      query:
        unit_id: "{{unit_id}}"
        start_date: "2025-03-15"
        end_date: "2025-03-19"
    expected:
      status: 200
      body:
        rates:
          length: 2
        missing_dates:
          - "2025-03-17"
          - "2025-03-18"
          - "2025-03-19"


  - name: bulk_rates
    endpoint: POST /bulk
    setup:
      - three units exist with rates
    request:
      body:
        unit_ids: ["{{unit_id_1}}", "{{unit_id_2}}", "{{unit_id_3}}"]
        start_date: "2025-03-15"
        end_date: "2025-03-19"
    expected:
      status: 200
      body:
        rates:
          length: 3


  - name: set_rates
    endpoint: POST /set
    request:
      body:
        unit_id: "{{unit_id}}"
        currency: "USD"
        rates:
          - date: "2025-03-15"
            rate_cents: 45000
          - date: "2025-03-16"
            rate_cents: 45000
    expected:
      status: 200
      body:
        status: "updated"
        count: 2

________________


Dependencies
Upstream (writes to Pricing)
Service
	Operation
	Sync Service
	PMS rate updates via /set
	Pricing Intelligence Service (future)
	Dynamic pricing updates via /set
	Downstream (reads from Pricing)
Service
	Operation
	Booking Service
	Gets rates for quote calculation
	Search
	Price filtering and display via /bulk
	

________________


Data Volume Estimates
Table
	Current Rows (Est.)
	New Table
	Projected Rows
	PricingCalendarDay
	~3M
	pricing
	~180K (365 × 500 units)
	

Storage optimization: ~95% reduction by removing redundant columns and storing only the final rate.


________________


Notes
Why no promo tables? Promo codes are applied during booking, not during rate lookup. Booking Service owns promo redemption.


Why no fee tables? Fees (cleaning, pet, etc.) are property configuration. Property Service owns them. Booking Service combines rates + fees to create quotes.


Why no quote table? Quotes are transient calculations. Booking Service computes them on-demand or caches them briefly. No need for a persistent quote table in Pricing Service.


LOS discounts and adjustment rules are currently owned by the data team's pricing pipeline. These rules are applied before rates are written to Pricing Service. Pricing Service stores the final computed rate only. Future: Pricing Intelligence Service will own this logic.






Booking
Booking Service
Service Owner: TBD Base URL: /api/v1/bookings Source of Truth: Booking state, UBR generation, reservation lifecycle Migration Status: Not Started


________________


Overview
Coordinates the booking transaction across Availability, Pricing, Payments, and PMS systems. Owns booking records, price snapshots, and UBR generation.


Booking is a synchronous transaction. User clicks "Book Now", waits 2-3 seconds, gets a result. No emails, no "check back later."
System Dependencies
Dependency
	Role
	PostgreSQL
	Booking records, price snapshots, UBR
	Availability Service
	Check and block dates
	Pricing Service
	Get quotes
	Payments Service
	Authorize, capture, refund
	PMS Adapters
	Verify availability, create reservations
	Property Service
	Property config, cancellation policies
	Guest Service
	Guest lookup
	Event Service
	Emit booking events
	Responsibilities
* Booking creation with payment authorization
* Universal Booking Reference generation and management
* Cancellation with refund calculation
* Booking state machine (CONFIRMED → COMPLETED)
* 3DS flow handling
* Idempotent booking operations
* Price snapshot (full line-item breakdown, frozen at booking)
Does NOT Own
Concern
	Owner
	Calculate rates
	Pricing Service
	Manage availability
	Availability Service
	Process payments
	Payments Service
	Define cancellation policies
	Property Service
	Sync calendars
	Sync Service
	Store guest profiles
	Guest Service
	

________________


API Reference
POST /bookings
Create a new booking with payment.


Request


{
  "idempotency_key": "book_req_abc123",
  "property_id": "prop_abc123",
  "check_in": "2025-03-15",
  "check_out": "2025-03-20",
  "guest_id": "user_456",
  "guests": {
    "adults": 2,
    "children": 1,
    "infants": 0,
    "pets": 0,
    "primary": {
      "first_name": "John",
      "last_name": "Doe",
      "email": "john@example.com",
      "phone": "+1-555-123-4567"
    }
  },
  "payment_method_id": "pm_stripe_xyz",
  "customer_id": "cus_xyz"
}

Field
	Type
	Required
	Description
	idempotency_key
	string
	Yes
	Client-generated unique key
	property_id
	uuid
	Yes
	Property to book
	check_in
	date
	Yes
	Check-in date (inclusive)
	check_out
	date
	Yes
	Check-out date (exclusive)
	guest_id
	uuid
	Yes
	Primary guest user ID
	guests
	object
	Yes
	Guest details
	payment_method_id
	string
	Yes
	Stripe payment method
	customer_id
	string
	Yes
	Stripe customer
	

Response - Confirmed (201)


{
  "id": "booking_xyz789",
  "ubr": "UBR-5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ",
  "status": "CONFIRMED",
  "property_id": "prop_abc123",
  "check_in": "2025-03-15",
  "check_out": "2025-03-20",
  "nights": 5,
  "guests": {
    "adults": 2,
    "children": 1,
    "total": 3
  },
  "price": {
    "currency": "USD",
    "subtotal_cents": 245000,
    "fees_total_cents": 68500,
    "taxes_total_cents": 50160,
    "discounts_total_cents": 20000,
    "total_cents": 343660
  },
  "pms_confirmation_id": "GY-99887",
  "payment_intent_id": "pi_stripe_abc",
  "confirmed_at": "2025-03-10T14:32:01Z"
}

Response - 3DS Required (202)


{
  "pending_id": "pending_abc123",
  "requires_action": true,
  "client_secret": "pi_xyz_secret_abc",
  "expires_at": "2025-03-10T14:47:01Z"
}

Frontend handles 3DS inline with Stripe.js (user sees bank popup, authenticates, returns). After authentication completes, frontend retries with same idempotency_key. Second call sees payment is authorized and continues synchronously.


Error Responses


Status
	Code
	Condition
	400
	INVALID_REQUEST
	Missing/invalid fields
	402
	PAYMENT_FAILED
	Payment authorization failed
	404
	PROPERTY_NOT_FOUND
	Property does not exist
	409
	DATES_UNAVAILABLE
	Dates no longer available
	422
	PROPERTY_NOT_BOOKABLE
	Property is not accepting bookings
	

________________


GET /bookings/{booking_id}
Retrieve booking details.


Request


Parameter
	Location
	Type
	Required
	Description
	booking_id
	path
	uuid
	Yes
	Booking identifier
	

Response (200)


{
  "id": "booking_xyz789",
  "ubr": "UBR-5HueCGU8rMjxEXxiPuD5BDku...",
  "status": "CONFIRMED",
  "property": {
    "id": "prop_abc123",
    "name": "Sunset Retreat"
  },
  "check_in": "2025-03-15",
  "check_out": "2025-03-20",
  "nights": 5,
  "guest": {
    "id": "user_xyz",
    "name": "John Doe"
  },
  "guests": {
    "adults": 2,
    "children": 1,
    "pets": 0
  },
  "price": {
    "currency": "USD",
    "nightly_rates": [
      {"date": "2025-03-15", "cents": 45000},
      {"date": "2025-03-16", "cents": 45000},
      {"date": "2025-03-17", "cents": 52000},
      {"date": "2025-03-18", "cents": 52000},
      {"date": "2025-03-19", "cents": 45000}
    ],
    "subtotal_cents": 239000,
    "fees": [
      {"kind": "cleaning", "label": "Cleaning fee", "cents": 25000},
      {"kind": "pet", "label": "Pet fee", "cents": 15000}
    ],
    "fees_total_cents": 40000,
    "taxes": [
      {"kind": "occupancy", "label": "Occupancy Tax (12%)", "rate": 0.12, "cents": 33480}
    ],
    "taxes_total_cents": 33480,
    "discounts": [],
    "discounts_total_cents": 0,
    "total_cents": 312480
  },
  "pms_confirmation_id": "GY-99887",
  "confirmed_at": "2025-03-10T14:32:01Z"
}

________________


GET /bookings/ubr/{ubr}
Retrieve booking by Universal Booking Reference.


Same response as GET /bookings/{booking_id}.


________________


GET /bookings
List bookings with filters.


Request


Parameter
	Location
	Type
	Required
	Description
	guest_id
	query
	uuid
	No
	Filter by guest
	property_id
	query
	uuid
	No
	Filter by property
	status
	query
	string
	No
	Filter by status
	check_in_from
	query
	date
	No
	Check-in range start
	check_in_to
	query
	date
	No
	Check-in range end
	limit
	query
	int
	No
	Max results (default 50, max 200)
	cursor
	query
	string
	No
	Pagination cursor
	

Response (200)


{
  "data": [
    {
      "id": "booking_abc123",
      "ubr": "UBR-5HueCGU8rMjxEXxiPuD5BDku...",
      "status": "CONFIRMED",
      "property_id": "prop_abc123",
      "check_in": "2025-03-15",
      "check_out": "2025-03-20",
      "nights": 5,
      "total_cents": 302480,
      "confirmed_at": "2025-03-10T14:32:01Z"
    }
  ],
  "next_cursor": "eyJpZCI6Ij..."
}

________________


POST /bookings/{booking_id}/cancel
Cancel a confirmed booking. Use dry_run: true to preview refund without executing.


Request


{
  "reason": "Guest requested cancellation",
  "actor": {
    "kind": "user",
    "id": "user_456"
  },
  "dry_run": false
}

Field
	Type
	Required
	Description
	reason
	string
	Yes
	Cancellation reason
	actor
	object
	Yes
	Who is cancelling
	dry_run
	boolean
	No
	If true, returns refund calculation without executing
	

Response - Executed (200)


{
  "id": "booking_xyz789",
  "status": "CANCELLED",
  "cancellation": {
    "cancelled_at": "2025-03-12T10:00:00Z",
    "cancelled_by": "user",
    "reason": "Guest requested cancellation",
    "refund_amount_cents": 200000,
    "penalty_cents": 143660,
    "refund_percent": 58
  }
}

Response - Dry Run (200)


{
  "id": "booking_xyz789",
  "dry_run": true,
  "cancellation": {
    "refund_amount_cents": 200000,
    "penalty_cents": 143660,
    "refund_percent": 58,
    "policy_name": "Flexible"
  }
}

________________


Events
Event
	When
	Payload
	booking.requested
	Payment authorized, attempting PMS confirmation
	{idempotency_key, unit_id, check_in, check_out}
	booking.confirmed
	PMS confirmed, payment captured, booking complete
	{booking_id, ubr, unit_id, total_cents}
	booking.cancelled
	Booking cancelled
	{booking_id, ubr, reason, refund_cents}
	booking.failed
	Booking failed at any step
	{idempotency_key, error, guest_id}
	

________________


Booking Flow
User clicks "Book Now". Everything happens synchronously.


┌─────────────────────────────────────────────────────────────────────────────┐
│ 1. AUTHORIZE PAYMENT                                                        │
│    - Stripe auth hold (not capture)                                         │
│    - If 3DS required: return client_secret, pause flow                      │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 2. VERIFY WITH PMS (System of Record)                                       │
│    - Confirm availability                                                   │
│    - Get authoritative quote                                                │2
│    - If unavailable: cancel auth, return error                              │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 3. CREATE PMS RESERVATION                                                   │
│    - PMS returns confirmation ID                                            │
│    - If fails: cancel auth, return error                                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 4. CAPTURE PAYMENT                                                          │
│    - Capture at PMS price (authoritative)                                   │
│    - If fails: cancel PMS reservation, return error                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ 5. FINALIZE                                                                 │
│    - Generate UBR                                                           │
│    - Freeze price snapshot                                                  │
│    - Store card on file                                                     │
│    - Update Availability cache                                              │
│    - Emit booking.confirmed event                                           │
│    - Return confirmed booking                                               │
└─────────────────────────────────────────────────────────────────────────────┘

Total time: 2-3 seconds.


________________


3DS Flow
3DS authentication is handled synchronously via Stripe.js:


1. create_booking returns requires_action=true with client_secret
2. Frontend calls stripe.confirmCardPayment(client_secret) — shows bank popup
3. User authenticates in popup (still on booking page)
4. Stripe.js returns success
5. Frontend retries POST /bookings with same idempotency_key
6. Second call sees payment is authorized, continues to PMS verification and capture[y]
7. User gets confirmed booking


Total time including 3DS: 2-3 seconds. No webhooks, no "check back later."


________________


Data Model
Two booking tables based on System of Record:


Table
	When Used
	EXCLUDE constraint?
	booking
	Wander is SoR (we're authoritative)
	Yes — prevents double-booking at DB level
	booking_stub
	External PMS is SoR (guest booked through our portal, we push to PMS)
	No — we don't have full availability picture
	booking
Full booking record for units where Wander is System of Record.


CREATE TABLE booking (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    ubr                 VARCHAR(100) UNIQUE NOT NULL,


    unit_id             UUID NOT NULL,
    guest_id            UUID NOT NULL,


    check_in            DATE NOT NULL,
    check_out           DATE NOT NULL,
    nights              INT GENERATED ALWAYS AS (check_out - check_in) STORED,


    -- DATERANGE for exclusion constraint
    stay_range          DATERANGE GENERATED ALWAYS AS (daterange(check_in, check_out, '[)')) STORED,


    status              VARCHAR(20) NOT NULL DEFAULT 'CONFIRMED',
    -- CONFIRMED, CANCELLED, COMPLETED


    payment_intent_id   VARCHAR(100),


    idempotency_key     VARCHAR(100) UNIQUE,


    confirmed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at        TIMESTAMPTZ,


    -- Prevent double-booking at DB level (Wander-SoR only)
    EXCLUDE USING GIST (unit_id WITH =, stay_range WITH &&) WHERE (status = 'CONFIRMED')
);


CREATE INDEX idx_booking_unit ON booking(unit_id, status);
CREATE INDEX idx_booking_guest ON booking(guest_id);
CREATE INDEX idx_booking_dates ON booking(check_in, check_out);
CREATE INDEX idx_booking_ubr ON booking(ubr);
booking_stub
Lightweight record for bookings where external PMS is System of Record.


CREATE TABLE booking_stub (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    ubr                 VARCHAR(100) UNIQUE NOT NULL,


    unit_id             UUID NOT NULL,
    guest_id            UUID NOT NULL,


    check_in            DATE NOT NULL,
    check_out           DATE NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'CONFIRMED',


    -- External SoR metadata
    pms_type            VARCHAR(30) NOT NULL,       -- GUESTY, HOSTAWAY, etc.
    pms_confirmation_id VARCHAR(100) NOT NULL,      -- Their record locator
    pms_reservation_id  VARCHAR(100),               -- Their internal ID


    payment_intent_id   VARCHAR(100),


    idempotency_key     VARCHAR(100) UNIQUE,


    confirmed_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at        TIMESTAMPTZ


    -- No EXCLUDE constraint: we trust PMS to manage availability
);


CREATE INDEX idx_stub_unit ON booking_stub(unit_id, status);
CREATE INDEX idx_stub_guest ON booking_stub(guest_id);
CREATE INDEX idx_stub_pms ON booking_stub(pms_type, pms_confirmation_id);
CREATE INDEX idx_stub_ubr ON booking_stub(ubr);
booking_guests
Guest counts by type.


CREATE TABLE booking_guests (
    booking_id      UUID NOT NULL REFERENCES booking(id) ON DELETE CASCADE,
    guest_kind      VARCHAR(20) NOT NULL,  -- adult, child, infant, pet
    count           INT NOT NULL DEFAULT 1,
    PRIMARY KEY (booking_id, guest_kind)
);
booking_price
Full line-item breakdown, frozen at confirmation.


CREATE TABLE booking_price (
    booking_id              UUID PRIMARY KEY REFERENCES booking(id),


    currency                CHAR(3) NOT NULL DEFAULT 'USD',


    nightly_rates           JSONB NOT NULL,
    -- [{"date": "2025-03-15", "cents": 45000}, ...]


    subtotal_cents          INT NOT NULL,


    fees                    JSONB NOT NULL,
    -- [{"kind": "cleaning", "label": "Cleaning fee", "cents": 25000}, ...]


    fees_total_cents        INT NOT NULL,


    taxes                   JSONB NOT NULL,
    -- [{"kind": "occupancy", "label": "Occupancy Tax", "rate": 0.12, "cents": 33480}, ...]


    taxes_total_cents       INT NOT NULL,


    discounts               JSONB,
    -- [{"kind": "loyalty", "cents": 5000}, ...]


    discounts_total_cents   INT NOT NULL DEFAULT 0,


    total_cents             INT NOT NULL,


    pms_quote_snapshot      JSONB   -- For stubs: the PMS's original quote
);
booking_ubr
UBR with signatures and PMS acceptance.


CREATE TABLE booking_ubr (
    booking_id          UUID PRIMARY KEY REFERENCES booking(id),
    ubr                 VARCHAR(100) UNIQUE NOT NULL,


    wander_signature    BYTEA NOT NULL,
    signed_at           TIMESTAMPTZ NOT NULL,


    pms_confirmation_id VARCHAR(100),  -- For stubs
    pms_accepted_at     TIMESTAMPTZ,
    pms_snapshot        JSONB
);
booking_cancellation
Cancellation details.


CREATE TABLE booking_cancellation (
    booking_id          UUID PRIMARY KEY REFERENCES booking(id),
    cancelled_by        VARCHAR(30) NOT NULL,  -- guest, admin, pms, system
    reason              VARCHAR(50) NOT NULL,
    reason_details      TEXT,
    refund_amount_cents INT,
    penalty_cents       INT,
    cancelled_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

________________


Current State Analysis
Existing Tables
v2Booking (74 columns)
CREATE TABLE public."v2Booking" (
    id text NOT NULL,
    channel public."v2BookingChannel" DEFAULT 'WANDER_APPS' NOT NULL,
    status public."v2BookingStatus" DEFAULT 'INQUIRY' NOT NULL,
    "primaryGuestId" text NOT NULL,


    -- PMS IDs (should be in separate mapping table)
    "guestyId" text,
    "channexId" text,
    "pmsReservationId" text,


    -- Core booking data
    "checkInDate" timestamp(3) NOT NULL,
    "checkOutDate" timestamp(3) NOT NULL,
    "totalNights" integer NOT NULL,
    "unitId" text NOT NULL,


    -- Payment (should be in Payments Service)
    "paymentId" text,
    "payoutId" text,


    -- Event tracking flags (should be event log)
    "sentRadioConfirmedEvent" boolean DEFAULT false NOT NULL,
    "sentCheckOutEvent" boolean DEFAULT false NOT NULL,
    -- ... 20+ more event flags


    -- UTM tracking (should be separate table)
    utm_campaign text,
    utm_source text,
    -- ... more UTM fields
);

Issues:


* 74 columns mixing concerns: booking, payment, tracking, messaging[z][aa]
* PMS IDs scattered (should be normalized)
* Event sent flags (should be event service)
* UTM tracking embedded (should be separate)
* Payment data (should be Payments Service)


________________


Migration Strategy
Phase 1: Create Tables
1. Create new tables in Booking Service database
2. Determine SoR for each unit from Property Service
Phase 2: Backfill
Route existing bookings to correct table based on unit's SoR:


-- Migrate to booking (Wander-SoR units)
INSERT INTO booking (id, ubr, unit_id, guest_id, check_in, check_out, status, confirmed_at)
SELECT
    b.id::uuid,
    'UBR-' || UPPER(SUBSTRING(b.id, 1, 20)) as ubr,
    b."unitId"::uuid as unit_id,
    b."primaryGuestId"::uuid as guest_id,
    b."checkInDate"::date as check_in,
    b."checkOutDate"::date as check_out,
    CASE b.status
        WHEN 'CONFIRMED' THEN 'CONFIRMED'
        WHEN 'COMPLETED' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
        ELSE 'CONFIRMED'
    END as status,
    b."confirmedAt" as confirmed_at
FROM public."v2Booking" b
JOIN units u ON b."unitId" = u.id
WHERE u.system_of_record = 'WANDER';


-- Migrate to booking_stub (External-SoR units)
INSERT INTO booking_stub (id, ubr, unit_id, guest_id, check_in, check_out, status,
                          pms_type, pms_confirmation_id, confirmed_at)
SELECT
    b.id::uuid,
    'UBR-' || UPPER(SUBSTRING(b.id, 1, 20)) as ubr,
    b."unitId"::uuid as unit_id,
    b."primaryGuestId"::uuid as guest_id,
    b."checkInDate"::date as check_in,
    b."checkOutDate"::date as check_out,
    CASE b.status
        WHEN 'CONFIRMED' THEN 'CONFIRMED'
        WHEN 'COMPLETED' THEN 'COMPLETED'
        WHEN 'CANCELLED' THEN 'CANCELLED'
        ELSE 'CONFIRMED'
    END as status,
    u.pms_type,
    COALESCE(b."guestyId", b."channexId", b."pmsReservationId") as pms_confirmation_id,
    b."confirmedAt" as confirmed_at
FROM public."v2Booking" b
JOIN units u ON b."unitId" = u.id
WHERE u.system_of_record != 'WANDER';
Phase 3: Dual-Write Period
1. Feature flag to read from new tables
2. Gradual rollout with comparison logging
3. Monitor for discrepancies
Phase 4: Cutover
1. Switch writes to new tables as primary
2. Stop writing to old tables
3. Archive v2Booking data
Rollback Plan
1. Feature flags control read/write paths
2. Dual-write maintained for 30 days
3. Backfill script can restore from new → old
4. Old tables retained (read-only) for 90 days


________________


UBR Generation
Every confirmed booking gets a Universal Booking Reference.[ab][ac][ad]


def generate_ubr(property_id: str, check_in: str, check_out: str) -> str:
    timestamp = int(time.time() * 1000)
    nonce = os.urandom(8)


    payload = f"{property_id}|{check_in}|{check_out}|{timestamp}"
    signature = hmac.new(config.ubr_secret, payload.encode(), hashlib.sha256).digest()


    domain = b'\x00\x01'  # Wander = 0x0001
    raw = domain + timestamp.to_bytes(8, 'big') + nonce + signature[:16]


    return f"UBR-{base58.b58encode(raw).decode()}"

________________


Idempotency
Every request requires idempotency_key.


async def create_booking(request: BookingRequest) -> Booking:
    # Check for existing booking with this key
    existing = await db.booking.find_by_idempotency_key(request.idempotency_key)
    if existing:
        return existing  # Return same result


    # Check for existing payment intent (3DS retry case)
    existing_intent = await payments.get_intent_by_idempotency_key(
        f"{request.idempotency_key}_auth"
    )
    if existing_intent and existing_intent.status == "requires_action":
        # Still waiting for 3DS - return client_secret again
        return BookingPending(
            requires_action=True,
            payment_intent_id=existing_intent.id,
            client_secret=existing_intent.client_secret
        )


    # Proceed with new booking (or continue after 3DS if intent is authorized)
    ...

Stripe handles authorization expiry (typically 7 days). No cleanup job needed.


________________


Booking States[ae][af][ag][ah]
Status
	Description
	CONFIRMED
	Booking complete
	CANCELLED
	Cancelled
	COMPLETED
	Stay finished
	

Either the transaction succeeds and it's CONFIRMED, or it fails and there's no booking record.


________________


SLOs
Operation
	p95 Latency
	POST /bookings
	< 3s
	POST /bookings (with 3DS)
	< 3s
	GET /bookings/{id}
	< 50ms
	POST /cancel
	< 2s
	GET /bookings (list)
	< 100ms
	

Metric
	Target
	Success rate
	> 99%
	Double-booking rate
	< 0.01%
	

________________


Test Scenarios
test_cases:
  - name: successful_booking
    endpoint: POST /bookings
    setup:
      - property exists and is bookable
      - dates 2025-06-01 to 2025-06-04 are available
      - valid payment method
    request:
      body:
        idempotency_key: "test_book_001"
        property_id: "{{property_id}}"
        check_in: "2025-06-01"
        check_out: "2025-06-04"
        guest_id: "{{guest_id}}"
        guests:
          adults: 2
        payment_method_id: "{{payment_method_id}}"
    expected:
      status: 201
      body:
        status: "CONFIRMED"
        ubr: "^UBR-"


  - name: idempotent_booking_replay
    endpoint: POST /bookings
    setup:
      - booking already exists with idempotency_key "test_book_001"
    request:
      body:
        idempotency_key: "test_book_001"
    expected:
      status: 200
      body:
        id: "{{existing_booking_id}}"


  - name: booking_unavailable_dates
    endpoint: POST /bookings
    setup:
      - dates 2025-06-01 to 2025-06-04 are blocked
    request:
      body:
        idempotency_key: "test_book_002"
        check_in: "2025-06-01"
        check_out: "2025-06-04"
    expected:
      status: 409
      body:
        error:
          code: "DATES_UNAVAILABLE"


  - name: cancel_booking_full_refund
    endpoint: POST /bookings/{booking_id}/cancel
    setup:
      - booking exists with flexible policy
      - check_in is 7 days away (full refund eligible)
    request:
      body:
        reason: "Changed plans"
        actor:
          kind: "user"
          id: "{{guest_id}}"
    expected:
      status: 200
      body:
        status: "CANCELLED"
        cancellation:
          refund_percent: 100

________________


Dependencies
Upstream (writes to Booking)
* Search/Frontend — Creates bookings
* Sync Service — Creates booking_stubs for external-SoR units
Downstream (reads from Booking)
* Availability Service — Receives acquire/release calls
* Payments Service — Invoices and payments
* Event Service — Booking lifecycle events


________________


Data Volume Estimates
Table
	Current Rows (Est.)
	New Table
	Projected Rows
	v2Booking
	~100K
	booking + booking_stub
	~100K (split by SoR)
	v2BookingDays
	~500K
	booking_price
	~100K (aggregated)
	v2BookingFee
	~200K
	booking_price
	(merged into above)
	

Schema reduction: 74 columns → ~15 columns per table + normalized pricing




Property
Property Service
Service Owner: TBD Base URL: /api/v1/properties Source of Truth: Property content, overrides, fees, policies, documents Migration Status: Not Started


Working doc: https://linear.app/wander/document/property-service-high-level-documentation-0b9b7ceea898 


________________


Overview
Content Management System for property data. Manages the canonical property record with field-level override support, multi-source tracking, and conflict resolution.


Property is the reference data backbone. Other services read from it. It handles the complexity of data coming from multiple sources (PMS, PM edits, Wander overrides) and presenting a clean, effective view to consumers.
System Dependencies
Dependency
	Role
	PostgreSQL
	Property records, overrides, documents metadata
	Redis
	Read-through cache for effective data
	R2
	Document storage (contracts, PDFs)
	Event Service
	Emit property events
	Sync Service
	Receives sync requests
	Responsibilities
* Property records (base data from SoR)
* Field-level overrides (diffs, not mutations)
* Fees (cleaning, pet, damage waiver, etc.)
* Policies (check-in/out, cancellation, house rules)
* Documents (contracts, welcome PDFs, vendor contacts)
* PMS mappings and "also seen in" references
* Cache and sync operations
Does NOT Own
Concern
	Owner
	Availability calendars
	Availability Service
	Nightly rates
	Pricing Service
	Bookings
	Booking Service
	Payment processing
	Payments Service
	PMS communication
	Sync Service + PMS Adapters
	Guest profiles
	Guest Service
	Actual file storage
	S3[ai][aj] (Property stores metadata)
	

________________


Core Concepts
Effective Data
What consumers see. Computed as: base data + overrides applied.


effective[field] = override[field].to_value ?? base[field]
Override Model
An override is a diff, not a mutation:


{
  "field": "short_description",
  "from_value": "Cozy mountain cabin",
  "to_value": "Stunning mountain retreat with panoramic views",
  "actor_kind": "pm",
  "actor_id": "user_abc",
  "priority": 20,
  "created_at": "2025-03-01T10:00:00Z"
}

* from_value: "X" — Only apply if base equals X
* from_value: null — Always apply regardless of base
Override Hierarchy
Source
	Priority
	Description
	PMS
	10
	System of Record (base data)
	PM
	20
	Property Manager override
	Wander Team
	30
	Wander ops/support
	Admin
	40
	Wander executive override
[ak][al]	


Higher priority wins.
Conflict Resolution
When sync updates base data and override from_value doesn't match:


Policy
	Behavior
	KEEP_OVERRIDE
	Keep override, update from_value to match new base
	ACCEPT_NEW
	Drop override, use new base value
	ASK
	Create conflict todo, keep override until resolved
	

________________


API Reference
GET /properties/{property_id}
Get effective property data (base + overrides applied).


Response (200)


{
  "id": "prop_abc123",
  "organization_id": "org_xyz",
  "name": "Sunset Retreat",
  "slug": "sunset-retreat",
  "short_description": "Stunning mountain retreat with panoramic views",
  "property_type": "SINGLE_FAMILY",
  "management_type": "BRANDED",
  "bedrooms": 4,
  "bathrooms": 3.5,
  "max_occupancy": 10,
  "address": {
    "street": "123 Mountain Road",
    "city": "Aspen",
    "state": "CO",
    "postal_code": "81611",
    "country": "US",
    "coordinates": { "lat": 39.1911, "lng": -106.8175 }
  },
  "timezone": "America/Denver",
  "status": "LIVE",
  "is_bookable": true,
  "sor": { "pms_type": "GUESTY", "external_id": "guesty_123" },
  "has_overrides": true,
  "has_conflicts": false
}

________________


GET /properties
Search and list properties.


Request Parameters


Parameter
	Type
	Description
	organization_id
	uuid
	Filter by PM organization
	city
	string
	Filter by city
	state
	string
	Filter by state
	status
	string
	ONBOARDING, LIVE, PAUSED, ARCHIVED
	is_bookable
	boolean
	Filter bookable
	management_type
	string
	OWNED, OPERATED, BRANDED
	pms_type
	string
	Filter by PMS
	has_conflicts
	boolean
	Filter with pending conflicts
	q
	string
	Text search
	near
	string
	Geo search: "lat,lng,radius_km"
	limit
	int
	Max results (default 50)
	cursor
	string
	Pagination cursor
	

________________


POST /properties/{property_id}/overrides
Set an override for a field.


Request


{
  "field": "short_description",
  "from_value": "Cozy mountain cabin",
  "to_value": "Stunning mountain retreat",
  "note": "PM requested more appealing description"
}

For "always override": set from_value: null.


Error Responses


Status
	Code
	Condition
	400
	INVALID_FIELD
	Invalid field name
	403
	INSUFFICIENT_PRIORITY
	Lower priority override exists
	409
	OVERRIDE_EXISTS
	Override already exists at same priority
	

________________


GET /properties/{property_id}/overrides/conflicts
Get pending conflicts needing resolution.


________________


POST /properties/{property_id}/overrides/conflicts/{conflict_id}/resolve
Resolve a conflict.


Request


{ "resolution": "KEEP_OVERRIDE" }

Options: KEEP_OVERRIDE, ACCEPT_NEW, NEW_OVERRIDE


________________


GET /properties/{property_id}/fees
Get property fees.


Response


{
  "property_id": "prop_abc123",
  "currency": "USD",
  "fees": [
    {
      "kind": "cleaning",
      "label": "Cleaning Fee",
      "amount_cents": 25000,
      "per": "stay",
      "required": true
    },
    {
      "kind": "pet",
      "label": "Pet Fee",
      "amount_cents": 15000,
      "per": "stay",
      "required": false,
      "conditions": { "max_pets": 2 }
    }
  ]
}

________________


GET /properties/{property_id}/policies
Get property policies (check-in/out times, cancellation, pets, etc.).


________________


GET /properties/{property_id}/sources
Get System of Record and "also seen in" sources.


________________


POST /properties/{property_id}/cache/flush
Flush cached property data.[am][an][ao]


________________


POST /properties/{property_id}/sync
Enqueue property for sync with System of Record.


________________


Events
Event
	When
	Payload
	property.created
	New property
	{property_id, organization_id}
	property.updated
	Effective data changed
	{property_id, fields_changed}
	property.override_set
	Override added/modified
	{property_id, field, actor}
	property.override_conflict
	Sync found conflict
	{property_id, field, old_base, new_base}
	property.sync_requested
	Sync enqueued
	{property_id, priority}
	

________________


Data Model
property
Core property record (~45 columns).


CREATE TABLE property (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id     UUID NOT NULL,


    name                VARCHAR(255) NOT NULL,
    slug                VARCHAR(255) NOT NULL UNIQUE,


    property_type       VARCHAR(50) NOT NULL DEFAULT 'SINGLE_FAMILY',
    management_type     VARCHAR(30) NOT NULL DEFAULT 'OPERATED',


    bedrooms            DECIMAL(3,1) NOT NULL,
    bathrooms           DECIMAL(3,1) NOT NULL,
    beds                INTEGER NOT NULL,
    max_occupancy       INTEGER NOT NULL,
    square_feet         INTEGER,


    address_id          UUID NOT NULL REFERENCES property_address(id),
    timezone            VARCHAR(50) NOT NULL,


    status              VARCHAR(30) NOT NULL DEFAULT 'ONBOARDING',
    is_bookable         BOOLEAN NOT NULL DEFAULT false,
    is_listed           BOOLEAN NOT NULL DEFAULT false,


    sor_type            VARCHAR(30),
    sor_external_id     VARCHAR(255),


    short_description   TEXT,
    long_description    TEXT,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_property_org ON property(organization_id);
CREATE INDEX idx_property_slug ON property(slug);
CREATE INDEX idx_property_status ON property(status);
property_address
CREATE TABLE property_address (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    street              VARCHAR(255) NOT NULL,
    city                VARCHAR(100) NOT NULL,
    state               VARCHAR(50) NOT NULL,
    postal_code         VARCHAR(20) NOT NULL,
    country             VARCHAR(2) NOT NULL DEFAULT 'US',
    latitude            DECIMAL(10, 7),
    longitude           DECIMAL(10, 7),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_address_location ON property_address USING GIST (
    ST_MakePoint(longitude, latitude)
);
property_override
CREATE TABLE property_override (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         UUID NOT NULL REFERENCES property(id),
    field               VARCHAR(100) NOT NULL,
    from_value          JSONB,
    to_value            JSONB NOT NULL,
    priority            INTEGER NOT NULL,
    actor_kind          VARCHAR(20) NOT NULL,
    actor_id            UUID,
    note                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (property_id, field, priority)
);
property_override_conflict
CREATE TABLE property_override_conflict (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         UUID NOT NULL REFERENCES property(id),
    override_id         UUID NOT NULL REFERENCES property_override(id),
    field               VARCHAR(100) NOT NULL,
    old_base            JSONB NOT NULL,
    new_base            JSONB NOT NULL,
    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    resolution          VARCHAR(30),
    resolved_by         UUID,
    resolved_at         TIMESTAMPTZ,
    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
property_fee
CREATE TABLE property_fee (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         UUID NOT NULL REFERENCES property(id),
    fee_type            VARCHAR(50) NOT NULL,
    label               VARCHAR(255) NOT NULL,
    calculation_type    VARCHAR(30) NOT NULL,
    amount_cents        INTEGER NOT NULL,
    required            BOOLEAN NOT NULL DEFAULT true,
    conditions          JSONB,
    active              BOOLEAN NOT NULL DEFAULT true,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
property_policy
CREATE TABLE property_policy (
    property_id             UUID PRIMARY KEY REFERENCES property(id),
    check_in_time           TIME NOT NULL DEFAULT '16:00',
    check_out_time          TIME NOT NULL DEFAULT '10:00',
    min_nights              INTEGER NOT NULL DEFAULT 1,
    cancellation_policy_id  UUID,
    pets_allowed            BOOLEAN NOT NULL DEFAULT false,
    max_pets                INTEGER,
    events_allowed          VARCHAR(30) NOT NULL DEFAULT 'NOT_ALLOWED',
    smoking_allowed         BOOLEAN NOT NULL DEFAULT false,
    house_rules             TEXT,
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
property_external_id
PMS/OTA external ID mappings.


CREATE TABLE property_external_id (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id         UUID NOT NULL REFERENCES property(id),
    source              VARCHAR(50) NOT NULL,
    external_id         VARCHAR(255) NOT NULL,
    is_sor              BOOLEAN NOT NULL DEFAULT false,
    metadata            JSONB,
    synced_at           TIMESTAMPTZ,
    UNIQUE (source, external_id)
);
cancellation_policy
CREATE TABLE cancellation_policy (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                VARCHAR(100) NOT NULL,
    code                VARCHAR(50) NOT NULL UNIQUE,
    tiers               JSONB NOT NULL,
    is_default          BOOLEAN NOT NULL DEFAULT false,
    active              BOOLEAN NOT NULL DEFAULT true
);

________________


Current State Analysis
Unit Table (~110 columns)
Issues:


* ~110 columns mixing concerns
* Pricing data embedded (should be Pricing Service)
* PMS IDs scattered (should be normalized)
* Rating data embedded (should be separate)
* Onboarding state mixed with core data


________________


Migration Strategy
Phase 1-2: Create Tables & Migrate Data
1. Create new tables
2. Migrate addresses, core properties, external IDs
3. Migrate amenities, policies, fees, ratings
Phase 3-4: Read/Write Migration
1. Feature flag to read from new tables[ap][aq][ar]
2. Switch writes to new tables
3. Dual-write for rollback
Phase 5: Cleanup
1. Stop writing to Unit table
2. Archive old data
3. Remove pricing columns
Rollback Plan
1. Feature flags control read/write paths
2. Dual-write maintained for 30 days
3. Old Unit table retained (read-only) for 90 days


________________


SLOs
Operation
	p95 Latency
	GET /properties/{id}
	< 10ms (cached), < 50ms (uncached)
	GET /properties (search)
	< 200ms
	POST /overrides
	< 100ms
	POST /cache/flush
	< 50ms
	POST /sync
	< 100ms (enqueue only)
	

Metric
	Target
	Cache hit rate
	> 90%
	Conflict resolution time
	< 24 hours
	

________________


Data Volume Estimates
Table
	Current
	New
	Notes
	Unit
	~500
	property ~500
	Core data
	UnitAddress
	~500
	property_address ~500
	Addresses
	UnitAmenity
	~5K
	property_amenity ~5K
	Amenities
	Fee
	~2K
	property_fee ~2K
	Fees
	

Schema reduction: ~110 columns → ~45 core columns + normalized tables




Payment
Payments Service
Service Owner: TBD Base URL: /api/v1/payments Source of Truth: Invoices, payments (mirror of Stripe), payouts Migration Status: Not Started


________________


Overview
Stripe orchestration and financial ledger mirror. Manages invoices, processes payments, handles refunds, batches owner payouts.


Stripe is the ledger. We maintain a read-optimized mirror for queries and reconciliation. We don't reinvent financial infrastructure.
System Dependencies
Dependency
	Role
	Stripe
	Payment processing, transfers, refunds
	PostgreSQL
	Invoice, payment, payout records
	Event Service
	Emit payment events
	Booking Service
	Booking context for invoices
	Property Service
	Owner IDs for payouts
	Responsibilities
* Invoices and line items
* Payment records (mirror of Stripe charges)
* Refund records
* Payout snapshots (immutable, hash-chained)
* Stripe object mirror (for reconciliation)
Does NOT Own
Concern
	Owner
	Pricing calculations
	Pricing Service
	Booking state
	Booking Service
	Owner/property relationships
	Property Service
	Tax calculation
	External tax service or Stripe Tax
	Currency conversion
	Stripe
	Dispute handling
	Manual + Stripe Dashboard
	Called By
Service
	Operations
	Booking
	create invoice, record payment, process refund
	Admin
	trigger payout run, view reconciliation
	Owner Portal
	get payout history
	

________________


API Reference
POST /invoices
Create an invoice for a booking.


Request


{
  "booking_id": "booking_12345",
  "customer_id": "user_abc",
  "items": [
    { "type": "NIGHTLY_RATE", "description": "5 nights @ $450", "cents": 225000 },
    { "type": "FEE", "description": "Cleaning fee", "cents": 25000 },
    { "type": "FEE", "description": "Pet fee", "cents": 15000 },
    { "type": "TAX", "description": "Occupancy Tax (12%)", "cents": 31800 }
  ],
  "due_date": "2025-03-10"
}

Field
	Type
	Required
	Description
	booking_id
	uuid
	No
	Associated booking
	customer_id
	uuid
	Yes
	Customer identifier
	items
	array
	Yes
	Line items
	due_date
	date
	No
	Payment due date
	

Item Types: NIGHTLY_RATE, FEE, TAX, DISCOUNT, ADJUSTMENT


Response (201)


{
  "id": "inv_abc123",
  "invoice_number": "INV-2025-00123",
  "status": "OPEN",
  "subtotal_cents": 265000,
  "discount_cents": 0,
  "tax_cents": 31800,
  "total_cents": 296800,
  "due_cents": 296800,
  "stripe_payment_intent_id": "pi_xyz",
  "payment_url": "https://checkout.stripe.com/...",
  "created_at": "2025-03-10T14:00:00Z"
}

Invoice Statuses: DRAFT, OPEN, PAID, PARTIALLY_PAID, VOID, REFUNDED


Errors


Status
	Code
	Condition
	400
	INVALID_ITEMS
	Invalid items or amounts
	409
	INVOICE_EXISTS
	Invoice already exists for booking
	

________________


GET /invoices/{invoice_id}
Get invoice details.


Response (200)


{
  "id": "inv_abc123",
  "invoice_number": "INV-2025-00123",
  "booking_id": "booking_12345",
  "customer_id": "user_abc",
  "status": "PAID",
  "items": [
    { "type": "NIGHTLY_RATE", "description": "5 nights @ $450", "cents": 225000 },
    { "type": "FEE", "description": "Cleaning fee", "cents": 25000 }
  ],
  "subtotal_cents": 265000,
  "discount_cents": 0,
  "tax_cents": 31800,
  "total_cents": 296800,
  "paid_cents": 296800,
  "refunded_cents": 0,
  "due_cents": 0,
  "currency": "USD",
  "stripe_payment_intent_id": "pi_xyz",
  "created_at": "2025-03-10T14:00:00Z",
  "paid_at": "2025-03-10T14:05:00Z"
}

________________


POST /invoices/{invoice_id}/pay
Record a payment against an invoice. Called after Stripe confirms payment.


Request


{
  "stripe_payment_intent_id": "pi_xyz",
  "amount_cents": 296800
}

Response (200)


{
  "id": "inv_abc123",
  "status": "PAID",
  "paid_cents": 296800,
  "due_cents": 0,
  "paid_at": "2025-03-10T14:05:00Z"
}

If amount_cents < due_cents, status becomes PARTIALLY_PAID.


________________


POST /invoices/{invoice_id}/void
Void an unpaid invoice.


Request


{
  "reason": "Booking cancelled before payment"
}

Response (200)


{
  "id": "inv_abc123",
  "status": "VOID",
  "voided_at": "2025-03-10T16:00:00Z"
}

________________


POST /refunds
Process a refund through Stripe.


Request


{
  "booking_id": "booking_12345",
  "amount_cents": 200000,
  "reason": "CANCELLATION",
  "note": "Cancelled per flexible policy"
}

Reason Types: CANCELLATION, PRICE_ADJUSTMENT, DUPLICATE, GUEST_COMPLAINT, OTHER


Response (201)


{
  "id": "refund_xyz",
  "invoice_id": "inv_abc123",
  "amount_cents": 200000,
  "status": "SUCCEEDED",
  "stripe_refund_id": "re_abc",
  "created_at": "2025-03-12T10:00:00Z"
}

Errors


Status
	Code
	Condition
	400
	EXCEEDS_PAID
	Amount exceeds paid amount
	402
	REFUND_FAILED
	Stripe refund failed
	404
	NOT_FOUND
	Booking/invoice not found
	

________________


GET /refunds/{refund_id}
Get refund status.


Response (200)


{
  "id": "refund_xyz",
  "invoice_id": "inv_abc123",
  "booking_id": "booking_12345",
  "amount_cents": 200000,
  "reason": "CANCELLATION",
  "note": "Cancelled per flexible policy",
  "status": "SUCCEEDED",
  "stripe_refund_id": "re_abc",
  "created_at": "2025-03-12T10:00:00Z",
  "completed_at": "2025-03-12T10:00:05Z"
}

________________


POST /payouts/run
Trigger batched payouts to owners. Admin only.


Request


{
  "cycle": "2025-W10",
  "owner_ids": ["owner_abc", "owner_def"],
  "dry_run": false
}

If owner_ids omitted, processes all owners with pending payouts.


Response (200)


{
  "cycle": "2025-W10",
  "payout_snapshots": [
    {
      "id": "payout_snap_123",
      "owner_id": "owner_abc",
      "booking_count": 5,
      "total_cents": 1150000,
      "status": "PENDING"
    },
    {
      "id": "payout_snap_124",
      "owner_id": "owner_def",
      "booking_count": 3,
      "total_cents": 875000,
      "status": "PENDING"
    }
  ],
  "total_payout_cents": 2025000
}

________________


GET /payouts/{payout_id}
Get full payout snapshot with booking breakdown.


Response (200)


{
  "id": "payout_snap_123",
  "owner_id": "owner_abc",
  "period_start": "2025-03-03",
  "period_end": "2025-03-09",


  "bookings": [
    {
      "booking_id": "booking_123",
      "property_name": "Sunset Retreat",
      "guest_name": "John D.",
      "check_in": "2025-03-05",
      "check_out": "2025-03-08",
      "gross_cents": 135000,
      "platform_fee_cents": 20250,
      "net_cents": 114750
    }
  ],


  "totals": {
    "booking_payouts_cents": 1200000,
    "adjustments_cents": 0,
    "capex_repaid_cents": -50000,
    "total_cents": 1150000
  },


  "status": "PAID",
  "stripe_transfer_ids": ["tr_abc", "tr_def"],
  "paid_at": "2025-03-10T12:00:00Z",


  "hash": "a1b2c3d4...",
  "prev_hash": "z9y8x7w6..."
}

Payout Statuses: PENDING, PROCESSING, PAID, FAILED


________________


GET /owners/{owner_id}/payouts
List payout snapshots for an owner.


Request Parameters


Parameter
	Type
	Required
	Description
	owner_id
	uuid
	Yes
	Owner identifier
	status
	string
	No
	Filter by status
	limit
	int
	No
	Max results (default 20)
	cursor
	string
	No
	Pagination cursor
	

Response (200)


{
  "owner_id": "owner_abc",
  "payouts": [
    {
      "id": "payout_snap_123",
      "period_start": "2025-03-03",
      "period_end": "2025-03-09",
      "booking_count": 5,
      "totals": {
        "booking_payouts_cents": 1200000,
        "adjustments_cents": 0,
        "capex_repaid_cents": -50000,
        "total_cents": 1150000
      },
      "status": "PAID",
      "paid_at": "2025-03-10T12:00:00Z"
    }
  ],
  "next_cursor": null
}

________________


GET /reconciliation/status
Get current reconciliation status. Admin only.


Response (200)


{
  "last_run": "2025-03-10T03:00:00Z",
  "status": "OK",
  "exceptions_pending": 0,
  "drift_amount_cents": 0,
  "next_run": "2025-03-11T03:00:00Z"
}

________________


GET /reconciliation/exceptions
List unresolved reconciliation exceptions. Admin only.


Response (200)


{
  "exceptions": [
    {
      "id": "exc_123",
      "type": "ORPHANED_CHARGE",
      "stripe_id": "ch_abc123",
      "amount_cents": 50000,
      "status": "PENDING",
      "detected_at": "2025-03-10T03:00:00Z"
    }
  ],
  "total": 1
}

Exception Types: ORPHANED_CHARGE, ORPHANED_REFUND, ORPHANED_TRANSFER, AMOUNT_MISMATCH


________________


POST /reconciliation/exceptions/{exception_id}/resolve
Resolve a reconciliation exception. Admin only.


Request


{
  "resolution": "IGNORED",
  "note": "Test charge, no action needed"
}

Resolution Types: RESOLVED, IGNORED


________________


Events
Event
	When
	Payload
	payment.invoice_created
	Invoice created
	{invoice_id, booking_id, total_cents}
	payment.succeeded
	Payment completed
	{invoice_id, amount_cents}
	payment.failed
	Payment failed
	{invoice_id, error}
	payment.refunded
	Refund processed
	{refund_id, invoice_id, amount_cents}
	payment.payout_created
	Payout snapshot created
	{payout_id, owner_id, total_cents}
	payment.payout_sent
	Payout transferred to owner
	{payout_id, owner_id, stripe_transfer_id}
	payment.reconciliation_mismatch
	Drift detected
	{exception_type, stripe_id, amount}
	

________________


Data Model
invoice
CREATE TABLE invoice (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number      VARCHAR(50) UNIQUE NOT NULL,


    booking_id          UUID,
    customer_id         UUID NOT NULL,
    organization_id     UUID NOT NULL,


    invoice_type        VARCHAR(30) NOT NULL DEFAULT 'BOOKING',
    -- BOOKING, ADDON, DAMAGE, ADJUSTMENT, SUBSCRIPTION


    status              VARCHAR(20) NOT NULL DEFAULT 'DRAFT',
    -- DRAFT, OPEN, PAID, PARTIALLY_PAID, VOID, REFUNDED


    currency            VARCHAR(3) NOT NULL DEFAULT 'USD',
    subtotal_cents      INTEGER NOT NULL DEFAULT 0,
    discount_cents      INTEGER NOT NULL DEFAULT 0,
    tax_cents           INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL DEFAULT 0,
    paid_cents          INTEGER NOT NULL DEFAULT 0,
    refunded_cents      INTEGER NOT NULL DEFAULT 0,
    due_cents           INTEGER NOT NULL DEFAULT 0,


    platform_fee_cents  INTEGER NOT NULL DEFAULT 0,
    platform_fee_rate   DECIMAL(5, 4),


    stripe_invoice_id           VARCHAR(100),
    stripe_payment_intent_id    VARCHAR(100),


    due_date            DATE,
    opened_at           TIMESTAMPTZ,
    paid_at             TIMESTAMPTZ,
    voided_at           TIMESTAMPTZ,


    memo                TEXT,
    metadata            JSONB DEFAULT '{}',


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT positive_amounts CHECK (
        subtotal_cents >= 0 AND
        total_cents >= 0 AND
        paid_cents >= 0 AND
        refunded_cents >= 0
    )
);


CREATE INDEX idx_invoice_booking ON invoice(booking_id);
CREATE INDEX idx_invoice_customer ON invoice(customer_id);
CREATE INDEX idx_invoice_status ON invoice(status);
CREATE INDEX idx_invoice_stripe ON invoice(stripe_payment_intent_id);
invoice_item
CREATE TABLE invoice_item (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id          UUID NOT NULL REFERENCES invoice(id) ON DELETE CASCADE,


    item_type           VARCHAR(50) NOT NULL,
    -- NIGHTLY_RATE, FEE, TAX, DISCOUNT, ADJUSTMENT


    description         VARCHAR(255) NOT NULL,
    quantity            INT NOT NULL DEFAULT 1,
    unit_cents          INT NOT NULL,
    total_cents         INT NOT NULL,


    metadata            JSONB,


    display_order       INT NOT NULL DEFAULT 0
);


CREATE INDEX idx_invoice_item ON invoice_item(invoice_id);
payment
CREATE TABLE payment (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id                  UUID NOT NULL REFERENCES invoice(id),


    amount_cents                INT NOT NULL,
    status                      VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, SUCCEEDED, FAILED, REFUNDED


    stripe_payment_intent_id    VARCHAR(100),
    stripe_charge_id            VARCHAR(100),
    stripe_payment_method_id    VARCHAR(100),


    failure_reason              TEXT,


    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at                TIMESTAMPTZ
);


CREATE INDEX idx_payment_invoice ON payment(invoice_id);
CREATE INDEX idx_payment_stripe ON payment(stripe_charge_id);
refund
CREATE TABLE refund (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id          UUID NOT NULL REFERENCES invoice(id),
    payment_id          UUID REFERENCES payment(id),


    amount_cents        INT NOT NULL,
    reason              VARCHAR(50) NOT NULL,
    note                TEXT,


    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, SUCCEEDED, FAILED


    stripe_refund_id    VARCHAR(100),
    failure_reason      TEXT,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at        TIMESTAMPTZ
);


CREATE INDEX idx_refund_invoice ON refund(invoice_id);
CREATE INDEX idx_refund_stripe ON refund(stripe_refund_id);
payout_snapshot
Immutable, hash-chained for auditability[as][at].


CREATE TABLE payout_snapshot (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id            UUID NOT NULL,
    organization_id     UUID NOT NULL,


    period_start        DATE NOT NULL,
    period_end          DATE NOT NULL,
    cycle_identifier    VARCHAR(20) NOT NULL,


    currency            VARCHAR(3) NOT NULL DEFAULT 'USD',
    booking_total_cents INTEGER NOT NULL DEFAULT 0,
    adjustments_cents   INTEGER NOT NULL DEFAULT 0,
    capex_deduction_cents INTEGER NOT NULL DEFAULT 0,
    total_cents         INTEGER NOT NULL,


    booking_count       INTEGER NOT NULL DEFAULT 0,
    bookings            JSONB NOT NULL,
    -- [{booking_id, property_name, guest_name, check_in, check_out, gross, fees, net}]


    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, PROCESSING, PAID, FAILED


    stripe_transfer_id  VARCHAR(255),


    paid_at             TIMESTAMPTZ,
    failed_at           TIMESTAMPTZ,
    failure_reason      TEXT,


    content_hash        VARCHAR(64) NOT NULL,
    prev_hash           VARCHAR(64),


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_payout_owner ON payout_snapshot(owner_id, period_start DESC);
CREATE INDEX idx_payout_cycle ON payout_snapshot(cycle_identifier);
CREATE INDEX idx_payout_status ON payout_snapshot(status);
payout_config
CREATE TABLE payout_config (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    organization_id     UUID NOT NULL,
    property_id         UUID,


    schedule            VARCHAR(30) NOT NULL DEFAULT 'BIWEEKLY',
    -- WEEKLY, BIWEEKLY, MONTHLY, MANUAL


    is_paused           BOOLEAN NOT NULL DEFAULT false,
    paused_at           TIMESTAMPTZ,
    paused_reason       TEXT,


    capex_deduction_type VARCHAR(30),
    capex_deduction_value DECIMAL(10, 2),


    new_host_hold_days  INTEGER DEFAULT 30,
    hold_release_at     TIMESTAMPTZ,


    payout_after_checkout BOOLEAN NOT NULL DEFAULT false,
    payout_epoch        TIMESTAMPTZ,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT unique_org_property UNIQUE (organization_id, property_id)
);


CREATE INDEX idx_payout_config_org ON payout_config(organization_id);
stripe_object
Mirror of Stripe objects for reconciliation.


CREATE TABLE stripe_object (
    stripe_id           VARCHAR(100) PRIMARY KEY,
    object_type         VARCHAR(50) NOT NULL,
    -- payment_intent, charge, refund, transfer, payout, balance_transaction


    payload             JSONB NOT NULL,


    event_id            VARCHAR(100),
    event_seq           BIGINT,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_stripe_type ON stripe_object(object_type, created_at);
CREATE INDEX idx_stripe_event ON stripe_object(event_id);
reconciliation_exception
CREATE TABLE reconciliation_exception (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exception_type  VARCHAR(50) NOT NULL,
    -- ORPHANED_CHARGE, ORPHANED_REFUND, ORPHANED_TRANSFER, AMOUNT_MISMATCH


    stripe_id       VARCHAR(100) NOT NULL,
    details         JSONB NOT NULL,


    status          VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, RESOLVED, IGNORED


    resolved_by     UUID,
    resolved_at     TIMESTAMPTZ,
    resolution_note TEXT,


    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_exception_status ON reconciliation_exception(status, created_at);

________________


Stripe Integration
Webhook Handler
Listen for Stripe events and update local state:


HANDLED_EVENTS = [
    'payment_intent.succeeded',
    'payment_intent.payment_failed',
    'charge.refunded',
    'transfer.created',
    'transfer.failed',
]


async def handle_stripe_webhook(event: StripeEvent):
    # Store raw event
    await db.stripe_object.upsert(
        stripe_id=event.data.object.id,
        object_type=event.data.object.object,
        payload=event.data.object,
        event_id=event.id,
        event_seq=event.created
    )


    match event.type:
        case 'payment_intent.succeeded':
            await handle_payment_succeeded(event.data.object)
        case 'payment_intent.payment_failed':
            await handle_payment_failed(event.data.object)
        case 'charge.refunded':
            await handle_refund_completed(event.data.object)
        case 'transfer.created':
            await handle_transfer_created(event.data.object)
        case 'transfer.failed':
            await handle_transfer_failed(event.data.object)




async def handle_payment_succeeded(payment_intent):
    invoice = await db.invoice.find(
        stripe_payment_intent_id=payment_intent.id
    )
    if not invoice:
        await create_exception('ORPHANED_PAYMENT', payment_intent)
        return


    await db.payment.create(
        invoice_id=invoice.id,
        amount_cents=payment_intent.amount,
        status='SUCCEEDED',
        stripe_payment_intent_id=payment_intent.id,
        stripe_charge_id=payment_intent.latest_charge,
        completed_at=now()
    )


    new_paid = invoice.paid_cents + payment_intent.amount
    new_status = 'PAID' if new_paid >= invoice.total_cents else 'PARTIALLY_PAID'


    await db.invoice.update(
        id=invoice.id,
        paid_cents=new_paid,
        status=new_status,
        paid_at=now() if new_status == 'PAID' else None
    )


    emit('payment.succeeded', entity_id=invoice.booking_id, context={
        'invoice_id': invoice.id,
        'amount_cents': payment_intent.amount
    })
Creating Payment Intent
When invoice is created:


async def create_invoice(request: CreateInvoiceRequest) -> Invoice:
    # Calculate totals
    subtotal = sum(item.cents for item in request.items if item.type != 'TAX')
    tax = sum(item.cents for item in request.items if item.type == 'TAX')
    total = subtotal + tax


    # Create Stripe PaymentIntent
    payment_intent = stripe.PaymentIntent.create(
        amount=total,
        currency='usd',
        metadata={
            'booking_id': request.booking_id,
            'customer_id': request.customer_id
        }
    )


    # Create local invoice
    invoice = await db.invoice.create(
        invoice_number=generate_invoice_number(),
        booking_id=request.booking_id,
        customer_id=request.customer_id,
        subtotal_cents=subtotal,
        tax_cents=tax,
        total_cents=total,
        status='OPEN',
        stripe_payment_intent_id=payment_intent.id,
        due_date=request.due_date
    )


    # Create line items
    for i, item in enumerate(request.items):
        await db.invoice_item.create(
            invoice_id=invoice.id,
            item_type=item.type,
            description=item.description,
            unit_cents=item.cents,
            total_cents=item.cents,
            display_order=i
        )


    emit('payment.invoice_created', entity_id=request.booking_id, context={
        'invoice_id': invoice.id,
        'total_cents': total
    })


    return invoice

________________


Payout Processing
Payout Run
async def run_payouts(cycle: str, owner_ids: list[str] = None, dry_run: bool = False):
    period_start, period_end = parse_cycle(cycle)


    query = """
        SELECT
            p.owner_id,
            b.id as booking_id,
            b.property_id,
            inv.total_cents as gross_cents,
            (inv.subtotal_cents * 0.15)::int as platform_fee_cents
        FROM booking b
        JOIN property p ON p.id = b.property_id
        JOIN invoice inv ON inv.booking_id = b.id
        WHERE b.check_out >= $1 AND b.check_out < $2
        AND b.status = 'COMPLETED'
        AND inv.status = 'PAID'
        AND NOT EXISTS (
            SELECT 1 FROM payout_snapshot ps
            WHERE b.id = ANY(ps.booking_ids)
        )
    """


    if owner_ids:
        query += " AND p.owner_id = ANY($3)"


    bookings = await db.query(query, period_start, period_end, owner_ids)


    by_owner = defaultdict(list)
    for b in bookings:
        by_owner[b.owner_id].append(b)


    snapshots = []


    for owner_id, owner_bookings in by_owner.items():
        prev = await db.payout_snapshot.find_latest(owner_id=owner_id)


        booking_payouts = sum(b.gross_cents - b.platform_fee_cents for b in owner_bookings)
        capex = await get_capex_repayment(owner_id, booking_payouts)
        total = booking_payouts + capex


        snapshot_data = {
            'owner_id': owner_id,
            'period_start': period_start,
            'period_end': period_end,
            'booking_ids': [b.booking_id for b in owner_bookings],
            'booking_details': [
                {
                    'booking_id': b.booking_id,
                    'property_id': b.property_id,
                    'gross_cents': b.gross_cents,
                    'platform_fee_cents': b.platform_fee_cents,
                    'net_cents': b.gross_cents - b.platform_fee_cents
                }
                for b in owner_bookings
            ],
            'totals': {
                'booking_payouts_cents': booking_payouts,
                'adjustments_cents': 0,
                'capex_repaid_cents': capex,
                'total_cents': total
            }
        }


        content = json.dumps(snapshot_data, sort_keys=True)
        hash = hashlib.sha256(content.encode()).hexdigest()


        if dry_run:
            snapshots.append({**snapshot_data, 'hash': hash, 'status': 'DRY_RUN'})
            continue


        snapshot = await db.payout_snapshot.create(
            **snapshot_data,
            hash=hash,
            prev_hash=prev.hash if prev else None,
            status='PENDING'
        )


        transfer = stripe.Transfer.create(
            amount=total,
            currency='usd',
            destination=await get_owner_stripe_account(owner_id),
            metadata={'payout_snapshot_id': snapshot.id}
        )


        await db.payout_snapshot.update(
            id=snapshot.id,
            stripe_transfer_ids=[transfer.id],
            status='PROCESSING'
        )


        snapshots.append(snapshot)


        emit('payment.payout_created', entity_id=owner_id, context={
            'payout_id': snapshot.id,
            'total_cents': total,
            'booking_count': len(owner_bookings)
        })


    return snapshots

________________


Reconciliation
Nightly Job
async def reconcile():
    """
    Compare Stripe balance transactions with our records.
    Run nightly, looking at past 48 hours.
    """
    cutoff = now() - timedelta(hours=48)


    stripe_txns = stripe.BalanceTransaction.list(
        created={'gte': int(cutoff.timestamp())},
        limit=100
    )


    exceptions = []


    for txn in stripe_txns.auto_paging_iter():
        await db.stripe_object.upsert(
            stripe_id=txn.id,
            object_type='balance_transaction',
            payload=txn,
            event_seq=txn.created
        )


        if txn.type == 'charge':
            payment = await db.payment.find(stripe_charge_id=txn.source)
            if not payment:
                exceptions.append({
                    'type': 'ORPHANED_CHARGE',
                    'stripe_id': txn.source,
                    'amount': txn.amount
                })
            elif payment.amount_cents != txn.amount:
                exceptions.append({
                    'type': 'AMOUNT_MISMATCH',
                    'stripe_id': txn.source,
                    'stripe_amount': txn.amount,
                    'our_amount': payment.amount_cents
                })


        elif txn.type == 'refund':
            refund = await db.refund.find(stripe_refund_id=txn.source)
            if not refund:
                exceptions.append({
                    'type': 'ORPHANED_REFUND',
                    'stripe_id': txn.source,
                    'amount': txn.amount
                })


        elif txn.type == 'transfer':
            snapshot = await db.payout_snapshot.find(
                stripe_transfer_ids={'contains': txn.source}
            )
            if not snapshot:
                exceptions.append({
                    'type': 'ORPHANED_TRANSFER',
                    'stripe_id': txn.source,
                    'amount': txn.amount
                })


    for exc in exceptions:
        await db.reconciliation_exception.create(
            exception_type=exc['type'],
            stripe_id=exc['stripe_id'],
            details=exc,
            status='PENDING'
        )
        emit('payment.reconciliation_mismatch', context=exc)


    if exceptions:
        alert('reconciliation_exceptions', count=len(exceptions))


    return {'exceptions': len(exceptions), 'processed': len(list(stripe_txns))}

________________


SLOs
Operation
	p95 Latency
	POST /invoices
	< 500ms (includes Stripe call)
	POST /invoices/{id}/pay
	< 100ms (webhook already confirmed)
	POST /refunds
	< 1s (includes Stripe call)
	GET /owners/{id}/payouts
	< 50ms
	Webhook processing
	< 200ms
	

Metric
	Target
	Reconciliation drift
	$0
	Webhook processing success
	> 99.9%
	Payout success rate
	> 99.5%
	

________________


Implementation Notes
Idempotency
All Stripe operations use idempotency keys:


stripe.PaymentIntent.create(
    amount=total,
    currency='usd',
    idempotency_key=f"invoice_{invoice.id}"
)
Retry Logic
Stripe calls retry with exponential backoff:


* Initial: 1s
* Max: 60s
* Max attempts: 5
Payout Immutability
Once a payout_snapshot is created:


* Numbers never change
* Hash chain prevents tampering
* Owner sees exactly what was calculated at payout time


If adjustment needed after payout, create a new adjustment record in next cycle — don't mutate history.


________________


Current State Analysis
Existing Tables
Table
	Columns
	Status
	OsInvoice
	44
	Good structure, HNHL fields mixed in
	OsInvoiceItem
	27
	Good line item structure
	OsPayment
	18
	Good, Stripe integration clear
	OsRefund
	17
	Good structure
	Payout
	12
	Missing owner reference, JSONB breakdown
	PayoutConfig
	15
	Good config structure
	Payment
	20
	Legacy, being replaced
	

________________


Migration Strategy
Phase 1-2: Create Tables & Migrate Data
1. Create new tables in Payments Service database
2. Migrate invoices, invoice items
3. Migrate payments, refunds
Phase 3-4: Payout Migration
1. Migrate payout snapshots
2. Migrate payout configs
3. Build hash chain for existing payouts
Phase 5-6: Read/Write Migration
1. Feature flag to read from new tables
2. Compare totals between old and new
3. Switch writes to new tables
4. Update Stripe webhooks
Phase 7: Cleanup
1. Stop writing to old tables
2. Archive old data
3. Mark migratedToV3 = true
Rollback Plan
1. Feature flags control read/write paths
2. Dual-write maintained for 30 days
3. Stripe is source of truth — can always reconcile
4. Old tables retained (read-only) for 90 days


________________


Data Volume Estimates
Table
	Current
	New
	OsInvoice
	~100K
	invoice ~100K
	OsInvoiceItem
	~500K
	invoice_item ~500K
	OsPayment
	~100K
	payment ~100K
	OsRefund
	~15K
	refund ~15K
	Payout
	~5K
	payout_snapshot ~5K
	PayoutConfig
	~500
	payout_config ~500
	

________________


Test Scenarios
test_cases:
  - name: create_invoice
    endpoint: POST /invoices
    request:
      body:
        booking_id: "{{booking_id}}"
        customer_id: "{{customer_id}}"
        items:
          - type: "NIGHTLY_RATE"
            description: "3 nights"
            cents: 120000
          - type: "FEE"
            description: "Cleaning"
            cents: 25000
    expected:
      status: 201
      body:
        status: "OPEN"
        total_cents: 145000


  - name: record_payment
    endpoint: POST /invoices/{invoice_id}/pay
    setup:
      - invoice exists with total 145000
      - Stripe payment succeeded
    request:
      body:
        stripe_payment_intent_id: "pi_test"
        amount_cents: 145000
    expected:
      status: 200
      body:
        status: "PAID"
        due_cents: 0


  - name: partial_payment
    endpoint: POST /invoices/{invoice_id}/pay
    setup:
      - invoice exists with total 145000
    request:
      body:
        amount_cents: 50000
    expected:
      status: 200
      body:
        status: "PARTIALLY_PAID"
        due_cents: 95000


  - name: process_refund
    endpoint: POST /refunds
    setup:
      - invoice is paid
    request:
      body:
        booking_id: "{{booking_id}}"
        amount_cents: 100000
        reason: "CANCELLATION"
    expected:
      status: 201
      body:
        status: "SUCCEEDED"


  - name: refund_exceeds_paid
    endpoint: POST /refunds
    setup:
      - invoice paid 145000
    request:
      body:
        booking_id: "{{booking_id}}"
        amount_cents: 200000
        reason: "CANCELLATION"
    expected:
      status: 400
      body:
        error:
          code: "EXCEEDS_PAID"


  - name: payout_dry_run
    endpoint: POST /payouts/run
    request:
      body:
        cycle: "2025-W10"
        dry_run: true
    expected:
      status: 200
      body:
        payout_snapshots:
          - status: "DRY_RUN"



Sync
Sync Service
Service Owner: TBD Base URL: /api/v1/sync Source of Truth: PMS/OTA connections, protocol translation, rate limiting Migration Status: Not Started


________________


Overview
Near-real-time delta synchronization engine. Handles two-way sync between wOS and external systems (PMS, OTA, iCal). Translates protocols, manages rate limits, debounces rapid changes.


Sync is the bridge. Internal services speak a unified language; Sync translates to/from the chaos of external APIs.
Architecture
┌───────────────────────────────────────────────────────────────────┐
│                              SYNC SERVICE                         │
│                                                                   │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                         REQUEST ROUTER                      │  │
│  │         Routes to correct adapter based on property mapping │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                    │                              │
│       ┌────────────────────────────┼────────────────────┐         │
│       ▼                            ▼                    ▼         │
│  ┌────────────┐            ┌───────────┐        ┌───────────┐     │
│  │  Property  │            │ Availabil │        │   Pricing │     │
│  │ Subsystem  │            │ Subsystem │        │  Subsystem│     │
│  └────┬───────┘            └──────┬────┘        └──────┬────┘     │
│       │                           │                    │          │
│       └───────────────────────────┼────────────────────┘          │
│                                   ▼                               │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                      ADAPTERS (Translation)                 │  │
│  │  Guesty │ Hostaway │ Streamline │ Escapia │ OwnerRez │ iCal │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                   │                               │
│  ┌─────────────────────────────────────────────────────────────┐  │
│  │                     CONNECTORS (Transport)                  │  │
│  │              HTTP │ SOAP │ iCal │ Webhook Receiver          │  │
│  └─────────────────────────────────────────────────────────────┘  │
│                                   │                               │
│  ┌────────────────────────────────────────────────────────────┐   │
│  │                      RATE LIMITER + BACKOFF                │   │
│  │                   Token bucket per partner + jitter        │   │
│  └────────────────────────────────────────────────────────────┘   │
│                                   │                               │
└───────────────────────────────────────────────────────────────────┘
                                    ▼
                          External Systems (PMS/OTA)
System Dependencies
Dependency
	Role
	PostgreSQL
	PMS mappings, sync logs, fee dictionary
	Redis
	Rate limiters, debounce buffers, job queues
	Kafka
	Internal event consumption/production
	Property Service
	Property data, PMS mapping lookups[au]
	Availability Service
	Calendar updates
	Pricing Service
	Rate updates
	Event Service
	Emit sync events
	Responsibilities
* PMS/OTA connections and credentials
* Protocol translation (REST, SOAP, iCal)
* Rate limit management
* Debouncing and coalescing
* Sync event logging
* Fee dictionary (external → standard mapping)
Subsystems
Subsystem
	Responsibility
	Runs Independently
	Property
	Content, descriptions, amenities, images
	Yes
	Availability
	Calendar blocks, bookings
	Yes
	Pricing
	Rates, fees, taxes
	Yes
	

Each subsystem uses the same adapters/connectors but operates on its own schedule and queue.
Sync Modes
Mode
	Direction
	Source of Truth
	Example
	OPERATED
	Outbound
	wOS
	Wander-managed properties → Channex → OTAs
	BRANDED
	Inbound
	External PMS
	Guesty/Hostaway → wOS (mirror)
	Does NOT Own
Concern
	Owner
	Decide if property is available
	Availability Service
	Calculate prices
	Pricing Service
	Create bookings
	Booking Service
	Store property data
	Property Service
	Process payments
	Payments Service
	PMS credential management
	Secrets Manager
	

________________


API Reference
POST /webhook/{pms_type}
Receive webhooks from external PMS. Acknowledge immediately, process async.


Request Parameters


Parameter
	Location
	Type
	Required
	Description
	pms_type
	path
	string
	Yes
	PMS identifier (guesty, hostaway, etc.)
	

Response (200) — Always return 200 within 500ms to prevent retries.


{
  "received": true,
  "event_id": "evt_123"
}

Processing:


1. Validate signature (if PMS provides HMAC)
2. Check idempotency (payload hash)
3. Enqueue for async processing
4. Route to appropriate subsystem based on event type


________________


POST /sync/push
Trigger outbound sync for OPERATED properties.


Request


{
  "property_id": "prop_abc",
  "subsystems": ["availability", "pricing"],
  "date_range": {
    "from": "2025-03-01",
    "to": "2025-06-01"
  }
}

Field
	Type
	Required
	Description
	property_id
	uuid
	Yes
	Property to sync
	subsystems
	array
	Yes
	Which subsystems to sync
	date_range
	object
	No
	Date range for availability/pricing
	

Subsystems: property, availability, pricing


Response (202)


{
  "job_id": "sync_job_789",
  "status": "QUEUED",
  "subsystem_jobs": [
    { "subsystem": "availability", "status": "QUEUED" },
    { "subsystem": "pricing", "status": "QUEUED" }
  ]
}

________________


POST /sync/pull
Trigger inbound sync for BRANDED properties.


Request


{
  "property_id": "prop_abc",
  "subsystems": ["property", "availability", "pricing"],
  "full": false
}

Field
	Type
	Required
	Description
	property_id
	uuid
	Yes
	Property to sync
	subsystems
	array
	Yes
	Which subsystems to sync
	full
	boolean
	No
	Full refresh vs delta (default: false)
[av][aw][ax][ay]	


________________


GET /sync/jobs/{job_id}
Get status of a sync job.


Response (200)


{
  "job_id": "sync_job_789",
  "job_type": "PUSH",
  "property_id": "prop_abc123",
  "pms_type": "GUESTY",
  "status": "COMPLETED",
  "subsystem_jobs": [
    {
      "subsystem": "availability",
      "status": "COMPLETED",
      "records_synced": 90,
      "completed_at": "2025-03-10T14:35:00Z"
    },
    {
      "subsystem": "pricing",
      "status": "COMPLETED",
      "records_synced": 90,
      "completed_at": "2025-03-10T14:35:05Z"
    }
  ],
  "created_at": "2025-03-10T14:30:00Z",
  "completed_at": "2025-03-10T14:35:05Z"
}

Job Statuses: QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED


________________


POST /sync/reconcile
Full reconciliation: compare internal state vs external, flag discrepancies.


Request


{
  "property_ids": ["prop_abc", "prop_def"],
  "subsystems": ["availability"]
}

If property_ids omitted, reconciles all properties (batch job).


Response (202)


{
  "job_id": "reconcile_456",
  "status": "QUEUED",
  "property_count": 2
}

________________


GET /sync/reconcile/{job_id}
Get reconciliation status.


Response (200)


{
  "job_id": "reconcile_456",
  "status": "COMPLETED",
  "properties_checked": 2,
  "discrepancies_found": 1,
  "discrepancies": [
    {
      "property_id": "prop_abc123",
      "subsystem": "availability",
      "diff": {
        "missing_internal": ["2025-03-20"],
        "missing_external": []
      }
    }
  ],
  "completed_at": "2025-03-10T15:00:00Z"
}

________________


GET /sync/status
Overall sync health.


Response (200)


{
  "status": "HEALTHY",
  "adapters": {
    "guesty": { "status": "OK", "last_sync": "2025-03-10T14:30:00Z" },
    "hostaway": { "status": "OK", "last_sync": "2025-03-10T14:28:00Z" },
    "streamline": { "status": "DEGRADED", "last_error": "Rate limited" }[az][ba]
  },
  "queue_depths": {
    "guesty": 5,
    "hostaway": 0,
    "streamline": 45
  },
  "webhooks_received_24h": 1543,
  "sync_success_rate_24h": 0.994
}

________________


GET /sync/queue
Queue depths by partner.


Response (200)


{
  "queues": [
    {
      "partner": "guesty",
      "pending": 5,
      "processing": 2,
      "failed_24h": 3
    },
    {
      "partner": "streamline",
      "pending": 45,
      "processing": 1,
      "failed_24h": 12
    }
  ]
}

________________


GET /sync/rate-limits
Current rate limit state by partner.[bb][bc]


Response (200)


{
  "limits": [
    {
      "partner": "guesty",
      "max_per_minute": 120,[bd]
      "current_tokens": 115,
      "in_backoff": false
    },
    {
      "partner": "streamline",
      "max_per_minute": 60,
      "current_tokens": 0,
      "in_backoff": true,
      "backoff_until": "2025-03-10T14:35:00Z"
    }
  ]
}

________________


Internal Endpoints
POST /sync/pms/quote
Get a quote from the PMS (used by Booking Service).


Request


{
  "property_id": "prop_abc123",
  "check_in": "2025-03-15",
  "check_out": "2025-03-20",
  "guests": 4
}

Response (200)


{
  "quote_id": "pms_quote_xyz",
  "pms_type": "GUESTY",
  "rates": [
    { "date": "2025-03-15", "amount_cents": 45000 }
  ],
  "fees": [
    { "type": "FEE_CLEANING", "amount_cents": 25000 }
  ],
  "total_cents": 295000,
  "expires_at": "2025-03-10T15:00:00Z"
}

________________


POST /sync/pms/reservation
Create a reservation in the PMS.


Request


{
  "property_id": "prop_abc123",
  "check_in": "2025-03-15",
  "check_out": "2025-03-20",
  "guest": {
    "first_name": "John",
    "last_name": "Doe",
    "email": "john@example.com",
    "phone": "+1-555-123-4567"
  },
  "quote_id": "pms_quote_xyz"[be][bf][bg]
}

Response (201)


{
  "external_id": "guesty_res_456",
  "confirmation_code": "GY-99887",
  "status": "CONFIRMED"
}

________________


DELETE /sync/pms/reservation/{external_id}
Cancel a reservation in the PMS.


Response (200)


{
  "external_id": "guesty_res_456",
  "cancelled": true
}

________________


Subsystem Processing
Property Subsystem
Syncs content: name, description, amenities, photos, policies.


Inbound (BRANDED):


async def pull_property(property_id: str):
    adapter = get_adapter(property_id)
    external = await adapter.get_property()


    normalized = adapter.translate_property(external)


    await property_service.update_from_sync(
        property_id=property_id,
        data=normalized,
        source='PMS'
    )


    emit('sync.property_pulled', entity_id=property_id)

Outbound (OPERATED):


async def push_property(property_id: str):
    property = await property_service.get(property_id)
    adapter = get_adapter(property_id)


    external_format = adapter.format_property(property)


    await adapter.update_property(external_format)


    emit('sync.property_pushed', entity_id=property_id)
Availability Subsystem
Syncs calendar blocks and bookings.


Inbound (BRANDED):


async def pull_availability(property_id: str, date_range: DateRange):
    adapter = get_adapter(property_id)


    # Get external calendar
    external_blocks = await adapter.get_availability(date_range)


    # Translate to internal format
    blocks = adapter.translate_blocks(external_blocks)


    # Get unit_id for this property (Availability Service uses unit_id)
    unit_id = await property_service.get_unit_id(property_id)


    # Update availability (mirror mode)
    await availability_service.apply_external_blocks[bh][bi](
        unit_id=unit_id,
        blocks=blocks,
        source=adapter.pms_type
    )


    emit('sync.availability_pulled', entity_id=property_id)

Outbound (OPERATED):


async def push_availability(property_id: str, changed_dates: list[date]):
    """Push only changed dates, not full calendar."""


    # Get unit_id for this property (Availability Service uses unit_id)
    unit_id = await property_service.get_unit_id(property_id)


    # Get current availability for changed dates
    availability = await availability_service.get_range(
        unit_id,
        min(changed_dates),
        max(changed_dates)
    )


    adapter = get_adapter(property_id)


    # Push delta only
    await adapter.update_availability(changed_dates, availability)


    emit('sync.availability_pushed', entity_id=property_id, context={
        'dates_count': len(changed_dates)
    })
Pricing Subsystem
Syncs nightly rates, fees, taxes.


Inbound (BRANDED):


async def pull_pricing(property_id: str, date_range: DateRange):
    adapter = get_adapter(property_id)


    external_rates = await adapter.get_rates(date_range)


    # Translate fees using fee dictionary
    rates = adapter.translate_rates(external_rates)
    fees = adapter.translate_fees(external_rates.fees)


    await pricing_service.update_from_sync(
        property_id=property_id,
        rates=rates,
        fees=fees
    )


    emit('sync.pricing_pulled', entity_id=property_id)

________________


Connector Layer
Handles HOW to communicate. Protocol-specific, reusable.
HTTP Connector
class HttpConnector:
    def __init__(self, config: HttpConfig):
        self.base_url = config.base_url
        self.auth = config.auth
        self.timeout = config.timeout
        self.rate_limiter = config.rate_limiter


    async def send(self, request: Request) -> Response:
        # Acquire rate limit token (with backoff)
        await self.rate_limiter.acquire()


        try:
            response = await self.execute_with_retry(request)
            return response
        except RateLimitError:
            # External rate limit hit — back off
            await self.rate_limiter.backoff()
            raise RetryableError()


    def backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with jitter."""
        base = min(60, 2 ** attempt)
        jitter = random.uniform(0, base * 0.1)
        return base + jitter
SOAP Connector
class SoapConnector:
    """For legacy systems like Escapia."""


    async def send(self, request: SoapRequest) -> Response:
        envelope = self.build_envelope(request)


        response = await httpx.post(
            self.wsdl_url,
            content=envelope,
            headers={'Content-Type': 'text/xml'}
        )


        return self.parse_envelope(response.text)
iCal Connector
class ICalConnector:
    """For calendar feeds (read-only availability)."""


    async def fetch(self, url: str) -> list[ICalEvent]:
        response = await httpx.get(url, timeout=30)
        return self.parse_ics(response.text)


    def generate(self, blocks: list[Block]) -> str:
        """Generate .ics file for export."""
        events = [self.block_to_vevent(b) for b in blocks]
        return f"BEGIN:VCALENDAR\nVERSION:2.0\n{''.join(events)}END:VCALENDAR"

________________


Adapter Layer
Handles WHAT to send/receive. Translates between Wander format and external format.
Adapter Interface
class Adapter(Protocol):
    pms_type: str
    connector: Connector


    # Availability
    async def get_availability(self, date_range: DateRange) -> ExternalBlocks: ...
    async def update_availability(self, dates: list[date], blocks: Blocks) -> None: ...
    def translate_blocks(self, external: ExternalBlocks) -> list[Block]: ...


    # Pricing
    async def get_rates(self, date_range: DateRange) -> ExternalRates: ...
    async def update_rates(self, rates: list[Rate]) -> None: ...
    def translate_rates(self, external: ExternalRates) -> list[Rate]: ...
    def translate_fees(self, external: ExternalFees) -> list[Fee]: ...


    # Property
    async def get_property(self) -> ExternalProperty: ...
    async def update_property(self, property: PropertyData) -> None: ...
    def translate_property(self, external: ExternalProperty) -> PropertyData: ...


    # Bookings
    async def get_quote(self, request: QuoteRequest) -> Quote: ...
    async def create_reservation(self, request: ReservationRequest) -> Reservation: ...
    async def cancel_reservation(self, external_id: str) -> None: ...


    # Webhooks
    def parse_webhook(self, payload: dict) -> NormalizedEvent: ...
Guesty Adapter
class GuestyAdapter:
    pms_type = 'GUESTY'


    def __init__(self, credentials: GuestyCredentials):
        self.connector = HttpConnector(HttpConfig(
            base_url='https://api.guesty.com/api/v2',
            auth=BearerAuth(credentials.api_key),
            timeout=30,
            rate_limiter=TokenBucket(tokens=120, refill_rate=2)  # 120/min
        ))


    def translate_fees(self, external_fees: list) -> list[Fee]:
        """Map Guesty fee types to standard."""
        fee_map = {
            'CLEANING': 'FEE_CLEANING',
            'PET_FEE': 'FEE_PET',
            'PET_FEE_LARGE': 'FEE_PET',
            'SERVICE_FEE': 'FEE_PLATFORM',
        }


        return [
            Fee(
                kind=fee_map.get(f['type'], 'FEE_MISC'),
                amount_cents=int(f['amount'] * 100),
                description=f.get('description', f['type'])
            )
            for f in external_fees
        ]


    def parse_webhook(self, payload: dict) -> NormalizedEvent:
        event_map = {
            'reservation.created': 'booking.created',
            'reservation.updated': 'booking.updated',
            'reservation.cancelled': 'booking.cancelled',
            'listing.calendar.updated': 'availability.changed',
        }


        return NormalizedEvent(
            type=event_map.get(payload['event'], 'unknown'),
            external_id=payload['data']['_id'],
            property_external_id=payload['data'].get('listingId'),
            raw=payload['data']
        )
Streamline Adapter
class StreamlineAdapter:
    """Streamline requires quote-to-book flow."""


    pms_type = 'STREAMLINE'


    async def get_quote(self, request: QuoteRequest) -> Quote:
        # Streamline locks price for ~15 min with quote
        response = await self.connector.send(Request(
            method='POST',
            path='/api/quotes',
            body={
                'unit_id': request.external_id,
                'arrival_date': request.check_in.isoformat(),
                'departure_date': request.check_out.isoformat(),
                'num_guests': request.guests
            }
        ))


        return Quote(
            quote_id=response.body['quote_id'],  # Required for booking
            expires_at=datetime.now() + timedelta(minutes=15),
            rates=self.translate_rates(response.body['rates']),
            fees=self.translate_fees(response.body['fees']),
            total_cents=int(response.body['total'] * 100)
        )


    async def create_reservation(self, request: ReservationRequest) -> Reservation:
        if not request.quote_id:
            raise ValueError('Streamline requires quote_id')


        response = await self.connector.send(Request(
            method='POST',
            path='/api/reservations',
            body={
                'quote_id': request.quote_id,
                'guest': self.format_guest(request.guest),
                'disable_payments': True  # Wander is merchant of record
            }
        ))


        return Reservation(
            external_id=response.body['reservation_id'],
            confirmation_code=response.body['confirmation_code'],
            status='CONFIRMED'
        )
iCal Adapter
class ICalAdapter:
    """Minimal adapter for calendar feeds. Availability only."""


    pms_type = 'ICAL'


    async def get_availability(self, property_id: str, date_range: DateRange):
        mapping = await get_mapping(property_id)
        events = await self.connector.fetch(mapping.ical_url)


        return [
            e for e in events
            if dates_overlap(e.start, e.end, date_range.start, date_range.end)
        ]


    def translate_blocks(self, events: list[ICalEvent]) -> list[Block]:
        return [
            Block(
                start_date=e.start,
                end_date=e.end,
                blocked=True,
                reason=e.summary or 'Blocked via iCal'
            )
            for e in events
        ]


    async def get_quote(self, request: QuoteRequest) -> Quote:
        raise NotImplementedError('iCal does not support pricing')


    async def create_reservation(self, request: ReservationRequest):
        raise NotImplementedError('iCal is read-only')

________________


Rate Limiting
Token Bucket with Backoff
class RateLimiter:
    """Token bucket with external backoff support."""


    def __init__(self, partner: str, config: RateLimitConfig):
        self.partner = partner
        self.max_tokens = config.max_tokens
        self.refill_rate = config.refill_rate
        self.redis = Redis()
        self.backoff_until = None


    async def acquire(self, tokens: int = 1) -> bool:
        """Acquire tokens. Blocks if unavailable."""
        # Check if in backoff
        if self.backoff_until and datetime.now() < self.backoff_until:
            wait = (self.backoff_until - datetime.now()).total_seconds()
            await asyncio.sleep(wait)


        # Lua script for atomic token bucket
        acquired = await self.redis.eval(
            TOKEN_BUCKET_SCRIPT,
            keys=[f'ratelimit:{self.partner}'],
            args=[self.max_tokens, self.refill_rate, time.time(), tokens]
        )


        if not acquired:
            wait_time = tokens / self.refill_rate
            await asyncio.sleep(wait_time)
            return await self.acquire(tokens)


        return True


    async def backoff(self, seconds: float = None):
        """External rate limit hit. Back off."""
        if seconds is None:
            current = getattr(self, '_backoff_count', 0)
            seconds = min(60, 2 ** current)
            self._backoff_count = current + 1


        self.backoff_until = datetime.now() + timedelta(seconds=seconds)


        emit('sync.rate_limit_backoff', context={
            'partner': self.partner,
            'backoff_seconds': seconds
        })


    def reset_backoff(self):
        """Call on successful request."""
        self._backoff_count = 0
        self.backoff_until = None
Partner Limits
Partner
	Rate Limit
	Token Config
	Channex
	600/min
	600 tokens, refill 10/sec
	Guesty
	120/min
	120 tokens, refill 2/sec
	Hostaway
	100/min
	100 tokens, refill 1.67/sec
	Streamline
	60/min
	60 tokens, refill 1/sec
	Escapia
	15/min
	15 tokens, refill 0.25/sec
	OwnerRez
	60/min
	60 tokens, refill 1/sec
	

________________


Debouncing
Coalesce rapid changes into single API calls.


class Debouncer:
    """Buffer changes, flush after quiet period."""


    def __init__(self, flush_delay: float = 5.0):
        self.flush_delay = flush_delay
        self.buffers: dict[str, set[date]] = defaultdict(set)
        self.timers: dict[str, asyncio.Task] = {}


    def add(self, property_id: str, dates: list[date]):
        """Add dates to buffer. Resets flush timer."""
        self.buffers[property_id].update(dates)


        # Cancel existing timer
        if property_id in self.timers:
            self.timers[property_id].cancel()


        # Start new timer
        self.timers[property_id] = asyncio.create_task(
            self._delayed_flush(property_id)
        )


    async def _delayed_flush(self, property_id: str):
        await asyncio.sleep(self.flush_delay)
        await self.flush(property_id)


    async def flush(self, property_id: str):
        """Flush buffered dates to sync."""
        dates = self.buffers.pop(property_id, set())
        self.timers.pop(property_id, None)


        if dates:
            await sync_subsystem.availability.push(property_id, sorted(dates))




# Event listener for internal changes
async def on_availability_changed(event: Event):
    property_id = event.entity_id
    dates = event.context['dates']


    mapping = await get_mapping(property_id)
    if mapping.mode == 'OPERATED':
        debouncer.add(property_id, dates)

________________


Bulk Ingestion
10k properties in under an hour requires parallelism with rate limit awareness.


async def bulk_ingest(property_ids: list[str], subsystems: list[str]):
    """
    Ingest multiple properties with controlled parallelism.
    Target: 10k properties in < 1 hour = ~3 properties/second
    """


    # Group by PMS type for rate limit efficiency
    by_pms = defaultdict(list)
    for prop_id in property_ids:
        mapping = await get_mapping(prop_id)
        by_pms[mapping.pms_type].append(prop_id)


    # Process each PMS type with its own concurrency limit
    tasks = []
    for pms_type, props in by_pms.items():
        config = get_pms_config(pms_type)


        # Concurrency = rate_limit / avg_requests_per_property
        concurrency = max(1, config.rate_limit // (3 * 60))


        task = ingest_pms_batch(pms_type, props, subsystems, concurrency)
        tasks.append(task)


    await asyncio.gather(*tasks)




async def ingest_pms_batch(
    pms_type: str,
    property_ids: list[str],
    subsystems: list[str],
    concurrency: int
):
    """Ingest batch with semaphore-controlled concurrency."""


    semaphore = asyncio.Semaphore(concurrency)


    async def ingest_one(property_id: str):
        async with semaphore:
            for subsystem in subsystems:
                await sync_subsystem[subsystem].pull(property_id)


    await asyncio.gather(*[ingest_one(p) for p in property_ids])

________________


Reconciliation
Nightly job to detect drift between internal and external state.


async def reconcile(property_ids: list[str] = None):
    """
    Compare internal state vs external.
    Run nightly for all properties, or on-demand for specific ones.
    """


    if not property_ids:
        property_ids = await get_all_synced_properties()


    discrepancies = []


    for property_id in property_ids:
        mapping = await get_mapping(property_id)
        adapter = get_adapter(mapping.pms_type)


        # Get unit_id for this property (Availability Service uses unit_id)
        unit_id = await property_service.get_unit_id(property_id)


        # Compare availability
        internal = await availability_service.get_range(
            unit_id,
            date.today(),
            date.today() + timedelta(days=90)
        )


        external = await adapter.get_availability(
            property_id,
            DateRange(date.today(), date.today() + timedelta(days=90))
        )
        external_translated = adapter.translate_blocks(external)


        diff = compare_availability(internal, external_translated)


        if diff:
            discrepancies.append({
                'property_id': property_id,
                'pms_type': mapping.pms_type,
                'subsystem': 'availability',
                'diff': diff
            })


    # Log and alert
    for d in discrepancies:
        await db.reconciliation_discrepancy.create(**d)
        emit('sync.reconciliation_drift', context=d)


    if discrepancies:
        alert('reconciliation_drift', count=len(discrepancies))


    return {
        'properties_checked': len(property_ids),
        'discrepancies': len(discrepancies)
    }

________________


iCal Polling
For sources without webhooks.


class ICalPoller:
    """Adaptive polling for iCal feeds."""


    async def run(self):
        """Main polling loop."""
        while True:
            feeds = await get_ical_feeds_due_for_poll()


            for feed in feeds:
                try:
                    await self.poll_feed(feed)
                except Exception as e:
                    await self.handle_error(feed, e)


            await asyncio.sleep(60)  # Check every minute


    async def poll_feed(self, feed: ICalFeed):
        adapter = ICalAdapter()


        events = await adapter.connector.fetch(feed.url)
        current_blocks = adapter.translate_blocks(events)


        # Compare with known state
        previous_blocks = await get_known_blocks(feed.property_id)


        added = [b for b in current_blocks if b not in previous_blocks]
        removed = [b for b in previous_blocks if b not in current_blocks]


        if added or removed:
            # Get unit_id for this property (Availability Service uses unit_id)
            unit_id = await property_service.get_unit_id(feed.property_id)


            await availability_service.apply_external_changes(
                unit_id=unit_id,
                add=added,
                remove=removed,
                source='ICAL'
            )


            emit('sync.ical_changed', entity_id=feed.property_id, context={
                'added': len(added),
                'removed': len(removed)
            })


            # Increase poll frequency for active properties
            await update_poll_interval(feed.property_id, minutes=15)
        else:
            # Decrease poll frequency for quiet properties
            await update_poll_interval(feed.property_id, minutes=60)


        await update_last_poll(feed.property_id)

________________


Events
Event
	When
	Payload
	sync.property_pulled
	Property data pulled from PMS
	{property_id, pms_type}
	sync.property_pushed
	Property data pushed to PMS
	{property_id, pms_type}
	sync.availability_pulled
	Availability pulled
	{property_id, dates_count}
	sync.availability_pushed
	Availability pushed
	{property_id, dates_count}
	sync.pricing_pulled
	Pricing pulled
	{property_id, dates_count}
	sync.booking_received
	External booking webhook
	{property_id, external_id}
	sync.rate_limit_backoff
	Rate limit triggered backoff
	{partner, backoff_seconds}
	sync.reconciliation_drift
	Discrepancy detected
	{property_id, subsystem, diff}
	sync.failed
	Sync operation failed
	{job_id, error}
	

________________


Data Model
pms_connection
PMS connection configuration per property.


CREATE TABLE pms_connection (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    property_id         UUID NOT NULL,
    organization_id     UUID NOT NULL,


    pms_type            VARCHAR(50) NOT NULL,
    -- GUESTY, HOSTAWAY, STREAMLINE, ESCAPIA, OWNERREZ, LODGIFY, TRACK, BAREFOOT


    external_id         VARCHAR(255) NOT NULL,


    sync_mode           VARCHAR(20) NOT NULL,
    -- OPERATED (outbound), BRANDED (inbound)


    is_sor              BOOLEAN NOT NULL DEFAULT false,[bj][bk][bl]


    credential_id       UUID,


    sync_enabled        BOOLEAN NOT NULL DEFAULT true,
    sync_availability   BOOLEAN NOT NULL DEFAULT true,
    sync_pricing        BOOLEAN NOT NULL DEFAULT true,
    sync_property       BOOLEAN NOT NULL DEFAULT true,


    last_sync_at[bm][bn]        TIMESTAMPTZ,
    last_sync_status    VARCHAR(20),


    metadata            JSONB DEFAULT '{}',


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT unique_pms_connection UNIQUE (pms_type, external_id)
);


CREATE INDEX idx_pms_conn_property ON pms_connection(property_id);
CREATE INDEX idx_pms_conn_type ON pms_connection(pms_type);
CREATE INDEX idx_pms_conn_sor ON pms_connection(property_id) WHERE is_sor = true;
channel_connection
OTA channel connections (via Channex, etc.).


CREATE TABLE channel_connection (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    property_id         UUID NOT NULL,


    channel             VARCHAR(50) NOT NULL,
    -- AIRBNB, VRBO, BOOKING_COM, EXPEDIA, etc.


    aggregator          VARCHAR(50) NOT NULL DEFAULT 'CHANNEX',


    external_property_id VARCHAR(255) NOT NULL,
    external_room_id    VARCHAR(255),
    external_rate_id    VARCHAR(255),


    active              BOOLEAN NOT NULL DEFAULT true,


    last_sync_at        TIMESTAMPTZ,
    last_sync_status    VARCHAR(20),


    metadata            JSONB DEFAULT '{}',


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT unique_channel UNIQUE (channel, external_property_id)
);


CREATE INDEX idx_channel_property ON channel_connection(property_id);
CREATE INDEX idx_channel_active ON channel_connection(active) WHERE active = true;
sync_job
CREATE TABLE sync_job (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    property_id         UUID NOT NULL,
    pms_type            VARCHAR(50) NOT NULL,


    job_type            VARCHAR(20) NOT NULL,
    -- PUSH (outbound), PULL (inbound), RECONCILE


    subsystems          VARCHAR(50)[] NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'QUEUED',
    -- QUEUED, RUNNING, COMPLETED, FAILED, CANCELLED


    priority            INTEGER NOT NULL DEFAULT 0,


    date_from           DATE,
    date_to             DATE,


    records_processed   INTEGER DEFAULT 0,
    records_failed      INTEGER DEFAULT 0,


    error               TEXT,
    error_code          VARCHAR(50),


    queued_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,


    metadata            JSONB DEFAULT '{}'
);


CREATE INDEX idx_sync_job_property ON sync_job(property_id, queued_at DESC);
CREATE INDEX idx_sync_job_status ON sync_job(status, queued_at)
    WHERE status IN ('QUEUED', 'RUNNING');
sync_job_subsystem
CREATE TABLE sync_job_subsystem (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id              UUID NOT NULL REFERENCES sync_job(id),


    subsystem           VARCHAR(50) NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, RUNNING, COMPLETED, FAILED, SKIPPED[bo][bp]


    records_synced      INTEGER DEFAULT 0,


    error               TEXT,


    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ
);


CREATE INDEX idx_subsystem_job ON sync_job_subsystem(job_id);
webhook_event
CREATE TABLE webhook_event (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    source              VARCHAR(50) NOT NULL,


    event_type          VARCHAR(100) NOT NULL,


    external_id         VARCHAR(255),


    payload             JSONB NOT NULL,
    payload_hash        VARCHAR(64) NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'RECEIVED',
    -- RECEIVED, PROCESSING, PROCESSED, FAILED, IGNORED


    property_id         UUID,
    booking_id          UUID,


    processing_time_ms  INTEGER,
    error               TEXT,
    retry_count         INTEGER DEFAULT 0,


    received_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at        TIMESTAMPTZ
);


CREATE INDEX idx_webhook_source ON webhook_event(source, received_at DESC);
CREATE INDEX idx_webhook_status ON webhook_event(status)
    WHERE status IN ('RECEIVED', 'PROCESSING');
CREATE INDEX idx_webhook_hash ON webhook_event(payload_hash);
sync_reconciliation
CREATE TABLE sync_reconciliation (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    job_id              UUID REFERENCES sync_job(id),


    property_id         UUID NOT NULL,
    pms_type            VARCHAR(50) NOT NULL,
    subsystem           VARCHAR(50) NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, IN_SYNC, DRIFT_DETECTED, RESOLVED


    discrepancy_type    VARCHAR(50),
    -- MISSING_INTERNAL, MISSING_EXTERNAL, VALUE_MISMATCH


    internal_value      JSONB,
    external_value      JSONB,
    diff                JSONB,


    resolution          VARCHAR(30),
    -- AUTO_FIXED, MANUAL_FIXED, IGNORED


    resolved_at         TIMESTAMPTZ,
    resolved_by         UUID,


    detected_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_reconciliation_property ON sync_reconciliation(property_id);
CREATE INDEX idx_reconciliation_status ON sync_reconciliation(status)
    WHERE status = 'DRIFT_DETECTED';
rate_limit_state
CREATE TABLE rate_limit_state (
    partner             VARCHAR(50) PRIMARY KEY,


    max_per_minute      INTEGER NOT NULL,
    current_tokens      INTEGER NOT NULL,


    in_backoff          BOOLEAN NOT NULL DEFAULT false,
    backoff_until       TIMESTAMPTZ,
    backoff_count       INTEGER DEFAULT 0,


    last_request_at     TIMESTAMPTZ,
    last_refill_at      TIMESTAMPTZ NOT NULL DEFAULT now(),


    requests_24h        INTEGER DEFAULT 0,
    errors_24h          INTEGER DEFAULT 0,


    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
fee_dictionary
CREATE TABLE fee_dictionary (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    pms_type            VARCHAR(50) NOT NULL,


    external_code       VARCHAR(100) NOT NULL,
    external_name       VARCHAR(255),


    standard_type       VARCHAR(50) NOT NULL,
    -- CLEANING, PET, EXTRA_GUEST, SERVICE, TAX, RESORT, DAMAGE_WAIVER, OTHER


    calculation_type[bq][br]    VARCHAR(30),


    active              BOOLEAN NOT NULL DEFAULT true,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT unique_fee_mapping UNIQUE (pms_type, external_code)
);


-- Seed defaults
INSERT INTO fee_dictionary (pms_type, external_code, standard_type) VALUES
    ('GUESTY', 'CLEANING', 'FEE_CLEANING'),
    ('GUESTY', 'PET_FEE', 'FEE_PET'),
    ('HOSTAWAY', 'cleaningFee', 'FEE_CLEANING'),
    ('STREAMLINE', 'Cleaning Fee', 'FEE_CLEANING');
pms_quote_cache
CREATE TABLE pms_quote_cache (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    property_id         UUID NOT NULL,
    pms_type            VARCHAR(50) NOT NULL,


    check_in            DATE NOT NULL,
    check_out           DATE NOT NULL,
    guests              INTEGER NOT NULL,


    nightly_rates       JSONB NOT NULL,
    fees                JSONB NOT NULL,
    total_cents         INTEGER NOT NULL,
    currency            VARCHAR(3) NOT NULL DEFAULT 'USD',


    raw_response        JSONB,


    expires_at          TIMESTAMPTZ NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_quote_cache_lookup ON pms_quote_cache(property_id, check_in, check_out, guests);
CREATE INDEX idx_quote_cache_expires ON pms_quote_cache(expires_at);
pms_reservation_mapping
CREATE TABLE pms_reservation_mapping (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    booking_id          UUID NOT NULL,
    pms_type            VARCHAR(50) NOT NULL,
    external_id         VARCHAR(255) NOT NULL,


    confirmation_code   VARCHAR(100),


    raw_payload         JSONB,


    synced_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),


    CONSTRAINT unique_pms_reservation UNIQUE (pms_type, external_id)
);


CREATE INDEX idx_reservation_booking ON pms_reservation_mapping(booking_id);
CREATE INDEX idx_reservation_external ON pms_reservation_mapping(pms_type, external_id);

________________


SLOs
Operation
	Target
	Webhook acknowledgment
	< 500ms
	Webhook processing
	< 5s
	Delta sync (outbound)
	< 60s from event
	Bulk ingest (10k properties)
	< 1 hour
	Reconciliation (full)
	< 2 hours
	

Metric
	Target
	Webhook success rate
	> 99.9%
	Sync success rate
	> 99%
	Reconciliation drift
	< 0.01% bookings/day
	Rate limit queue depth
	< 100 per partner
	

________________


Current State Analysis
Existing Tables
Table
	Status
	PmsSyncEvent
	Good basic structure for sync job tracking
	PmsWebhookEvent
	Good structure for webhook ingestion
	PmsBookingMetadata
	Good for storing raw PMS data
	ChannelConnection
	Good for tracking OTA channel connections
	ExternalAvailabilityCalendarDay
	Should be moved to Availability Service
	

________________


Migration Strategy
Phase 1-2: Create Tables & Connection Migration (Week 1-2)
1. Create new tables in Sync Service database
2. Extract PMS connections from Unit table
3. Migrate channel connections
Phase 3-4: Sync Job & Webhook Migration (Week 3-5)
1. Migrate sync events to sync_job
2. Migrate webhooks to webhook_event
3. Set up rate limit state
Phase 5-6: Read/Write Migration (Week 6-8)
1. Feature flag to read from new tables
2. Switch webhook handlers to write to new tables
3. Update sync orchestrator to use new schema
Phase 7: Cleanup (Week 9)
1. Stop writing to old tables
2. Archive old sync events
3. Remove duplicate external ID columns from Unit table
Rollback Plan
1. Feature flags control sync behavior
2. Dual-write to old tables during migration
3. Webhook handlers can be reverted quickly
4. Old tables retained (read-only) for 90 days
5. External PMS data can always be re-fetched


________________


Data Volume Estimates
Table
	Current
	New
	ChannelConnection
	~1K
	channel_connection ~1K
	PmsSyncEvent
	~50K
	sync_job ~50K
	PmsWebhookEvent
	~100K
	webhook_event ~100K
	PmsBookingMetadata
	~80K
	pms_reservation_mapping ~80K
	—
	—
	pms_connection ~700
	—
	—
	rate_limit_state ~10
	

________________


Test Scenarios
test_cases:
  - name: push_availability
    endpoint: POST /sync/push
    setup:
      - property exists in OPERATED mode
      - property linked to Channex
    request:
      body:
        property_id: "{{property_id}}"
        subsystems: ["availability"]
        date_range:
          from: "2025-06-01"
          to: "2025-06-30"
    expected:
      status: 202
      body:
        status: "QUEUED"


  - name: pull_property
    endpoint: POST /sync/pull
    setup:
      - property exists in BRANDED mode
      - property linked to Guesty
    request:
      body:
        property_id: "{{property_id}}"
        subsystems: ["property"]
        full: true
    expected:
      status: 202
      body:
        status: "QUEUED"


  - name: webhook_received
    endpoint: POST /webhook/guesty
    request:
      body:
        event: "reservation.created"
        data:
          _id: "guesty_res_123"
          listingId: "guesty_listing_456"
    expected:
      status: 200
      body:
        received: true


  - name: reconciliation_job
    endpoint: POST /sync/reconcile
    request:
      body:
        property_ids: ["{{property_id}}"]
        subsystems: ["availability"]
    expected:
      status: 202
      body:
        status: "QUEUED"


  - name: rate_limit_status
    endpoint: GET /sync/rate-limits
    expected:
      status: 200
      body:
        limits:
          - partner: "guesty"
            max_per_minute: 120


  - name: pms_quote
    endpoint: POST /sync/pms/quote
    setup:
      - property exists with Guesty SoR
    request:
      body:
        property_id: "{{property_id}}"
        check_in: "2025-06-01"
        check_out: "2025-06-04"
        guests: 4
    expected:
      status: 200
      body:
        pms_type: "GUESTY"

________________


The Sync service mental model
Sync is not “a bunch of PMS integrations.” Sync is a translation and orchestration boundary:
* Inside Wander: everyone speaks Wander Canonical (your structs)
* Outside: every PMS speaks its own dialect (REST/SOAP/iCal, different schemas, weird semantics)
* Sync sits in the middle and guarantees: canonical structs in, canonical structs out, reliably.
________________


The pipeline (the flow we’re trying to standardize)
1) Define the Canonical Structs (Canonical Data Model)
These are our contracts: Property, Availability, Pricing, Reservation, etc.
Rule: internal services never depend on PMS-specific fields. They only depend on canonical structs + metadata (source, confidence, timestamps, etc).
This is the Anti-Corruption Layer.
________________


2) Per PMS: pick the minimal endpoint set (Capability Mapping)
For each PMS:
* Identify the smallest set of calls needed to fully populate the canonical structs.
* Document gaps / optional fields / weird semantics.
* Decide what’s delta-capable (webhooks) vs poll-only.
This is the capability matrix.
________________


3) Connector: talk to the PMS (transport/protocol)
Connector = “how we communicate”
* HTTP connector (auth, retries, timeouts)
* SOAP connector
* iCal connector
* Webhook receiver
* Rate limiting + backoff
* Idempotency keys, request signing, tracing
No mapping logic lives here. It just returns raw responses.
________________


4) Adapter: translate PMS responses → canonical structs
Adapter = “what we mean”
* Maps PMS fields to canonical fields
* Normalizes weird behaviors (date inclusivity, time zones, statuses)
* Applies dictionaries (amenities, fees)
* Emits canonical events: availability.changed, reservation.updated, etc.
The adapter should accept raw connector results and output fully-populated canonical structs plus metadata.
________________


5) Ingest / Apply: canonical structs → internal services
This is where we hand off to Property/Availability/Pricing/Booking services.
Key idea: Sync does not store the “truth” of property data long-term. It coordinates and logs, then applies changes through the real owners.
This aligns with Ports & Adapters: core domain services are the “inside,” Sync is an edge boundary.
________________


Two modes, same architecture
It as two “lanes,” with the same primitives.
Inbound (BRANDED)
External PMS is Source of Truth.
* Webhook received OR poll tick
* Connector fetches minimal delta (or range)
* Adapter outputs canonical structs
* Ingest applies to internal services
* Log job + emit internal events
Outbound (OPERATED)
Wander is Source of Truth.
* Internal event occurs (availability changed, rate changed)
* Debounce/coalesce
* Adapter formats outbound payload (canonical → PMS format)
* Connector sends with rate limiting
* Log job + emit internal events
________________


The non-negotiables (what keeps this from turning into hacks)
1. Canonical-first: no one-off PMS fields leak inward.
2. Connector/Adapter separation: transport vs translation never mix.
3. Idempotency everywhere: webhook events and sync jobs must be replayable.
4. Observable by default: every sync is a job with status, counts, errors, latency.
5. Delta first, reconcile second:
   * Webhooks/deltas for speed
   * Scheduled reconcile for drift detection
6. Authority rules live outside Sync:
   * Sync carries “source” metadata
   * The owning service decides “imported default vs curated value” and what can overwrite what
That last one is our governance point: authority is a domain policy, not an integration hack.
________________


A short Slack-native explanation
Sync service = Ports & Adapters / Anti-Corruption Layer for PMS integrations.
* Define canonical structs (Property/Availability/Pricing/Reservation).
* For each PMS, pick a minimal endpoint set that can populate those structs.
* Connector handles protocol: auth/retries/rate limits/webhooks (no mapping).
* Adapter handles translation: PMS payloads → canonical structs (+ normalization).
* Hand canonical structs to the ingest/apply pipeline (Property/Availability/Pricing/Booking services).
* Run in two lanes:
   * Inbound (BRANDED): PMS → canonical → internal
   * Outbound (OPERATED): internal → canonical → PMS
* Reliability: idempotent webhook inbox + job log + reconcile for drift.
________________


Below is a practical set of unified structs that represent the intersection we actually care about for Sync (property/content, availability, pricing, bookings). This maps minimal API calls per PMS to cover those structs.
A couple caveats up front:
* Some PMSes keep full API docs behind partner login (not publicly crawlable). For those (notably Streamline and likely TrackPMS and parts of Lodgify/Rentals United), this is the minimal call-shape we should target; someone with access can confirm exact paths in the partner portal.
* Guesty and Hostaway are very well documented publicly; OwnerRez and Escapia are partially public; RU points to a developer portal. (Hostaway API)
________________


1) Canonical structs (the “complete intersection”)
These are TypeScript-ish types. The key is: every adapter must be able to produce these, but may leave fields NULL if the PMS can’t supply them.  NULLs should be noted.
Property / Content
type ExternalRef = {
  pmsType: "HOSTAWAY" | "GUESTY" | "LODGIFY" | "STREAMLINE" | "ESCAPIA" | "RENTALS_UNITED" | "TRACK" | "OWNERREZ";
  externalPropertyId: string;
  externalUnitId?: string; // when PMS distinguishes property vs unit
};


type PropertyCore = {
  ref: ExternalRef;
  name: string;
  internalName?: string;           // nickname
  status?: "ACTIVE" | "INACTIVE";
  timezone?: string;
  propertyType?: string;           // normalized enum on our side
};


type PropertyLocation = {
  ref: ExternalRef;
  address1?: string;
  address2?: string;
  city?: string;
  state?: string;
  postalCode?: string;
  countryCode?: string;
  lat?: number;
  lng?: number;
};


type PropertyCapacity = {
  ref: ExternalRef;
  bedrooms?: number;
  bathrooms?: number;
  beds?: number;
  maxOccupancy?: number;
};


type PropertyAmenities = {
  ref: ExternalRef;
  amenities: Array<{ code: string; name?: string }>; // normalize via dictionary
};


type PropertyMedia = {
  ref: ExternalRef;
  photos: Array<{ url: string; caption?: string; order?: number }>;
  virtualTourUrl?: string;
};


type PropertyPolicies = {
  ref: ExternalRef;
  checkIn?: string;   // "16:00"
  checkOut?: string;  // "10:00"
  houseRules?: string;
  cancellationPolicy?: string;
  minStayDefault?: number;
  maxStayDefault?: number;
};
Availability (calendar blocks + bookings representation)
type AvailabilityBlock = {
  ref: ExternalRef;
  startDate: string; // YYYY-MM-DD
  endDate: string;   // YYYY-MM-DD (exclusive or inclusive; normalize)
  status: "AVAILABLE" | "BLOCKED" | "BOOKED";
  reason?: string;   // "owner block", "maintenance", etc
  externalReservationId?: string; // if BOOKED
};


type AvailabilityDelta = {
  ref: ExternalRef;
  changedDates: string[]; // for webhook-driven delta (debounce/coalesce)
};
Pricing (rates + fees mapping)
type NightlyRate = {
  ref: ExternalRef;
  date: string; // YYYY-MM-DD
  amountCents: number;
  currency: string;
  minStay?: number;
  maxStay?: number;
  closedToArrival?: boolean;
  closedToDeparture?: boolean;
};


type Fee = {
  ref: ExternalRef;
  code: string;           // external fee code/name
  standardType?: string;  // your fee_dictionary mapping
  amountCents?: number;   // fixed fees
  percent?: number;       // percent fees
  appliesTo?: "STAY" | "NIGHT" | "GUEST" | "BOOKING";
  description?: string;
};


type Taxes = {
  ref: ExternalRef;
  items: Array<{ name: string; percent?: number; amountCents?: number }>;
};
Reservations / Bookings (what Sync needs for Booking Service + reconciliation)
type Guest = {
  firstName?: string;
  lastName?: string;
  email?: string;
  phone?: string;
};


type Reservation = {
  ref: ExternalRef;
  externalReservationId: string;
  status: "INQUIRY" | "HOLD" | "CONFIRMED" | "CANCELLED";
  checkIn: string;
  checkOut: string;
  guests?: number;
  guest?: Guest;
  totalCents?: number;
  currency?: string;
  createdAt?: string;
  updatedAt?: string;
};
This set maps cleanly onto the proposed subsystems and tables (pms_connection, sync_job, webhook_event, fee_dictionary, reservation mapping, etc.).
________________


2) Minimal API calls per PMS to fully cover those structs
Interpretation: minimal calls to populate:
* PropertyCore + Location + Capacity + Amenities + Media + Policies
* AvailabilityBlock
* NightlyRate + Fee/Taxes (if exposed)
* Reservation
Hostaway (public REST API)
Best case minimal pull set
1. Listings list + listing detail → PropertyCore/Location/Capacity/Policies/possibly amenities/media
2. Calendar / listing calendar → AvailabilityBlock
3. Reservations list/detail → Reservation
4. (Optional) Rates/pricing endpoints if you use Hostaway as pricing SoT for operated properties
Hostaway explicitly supports listings/reservations/calendar and blocking/unblocking calendar days, plus rate limits and webhooks. (Hostaway API)
Webhook-driven delta
* Use Hostaway unified webhooks to trigger AvailabilityDelta / Reservation refresh (then do targeted pulls). (Hostaway API)
________________


Guesty (Pro via Guesty Open API)
Minimal pull set (and it’s nicely “one endpoint per struct”)
1. GET Listings (all) + GET Listing (by id) → property structs (Guesty Open API)
2. GET Calendar (single listing) (or multi) → availability structs (Guesty Open API)
3. GET Rate Plan ARI Calendar (if you’re using ARI pricing) → nightly rates (Guesty Open API)
4. GET Reservations search + GET Reservation → reservations (Guesty Open API)
5. GET Additional fees for listing (if required for Fee struct) (Guesty Open API)
Webhook-driven delta
* Guesty webhooks cover listings + calendars + reservations, which is exactly what you want for near-real-time delta sync. (Guesty Open API)
________________


Lodgify
Public marketing pages are easy to find, but the real endpoint list is typically in their official docs/portal (often gated). (Lodgify)
So the right “minimal set” target looks like:
1. GET Properties/Listings + GET Property detail → property structs
2. GET Availability/Calendar (date-range) → availability
3. GET Rates (date-range) → rates
4. GET Bookings/Reservations (date-range) + GET Booking detail → reservations
5. (If exists) webhooks → deltas (property/calendar/reservation)
If Lodgify doesn’t give all of these, fall back to polling + reconcile for the missing pieces.
________________


Streamline
Streamline advertises an “Open API” and capabilities (calendars, availability, pricing, etc.), but the detailed endpoint reference is typically partner-only. (Streamline)
Minimal set target:
1. GET Units/Properties + GET Unit detail
2. GET Availability/Calendar (range)
3. GET Pricing/Rates (range)
4. POST Quote (since they often require “quote-to-book” flows)
5. POST Reservation / GET Reservations
Streamline must support the Reservation + NightlyRate set, even if via quote responses.
________________


Escapia
Escapia has a Gateway API that is GraphQL and (at least publicly) starts with Rates. (Escapia Developer)
Escapia historically also has SOAP-based EscapiaNet for broader inventory/availability, but the modern “Gateway” is where they’re moving. (Escapia Developer)
Minimal set target
1. GraphQL query for Rates (NightlyRate, possibly Fees/Taxes) (Escapia Developer)
2. EscapiaNet (or Gateway equivalent) query for Availability (blocks/booked dates) (Escapia Developer)
3. Reservations search/detail (Gateway or EscapiaNet/SOAP depending on what your partner access provides)
In practice: Escapia is often “availability + rates + reservations” but split across surfaces; the adapter should hide that split.
________________


Rentals United
RU’s docs page points to their developer portal for full method specs. (Rental United Documentation)
So I can’t truthfully list exact endpoints here without portal access, but RU’s model is typically:
* inventory/content methods
* availability methods
* pricing methods
* reservations methods
Minimal set target
1. GetProperties / GetUnits + GetProperty
2. GetAvailability (range)
3. GetPrices/Rates (range)
4. GetReservations (range) + GetReservation
5. (If supported) webhooks / push notifications → deltas
________________


TrackPMS (TravelNet Solutions)
Publicly, you can find “how to authenticate / what base domain looks like” via third-party integration docs, but not necessarily the full endpoint catalog. (Airbyte Docs)
Minimal set target
1. GET Properties/Units + detail
2. GET Availability
3. GET Rates
4. GET Reservations + detail
5. (If offered) webhook-like events; otherwise polling + reconcile
________________


OwnerRez
OwnerRez has:
* API overview, OAuth vs PAT guidance (OwnerRez)
* Webhooks for OAuth apps (OwnerRez)
* Availability lookup endpoint referenced in their docs example (OwnerRez)
* Quote/booking guidance (not all upgraded to v2 per their doc) (OwnerRez)
Minimal set target
1. GET Properties + GET Property detail
2. GET Availability (lookup) → availability (OwnerRez)
3. GET Rates / Pricing (v2 if available, otherwise older endpoint per their transition notes) (OwnerRez)
4. TEST Quote / POST Quote (as needed) (OwnerRez)
5. GET Bookings/Reservations + detail
6. Webhooks (OAuth) to drive deltas (OwnerRez)
________________


3) The “right way forward” (so this doesn’t become hack soup)
If we do just one thing before building RU-triggered resync:
1. Lock these structs as the Sync Service contract.
2. For each PMS, fill out a tiny “capability matrix”:
   * Can provide PropertyCore? Amenities? Media? Policies?
   * Availability as blocks vs per-day vs iCal?
   * Rates as ARI calendar vs quote-only?
   * Webhooks supported? For which entities?
3. Then define the adapter’s minimal endpoint set as:
   * Baseline pull: enough calls to populate all structs once
   * Delta pull: smallest calls to refresh only what changed after a webhook
   * Reconcile pull: heavier calls on schedule to detect drift
That gives the rigor we need: RU changes trigger a delta, delta causes a targeted pull, Sync updates only the “imported default” layer while preserving curated values.
________________




Event
Event Service
Service Owner: TBD Base URL: /api/v1/events Source of Truth: System event bus, audit log, routing configuration Migration Status: Not Started


________________


Overview
The central event bus for the platform. All system events flow through here. Classifies, routes, and delegates to five subsystems:


Subsystem
	Purpose
	Audit
	Permanent record of state changes
	Alerts
	PagerDuty, Slack, email
	Logging
	Datadog, CloudWatch, structured logs
	Monitoring
	Metrics emission, dashboards
	Analytics
	Amplitude, Segment, business intelligence
	

Designed for:


* Firehose scale — Every event, every service, every action
* Fault tolerance — No event loss, graceful degradation
* Loose coupling — Producers fire and forget
* Automatic idempotency — Content-addressable event IDs
Architecture
┌──────────────────────────────────────────────────────────────────┐
│                         Event Service                            │
├──────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌─────────────┐    ┌─────────────┐    ┌─────────────────────┐   │
│  │    SDK      │───▶│   Kafka     │───▶│    Classifier       │   │
│  │  (emit)     │    │ events.raw  │    │  (dedup, validate,  │   │
│  └─────────────┘    └─────────────┘    │   route by bitmask) │   │
│                                        └───────────┬─────────┘   │
│                                                    │             │
│         ┌──────────────┬───────────────┬───────────┼─────────┐   │
│         │              │               │           │         │   │
│         ▼              ▼               ▼           ▼         ▼   │
│   ┌──────────┐  ┌──────────┐   ┌──────────┐ ┌──────────┐ ┌─────┐ │
│   │  Audit   │  │  Alerts  │   │ Logging  │ │Monitoring│ │Anlyt│ │
│   │ Handler  │  │ Handlers │   │ Handlers │ │ Handlers │ │ics  │ │
│   └────┬─────┘  └────┬─────┘   └────┬─────┘ └────┬─────┘ └──┬──┘ │
│        │             │              │            │          │    │
│        ▼             ▼              ▼            ▼          ▼    │
│   ┌────────┐   ┌──────────┐   ┌──────────┐ ┌──────────┐ ┌─────┐  │
│   │ Postgr │   │PagerDuty │   │ Datadog  │ │Prometheus│ │Ampli│  │
│   │   es   │   │  Slack   │   │CloudWatch│ │  StatsD  │ │ tude│  │
│   │ (audit)│   │  Email   │   │  Stdout  │ │          │ │Segmt│  │
│   └────────┘   └──────────┘   └──────────┘ └──────────┘ └─────┘  │
│                                                                  │
└──────────────────────────────────────────────────────────────────┘
Flow
1. Emit — Services call SDK, which computes content hash as event_id
2. Ingest — SDK publishes to events.raw Kafka topic
3. Classify — Classifier dedupes, validates, routes to subsystem topics via bitmask
4. Fan-out — Each subsystem consumes its own topic independently
5. Deliver — Handlers batch and send to external destinations
System Dependencies
Dependency
	Role
	Kafka
	Event transport, persistence, backpressure
	Redis
	Dedup cache
	PostgreSQL
	Audit permanent storage
	Responsibilities
* Event ingestion and deduplication
* Content-addressable event IDs (automatic idempotency)
* Event classification and routing
* Audit log persistence
* Alert delivery (PagerDuty, Slack, Email)
* Log shipping (Datadog, CloudWatch)
* Metrics emission (Prometheus, StatsD)
* Analytics delivery (Amplitude, Segment, BigQuery)
Does NOT Own
Concern
	Owner
	Business logic
	Individual services
	Alert escalation policies
	PagerDuty
	Dashboard definitions
	Datadog, Grafana
	

________________


Event Schema
{
  "event_id": "evt_a1b2c3d4e5f6...",
  "event_kind": "booking.created",
  "entity_kind": "booking",
  "entity_id": "booking_abc123",
  "workflow_kind": "guest_checkout",
  "workflow_id": "wf_xyz789",
  "session_id": "sess_def456",
  "actor": {
    "kind": "user",
    "id": "user_456",
    "ip": "192.168.1.100",
    "authority": "guest"
  },
  "context": {
    "unit_id": "unit_123",
    "price_cents": 225000
  },
  "external_refs": [
    { "kind": "ubr", "id": "5HueCGU8rMjxEXxiPuD5BDku4MkFqeZyd4dZ1jvhTVqvbTLvyTJ" },
    { "kind": "pms_confirmation", "id": "GY-99887" },
    { "kind": "stripe_charge", "id": "ch_abc123" }
  ],
  "timestamp": "2025-03-10T14:32:01.123Z",
  "level": "info"
}

Field
	Type
	Required
	Description
	event_id
	string
	Yes
	Content-addressable hash (computed by SDK)
	event_kind
	string
	Yes
	Event type (e.g., booking.created)
	entity_kind
	string
	Yes
	Entity type (e.g., booking)
	entity_id
	string
	Yes
	Entity identifier
	workflow_kind
	string
	No
	Workflow type (e.g., guest_checkout)
	workflow_id
	string
	No
	Workflow identifier
	session_id
	string
	No
	Session identifier
	actor
	object
	Yes
	Who performed the action
	context
	object
	No
	Event-specific payload (JSONB)
	external_refs
	array
	No
	External system references
	timestamp
	ISO8601
	Yes
	When event occurred
	level
	string
	No
	debug, info, warn, error, critical
	External Reference Kinds
Kind
	Description
	Example ID
	ubr
	Universal Booking Reference
	5HueCGU8rMjxEXxiPuD5BDku...
	pms_confirmation
	PMS confirmation code
	GY-99887
	stripe_charge
	Stripe charge ID
	ch_abc123
	stripe_payout
	Stripe payout ID
	po_xyz789
	stripe_refund
	Stripe refund ID
	re_def456
	airbnb_reservation
	Airbnb confirmation
	HMABCDEF
	vrbo_reservation
	VRBO confirmation
	HA-12345
	booking_com_reservation
	Booking.com ID
	1234567890
	

________________


Content-Addressable Event ID
Events are identified by a hash of their content. Same content = same ID = automatic idempotency.
Hash Computation
import hashlib
import json


def compute_event_id(
    event_kind: str,
    entity_kind: str,
    entity_id: str,
    actor: dict,
    context: dict,
    workflow_id: str = None,
    external_refs: list = None
) -> str:
    """
    Compute content-addressable event ID.


    Excludes: timestamp, session_id, level (metadata, not content)
    Includes: everything that defines "what happened"
    """
    content = {
        "event_kind": event_kind,
        "entity_kind": entity_kind,
        "entity_id": entity_id,
        "actor_kind": actor["kind"],
        "actor_id": actor["id"],
        "context": _normalize(context),
    }


    if workflow_id:
        content["workflow_id"] = workflow_id


    if external_refs:
        content["external_refs"] = sorted(
            [f"{r['kind']}:{r['id']}" for r in external_refs]
        )


    canonical = json.dumps(content, sort_keys=True, separators=(",", ":"))
    hash_bytes = hashlib.sha256(canonical.encode()).digest()


    return f"evt_{hash_bytes[:16].hex()}"


def _normalize(obj):
    """Recursively sort dicts for stable hashing."""
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_normalize(x) for x in obj]
    return obj
Idempotency Behavior
Scenario
	Same event_id?
	Result
	Retry of same event
	Yes
	Deduplicated
	Same action, different time
	Yes
	Deduplicated within window
	Same action, hours apart
	Yes
	Deduplicated (same content)
	Similar action, different entity
	No
	Stored separately
	Same entity, different action
	No
	Stored separately
	Deduplication
class Deduplicator:
    def __init__(self, redis: Redis, window_seconds: int = 300):
        self.redis = redis
        self.window = window_seconds


    def is_duplicate(self, event_id: str) -> bool:
        key = f"event:seen:{event_id}"
        is_new = self.redis.setnx(key, "1")


        if is_new:
            self.redis.expire(key, self.window)
            return False


        return True

________________


SDK
Services use a thin client that computes the content hash and publishes:


from wander.events import emit


# Minimal
emit(
    event_kind="booking.created",
    entity_kind="booking",
    entity_id=booking.id,
    actor={"kind": "user", "id": user.id}
)


# Full
emit(
    event_kind="booking.created",
    entity_kind="booking",
    entity_id=booking.id,
    workflow_kind="guest_checkout",
    workflow_id=workflow.id,
    session_id=request.session_id,
    actor={
        "kind": "user",
        "id": user.id,
        "ip": request.ip,
        "authority": "guest"
    },
    context={
        "unit_id": unit.id,
        "price_cents": 225000,
        "nights": 5
    },
    external_refs=[
        {"kind": "ubr", "id": ubr_token},
        {"kind": "pms_confirmation", "id": pms_response.confirmation_id},
        {"kind": "stripe_charge", "id": charge.id}
    ],
    level="info"
)
SDK Implementation
import hashlib
import json
from datetime import datetime
from threading import Thread
from queue import Queue, Empty


class EventSDK:
    def __init__(self, kafka_brokers: list[str], buffer_size: int = 10000):
        self.producer = KafkaProducer(
            bootstrap_servers=kafka_brokers,
            value_serializer=lambda v: json.dumps(v).encode()
        )
        self.buffer = Queue(maxsize=buffer_size)
        self.running = True


        Thread(target=self._sender, daemon=True).start()


    def emit(
        self,
        event_kind: str,
        entity_kind: str,
        entity_id: str,
        actor: dict,
        context: dict = None,
        workflow_kind: str = None,
        workflow_id: str = None,
        session_id: str = None,
        external_refs: list = None,
        level: str = "info"
    ):
        context = context or {}
        external_refs = external_refs or []


        event_id = self._compute_id(
            event_kind, entity_kind, entity_id, actor,
            context, workflow_id, external_refs
        )


        event = {
            "event_id": event_id,
            "event_kind": event_kind,
            "entity_kind": entity_kind,
            "entity_id": entity_id,
            "actor": actor,
            "context": context,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": level
        }


        if workflow_kind:
            event["workflow_kind"] = workflow_kind
        if workflow_id:
            event["workflow_id"] = workflow_id
        if session_id:
            event["session_id"] = session_id
        if external_refs:
            event["external_refs"] = external_refs


        try:
            self.buffer.put_nowait(event)
        except:
            logger.error(f"Event buffer full, dropping: {event_id}")


    def _sender(self):
        while self.running:
            try:
                event = self.buffer.get(timeout=0.1)
                self.producer.send("events.raw", value=event)
            except Empty:
                continue
            except Exception as e:
                logger.error(f"Failed to send event: {e}")
                self.buffer.put(event)


_sdk = None


def init(kafka_brokers: list[str]):
    global _sdk
    _sdk = EventSDK(kafka_brokers)


def emit(**kwargs):
    if _sdk is None:
        raise RuntimeError("Event SDK not initialized. Call init() first.")
    _sdk.emit(**kwargs)

________________


Kafka Topics
Topic
	Purpose
	Partitions
	Retention
	events.raw
	Ingest firehose
	32
	7 days
	events.audit
	Audit subsystem
	16
	7 days
	events.alert
[bs]	
Alerts subsystem
	8
	1 day
	events.log
[bt]	
Logging subsystem
	16
	1 day
	events.metric
[bu]	
Monitoring subsystem
	8
	1 day
	events.analytics
[bv]	
Analytics subsystem
	8
	3 days
	events.dlq
	Dead letter queue
	8
	30 days
	

Partitioned by entity_id hash for ordering within an entity.


________________


Destination Bitmask
Flags
class Dest:
    NONE        = 0x0000


    # Alerting
    PAGERDUTY   = 0x0001
    SLACK       = 0x0002
    EMAIL       = 0x0004


    # Logging
    DATADOG     = 0x0008
    CLOUDWATCH  = 0x0010
    STDOUT      = 0x0020


    # Metrics
    PROMETHEUS  = 0x0040
    STATSD      = 0x0080


    # Analytics
    AMPLITUDE   = 0x0100
    SEGMENT     = 0x0200
    BIGQUERY    = 0x0400


    # Internal
    AUDIT       = 0x1000


    # Convenience
    ALL_ALERTS    = PAGERDUTY | SLACK | EMAIL
    ALL_LOGS      = DATADOG | CLOUDWATCH | STDOUT
    ALL_METRICS   = PROMETHEUS | STATSD
    ALL_ANALYTICS = AMPLITUDE | SEGMENT | BIGQUERY
Handler Registry
class HandlerRegistry:
    def __init__(self):
        self.handlers: dict[int, Handler] = {}


    def register(self, flag: int, handler: Handler):
        self.handlers[flag] = handler


    def dispatch(self, destinations: int, event: dict, config: dict):
        for flag, handler in self.handlers.items():
            if destinations & flag:
                handler.enqueue(event, config)


# At startup
registry = HandlerRegistry()
registry.register(Dest.PAGERDUTY, PagerDutyHandler(os.environ["PAGERDUTY_KEY"]))
registry.register(Dest.SLACK, SlackHandler(os.environ["SLACK_WEBHOOK"]))
registry.register(Dest.DATADOG, DatadogHandler(os.environ["DD_API_KEY"]))
registry.register(Dest.PROMETHEUS, PrometheusHandler(prometheus_registry))
registry.register(Dest.AMPLITUDE, AmplitudeHandler(os.environ["AMPLITUDE_KEY"]))

________________


Classifier
Reads events.raw, deduplicates, validates, routes to subsystem topics.


class Classifier:
    def __init__(self, config_path: str):
        self.config = load_yaml(config_path)
        self.consumer = KafkaConsumer("events.raw", group_id="classifier")
        self.producers = {
            "audit": KafkaProducer("events.audit"),
            "alert": KafkaProducer("events.alert"),
            "log": KafkaProducer("events.log"),
            "metric": KafkaProducer("events.metric"),
            "analytics": KafkaProducer("events.analytics"),
            "dlq": KafkaProducer("events.dlq")
        }
        self.dedup = Deduplicator(Redis(), window_seconds=300)


    def run(self):
        for message in self.consumer:
            try:
                event = json.loads(message.value)
                self.process(event)
                self.consumer.commit()
            except Exception as e:
                logger.error(f"Classifier error: {e}")
                self.producers["dlq"].send(message.value)


    def process(self, event: dict):
        if self.dedup.is_duplicate(event["event_id"]):
            metrics.increment("events_deduplicated")
            return


        destinations = self.route(event)


        if destinations & Dest.AUDIT:
            self.producers["audit"].send(event)
        if destinations & Dest.ALL_ALERTS:
            self.producers["alert"].send(event)
        if destinations & Dest.ALL_LOGS:
            self.producers["log"].send(event)
        if destinations & Dest.ALL_METRICS:
            self.producers["metric"].send(event)
        if destinations & Dest.ALL_ANALYTICS:
            self.producers["analytics"].send(event)


    def route(self, event: dict) -> int:
        for rule in self.config.get("rules", []):
            if self.matches(event, rule.get("match", {})):
                return self.resolve_destinations(rule["destinations"])
        return self.resolve_destinations(self.config["default"]["destinations"])

________________


Routing Configuration
Stored in config file (/etc/wander/event-service/routing.yaml). Restart to apply changes.[bw][bx]


destination_aliases:
  booking_standard: [AUDIT, SLACK, DATADOG, AMPLITUDE]
  critical_alert: [AUDIT, PAGERDUTY, SLACK, DATADOG]
  analytics_only: [AMPLITUDE, SEGMENT, BIGQUERY]
  log_default: [DATADOG, PROMETHEUS]


rules:
  - match: { event_kind: "booking.*" }
    destinations: booking_standard
    slack_channel: "#bookings"


  - match: { level: critical }
    destinations: critical_alert
    slack_channel: "#ops-critical"
    pagerduty_severity: critical


  - match: { event_kind: "search.*" }
    destinations: analytics_only


  - match: { event_kind: "pms.sync_failed" }
    destinations: [AUDIT, SLACK, DATADOG, PAGERDUTY]
    slack_channel: "#integrations"


default:
  destinations: log_default

________________


Subsystem: Audit
Permanent, append-only record of state-changing events. All events go to PostgreSQL.


class AuditSubsystem:
    def __init__(self):
        self.consumer = KafkaConsumer("events.audit", group_id="audit")
        self.db = PostgreSQL()


    def run(self):
        for message in self.consumer:
            event = json.loads(message.value)
            self.store(event)
            self.consumer.commit()


    def store(self, event: dict):
        self.db.execute("""
            INSERT INTO audit_event (
                event_id, event_kind, entity_kind, entity_id,
                workflow_kind, workflow_id, session_id,
                actor_kind, actor_id, actor_ip, actor_authority,
                context, external_refs, level, timestamp
            ) VALUES (
                %(event_id)s, %(event_kind)s, %(entity_kind)s, %(entity_id)s,
                %(workflow_kind)s, %(workflow_id)s, %(session_id)s,
                %(actor_kind)s, %(actor_id)s, %(actor_ip)s, %(actor_authority)s,
                %(context)s, %(external_refs)s, %(level)s, %(timestamp)s
            )
            ON CONFLICT (event_id) DO NOTHING
        """, self._extract_params(event))

________________


Subsystem: Alerts
Routes events to PagerDuty, Slack, email.


class AlertsSubsystem:
    def __init__(self):
        self.consumer = KafkaConsumer("events.alert", group_id="alerts")
        self.handlers = {
            Dest.PAGERDUTY: PagerDutyHandler(),
            Dest.SLACK: SlackHandler(),
            Dest.EMAIL: EmailHandler()
        }


    def process(self, event: dict):
        destinations = event.get("_alert_destinations", 0)
        for flag, handler in self.handlers.items():
            if destinations & flag:
                handler.send(event)

________________


Subsystem: Logging[by][bz][ca]
Forwards to Datadog, CloudWatch, stdout. Level filtering.
Configuration
{
  "default_level": "info",
  "level_overrides": {
    "booking.*": "debug",
    "health.*": "warn"
  }
}
Processing
class LoggingSubsystem:
    LEVELS = {"debug": 0, "info": 1, "warn": 2, "error": 3, "critical": 4}


    def should_log(self, event: dict) -> bool:
        event_level = event.get("level", "info")
        min_level = self._get_min_level(event["event_kind"])


        return self.LEVELS.get(event_level, 1) >= self.LEVELS.get(min_level, 1)

________________


Subsystem: Monitoring[cb]
Extracts metrics from events. Emits to Prometheus, StatsD.
Configuration
{
  "metrics": [
    {
      "name": "events_total",
      "type": "counter",
      "match": { "event_kind": "*" },
      "labels": ["event_kind", "level"]
    },
    {
      "name": "bookings_total",
      "type": "counter",
      "match": { "event_kind": "booking.created" },
      "labels": ["entity_kind"]
    },
    {
      "name": "booking_value_cents",
      "type": "histogram",
      "match": { "event_kind": "booking.created" },
      "value_path": "context.price_cents",
      "labels": ["entity_kind"],
      "buckets": [10000, 50000, 100000, 250000, 500000, 1000000]
    }
  ]
}

________________


Subsystem: Analytics
Forwards to Amplitude, Segment, BigQuery[cc] for business intelligence.
Configuration
{
  "include": ["booking.*", "search.*", "checkout.*"],
  "exclude": ["health.*", "debug.*"],
  "anonymize_ip": true,
  "user_id_path": "actor.id",
  "batch_size": 100,
  "flush_interval_seconds": 10
}

________________


Handler Base Class
All handlers share batching, circuit breaking, retry logic.


class Handler(ABC):
    def __init__(self, name: str, batch_size: int = 100, flush_interval: int = 5):
        self.name = name
        self.batch_size = batch_size
        self.flush_interval = flush_interval
        self.queue = Queue()
        self.circuit = CircuitBreaker()
        self.batch = []


        threading.Thread(target=self._run, daemon=True).start()


    @abstractmethod
    def send_batch(self, events: list[dict], configs: list[dict]):
        pass


    def enqueue(self, event: dict, config: dict):
        self.queue.put((event, config))


    def _flush(self):
        if not self.batch:
            return


        if not self.circuit.allow():
            metrics.increment("handler_circuit_open", tags={"handler": self.name})
            self.batch = []
            return


        try:
            events = [e for e, _ in self.batch]
            configs = [c for _, c in self.batch]
            self.send_batch(events, configs)
            self.circuit.success()
            metrics.increment("handler_sent", len(events), tags={"handler": self.name})
        except Exception as e:
            self.circuit.failure()
            logger.error(f"{self.name} batch failed: {e}")
            metrics.increment("handler_failed", tags={"handler": self.name})
        finally:
            self.batch = []




class CircuitBreaker:
    def __init__(self, failure_threshold: int = 5, reset_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self.failures = 0
        self.last_failure = 0
        self.state = "closed"


    def allow(self) -> bool:
        if self.state == "closed":
            return True


        if self.state == "open":
            if time.time() - self.last_failure >= self.reset_timeout:
                self.state = "half-open"
                return True
            return False


        return True


    def success(self):
        self.failures = 0
        self.state = "closed"


    def failure(self):
        self.failures += 1
        self.last_failure = time.time()


        if self.failures >= self.failure_threshold:
            self.state = "open"

________________


Failure Handling
Failure
	Behavior
	SDK buffer full
	Drop event, log locally
	Kafka unavailable
	SDK buffers, retries
	Classifier down
	Events queue in events.raw
	Subsystem down
	Events queue in subsystem topic
	Handler fails
	Circuit breaker, retry, DLQ
	Redis unavailable
	Skip deduplication, continue processing
	PostgreSQL unavailable
	Audit pauses, Kafka holds
	External API down
	Circuit breaker opens, events buffer
	Dead Letter Queue
def process_with_dlq(self, message, max_retries: int = 3):
    event = json.loads(message.value)
    retries = event.get("_retries", 0)


    try:
        self.process(event)
    except Exception as e:
        if retries < max_retries:
            event["_retries"] = retries + 1
            event["_last_error"] = str(e)
            event["_last_attempt"] = datetime.utcnow().isoformat()
            self.retry_producer.send(event)
        else:
            self.dlq_producer.send(event)
            logger.error(f"Event to DLQ after {max_retries} retries: {event['event_id']}")

________________


API Reference
GET /events/audit
Query the audit log.


Request Parameters


Parameter
	Type
	Required
	Description
	entity_kind
	string
	No
	Filter by entity type
	entity_id
	string
	No
	Filter by entity ID
	event_kind
	string
	No
	Filter by event type (supports wildcards: booking.*)
	actor_id
	string
	No
	Filter by actor ID
	workflow_id
	string
	No
	Filter by workflow ID
	level
	string
	No
	Minimum level
	from
	datetime
	No
	Start timestamp
	to
	datetime
	No
	End timestamp
	limit
	int
	No
	Max results (default 100)
	cursor
	string
	No
	Pagination cursor
	

Response (200)


{
  "events": [
    {
      "event_id": "evt_a1b2c3d4e5f6",
      "event_kind": "booking.created",
      "entity_kind": "booking",
      "entity_id": "booking_abc123",
      "actor": {
        "kind": "user",
        "id": "user_456"
      },
      "context": {
        "price_cents": 225000
      },
      "timestamp": "2025-03-10T14:32:01.123Z",
      "level": "info"
    }
  ],
  "next_cursor": "eyJpZCI6...",
  "total": 1543
}

________________


GET /events/audit/{event_id}
Get a specific event by ID.


________________


GET /events/audit/entity/{entity_kind}/{entity_id}
Get all events for an entity.


Response (200)


{
  "entity_kind": "booking",
  "entity_id": "booking_abc123",
  "events": [
    {
      "event_id": "evt_1",
      "event_kind": "booking.created",
      "timestamp": "2025-03-10T14:32:01Z"
    },
    {
      "event_id": "evt_2",
      "event_kind": "booking.payment_received",
      "timestamp": "2025-03-10T14:32:05Z"
    },
    {
      "event_id": "evt_3",
      "event_kind": "booking.confirmed",
      "timestamp": "2025-03-10T14:32:06Z"
    }
  ]
}

________________


GET /events/audit/workflow/{workflow_kind}/{workflow_id}
Get all events for a workflow.


________________


GET /events/audit/actor/{actor_kind}/{actor_id}
Get all events by an actor.


________________


GET /events/audit/ref/{ref_kind}/{ref_id}
Get all events by external reference.


-- Find all events for a UBR
SELECT * FROM audit_event
WHERE external_refs @> '[{"kind": "ubr", "id": "5HueCGU8rMjxEXxiPuD5BDku..."}]'
ORDER BY timestamp;


-- Find all events for a Stripe charge
SELECT * FROM audit_event
WHERE external_refs @> '[{"kind": "stripe_charge", "id": "ch_abc123"}]'
ORDER BY timestamp;

________________


GET /events/config/routing
Get current routing configuration. Admin only.


________________


GET /events/handlers/status
Get status of all handlers. Admin only.


Response (200)


{
  "handlers": [
    {
      "name": "audit",
      "status": "HEALTHY",
      "queue_depth": 0,
      "processed_24h": 150432,
      "failed_24h": 0
    },
    {
      "name": "slack",
      "status": "HEALTHY",
      "queue_depth": 3,
      "processed_24h": 2341,
      "failed_24h": 2
    },
    {
      "name": "amplitude",
      "status": "DEGRADED",
      "queue_depth": 450,
      "processed_24h": 45000,
      "failed_24h": 120,
      "last_error": "Rate limited"
    }
  ]
}

________________


GET /events/metrics
Get event processing metrics.


Response (200)


{
  "ingestion": {
    "events_per_second": 52.3,
    "events_24h": 4519234,
    "deduplicated_24h": 1234
  },
  "processing": {
    "latency_p50_ms": 5,
    "latency_p95_ms": 25,
    "latency_p99_ms": 120
  },
  "by_event_kind": {
    "booking.created": 1543,
    "booking.cancelled": 234,
    "availability.blocked": 45234,
    "sync.completed": 12453
  }
}

________________


GET /health
Health endpoint.


Response (200)


{
  "status": "healthy",
  "classifier": { "status": "running", "lag": 12 },
  "subsystems": {
    "audit": { "status": "running", "lag": 5 },
    "alerts": { "status": "running", "lag": 0 },
    "logging": { "status": "running", "lag": 8 },
    "monitoring": { "status": "running", "lag": 2 },
    "analytics": { "status": "running", "lag": 15 }
  },
  "handlers": {
    "pagerduty": { "circuit": "closed" },
    "slack": { "circuit": "closed" },
    "datadog": { "circuit": "half-open" }
  },
  "dependencies": {
    "kafka": "connected",
    "redis": "connected",
    "postgres": "connected"
  }
}

________________


Data Model
audit_event
CREATE TABLE audit_event (
    event_id        VARCHAR(100) PRIMARY KEY,
    event_kind      VARCHAR(100) NOT NULL,


    entity_kind     VARCHAR(50) NOT NULL,
    entity_id       VARCHAR(100) NOT NULL,


    workflow_kind   VARCHAR(50),
    workflow_id     VARCHAR(100),


    session_id      VARCHAR(100),


    actor_kind      VARCHAR(20) NOT NULL,
    actor_id        VARCHAR(100) NOT NULL,
    actor_ip        INET,
    actor_authority VARCHAR(100),


    context         JSONB NOT NULL DEFAULT '{}',


    external_refs   JSONB NOT NULL DEFAULT '[]',


    level           VARCHAR(10),


    timestamp       TIMESTAMPTZ NOT NULL
);


CREATE INDEX idx_audit_entity ON audit_event (entity_kind, entity_id, timestamp);
CREATE INDEX idx_audit_workflow ON audit_event (workflow_kind, workflow_id, timestamp);
CREATE INDEX idx_audit_actor ON audit_event (actor_kind, actor_id, timestamp);
CREATE INDEX idx_audit_session ON audit_event (session_id, timestamp);
CREATE INDEX idx_audit_event_kind ON audit_event (event_kind, timestamp);


-- GIN index for external_refs array lookup
CREATE INDEX idx_audit_external_refs ON audit_event USING GIN (external_refs);


-- Immutability
CREATE OR REPLACE FUNCTION prevent_modification() RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are immutable';
END;
$$ LANGUAGE plpgsql;


CREATE TRIGGER audit_no_update BEFORE UPDATE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION prevent_modification();
CREATE TRIGGER audit_no_delete BEFORE DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION prevent_modification();
event_schema
Schema registry for validation.


CREATE TABLE event_schema (
    event_kind      VARCHAR(100) PRIMARY KEY,
    json_schema     JSONB NOT NULL
);
dead_letter_queue
Failed events for manual review.


CREATE TABLE dead_letter_queue (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),


    event_id            VARCHAR(64) NOT NULL,
    handler             VARCHAR(50) NOT NULL,


    event_type          VARCHAR(200) NOT NULL,
    payload             JSONB NOT NULL,


    failure_reason      TEXT NOT NULL,
    failure_count       INTEGER NOT NULL DEFAULT 1,
    last_failure_at     TIMESTAMPTZ NOT NULL,


    status              VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    -- PENDING, RESOLVED, IGNORED


    resolved_by         UUID,
    resolved_at         TIMESTAMPTZ,
    resolution_note     TEXT,


    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);


CREATE INDEX idx_dlq_status ON dead_letter_queue(status) WHERE status = 'PENDING';

________________


Observability
Metrics
Metric
	Type
	Description
	event_service_received_total
	counter
	Events received by topic
	event_service_processed_total
	counter
	Events processed by subsystem
	event_service_deduplicated_total
	counter
	Duplicates dropped
	event_service_kafka_lag
	gauge
	Consumer lag per topic
	event_service_handler_sent_total
	counter
	Events sent by handler
	event_service_handler_failed_total
	counter
	Handler failures
	event_service_circuit_open_total
	counter
	Circuit breaker opens
	

________________


SLOs
Operation
	p95 Latency
	SDK emit
	< 5 ms
	Classifier
	< 10 ms
	Audit store
	< 20 ms
	Alert delivery
	< 500 ms
	Log forward
	< 100 ms
	Metric emit
	< 10 ms
	Analytics batch
	< 1 s
	

Metric
	Target
	Event loss
	< 0.001%
	Kafka lag (normal)
	< 1000
	Circuit breaker recovery
	< 60 s
	Event delivery rate
	> 99.99%
	Deduplication accuracy
	100%
	Audit log durability
	100%
	

________________


Retention
Data
	Retention
	events.raw
	7 days
	Subsystem topics
	1-3 days
	Audit (PostgreSQL)
	7 years[cd][ce]
	DLQ
	30 days
	Dedup keys (Redis)
	5 minutes
	

________________


Scaling
Scale
	Events/sec
	Kafka Partitions
	Consumers
	Current
	100
	8
	1 per subsystem
	10x
	1,000
	16
	2 per subsystem
	100x
	10,000
	32
	4 per subsystem
	1000x
	100,000
	64
	8+ per subsystem
	

Add consumers to a subsystem's consumer group for horizontal scaling. Kafka handles partition assignment.


________________


Current State Analysis
Existing Tables
Table
	Status
	Event
	Minimal structure, no correlation/causation
	OsAudit
	Good audit structure
	OsAuditLog
	Good field-level change tracking
	OsTaskEvent
	Task-specific, could be generalized
	

________________


Migration Strategy
Phase 1: Create Tables
1. Create audit_event table
2. Set up Kafka topics
3. Deploy routing config file
Phase 2: Audit Log Migration
1. Migrate OsAudit to audit_event
2. Generate content-addressable IDs for existing events
Phase 3: SDK Rollout
1. Deploy Event SDK to services
2. Update event publishers to use SDK
3. Dual-write to old Event table during migration
Phase 4: Cutover
1. Stop writing to old tables
2. Archive old data
Rollback Plan
1. Feature flags control event routing
2. Old tables retained (read-only) for 90 days
3. DLQ catches any processing failures


________________


Event Type Taxonomy
<service>.<entity>.<action>


booking.created
booking.confirmed
booking.paid
booking.cancelled
booking.checked_in
booking.checked_out


property.created
property.updated
property.published
property.archived


availability.blocked
availability.unblocked
availability.synced


pricing.rate_changed
pricing.rates_updated


payment.invoice_created
payment.succeeded
payment.failed
payment.refunded
payment.payout_created
payment.payout_sent


sync.webhook_received
sync.push_completed
sync.pull_completed
sync.reconciliation_drift

________________


Data Volume Estimates
Table
	Current
	Projected
	Event
	~500K
	domain_event ~500K initial
	OsAudit
	~1M
	audit_event ~1M
	OsAuditLog
	~5M
	audit_field_change ~5M
	—
	—
	dead_letter_queue ~1K
	

Growth projection: ~100K events/day, 30-day retention for delivery tracking


________________


Test Scenarios
test_cases:
  - name: query_audit_by_entity
    endpoint: GET /events/audit
    request:
      entity_kind: "booking"
      entity_id: "{{booking_id}}"
    expected:
      status: 200
      body:
        events:
          - entity_id: "{{booking_id}}"


  - name: query_audit_by_event_kind_wildcard
    endpoint: GET /events/audit
    request:
      event_kind: "booking.*"
    expected:
      status: 200


  - name: get_entity_timeline
    endpoint: GET /events/audit/entity/booking/{entity_id}
    setup:
      - booking exists with multiple events
    expected:
      status: 200
      body:
        entity_kind: "booking"


  - name: event_deduplication
    description: Same content produces same event_id
    test_type: unit
    input:
      event_kind: "booking.created"
      entity_kind: "booking"
      entity_id: "booking_123"
      actor:
        kind: "user"
        id: "user_456"
      context:
        price_cents: 100000
    expected:
      assert: event_id_1 == event_id_2


  - name: different_content_different_id
    description: Different content produces different event_id
    test_type: unit
    input_1:
      event_kind: "booking.created"
      entity_id: "booking_123"
    input_2:
      event_kind: "booking.created"
      entity_id: "booking_456"
    expected:
      assert: event_id_1 != event_id_2



[a]@cam@wander.com
_Assigned to cam@wander.com_
[b]@tuan@wander.com
[c]@shawn@wander.com
_Assigned to shawn@wander.com_
[d]@elwin@wander.com
[e]@otavio@wander.com
_Assigned to otavio@wander.com_
[f]@renato@wander.com
1 total reaction
Renato Silveira reacted with 👍 at 2026-01-30 19:36 PM
[g]@erik@wander.com
_Assigned to erik@wander.com_
[h]@ayo@wander.com
[i]@pierce.briney@wander.com
[j]@dushyant@wander.com
[k]@devon@wander.com
[l]Which DB strategy?
1. Single shared schema (current)
wander_db
schema: public
- Unit
- v2Booking


2. Shared instance, separate schemas
wander_db
schema: property_service
schema: booking_service
schema: legacy
=> logical isolation, can split later


3. Separate instance per service 
property_service_db
booking_service_db
=> true isolation
[m]Separate instances.  The service should be the authoritative accessor for the data under its control.
1 total reaction
Tuan Vu Cong reacted with 👍 at 2026-01-09 04:22 AM
[n]_Marked as resolved_
[o]_Re-opened_
[p]we will have to handle more case, as min nights, gap...
[q]The theory is that each of many systems perform searches according to the criteria they're given.  The search then intersects those results for the final collection of results returned to the user.


If you search for "beach" homes available on Feb 1, 2 and 3rd for a three-night stay...


Availability says units: {A, B, C, D, E, and F} are available.
Property type "beach" matches units: {A, C, D, F, G, H, and I}
The intersection is {A, C, D, F}
When loading those properties, you can do something like:


properties = {new Property(p) for p in intersect(avail, prop_type)}
return {p for p in properties if p.mlos <= los}


Or do it at the DB level as a constraint.
SELECT * FROM property WHERE id IN {$ids} AND mlos <= $los;


This way your DB filters property-specific constraints before returning any data rather than filtering those properties after the fact.
[r]So the complex dates filters logic won't live on this service, got it!
[s]https://linear.app/wander/issue/API-275/remove-pricing-generation-config-tables
[t]This was made when we had to migrate from the pricing generation feature owned by the data team
[u]Should we handle the currency on the pricing side and not invoice side ?
I'm thinking about the related conversion logic we will have to implement for promo code with fixed $ amount.
[v]I think prices should be in the currency of the property and any conversion should be done in the UI as a convenience to the user.  Some places, like Mexico, require prices to be in their local currency.  If we're not the merchant of record, we have leeway there.  But, it's good practice, as a rule, to have the currency of record be local and any conversion happen as a convenience to the user.
[w]This mean we can only have 1 currency per property ?
[x]I think that's the best way otherwise as the prices drift there'll be an issue.  That's why we'd want to use the property's currency as the reference currency.
[y]At the moment this is triggered by the stripe webhook once the payment intent is confirmed. 


What happens if this flow isn't triggered after the user pay?
[z]I think theres way more work for updating all the code and logic than creating the new service from scratch
[aa]I think you're right.
[ab]how and where do you want to decode it ? 


If its just used as an id, why adding this field ?
[ac]The goal with UBR is to have a signed request with a countersigned confirmation.  The token is a unique ID which also encodes the authoritative request and response for transparency.  Right now, we'd be the only system doing it.  But, it is a mechanism for building trust in the distributed system of STR.
[ad]I get it! Do you have an opinion about adding a prefix as stripe does ? (cus_..., py_..., pi_.., ch_...). I found it easier when we debug chunks of data
[ae]The reserved status logic will be handled by the availability /hold ?
[af]What's "reserved"?


Our system should have reservations (properties for which we are the system of record will have reservations).  And bookings (reservations made via our booking portal will have bookings).  And availability -- a fast cache of what's available and what's not which is non-authoritative. 


I assume we already have reservations and bookings as we already do those things.  We may need to refine those systems in the process of building this service.
[ag]I was thinking about the "awaiting payment" or "reserved" status: 
https://github.com/wandercom/wander/blob/2ca0d67fd8e9b3e9543d250dea8a7f0f037ea9f2/packages/infrastructure/utils/src/index.ts#L40-L47
[ah]I was thinking of an ephemeral store for in-flight (serialized state object) so we don't overload the reservations table or booking table with stub info.
[ai]Presumably R2, or GCS?
[aj]I think R2 is our current standard.  But, whichever.
[ak]Am I to understand that this is seldom used, and only in an emergency? If so, we should probably emit a warning when we apply admin diffs, hooked up to a pager somewhere
[al]Yes.  Events emitted should be able to annotate that for the event system.  We're also looking at a full RBAC access audit and refinement.
[am]This shouldn't be handled by the logic instead the client?
[an]I could see this being useful if Property service cache includes derived data from downstream services, but I agree -- maybe this is left over from before the overrides system was designed?
[ao]Currently, when we force a sync, the updates don't appear and people think the sync isn't working because the cache timeout is 30 minutes.


If property data is being cached and served by the property service, the sync service should be able to ask the property service to flush its cache.
[ap]some quick back of the napkin grepping,


```
$ git grep -ho '\<\(db\|readonlyDb\)\>\.unit\.[a-zA-Z]*\>' -- '*.ts' | sort | uniq -c | sort -n
1 db.unit.deleteMany
1 db.unit.upsert
1 readonlyDb.unit.findFirst
1 readonlyDb.unit.findFirstOrThrow
4 db.unit.delete
6 db.unit.updateMany
6 readonlyDb.unit.findUniqueOrThrow
7 db.unit.create
7 db.unit.findFirstOrThrow
19 db.unit.count
19 readonlyDb.unit.findUnique
27 db.unit.findFirst
40 db.unit.update
55 db.unit.findUniqueOrThrow
102 db.unit.findMany
104 db.unit.findUnique
```


400 "unit" database queries with ~300 of those being `find`'s, the mutations being


```
$ git grep -ho '\<\(db\|readonlyDb\)\>\.unit\.[a-zA-Z]*\>' -- '*.ts' | grep -iv find | sort | uniq -c | sort -n
1 db.unit.deleteMany
1 db.unit.upsert
4 db.unit.delete
6 db.unit.updateMany
7 db.unit.create
19 db.unit.count
40 db.unit.update
```


seems tractable once we can ensure parity with the receiver schemas
[aq]ignore the strikethrough, comments do not seem to permit formatting
[ar]I think there are more "property" related db calls such as:
```
const rooms = await db.unitRoom.findMany({
where: { unitId },
select: { id: true, type: true },
});


const airportsGuide = await db.unitActivity.findMany({
where: {
unitId,
type: 'AIRPORT',
},
take: 1,
});
``
[as]This feels redundant to the audit log with the added effect of creating a whole lot of complex overhead and edge cases.
[at]@devon@wander.com @otavio@wander.com @dushyant@wander.com @renato@wander.com 


This is envisioned for financial audit which has different constraints and needs than the audits in the event system which are more "casual" chain of event type things.  We wouldn't want financial things available to most people and this should make it easy to correlate to Stripe, which is our primary source of truth.


But, I'm open to alternatives.
[au]@elwin@wander.com Looks like there's something else we need to pick up as part of Property Service
[av]I know this was not intentional, just sharing for visibility that:


For availability and pricing, “delta” cannot be inferred without an explicit window or a PMS-supported change cursor. In practice this means delta must be implemented as bounded window pulls (hot windows) or webhook-scoped pull
[aw]_Marked as resolved_
[ax]_Re-opened_
[ay]I think sync should always pull as much information as it can.  Availability calculates deltas, I believe, from what it is given by the sync service.  Pricing may or may not depending on the cost and frequency of updates.


As for Property, it has a built-in (or should) mechanism for determining overrides on changes and must therefore have a plan of action for deltas.
[az]Rate limits are often per-credential/org, not global. One org hitting Guesty's limit doesn't affect others
[ba]If the API key has a public value (key name or org name) we can push which one(s) are being limited.  The idea here is quick visibility into how we're doing and to identify errors in the system (it should be using your  token bucket + backoff w/ alerts on 429s which note whether or not it thought it had a valid rate token).
[bb]To show accurate data we'll want to pass:


organization_id or unit_id params
[bc]This may exist already if your token bucket work is clean and portable.
[bd]This should be scoped by organization id or unit id depending on the scope of the tokens./ access keys
[be]This is a great addition
[bf]Btw: Hostaway doesn't support quotes. I think it is the only one, the rest should use it
[bg]For Hostaway does it have any meta information like creation timestamp?  If so, we can use a content addressable hash as its quote_id.  If not, there's no way to disambiguate and this should be allowed to be NULL.
[bh]`apply_external_blocks` is supplied by the `/force-acquire` route currently; it returns a coarse "there were conflicts" while acquiring the block, though we may want to convert this to a more granular "these were the days in conflict" as we escalate the conflict to an incidents channel.
[bi]see this for more detail:
https://github.com/wandercom/availability#post-force-acquire
[bj]In theory:
1. PMS is always source of record
2. For Channex, Wander is always the source of record


In any case, I think the work that `is_sor` is supposed to do could be taken from the sync_mode field
[bk]I thought we had at least one instance of a reservation record coming from two different external sources.  Perhaps that was an error in my memory, a fluke, or a situation we've accounted for already.


For Sites, as a direct-booking portal, we'd still defer to the PMS as system of record, I believe.  It should be the system that actually determines which booking to honor.


If it is derived from the sync_mode effectively, then it can be removed from the DB and added in (for convenience) to the model in the code.
[bl]Additionally, sync_enabled is equivalent to: sync_availability | sync_pricing | sync_property.


Can probably de-dupe that.  And to Shawn's point...
[bm]We should maintain in a separate table so that we can maintain metadata with type, rows processed, state, etc...
[bn]That should all be in the event system @devon@wander.com @dushyant@wander.com 


Here, we can denormalize the last sync per sync type. And @erik@wander.com @ayo@wander.com @pierce.briney@wander.com 
We can track: `last_availability_sync_attempt` and `last_availability_sync_success` for each type instead of status and time.


That allows us to deduce status (at least, success or fail) and will tell us when it was attempted and will tell us when the last data was actually pulled.  Same number of fields but slightly more (or different) information.  If a partial sync is necessary, we'd add a third field for status which could capture the nature of the partial attempt.
[bo]Wondering, when would a subsystem be skipped?
[bp]If we have a manual "sync" button, we should skip the automatic sync for those which are still on cool down.
[bq]Is this: Per-night? Per-stay? Percentage?
[br]Also some may be per-guest or per-pet?  Or even per-pet-per-night?
[bs]Deferring on this because grafana already does this. If we want to build out a property-level alerting framework that ties in to email as a downstream so we can notify operations and owners/operators of problems with their property, we can do that when timeframe allows.
[bt]Deferring on this as system logging is currently handled well by grafana. If we need property-based logging we can add this, but deferring until we get there.
[bu]Same. Provided by grafana currently. When we have valid requests from operations or owners/operators as to what metrics would be useful outside of just system-level metrics, we can add them.
[bv]Currently handled by a Google Run service. We can in-house that solution when we have available budget, as it will reduce the surface area we have to maintain.
[bw]we can accomplish this via k8s ConfigMap, periodically pulling the new map, doing some validity checks, and dynamically replacing while running.


The configmap itself can be updated via GHA deployment that reads from a file in the repo and updates the configmap value, similar to how we deploy schema migrations currently.


The value here is that we can individually deploy just the configmap without having to touch the services, and folks with write-access to the k8s cluster have a break-glass way to update the configmap without having to wait for GHA or the build process or a new deployment or whatever.
[bx]Could even make it so the configmap is picked up at boot and not refreshed afterwards, where deploying the new config touches the deployment manifest for the events service and causes an in-place redeployment without pushing a new image.
1 total reaction
Jeremy McEntire reacted with ➕ at 2026-01-08 23:23 PM
[by]We might not do this one, since the existing OTEL integration is low-enough of a maintenance burden and is already in place.
[bz]There may be value in tracking process logs explicitly with the intent to surface these to the user, this will need to be carefully designed to avoid spaghetti and maintain intelligibility.
[ca]Indeed.  It will be extremely helpful for business folks to be able to see a lot of information about the history of a booking and its payment successes and failures and other information.  Any PII would need a mechanism for cleaning after-the-fact.  We can easily justify it as a need for doing business (need to know who's paying and who's staying) but can be anonymized after a cool down period following the stay.  Might check with legal/compliance for details when dealing with PII; loop me in.
[cb]We might not do this one, since the existing OTEL integration is low-enough of a maintenance burden and is already in place.
1 total reaction
Jeremy McEntire reacted with ➕ at 2026-01-08 23:26 PM
[cc]1 total reaction
Guilherme Amorim reacted with 👀 at 2026-01-14 22:04 PM
[cd]7 years in Postgres is gonna be wild. We should consider keeping N months in something like TimescaleDB and then shuffling off older data to BigQuery. Data warehousing is a longer term goal, and should be slated for down the road.
[ce]BigQuery would suffice.  I'd prefer audit logs to be event-oriented to time-based.  So, I don't think it should be a timeseries, per se.