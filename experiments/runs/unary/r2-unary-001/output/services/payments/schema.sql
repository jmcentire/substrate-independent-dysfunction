-- Payments Service Schema
-- Stripe is ledger; this is read-optimized mirror.
-- Immutable payout snapshots with SHA256 hash chain.

CREATE TABLE IF NOT EXISTS invoice (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_number          VARCHAR(50) UNIQUE,
    booking_id              UUID NOT NULL,
    customer_id             VARCHAR(100) NOT NULL,
    status                  VARCHAR(20) DEFAULT 'DRAFT',  -- DRAFT, OPEN, PAID, PARTIALLY_PAID, VOID, REFUNDED
    currency                CHAR(3) DEFAULT 'USD',
    subtotal_cents          INTEGER NOT NULL DEFAULT 0,
    tax_cents               INTEGER NOT NULL DEFAULT 0,
    total_cents             INTEGER NOT NULL DEFAULT 0,
    paid_cents              INTEGER NOT NULL DEFAULT 0,
    refunded_cents          INTEGER NOT NULL DEFAULT 0,
    due_cents               INTEGER NOT NULL DEFAULT 0,
    platform_fee_cents      INTEGER NOT NULL DEFAULT 0,
    platform_fee_rate       DECIMAL(5,4) DEFAULT 0.15,
    stripe_payment_intent_id VARCHAR(100),
    due_date                DATE,
    paid_at                 TIMESTAMPTZ,
    voided_at               TIMESTAMPTZ,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoice_booking ON invoice(booking_id);
CREATE INDEX IF NOT EXISTS idx_invoice_customer ON invoice(customer_id);
CREATE INDEX IF NOT EXISTS idx_invoice_status ON invoice(status);

-- Invoice line items
CREATE TABLE IF NOT EXISTS invoice_item (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id  UUID NOT NULL REFERENCES invoice(id),
    item_type   VARCHAR(50) NOT NULL,   -- NIGHTLY, CLEANING_FEE, PET_FEE, TAX, etc.
    description VARCHAR(255) NOT NULL,
    cents       INTEGER NOT NULL,
    quantity    INTEGER DEFAULT 1,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_item_invoice ON invoice_item(invoice_id);

-- Payments (mirrors Stripe payment intents)
CREATE TABLE IF NOT EXISTS payment (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id              UUID NOT NULL REFERENCES invoice(id),
    stripe_payment_intent_id VARCHAR(100),
    amount_cents            INTEGER NOT NULL,
    status                  VARCHAR(20) DEFAULT 'PENDING',  -- PENDING, SUCCEEDED, FAILED
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at            TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_payment_invoice ON payment(invoice_id);

-- Refunds
CREATE TABLE IF NOT EXISTS refund (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    invoice_id      UUID NOT NULL REFERENCES invoice(id),
    booking_id      UUID NOT NULL,
    amount_cents    INTEGER NOT NULL,
    reason          VARCHAR(100),
    note            TEXT,
    status          VARCHAR(20) DEFAULT 'PENDING',  -- PENDING, SUCCEEDED, FAILED
    stripe_refund_id VARCHAR(100),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at    TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_refund_invoice ON refund(invoice_id);
CREATE INDEX IF NOT EXISTS idx_refund_booking ON refund(booking_id);

-- Payout snapshots (immutable, hash-chained)
CREATE TABLE IF NOT EXISTS payout_snapshot (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id        UUID NOT NULL,
    cycle           VARCHAR(20) NOT NULL,   -- YYYY-Wnn
    bookings        JSONB NOT NULL DEFAULT '[]',
    booking_count   INTEGER NOT NULL DEFAULT 0,
    gross_cents     INTEGER NOT NULL DEFAULT 0,
    platform_fee_cents INTEGER NOT NULL DEFAULT 0,
    adjustments_cents  INTEGER NOT NULL DEFAULT 0,
    total_cents     INTEGER NOT NULL DEFAULT 0,
    content_hash    VARCHAR(64) NOT NULL,   -- SHA256
    prev_hash       VARCHAR(64),            -- Links to previous payout
    status          VARCHAR(20) DEFAULT 'PENDING',  -- PENDING, SENT, FAILED
    stripe_transfer_id VARCHAR(100),
    paid_at         TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_payout_owner ON payout_snapshot(owner_id);
CREATE INDEX IF NOT EXISTS idx_payout_cycle ON payout_snapshot(cycle);

-- Reconciliation exceptions
CREATE TABLE IF NOT EXISTS reconciliation_exception (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    exception_type  VARCHAR(50) NOT NULL,   -- ORPHANED_CHARGE, ORPHANED_REFUND, AMOUNT_MISMATCH
    stripe_id       VARCHAR(100),
    local_id        UUID,
    expected_cents  INTEGER,
    actual_cents    INTEGER,
    status          VARCHAR(20) DEFAULT 'PENDING',  -- PENDING, RESOLVED, IGNORED
    resolution      TEXT,
    resolved_by     VARCHAR(100),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_recon_status ON reconciliation_exception(status);
