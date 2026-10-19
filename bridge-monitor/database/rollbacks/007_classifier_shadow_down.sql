-- Manual rollback for 007_classifier_shadow. Not applied by database.migrate.
-- Take a backup first; this discards every classifier observation and evaluation.
-- Content objects referenced by the dropped rows remain until normal retention removes them.
DROP TABLE IF EXISTS classifier_evaluations;
DROP TABLE IF EXISTS classifier_observations;
DROP FUNCTION IF EXISTS classifier_evaluations_append_only();
DROP FUNCTION IF EXISTS classifier_observations_guard();
DELETE FROM schema_migrations WHERE version = '007_classifier_shadow';
