-- Property Service Schema
-- 4-tier override hierarchy: PMS(10) < PM(20) < Wander(30) < Admin(40)

CREATE TABLE IF NOT EXISTS property (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    organization_id UUID,
    name            VARCHAR(255) NOT NULL,
    slug            VARCHAR(255),
    description     TEXT,
    property_type   VARCHAR(50) DEFAULT 'HOUSE',
    status          VARCHAR(20) DEFAULT 'ACTIVE',
    is_bookable     BOOLEAN DEFAULT FALSE,
    
    -- Capacity
    bedrooms        SMALLINT DEFAULT 0,
    bathrooms       DECIMAL(3,1) DEFAULT 0,
    max_guests      SMALLINT DEFAULT 1,
    beds            SMALLINT DEFAULT 0,
    
    -- Location
    address_line1   VARCHAR(255),
    address_line2   VARCHAR(255),
    city            VARCHAR(100),
    state           VARCHAR(100),
    country         VARCHAR(2) DEFAULT 'US',
    postal_code     VARCHAR(20),
    latitude        DECIMAL(10,7),
    longitude       DECIMAL(10,7),
    region          VARCHAR(50),
    timezone        VARCHAR(50) DEFAULT 'America/New_York',
    
    -- Management
    management_type VARCHAR(20) DEFAULT 'BRANDED',  -- BRANDED or OPERATED
    pms_type        VARCHAR(30),
    
    -- Metadata
    amenities       JSONB DEFAULT '[]',
    images          JSONB DEFAULT '[]',
    house_rules     TEXT,
    
    currency        CHAR(3) DEFAULT 'USD',
    
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_property_org ON property(organization_id);
CREATE INDEX IF NOT EXISTS idx_property_region ON property(region);
CREATE INDEX IF NOT EXISTS idx_property_status ON property(status);
CREATE INDEX IF NOT EXISTS idx_property_bookable ON property(is_bookable) WHERE is_bookable = TRUE;

-- Override table: field-level overrides as diffs
CREATE TABLE IF NOT EXISTS property_override (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES property(id),
    field       VARCHAR(100) NOT NULL,
    from_value  JSONB,              -- Expected base value (null = always apply)
    to_value    JSONB NOT NULL,     -- Override value
    priority    SMALLINT NOT NULL,  -- 10=PMS, 20=PM, 30=Wander, 40=Admin
    actor       VARCHAR(100) NOT NULL,
    note        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (property_id, field, priority)
);

CREATE INDEX IF NOT EXISTS idx_override_property ON property_override(property_id);

-- Override conflicts (when sync updates invalidate from_value)
CREATE TABLE IF NOT EXISTS property_override_conflict (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id     UUID NOT NULL REFERENCES property(id),
    override_id     UUID NOT NULL REFERENCES property_override(id),
    field           VARCHAR(100) NOT NULL,
    old_base        JSONB,
    new_base        JSONB,
    status          VARCHAR(20) DEFAULT 'PENDING',   -- PENDING, RESOLVED
    resolution      VARCHAR(30),   -- KEEP_OVERRIDE, ACCEPT_NEW, NEW_OVERRIDE
    resolved_at     TIMESTAMPTZ,
    resolved_by     VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_conflict_property ON property_override_conflict(property_id);
CREATE INDEX IF NOT EXISTS idx_conflict_status ON property_override_conflict(status);

-- Fee configuration per property
CREATE TABLE IF NOT EXISTS property_fee (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES property(id),
    kind        VARCHAR(50) NOT NULL,    -- cleaning, pet, extra_guest, etc.
    label       VARCHAR(100) NOT NULL,
    amount_cents INTEGER NOT NULL,
    per         VARCHAR(20) NOT NULL DEFAULT 'PER_STAY',  -- PER_STAY, PER_NIGHT, PER_GUEST, PER_PET
    required    BOOLEAN DEFAULT FALSE,
    conditions  JSONB DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_fee_property ON property_fee(property_id);

-- Cancellation policy
CREATE TABLE IF NOT EXISTS cancellation_policy (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES property(id),
    name        VARCHAR(50) NOT NULL DEFAULT 'MODERATE',
    tiers       JSONB NOT NULL DEFAULT '[]',  -- [{days_before, refund_percent}]
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cancel_policy_property ON cancellation_policy(property_id);

-- External PMS mappings
CREATE TABLE IF NOT EXISTS property_external_id (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    property_id UUID NOT NULL REFERENCES property(id),
    pms_type    VARCHAR(30) NOT NULL,
    external_id VARCHAR(200) NOT NULL,
    is_sor      BOOLEAN DEFAULT FALSE,  -- System of Record
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    
    UNIQUE (pms_type, external_id)
);

CREATE INDEX IF NOT EXISTS idx_external_property ON property_external_id(property_id);
