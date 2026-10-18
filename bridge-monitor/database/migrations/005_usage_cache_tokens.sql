ALTER TABLE usage_observations
    ADD COLUMN cache_read_input_tokens bigint,
    ADD COLUMN cache_creation_input_tokens bigint;
