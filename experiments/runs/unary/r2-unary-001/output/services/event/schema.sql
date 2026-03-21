-- Event Service Schema
-- Append-only audit log. Never delete, never update.
-- Content-addressable event IDs (SHA256).

CREATE TABLE IF NOT EXISTS audit_event (
    event_id        VARCHAR(50) PRIMARY KEY,
    event_kind      VARCHAR(100) NOT NULL,
    entity_kind     VARCHAR(50) NOT NULL,
    entity_id       VARCHAR(200) NOT NULL,
    actor           VARCHAR(100) NOT NULL DEFAULT 'system',
    context         JSONB NOT NULL DEFAULT '{}',
    workflow_kind   VARCHAR(100),
    workflow_id     VARCHAR(200),
    session_id      VARCHAR(200),
    external_refs   JSONB DEFAULT '[]',
    level           VARCHAR(10) NOT NULL DEFAULT 'info',
    timestamp       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_entity ON audit_event(entity_kind, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_kind ON audit_event(event_kind);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_event(actor);
CREATE INDEX IF NOT EXISTS idx_audit_workflow ON audit_event(workflow_kind, workflow_id);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_event(timestamp);
CREATE INDEX IF NOT EXISTS idx_audit_external_refs ON audit_event USING GIN (external_refs);

-- Immutability trigger: prevent UPDATE and DELETE
CREATE OR REPLACE FUNCTION prevent_audit_mutation()
RETURNS TRIGGER AS $$
BEGIN
    RAISE EXCEPTION 'Audit events are immutable: % not allowed', TG_OP;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS audit_immutable_update ON audit_event;
CREATE TRIGGER audit_immutable_update
    BEFORE UPDATE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();

DROP TRIGGER IF EXISTS audit_immutable_delete ON audit_event;
CREATE TRIGGER audit_immutable_delete
    BEFORE DELETE ON audit_event
    FOR EACH ROW EXECUTE FUNCTION prevent_audit_mutation();
