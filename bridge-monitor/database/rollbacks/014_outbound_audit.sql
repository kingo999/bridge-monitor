-- Destructive: only use after verifying a retained backup and stopping the sync job.
DROP TABLE outbound_audit.source, outbound_audit.observations, outbound_audit.policy,
           outbound_audit.active, outbound_audit.roots, outbound_audit.process_state;
DROP SCHEMA outbound_audit;
DELETE FROM schema_migrations WHERE version='014_outbound_audit';
