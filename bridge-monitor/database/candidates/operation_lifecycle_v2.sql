-- Explicit opt-in artifact. NOT discovered by database.migrate.
-- Apply only to a reviewed isolated database in this slice.
CREATE TABLE IF NOT EXISTS operation_identities (
    operation_id text PRIMARY KEY,
    identity jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_event_facts (
    event_id text PRIMARY KEY REFERENCES events(event_id),
    operation_id text NOT NULL REFERENCES operation_identities(operation_id),
    envelope jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS operation_facts_operation_idx
    ON operation_event_facts(operation_id, event_id);
