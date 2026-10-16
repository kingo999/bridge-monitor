-- Operational arbitration is separate from the event store's read projections.
CREATE TABLE runtime_active_policy (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    version text NOT NULL REFERENCES config_versions(version),
    source_hash text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime_policy_activations (
    activation_id text PRIMARY KEY,
    version text NOT NULL REFERENCES config_versions(version),
    source_hash text NOT NULL,
    approval_reference text NOT NULL,
    operator_id text NOT NULL,
    activated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime_incarnations (
    incarnation_id text PRIMARY KEY,
    worker_instance_id text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    last_heartbeat_at timestamptz NOT NULL DEFAULT now(),
    state text NOT NULL CHECK (state IN ('live', 'uncertain', 'terminated'))
);
CREATE TABLE runtime_task_budgets (
    task_id text PRIMARY KEY,
    scope jsonb NOT NULL,
    call_budget integer NOT NULL CHECK (call_budget >= 0),
    token_budget bigint NOT NULL CHECK (token_budget >= 0),
    calls_reserved integer NOT NULL DEFAULT 0 CHECK (calls_reserved >= 0),
    tokens_reserved bigint NOT NULL DEFAULT 0 CHECK (tokens_reserved >= 0)
);
CREATE TABLE runtime_jobs (
    call_id text PRIMARY KEY,
    task_id text NOT NULL REFERENCES runtime_task_budgets(task_id),
    work_item_id text NOT NULL,
    pool text NOT NULL CHECK (pool IN ('claude', 'qwen')),
    request jsonb NOT NULL,
    requested_model text,
    prompt_content_id text REFERENCES content_objects(content_id),
    policy_version text NOT NULL,
    state text NOT NULL CHECK (state IN ('queued', 'active', 'uncertain', 'completed', 'terminated', 'cancelled', 'fallback')),
    queue_deadline_at timestamptz NOT NULL,
    execution_deadline_at timestamptz,
    local_cutoff_at timestamptz,
    admitted_at timestamptz NOT NULL DEFAULT now(),
    owner_incarnation text REFERENCES runtime_incarnations(incarnation_id),
    worker_instance_id text,
    lease_id text UNIQUE,
    launch_claimed_at timestamptz,
    fencing_token bigint NOT NULL DEFAULT 0,
    slot_held boolean NOT NULL DEFAULT false,
    reserved_tokens bigint NOT NULL CHECK (reserved_tokens >= 0),
    source_snapshot text,
    source_mode text CHECK (source_mode IN ('read', 'write')),
    CHECK ((state = 'queued' AND NOT slot_held AND lease_id IS NULL)
        OR state <> 'queued')
);
CREATE INDEX runtime_jobs_pool_state_idx ON runtime_jobs(pool, state, admitted_at);
CREATE INDEX runtime_jobs_task_state_idx ON runtime_jobs(task_id, state);
CREATE TABLE runtime_pool_cursor (
    pool text PRIMARY KEY CHECK (pool IN ('claude', 'qwen')),
    last_task_id text
);
CREATE TABLE runtime_reconciliations (
    reconciliation_id text PRIMARY KEY,
    call_id text NOT NULL REFERENCES runtime_jobs(call_id),
    observed_state text NOT NULL CHECK (observed_state IN ('live', 'terminated', 'uncertain')),
    evidence jsonb NOT NULL,
    actor_kind text NOT NULL CHECK (actor_kind IN ('operator', 'system')),
    actor_id text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime_source_locks (
    source_id text NOT NULL,
    holder_call_id text NOT NULL REFERENCES runtime_jobs(call_id),
    mode text NOT NULL CHECK (mode IN ('read', 'write')),
    snapshot_ref text NOT NULL,
    acquired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(source_id, holder_call_id)
);
CREATE TABLE runtime_fallback_transitions (
    child_call_id text PRIMARY KEY REFERENCES runtime_jobs(call_id),
    work_item_id text NOT NULL,
    local_attempt_id text,
    state text NOT NULL CHECK (state IN ('committed', 'completed', 'failed', 'cancelled', 'timed_out', 'uncertain')),
    timeout_stage text NOT NULL,
    reason text NOT NULL,
    threshold_at timestamptz NOT NULL,
    elapsed_ms bigint NOT NULL CHECK (elapsed_ms >= 0),
    policy_version text NOT NULL,
    owner_execution_id text,
    recipient_call_id text NOT NULL,
    selected_model text,
    paid_result_id text REFERENCES results(result_id),
    resolved_at timestamptz,
    decision_id text NOT NULL UNIQUE REFERENCES delegation_decisions(decision_id),
    committed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime_recovery_links (
    recovery_call_id text PRIMARY KEY REFERENCES runtime_jobs(call_id),
    child_call_id text NOT NULL REFERENCES runtime_jobs(call_id),
    recipient_call_id text NOT NULL,
    owner_execution_id text,
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE dispositions ADD COLUMN part_id text REFERENCES result_parts(part_id);
ALTER TABLE dispositions ADD COLUMN input_sha256 text;
ALTER TABLE dispositions ADD COLUMN output_result_id text REFERENCES results(result_id);
ALTER TABLE dispositions ADD COLUMN details jsonb NOT NULL DEFAULT '{}'::jsonb;
CREATE TABLE runtime_disposition_obligations (
    obligation_id text PRIMARY KEY,
    result_id text NOT NULL REFERENCES results(result_id),
    part_id text REFERENCES result_parts(part_id),
    consumer_call_id text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('claude', 'gpt', 'human')),
    content_sha256 text NOT NULL,
    delivered_at timestamptz NOT NULL,
    UNIQUE NULLS NOT DISTINCT (result_id, stage, consumer_call_id, part_id)
);
CREATE TABLE runtime_completion_diagnostics (
    diagnostic_id text PRIMARY KEY,
    call_id text NOT NULL,
    attempt_id text NOT NULL,
    envelope jsonb NOT NULL,
    reason text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
