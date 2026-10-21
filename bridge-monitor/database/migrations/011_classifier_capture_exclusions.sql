-- Classifier-only metadata. No raw payloads or pretend content hashes.
CREATE TABLE classifier_capture_exclusions (
    exclusion_id text PRIMARY KEY,
    source_key text NOT NULL UNIQUE,
    task_id text NOT NULL,
    work_item_id text NOT NULL,
    paid_call_id text NOT NULL,
    decision_id text NOT NULL,
    origin_kind text NOT NULL CHECK (origin_kind IN ('natural','controlled')),
    capture_method text NOT NULL CHECK (capture_method IN ('route_work','bridge_ingress')),
    profile_id text NOT NULL,
    rubric_version text NOT NULL,
    rubric_sha256 text NOT NULL,
    requested_model text NOT NULL,
    config_sha256 text NOT NULL,
    reason text NOT NULL CHECK (reason='input_too_large'),
    input_size bigint NOT NULL CHECK (input_size>0),
    size_kind text NOT NULL CHECK (size_kind IN ('character_lower_bound','serialized_utf8_exact')),
    input_limit integer NOT NULL CHECK (input_limit>0 AND input_limit<=24576 AND input_size>input_limit),
    metadata_bytes integer NOT NULL CHECK (metadata_bytes>0 AND metadata_bytes<=4096),
    captured_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    metadata jsonb NOT NULL CHECK (octet_length(metadata::text)<=8192)
);
CREATE INDEX classifier_capture_exclusions_cohort_idx
    ON classifier_capture_exclusions(origin_kind,profile_id,captured_at);
CREATE TRIGGER classifier_capture_exclusions_no_change
    BEFORE UPDATE OR DELETE ON classifier_capture_exclusions
    FOR EACH ROW EXECUTE FUNCTION classifier_evaluations_append_only();
