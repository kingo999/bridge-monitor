CREATE TABLE IF NOT EXISTS schema_migrations (
    version text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id text PRIMARY KEY, origin text NOT NULL,
    scope jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS work_items (
    work_item_id text PRIMARY KEY, task_id text REFERENCES tasks(task_id),
    parent_work_item_id text REFERENCES work_items(work_item_id),
    origin text NOT NULL, category text,
    created_at timestamptz NOT NULL,
    resolved_at timestamptz,
    resolution text
);
CREATE INDEX IF NOT EXISTS work_items_task_time_idx ON work_items(task_id, created_at);
CREATE TABLE IF NOT EXISTS sessions (
    session_id text PRIMARY KEY, task_id text REFERENCES tasks(task_id),
    created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS hosts (
    host_id text PRIMARY KEY, name text NOT NULL
);
CREATE TABLE IF NOT EXISTS workers (
    worker_id text PRIMARY KEY, host_id text REFERENCES hosts(host_id),
    role text NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_instances (
    worker_instance_id text PRIMARY KEY, worker_id text REFERENCES workers(worker_id),
    incarnation text NOT NULL, started_at timestamptz NOT NULL,
    status text NOT NULL, capabilities jsonb NOT NULL DEFAULT '{}'::jsonb
);
CREATE TABLE IF NOT EXISTS calls (
    call_id text PRIMARY KEY, work_item_id text REFERENCES work_items(work_item_id),
    parent_call_id text REFERENCES calls(call_id),
    parent_attempt_id text,
    requested_model text, created_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS attempts (
    attempt_id text PRIMARY KEY, call_id text REFERENCES calls(call_id),
    attempt_number integer NOT NULL, status text NOT NULL,
    UNIQUE(call_id, attempt_number)
);
CREATE TABLE IF NOT EXISTS model_executions (
    model_execution_id text PRIMARY KEY, attempt_id text REFERENCES attempts(attempt_id),
    requested_model text, reported_model text,
    model_evidence jsonb NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS content_objects (
    content_id text PRIMARY KEY, raw_bytes bytea,
    byte_length bigint NOT NULL CHECK (byte_length >= 0),
    sha256 text, role text NOT NULL, direction text NOT NULL,
    content_type text NOT NULL, encoding text NOT NULL,
    origin text NOT NULL, capture_layer text NOT NULL,
    completeness text NOT NULL, message_id text, block_order integer NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (completeness <> 'complete' OR raw_bytes IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS messages (
    message_id text PRIMARY KEY, call_id text REFERENCES calls(call_id),
    role text NOT NULL, direction text NOT NULL,
    original_content_id text REFERENCES content_objects(content_id),
    effective_content_id text REFERENCES content_objects(content_id)
);
CREATE TABLE IF NOT EXISTS message_chunks (
    message_id text NOT NULL, chunk_sequence integer NOT NULL,
    content_id text REFERENCES content_objects(content_id),
    PRIMARY KEY (message_id, chunk_sequence)
);
CREATE TABLE IF NOT EXISTS results (
    result_id text PRIMARY KEY, call_id text REFERENCES calls(call_id),
    attempt_id text REFERENCES attempts(attempt_id),
    raw_content_id text REFERENCES content_objects(content_id),
    delivered_content_id text REFERENCES content_objects(content_id),
    delivered_at timestamptz
);
CREATE TABLE IF NOT EXISTS result_parts (
    part_id text PRIMARY KEY, result_id text REFERENCES results(result_id),
    content_id text REFERENCES content_objects(content_id)
);
CREATE TABLE IF NOT EXISTS result_links (
    from_result_id text REFERENCES results(result_id),
    to_result_id text REFERENCES results(result_id),
    relation text NOT NULL,
    PRIMARY KEY(from_result_id, to_result_id, relation)
);
CREATE TABLE IF NOT EXISTS delegation_decisions (
    decision_id text PRIMARY KEY, work_item_id text REFERENCES work_items(work_item_id),
    child_call_id text REFERENCES calls(call_id),
    route text NOT NULL, eligibility text NOT NULL,
    reason text, policy_version text,
    guidance_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    gate_facts jsonb NOT NULL DEFAULT '{}'::jsonb,
    decided_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS delegation_decisions_work_route_idx ON delegation_decisions(work_item_id, route);
CREATE TABLE IF NOT EXISTS dispositions (
    disposition_id text PRIMARY KEY, result_id text REFERENCES results(result_id),
    actor_id text NOT NULL, stage text NOT NULL, judgment text NOT NULL,
    reason text, supersedes_id text REFERENCES dispositions(disposition_id),
    recorded_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS dispositions_result_stage_idx ON dispositions(result_id, stage, recorded_at);
CREATE TABLE IF NOT EXISTS work_resolutions (
    resolution_id text PRIMARY KEY, work_item_id text REFERENCES work_items(work_item_id),
    outcome text NOT NULL, final_result_id text REFERENCES results(result_id),
    recorded_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS evaluations (
    evaluation_id text PRIMARY KEY, result_id text REFERENCES results(result_id),
    evidence jsonb NOT NULL, recorded_at timestamptz NOT NULL
);
CREATE TABLE IF NOT EXISTS usage_observations (
    observation_id text PRIMARY KEY, model_execution_id text REFERENCES model_executions(model_execution_id),
    scope text NOT NULL, is_cumulative boolean NOT NULL,
    input_tokens bigint, output_tokens bigint, provenance text NOT NULL,
    price_basis text
);
CREATE TABLE IF NOT EXISTS config_versions (
    version text PRIMARY KEY, source_hash text NOT NULL,
    review_reference text, activated_at timestamptz,
    policy jsonb NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id text PRIMARY KEY, event_type text NOT NULL,
    schema_version integer NOT NULL, producer_id text NOT NULL,
    producer_incarnation text NOT NULL, producer_sequence bigint NOT NULL,
    occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL,
    origin text NOT NULL, observation_layer text NOT NULL,
    capture_completeness text NOT NULL,
    task_id text, work_item_id text, session_id text,
    call_id text, attempt_id text, model_execution_id text,
    worker_instance_id text, parent_event_id text,
    requested_model text, reported_model text, config_version text,
    payload jsonb NOT NULL, payload_hash text NOT NULL,
    unresolved_references text[] NOT NULL DEFAULT ARRAY[]::text[]
);
CREATE INDEX IF NOT EXISTS events_task_time_idx ON events(task_id, occurred_at);
CREATE INDEX IF NOT EXISTS events_work_time_idx ON events(work_item_id, occurred_at);
CREATE INDEX IF NOT EXISTS events_type_time_idx ON events(event_type, occurred_at);
CREATE TABLE IF NOT EXISTS producer_sequences (
    producer_id text NOT NULL, producer_incarnation text NOT NULL,
    producer_sequence bigint NOT NULL, event_id text NOT NULL REFERENCES events(event_id),
    PRIMARY KEY(producer_id, producer_incarnation, producer_sequence)
);

CREATE TABLE IF NOT EXISTS publication_counter (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    next_position bigint NOT NULL CHECK (next_position >= 1)
);
INSERT INTO publication_counter(singleton, next_position) VALUES(true, 1)
ON CONFLICT(singleton) DO NOTHING;
CREATE TABLE IF NOT EXISTS publication_stream (
    position bigint PRIMARY KEY, event_id text NOT NULL UNIQUE REFERENCES events(event_id),
    published_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS dashboard_status (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active integer NOT NULL DEFAULT 0, queued integer NOT NULL DEFAULT 0,
    held_uncertain integer NOT NULL DEFAULT 0, blocked integer NOT NULL DEFAULT 0,
    capture_state text NOT NULL DEFAULT 'unknown',
    known_gaps integer NOT NULL DEFAULT 0,
    spool_pressure double precision,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO dashboard_status(singleton) VALUES(true) ON CONFLICT(singleton) DO NOTHING;
