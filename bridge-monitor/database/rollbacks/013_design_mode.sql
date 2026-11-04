DROP TABLE dm_audit, dm_deliveries, dm_evidence, dm_hops, dm_artifacts, dm_iterations, dm_workflows;
DROP FUNCTION dm_reject_mutation();
DELETE FROM schema_migrations WHERE version='013_design_mode';
