CREATE TABLE dm_workflows (
 workflow_id text PRIMARY KEY,
 owner_id text NOT NULL,
 repository_id text NOT NULL,
 project_root text NOT NULL,
 request_key text NOT NULL,
 status text NOT NULL CHECK (status IN ('awaiting_planner','paused','review_claimed','review_unknown',
   'ready','delivered','stale','accepted','rejected','cancelled','failed')),
 status_actor text NOT NULL CHECK (status_actor IN ('chat','codex','claude','bridge')),
 status_reason text,
 current_iteration integer NOT NULL CHECK (current_iteration >= 1),
 version integer NOT NULL DEFAULT 1 CHECK (version >= 1),
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (owner_id, request_key)
);
CREATE INDEX dm_workflows_updated ON dm_workflows(updated_at DESC);
CREATE TABLE dm_iterations (
 workflow_id text NOT NULL REFERENCES dm_workflows(workflow_id),
 iteration integer NOT NULL CHECK (iteration >= 1),
 request_key text NOT NULL,
 payload_sha256 text NOT NULL,
 task_id text NOT NULL,
 work_item_id text NOT NULL UNIQUE,
 source_head text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (workflow_id, iteration),
 UNIQUE (workflow_id, request_key)
);
CREATE TABLE dm_artifacts (
 workflow_id text NOT NULL,
 iteration integer NOT NULL,
 kind text NOT NULL CHECK (kind IN ('brief','plan','review','package')),
 sha256 text NOT NULL,
 body text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (workflow_id, iteration, kind),
 FOREIGN KEY (workflow_id, iteration) REFERENCES dm_iterations(workflow_id, iteration)
);
CREATE TABLE dm_hops (
 workflow_id text NOT NULL,
 iteration integer NOT NULL,
 sequence integer NOT NULL CHECK (sequence >= 1),
 from_actor text NOT NULL CHECK (from_actor IN ('chat','codex','claude')),
 to_actor text NOT NULL CHECK (to_actor IN ('chat','codex','claude')),
 kind text NOT NULL CHECK (kind IN ('brief','plan','package')),
 attempt integer NOT NULL DEFAULT 1 CHECK (attempt >= 1),
 state text NOT NULL CHECK (state IN ('recorded','claimed','completed','unknown','failed','cancelled')),
 worker_call_id text,
 decision_id text,
 result_id text,
 artifact_sha256 text,
 reason text,
 started_at timestamptz NOT NULL DEFAULT now(),
 finished_at timestamptz,
 PRIMARY KEY (workflow_id, iteration, sequence),
 FOREIGN KEY (workflow_id, iteration) REFERENCES dm_iterations(workflow_id, iteration)
);
CREATE INDEX dm_hops_worker ON dm_hops(worker_call_id) WHERE worker_call_id IS NOT NULL;
CREATE TABLE dm_evidence (
 workflow_id text NOT NULL,
 iteration integer NOT NULL,
 path text NOT NULL,
 sha256 text NOT NULL,
 read_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY (workflow_id, iteration, path),
 FOREIGN KEY (workflow_id, iteration) REFERENCES dm_iterations(workflow_id, iteration)
);
CREATE TABLE dm_deliveries (
 receipt_id text PRIMARY KEY,
 workflow_id text NOT NULL,
 iteration integer NOT NULL,
 package_sha256 text NOT NULL,
 owner_id text NOT NULL,
 delivered_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE (workflow_id, iteration),
 FOREIGN KEY (workflow_id, iteration) REFERENCES dm_iterations(workflow_id, iteration)
);
CREATE TABLE dm_audit (
 audit_id bigserial PRIMARY KEY,
 workflow_id text NOT NULL REFERENCES dm_workflows(workflow_id),
 iteration integer,
 actor text NOT NULL,
 action text NOT NULL,
 detail jsonb NOT NULL DEFAULT '{}'::jsonb,
 recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX dm_audit_workflow ON dm_audit(workflow_id, audit_id);
CREATE FUNCTION dm_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'design mode record % is immutable', TG_TABLE_NAME;
END $$;
CREATE TRIGGER dm_iterations_immutable BEFORE UPDATE OR DELETE ON dm_iterations
  FOR EACH ROW EXECUTE FUNCTION dm_reject_mutation();
CREATE TRIGGER dm_artifacts_immutable BEFORE UPDATE OR DELETE ON dm_artifacts
  FOR EACH ROW EXECUTE FUNCTION dm_reject_mutation();
CREATE TRIGGER dm_evidence_immutable BEFORE UPDATE OR DELETE ON dm_evidence
  FOR EACH ROW EXECUTE FUNCTION dm_reject_mutation();
CREATE TRIGGER dm_deliveries_immutable BEFORE UPDATE OR DELETE ON dm_deliveries
  FOR EACH ROW EXECUTE FUNCTION dm_reject_mutation();
CREATE TRIGGER dm_audit_immutable BEFORE UPDATE OR DELETE ON dm_audit
  FOR EACH ROW EXECUTE FUNCTION dm_reject_mutation();
