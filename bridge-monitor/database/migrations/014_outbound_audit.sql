CREATE SCHEMA outbound_audit;

-- One local capture journal per deployment. Bind retries to its filesystem identity.
CREATE TABLE outbound_audit.source (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    journal_path text NOT NULL,
    file_identity text NOT NULL,
    last_id bigint NOT NULL DEFAULT 0,
    synced_at timestamptz
);
CREATE TABLE outbound_audit.observations (
    id bigint PRIMARY KEY,
    evidence text NOT NULL,
    version text,
    decision text,
    matched text
);
COMMENT ON COLUMN outbound_audit.observations.evidence IS
    'Original SQLite JSON text, preserved verbatim with the recorded decision.';
CREATE TABLE outbound_audit.policy (
    version text PRIMARY KEY,
    document text NOT NULL
);
CREATE TABLE outbound_audit.active (
    singleton integer PRIMARY KEY CHECK (singleton = 1),
    version text
);
CREATE TABLE outbound_audit.roots (
    host text NOT NULL, pid bigint NOT NULL, birth bigint NOT NULL,
    owner text NOT NULL, PRIMARY KEY (host, pid, birth)
);
CREATE TABLE outbound_audit.process_state (
    host text NOT NULL, pid bigint NOT NULL, birth bigint NOT NULL,
    shared integer NOT NULL, ended_ns bigint, PRIMARY KEY (host, pid, birth)
);
