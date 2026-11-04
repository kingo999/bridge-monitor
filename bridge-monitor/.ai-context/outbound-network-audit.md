# Outbound network audit

**Approved on 2026-11-04.** The first release observes off-machine traffic, including LAN traffic, and attributes it to the sending process and agent run when evidence supports the link. Destinations are generic. Hostnames and model names are optional and have distinct sources.

Correlate host connections with application calls. Preserve unknowns, keep sensitive payloads out of logs, and distinguish an attempted connection from an observed transfer. Operators can replay a draft allowlist and evaluate it live in observe mode before deciding whether to introduce enforcement.

**Status on 2026-11-04.** Implemented on the isolated feature branch. The Windows collector, Bridge hooks and SQLite journal support inspect, register, live policy and replay commands. The host account verified a PID-scoped Winsock attempt to a controlled receiver. Live kernel process and byte evidence requires a Windows account with provider access; the send decoder was exercised using the installed event manifest. Production activation is pending. Retention is operator-managed.

See the [approved design and acceptance scenarios](../docs/superpowers/specs/2026-11-04-outbound-network-audit-design.md).

See the [operator guide](../docs/operations/outbound-network-audit.md) for setup, attribution boundaries, provider coverage and draft policy examples.

**Privileged verification on 2026-11-04.** An administrator-token smoke test passed with all requested providers available. The test receiver read the canary and the kernel emitted a matching TCP send for the test PID, destination and 12-byte payload. The reader emitted only the test PID's events. The temporary session stopped afterward. Receipt is in the local ignored `var/privileged-verification/result.json`. This verifies the live kernel send path on loopback; an off-machine receiver comparison is still pending.

A subsequent elevated synthetic HTTP health request to the configured LAN worker at `192.168.0.78:8874` timed out during connect. The off-machine comparison remains unverified. Local failure details are in `var/privileged-verification/lan-result.json`; the temporary reader was stopped in the test's finally block.
