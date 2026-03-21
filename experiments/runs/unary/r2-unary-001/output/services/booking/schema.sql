-- Booking Service Schema
-- Dual-table design: booking (Wander SoR) + inventory_block

-- Try to create btree_gist extension (needed for EXCLUDE constraint)
-- If it fails, the EXCLUDE constraint won't be created but booking still works
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS btree_gist;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'btree_gist not available - EXCLUDE constraint will not be created';
END
$$;

CREATE TABLE IF NOT EXISTS booking (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    ubr                 VARCHAR(100) UNIQUE NOT NULL,
    property_id         UUID NOT NULL,
    guest_id            UUID NOT NULL,
    guest_name          VARCHAR(200) NOT NULL,
    guest_email         VARCHAR(255) NOT NULL,
    guest_phone         VARCHAR(50),
    check_in            DATE NOT NULL,
    check_out           DATE NOT NULL,
    nights              INTEGER NOT NULL,
    guests_adults       INTEGER DEFAULT 1,
    guests_children     INTEGER DEFAULT 0,
    guests_infants      INTEGER DEFAULT 0,
    guests_pets         INTEGER DEFAULT 0,
    payment_intent_id   VARCHAR(100),
    customer_id         VARCHAR(100),
    currency            CHAR(3) DEFAULT 'USD',
    total_cents         INTEGER NOT NULL,
    status              VARCHAR(20) DEFAULT 'CONFIRMED',
    pms_confirmation_id VARCHAR(100),
    cancellation        JSONB,
    source_channel      VARCHAR(50),
    promo_code          VARCHAR(50),
    idempotency_key     VARCHAR(100) UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    confirmed_at        TIMESTAMPTZ,
    cancelled_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_booking_guest ON booking(guest_id);
CREATE INDEX IF NOT EXISTS idx_booking_property ON booking(property_id, check_in);
CREATE INDEX IF NOT EXISTS idx_booking_status ON booking(status, created_at);
CREATE INDEX IF NOT EXISTS idx_booking_ubr ON booking(ubr);

-- Price snapshot (frozen at booking time)
CREATE TABLE IF NOT EXISTS booking_price (
    booking_id          UUID PRIMARY KEY REFERENCES booking(id),
    currency            CHAR(3) DEFAULT 'USD',
    nightly_rates       JSONB NOT NULL,
    subtotal_cents      INTEGER NOT NULL,
    fees                JSONB NOT NULL DEFAULT '[]',
    fees_total_cents    INTEGER NOT NULL DEFAULT 0,
    taxes               JSONB NOT NULL DEFAULT '[]',
    taxes_total_cents   INTEGER NOT NULL DEFAULT 0,
    discounts           JSONB DEFAULT '[]',
    discounts_total_cents INTEGER DEFAULT 0,
    total_cents         INTEGER NOT NULL
);

-- Inventory block (double-booking prevention)
CREATE TABLE IF NOT EXISTS inventory_block (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    booking_id              UUID REFERENCES booking(id),
    property_id             UUID NOT NULL,
    check_in                DATE NOT NULL,
    check_out               DATE NOT NULL,
    status                  VARCHAR(20) DEFAULT 'CONFIRMED',
    guest_first_name        VARCHAR(100),
    guest_last_name         VARCHAR(100),
    guest_email             VARCHAR(255),
    guest_phone             VARCHAR(50),
    guests                  INTEGER,
    source                  VARCHAR(30) NOT NULL,     -- WANDER, GUESTY, HOSTAWAY, etc.
    external_pms_type       VARCHAR(30),
    external_reservation_id VARCHAR(100),
    notes                   TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_block_booking ON inventory_block(booking_id) WHERE booking_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_block_property ON inventory_block(property_id, check_in);
CREATE INDEX IF NOT EXISTS idx_block_external ON inventory_block(external_pms_type, external_reservation_id)
    WHERE external_reservation_id IS NOT NULL;

-- Try to create EXCLUDE constraint (requires btree_gist)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'inventory_block_property_id_daterange_excl'
    ) THEN
        ALTER TABLE inventory_block
            ADD CONSTRAINT inventory_block_property_id_daterange_excl
            EXCLUDE USING gist (
                property_id WITH =,
                daterange(check_in, check_out, '[)') WITH &&
            ) WHERE (status <> 'CANCELLED');
    END IF;
EXCEPTION
    WHEN OTHERS THEN
        RAISE NOTICE 'EXCLUDE constraint not created (btree_gist not available)';
END
$$;
