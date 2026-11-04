# Outbound network audit implementation plan

Implement the [approved design](../specs/2026-11-04-outbound-network-audit-design.md) in the isolated checkout. Use a local SQLite audit file, a Windows ETW metadata consumer and opt-in application instrumentation. Keep policy evaluation independent of the network path.

1. Exercise exact destination matches, missing evidence and deny precedence. Use immutable policy versions for live observations and replay.
2. Register worker process instances and join kernel network events to process creation records. Exercise concurrent roots, stale PIDs, loss of evidence and loopback exclusions.
3. Instrument Bridge HTTP and subprocess entry points. Capture only typed metadata. Test redaction, streamed calls, and operation with the audit sink unavailable.
4. Provide operator commands for collection, inspection and policy rehearsal. Compile the Windows collector, exercise isolated event fixtures and run affected adapter regressions.

Suggested execution model: Claude Opus (advisory), because process attribution and logging privacy span a trust boundary. Implementation is direct Codex under the user's selected route.
