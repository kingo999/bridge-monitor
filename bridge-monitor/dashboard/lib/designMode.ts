export type DesignWorkflow = {
  workflow_id: string; repository_id: string; project_root: string; owner_id: string;
  request_key: string; outcome_label: string; current_iteration: number; version: number;
  status: string; status_actor: string; status_reason: string | null; updated_at: string;
  accepted_count: number; rejected_count: number; revision_count: number;
};
export type DesignWorkflowReport = {
  available: boolean; decision_filter_configured?: boolean; workflows: DesignWorkflow[];
  projects: { repository_id: string; project_root: string }[];
};
export type DesignWorkflowDetail = DesignWorkflow & {
  iterations: Record<string, unknown>[]; hops: Record<string, unknown>[];
  artifacts: Record<string, unknown>[]; evidence: Record<string, unknown>[];
  deliveries: Record<string, unknown>[]; audit: Record<string, unknown>[];
};
