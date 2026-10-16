-- Registered snapshots and append-only human example-use reviews.
CREATE TABLE guidance_pack_versions (
    pack_version_id text PRIMARY KEY,
    pack_id text NOT NULL,
    version_label text NOT NULL,
    sha256 text NOT NULL,
    items jsonb NOT NULL,
    registered_by text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (pack_id, version_label)
);
CREATE TABLE example_reviews (
    review_id text PRIMARY KEY,
    pack_version_id text NOT NULL REFERENCES guidance_pack_versions(pack_version_id),
    example_id text NOT NULL,
    content_id text NOT NULL REFERENCES content_objects(content_id),
    source_work_item_id text REFERENCES work_items(work_item_id),
    source_result_id text REFERENCES results(result_id),
    reviewer_id text NOT NULL,
    status text NOT NULL CHECK (status IN ('candidate','approved','rejected')),
    permitted_use text NOT NULL CHECK (permitted_use IN
        ('prompt_example','training_candidate','evaluation_only')),
    exposure text NOT NULL CHECK (exposure IN ('unseen','exposed','unknown')),
    scope jsonb NOT NULL DEFAULT '[]'::jsonb,
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    supersedes_id text REFERENCES example_reviews(review_id),
    reviewed_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX example_reviews_item_idx ON example_reviews(pack_version_id,example_id,reviewed_at DESC);

CREATE FUNCTION bridge_monitor_reject_immutable_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'append-only audit record cannot be updated or deleted';
END $$;
CREATE TRIGGER guidance_pack_immutable BEFORE UPDATE OR DELETE ON guidance_pack_versions
    FOR EACH ROW EXECUTE FUNCTION bridge_monitor_reject_immutable_change();
CREATE TRIGGER example_review_immutable BEFORE UPDATE OR DELETE ON example_reviews
    FOR EACH ROW EXECUTE FUNCTION bridge_monitor_reject_immutable_change();
