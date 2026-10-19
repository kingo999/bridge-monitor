-- Advisory classifier shadow observations. Additive only: no existing table, column,
-- index or constraint is changed, and no production query reads these tables.
CREATE TABLE classifier_observations (
    observation_id text PRIMARY KEY,
    source_key text NOT NULL UNIQUE,
    origin_kind text NOT NULL CHECK (origin_kind IN ('natural','controlled','synthetic','replay')),
    origin_ref text NOT NULL,
    replay_id text,
    task_id text,
    work_item_id text,
    request_sha256 text NOT NULL,
    source_sha256 text NOT NULL,
    input_bytes integer NOT NULL CHECK (input_bytes >= 0),
    request_content_id text REFERENCES content_objects(content_id),
    source_content_id text REFERENCES content_objects(content_id),
    facts jsonb NOT NULL,
    facts_captured_at timestamptz NOT NULL,
    facts_provenance jsonb NOT NULL DEFAULT '{}'::jsonb,
    contract_version text NOT NULL,
    rubric_version text NOT NULL,
    rubric_sha256 text NOT NULL,
    profile_id text NOT NULL,
    profile jsonb NOT NULL,
    requested_model text NOT NULL,
    expected_digest text NOT NULL,
    state text NOT NULL CHECK (state IN
        ('captured','dispatching','completed','failed','skipped','uncertain','cancelled')),
    skip_reason text,
    error_type text,
    error_detail text,
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    runner_id text,
    captured_at timestamptz NOT NULL,
    dispatched_at timestamptz,
    first_content_at timestamptz,
    completed_at timestamptz,
    runtime_version text,
    observed_digest text,
    reported_model text,
    backend jsonb,
    done_reason text,
    response_content_id text REFERENCES content_objects(content_id),
    response_bytes integer,
    response_sha256 text,
    valid boolean,
    validation_error text,
    rec_category text,
    rec_route text,
    policy_version text,
    policy_allowed boolean,
    policy_blocks jsonb,
    policy_warnings jsonb,
    actual_route text,
    actual_route_source jsonb,
    actual_route_observed_at timestamptz,
    queue_ms integer,
    request_ms integer,
    first_content_ms integer,
    inference_ms integer,
    load_ms integer,
    prompt_eval_ms integer,
    eval_ms integer,
    end_to_end_ms integer,
    input_tokens integer,
    output_tokens integer,
    usage_provenance text,
    CHECK (state <> 'skipped' OR skip_reason IS NOT NULL)
);
CREATE INDEX classifier_observations_queue_idx
    ON classifier_observations(captured_at, observation_id) WHERE state = 'captured';
CREATE INDEX classifier_observations_work_idx ON classifier_observations(work_item_id);
CREATE INDEX classifier_observations_cohort_idx
    ON classifier_observations(origin_kind, requested_model, profile_id, captured_at);

CREATE TABLE classifier_evaluations (
    evaluation_id text PRIMARY KEY,
    observation_id text NOT NULL REFERENCES classifier_observations(observation_id),
    kind text NOT NULL CHECK (kind IN
        ('reference_label','operator_judgment','verification','acceptance','correction')),
    actor_id text NOT NULL,
    reference_category text,
    reference_route text,
    acceptable_routes jsonb,
    judgment text,
    reason text,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    supersedes_id text REFERENCES classifier_evaluations(evaluation_id),
    recorded_at timestamptz NOT NULL
);
CREATE INDEX classifier_evaluations_observation_idx
    ON classifier_evaluations(observation_id, recorded_at);

CREATE FUNCTION classifier_evaluations_append_only() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'classifier evaluations are append-only';
END $$;
CREATE TRIGGER classifier_evaluations_no_change
    BEFORE UPDATE OR DELETE ON classifier_evaluations
    FOR EACH ROW EXECUTE FUNCTION classifier_evaluations_append_only();

-- Capture-time facts and the first raw recommendation are immutable; terminal states are
-- final. Late route enrichment may fill a still-empty actual_route once.
CREATE FUNCTION classifier_observations_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'classifier observations cannot be deleted';
    END IF;
    IF ROW(NEW.observation_id, NEW.source_key, NEW.origin_kind, NEW.origin_ref, NEW.replay_id,
           NEW.task_id, NEW.work_item_id, NEW.request_sha256, NEW.source_sha256, NEW.input_bytes,
           NEW.request_content_id, NEW.source_content_id, NEW.facts, NEW.facts_captured_at,
           NEW.facts_provenance, NEW.contract_version, NEW.rubric_version, NEW.rubric_sha256,
           NEW.profile_id, NEW.profile, NEW.requested_model, NEW.expected_digest, NEW.captured_at)
       IS DISTINCT FROM
       ROW(OLD.observation_id, OLD.source_key, OLD.origin_kind, OLD.origin_ref, OLD.replay_id,
           OLD.task_id, OLD.work_item_id, OLD.request_sha256, OLD.source_sha256, OLD.input_bytes,
           OLD.request_content_id, OLD.source_content_id, OLD.facts, OLD.facts_captured_at,
           OLD.facts_provenance, OLD.contract_version, OLD.rubric_version, OLD.rubric_sha256,
           OLD.profile_id, OLD.profile, OLD.requested_model, OLD.expected_digest, OLD.captured_at) THEN
        RAISE EXCEPTION 'classifier observation capture fields are immutable';
    END IF;
    IF OLD.state IN ('completed','failed','skipped','uncertain','cancelled') AND (
           NEW.state IS DISTINCT FROM OLD.state
        OR NEW.rec_route IS DISTINCT FROM OLD.rec_route
        OR NEW.rec_category IS DISTINCT FROM OLD.rec_category
        OR NEW.response_content_id IS DISTINCT FROM OLD.response_content_id
        OR NEW.response_sha256 IS DISTINCT FROM OLD.response_sha256
        OR NEW.valid IS DISTINCT FROM OLD.valid
        OR NEW.policy_allowed IS DISTINCT FROM OLD.policy_allowed
        OR NEW.attempts IS DISTINCT FROM OLD.attempts) THEN
        RAISE EXCEPTION 'classifier observation outcome is final';
    END IF;
    IF OLD.actual_route IS NOT NULL AND NEW.actual_route IS DISTINCT FROM OLD.actual_route THEN
        RAISE EXCEPTION 'classifier actual route is set once; append an evaluation instead';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER classifier_observations_guard_trigger
    BEFORE UPDATE OR DELETE ON classifier_observations
    FOR EACH ROW EXECUTE FUNCTION classifier_observations_guard();
