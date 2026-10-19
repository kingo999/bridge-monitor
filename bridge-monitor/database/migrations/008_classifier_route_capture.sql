-- Optional classifier accounting only. No production table is altered.
CREATE TABLE classifier_hook_capture_budget (
    scope text PRIMARY KEY CHECK (scope = 'route_work-v1'),
    consumed_rows integer NOT NULL DEFAULT 0 CHECK (consumed_rows BETWEEN 0 AND 256),
    consumed_bytes bigint NOT NULL DEFAULT 0 CHECK (consumed_bytes BETWEEN 0 AND 4194304),
    last_persisted_at timestamptz
);
INSERT INTO classifier_hook_capture_budget(scope) VALUES ('route_work-v1');

CREATE TABLE classifier_route_link_progress (
    version text PRIMARY KEY CHECK (version = 'route-work-binding-v1'),
    last_at timestamptz,
    last_id text,
    upper_at timestamptz,
    upper_id text,
    CHECK ((last_at IS NULL) = (last_id IS NULL)),
    CHECK ((upper_at IS NULL) = (upper_id IS NULL)),
    CHECK (last_at IS NULL OR (upper_at IS NOT NULL AND (last_at,last_id) <= (upper_at,upper_id)))
);
INSERT INTO classifier_route_link_progress(version) VALUES ('route-work-binding-v1');
CREATE INDEX classifier_pending_hook_link_idx
    ON classifier_observations(captured_at, observation_id)
    WHERE state='completed' AND actual_route IS NULL AND work_item_id IS NOT NULL
      AND facts_provenance->>'capture_method'='route_work';
