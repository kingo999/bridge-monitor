# Scoped security-ai workflow capability

Goal: let the existing Bridge complete the authorized installation test in mri-java-services-4 without a direct Codex repository fallback.

Authorization: the user explicitly grants the Bridge authorization for this setup test. Preserve the no-push and no-application-fix boundaries. Configuration management is performed directly under the standing management exception; campaign and documentation work runs through the existing leased Bridge worker.

Suggested execution model: Claude Opus (advisory), because a new operation crosses the campaign-data and execution permission boundaries. The existing router chooses the actual profile; entry profile remains sonnet.

Design: retain the existing router, launcher, leases, source locks, process containment, audit and cancellation. Add one fixed-operation repository tool. Bind it to an explicitly configured repository and exact campaign name. Keep raw PT access and generic shell/Git mutation tools denied. Use existing framework validators, revision writers and renderers. Assessment can change only typed finding/item disposition fields and always clears approval; it cannot fix source, approve work, or invent evidence.

Files: bridge/security_ai_workflow.py implements request validation and the fixed workflow runner; bridge/repository.py adds the opt-in campaign map and contained operation; bridge/repository_worker.py registers it; tests/adapters/test_security_ai_workflow.py verifies permission boundaries. Existing modified Bridge files must be preserved in place; no commits or unrelated policy changes.

- [x] Write boundary tests and verify failure before implementation.
- [x] Implement exact-root/campaign/action validation and the contained fixed-operation runner.
- [x] Run boundary tests and existing repository execution tests.
- [x] Enable only the existing installation-test campaign for mri_test.
- [ ] Resume actual source assessment, inventory/base planning, group status and handover readiness through the existing Bridge launcher.
- [ ] Compare observed failures with SETUP.md and have Bridge make the necessary brief guide edits. Do not claim untested transfer/fix stages passed.

Validation: boundary/adapter tests passed; native synthetic writer integration passed, including assessment, stale replay rejection, base-plan publication, and verification with approval cleared. The three DB ownership integration tests were skipped because their test environment was not configured. Final combined test results are in var/authorization/security-ai-mri4-20261029/final-validation.log. No Bridge changes were staged or committed.

Live continuation is blocked by an observed Claude provider rate_limit; reset time unknown. Root -4 remains clean, campaign revision 2 and SETUP.md unchanged. The failed external review produced no findings to apply; independent review remains pending.
