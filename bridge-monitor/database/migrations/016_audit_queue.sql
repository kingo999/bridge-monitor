-- Append-only invalidations never contend with projection claims on a shared key.
ALTER TABLE audit_ui.dirty ADD COLUMN invalidation_id bigint GENERATED ALWAYS AS IDENTITY;
ALTER TABLE audit_ui.dirty DROP CONSTRAINT dirty_pkey;
ALTER TABLE audit_ui.dirty ADD PRIMARY KEY(invalidation_id);
ALTER TABLE audit_ui.dirty ADD COLUMN ready_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX audit_dirty_ready ON audit_ui.dirty(ready_at,invalidation_id);
CREATE INDEX audit_event_content ON events((payload->>'content_id'));
ALTER TABLE audit_ui.snapshots ADD COLUMN activated boolean NOT NULL DEFAULT false;
CREATE OR REPLACE FUNCTION audit_ui.invalidate() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE item jsonb; identifier text; parent text;
BEGIN
 item := CASE WHEN TG_OP='DELETE' THEN to_jsonb(OLD) ELSE to_jsonb(NEW) END;
 IF TG_TABLE_NAME='content_objects' THEN
   INSERT INTO audit_ui.dirty(work_item_id)
     SELECT DISTINCT work_item_id FROM events
     WHERE payload->>'content_id'=item->>'content_id' AND work_item_id IS NOT NULL;
   RETURN NULL;
 END IF;
 identifier := item->>'work_item_id';
 IF identifier IS NULL AND item->>'call_id' IS NOT NULL THEN
   SELECT work_item_id INTO identifier FROM calls WHERE call_id=item->>'call_id';
   IF identifier IS NULL THEN
     SELECT work_item_id INTO identifier FROM cc_reservations WHERE call_id=item->>'call_id';
   END IF;
 END IF;
 IF identifier IS NULL AND item->>'result_id' IS NOT NULL THEN
   SELECT c.work_item_id INTO identifier FROM results r JOIN calls c USING(call_id)
   WHERE r.result_id=item->>'result_id';
 END IF;
 IF identifier IS NULL AND item->>'model_execution_id' IS NOT NULL THEN
   SELECT c.work_item_id INTO identifier FROM model_executions m
   JOIN attempts a USING(attempt_id) JOIN calls c USING(call_id)
   WHERE m.model_execution_id=item->>'model_execution_id';
 END IF;
 -- Ambiguous dependencies request a background rescan with ONE lightweight row.
 INSERT INTO audit_ui.dirty(work_item_id) VALUES(coalesce(identifier,'*'));
 parent := item->>'parent_work_item_id';
 IF parent IS NOT NULL THEN
   INSERT INTO audit_ui.dirty(work_item_id) VALUES(parent);
 END IF;
 IF TG_OP='UPDATE' THEN
   item := to_jsonb(OLD);
   IF item->>'work_item_id' IS DISTINCT FROM identifier AND item->>'work_item_id' IS NOT NULL THEN
     INSERT INTO audit_ui.dirty(work_item_id) VALUES(item->>'work_item_id');
   END IF;
   IF item->>'parent_work_item_id' IS DISTINCT FROM parent AND item->>'parent_work_item_id' IS NOT NULL THEN
     INSERT INTO audit_ui.dirty(work_item_id) VALUES(item->>'parent_work_item_id');
   END IF;
 END IF;
 RETURN NULL;
END $$;
