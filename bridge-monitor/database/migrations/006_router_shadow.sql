ALTER TABLE calls
    ADD COLUMN execution_mode text NOT NULL DEFAULT 'production'
        CHECK (execution_mode IN ('production', 'shadow'));

ALTER TABLE runtime_jobs
    ADD COLUMN execution_mode text NOT NULL DEFAULT 'production'
        CHECK (execution_mode IN ('production', 'shadow'));

ALTER TABLE delegation_decisions
    ADD COLUMN decision_stage text NOT NULL DEFAULT 'legacy'
        CHECK (decision_stage IN ('legacy', 'parent_router', 'claude_child')),
    ADD COLUMN execution_mode text NOT NULL DEFAULT 'production'
        CHECK (execution_mode IN ('production', 'shadow'));

CREATE INDEX runtime_jobs_shadow_queue_idx
    ON runtime_jobs(admitted_at, call_id)
    WHERE pool='qwen' AND execution_mode='shadow' AND state='queued';
