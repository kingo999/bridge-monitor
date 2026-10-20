-- Classifier-only index. Reuse the existing bounded cursor and lifetime budget.
CREATE INDEX classifier_pending_bridge_link_idx
    ON classifier_observations(captured_at, observation_id)
    WHERE state='completed' AND actual_route IS NULL AND work_item_id IS NOT NULL
      AND facts_provenance->>'capture_method' IN ('route_work','bridge_ingress');
