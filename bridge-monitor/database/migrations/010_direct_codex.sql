-- Direct observations are not scheduled Bridge calls.
CREATE TABLE direct_executions (
    execution_id text PRIMARY KEY,
    work_item_id text NOT NULL UNIQUE REFERENCES work_items(work_item_id) ON DELETE CASCADE,
    producer_id text NOT NULL,
    session_id text NOT NULL,
    project_root text NOT NULL,
    first_observed_at timestamptz NOT NULL
);

ALTER TABLE usage_observations
    ADD COLUMN direct_execution_id text REFERENCES direct_executions(execution_id) ON DELETE CASCADE,
    ADD COLUMN source_turn_id text,
    ADD COLUMN evidence jsonb;
ALTER TABLE usage_observations ADD CONSTRAINT usage_direct_owner
    CHECK (direct_execution_id IS NULL OR
           (model_execution_id IS NULL AND source_turn_id IS NOT NULL AND evidence IS NOT NULL));
CREATE INDEX usage_direct_execution ON usage_observations(direct_execution_id)
    WHERE direct_execution_id IS NOT NULL;
CREATE INDEX events_direct_history ON events(work_item_id,occurred_at DESC)
    WHERE event_type='direct_task.recorded';
