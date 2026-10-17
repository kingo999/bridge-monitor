-- Preserve metadata and a durable replay boundary when retained bodies expire.
CREATE TABLE retention_runs (
    run_id text PRIMARY KEY,
    cutoff_at timestamptz NOT NULL,
    content_tombstones bigint NOT NULL,
    publications_removed bigint NOT NULL,
    earliest_position bigint NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT now()
);
