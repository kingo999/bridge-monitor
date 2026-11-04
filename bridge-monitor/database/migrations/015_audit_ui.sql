CREATE SCHEMA audit_ui;
CREATE TABLE audit_ui.state (
 singleton boolean PRIMARY KEY DEFAULT true CHECK(singleton),
 network_high bigint NOT NULL DEFAULT 0,
 execution_version bigint NOT NULL DEFAULT 0,
 updated_at timestamptz
);
INSERT INTO audit_ui.state DEFAULT VALUES;
CREATE TABLE audit_ui.entries (
 view text NOT NULL CHECK(view IN ('executions','network')),
 id text NOT NULL, source_order bigint,
 created_at timestamptz, project text, handler text, status text,
 has_usage boolean NOT NULL DEFAULT false,
 destination text, bytes numeric, layer text, kind text, process text,
 requested_name text, aliases text[] NOT NULL DEFAULT '{}', run text,
 attribution text, ids text[] NOT NULL DEFAULT '{}',
 search_text text NOT NULL DEFAULT '',
 search tsvector GENERATED ALWAYS AS (to_tsvector('simple',search_text)) STORED,
 data jsonb NOT NULL, error text,
 PRIMARY KEY(view,id)
);
CREATE INDEX audit_time ON audit_ui.entries(view,created_at,id);
CREATE INDEX audit_project ON audit_ui.entries(view,project,id);
CREATE INDEX audit_status ON audit_ui.entries(view,status,id);
CREATE INDEX audit_destination ON audit_ui.entries(view,destination,id);
CREATE INDEX audit_bytes ON audit_ui.entries(view,bytes,id);
CREATE INDEX audit_identifiers ON audit_ui.entries USING gin(ids);
CREATE INDEX audit_words ON audit_ui.entries USING gin(search);
CREATE INDEX audit_aliases ON audit_ui.entries USING gin(aliases);
CREATE INDEX audit_process ON audit_ui.entries(view,process text_pattern_ops);
CREATE INDEX audit_hostname ON audit_ui.entries(view,requested_name text_pattern_ops);
CREATE INDEX audit_run ON audit_ui.entries(view,run);
CREATE INDEX audit_port ON audit_ui.entries((data->>'port'));
CREATE TABLE audit_ui.dirty (work_item_id text PRIMARY KEY);
INSERT INTO audit_ui.dirty SELECT work_item_id FROM work_items
 UNION SELECT work_item_id FROM cc_decisions;
CREATE FUNCTION audit_ui.invalidate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item jsonb; identifier text; parent text;
BEGIN
 item := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 identifier := item->>'work_item_id';
 IF identifier IS NULL AND item->>'call_id' IS NOT NULL THEN
   SELECT work_item_id INTO identifier FROM calls WHERE call_id=item->>'call_id';
   IF identifier IS NULL THEN
     SELECT work_item_id INTO identifier FROM cc_reservations WHERE call_id=item->>'call_id';
   END IF;
 END IF;
 IF identifier IS NULL OR TG_TABLE_NAME='content_objects' THEN
   INSERT INTO audit_ui.dirty SELECT work_item_id FROM work_items
     UNION SELECT work_item_id FROM cc_decisions ON CONFLICT(work_item_id) DO UPDATE SET work_item_id=EXCLUDED.work_item_id;
 ELSE
   INSERT INTO audit_ui.dirty VALUES(identifier) ON CONFLICT(work_item_id) DO UPDATE SET work_item_id=EXCLUDED.work_item_id;
 END IF;
 parent := item->>'parent_work_item_id';
 IF parent IS NOT NULL THEN
   INSERT INTO audit_ui.dirty VALUES(parent) ON CONFLICT(work_item_id) DO UPDATE SET work_item_id=EXCLUDED.work_item_id;
 END IF;
 RETURN NULL;
END $$;
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['work_items','calls','events','work_resolutions','cc_decisions',
   'cc_reservations','cc_receipts','results','dispositions','content_objects',
   'runtime_disposition_obligations','usage_observations','delegation_decisions'] LOOP
   EXECUTE format('CREATE TRIGGER audit_dirty AFTER INSERT OR UPDATE OR DELETE ON %I
     FOR EACH ROW EXECUTE FUNCTION audit_ui.invalidate()',name);
 END LOOP;
END $$;
CREATE TABLE audit_ui.snapshots (
 token text PRIMARY KEY, owner text NOT NULL,
 expires_at timestamptz NOT NULL,
 view text NOT NULL, filters jsonb NOT NULL, coverage jsonb NOT NULL
);
CREATE INDEX audit_expiry ON audit_ui.snapshots(expires_at);
CREATE TABLE audit_ui.snapshot_rows (
 token text NOT NULL REFERENCES audit_ui.snapshots(token) ON DELETE CASCADE,
 rank bigint NOT NULL,
 entry jsonb NOT NULL,
 PRIMARY KEY(token,rank)
);
