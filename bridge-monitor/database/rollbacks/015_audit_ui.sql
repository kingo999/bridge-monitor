-- Stop the dashboard before applying. This removes derived data and snapshots.
DO $$
DECLARE name text;
BEGIN
 FOREACH name IN ARRAY ARRAY['work_items','calls','events','work_resolutions','cc_decisions',
   'cc_reservations','cc_receipts','results','dispositions','content_objects',
   'runtime_disposition_obligations','usage_observations','delegation_decisions'] LOOP
   EXECUTE format('DROP TRIGGER IF EXISTS audit_dirty ON %I',name);
 END LOOP;
END $$;
DROP INDEX IF EXISTS audit_event_content;
DROP SCHEMA audit_ui CASCADE;
DELETE FROM schema_migrations WHERE version IN ('015_audit_ui','016_audit_queue');
