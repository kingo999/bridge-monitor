# Per-agent outbound network audit

Approved on 2026-11-04. Status: design only.

## First release

An operator can inspect traffic leaving the machine running the collector, trace it to an agent or child process where possible, and use that history to draft and test an allowlist. The first release observes traffic. Activating enforcement is a separate, later decision.

The boundary is the host. A model service reached on another LAN machine is off-machine, just like any other remote service. A loopback request stays local. If a local service forwards a request off-machine, record the service as the sender and link the initiating agent only when evidence supports the link. The local collector can describe the hop to a remote host. Describing an onward hop requires evidence from that host or its gateway. Discovery works without registering machine names, model families or providers.

## How observations fit together

Use host network observations to discover connections, including those made directly by child processes or through protocols other than HTTP and gRPC. Add application records where the Bridge knows which operation it requested. Application records alone miss independent child traffic. Host records alone cannot reliably distinguish individual HTTP requests or RPCs sharing a connection. Keep both levels available and correlate them when possible.

| Operator question | Evidence to retain |
| --- | --- |
| Who sent it? | Time, host, sending process instance, parent process, and the agent, task and run when attributable. Preserve an unknown owner if a shared process makes attribution ambiguous. |
| Where did it go? | Observed remote IP, port and transport. Keep the requested hostname when available and label names learned later from a lookup. Record a model name only when application evidence supplies it. |
| What left? | Outbound byte count at the level actually measured. Add a data category such as task brief or source excerpt when the sender knows it. Opaque traffic can have an unknown content category. |
| What happened? | Distinguish an attempted connection, an observed transfer and a completed application call. An absent byte measurement stays unknown. A shared connection's bytes cannot be assigned in full to every RPC. |

The audit retains metadata. Exclude message bodies, prompt text, credentials, cookies and raw URL queries. Expose gaps in collection or attribution so silence cannot be mistaken for evidence that nothing left.

## Testing a draft policy

The operator selects observed destinations and authors rules scoped to an agent, destination and port or protocol. An observed destination never grants permission by itself. Support an allowlist with default deny and explicit deny rules for exceptions. A denylist with default allow can identify known prohibited destinations. An explicit matching deny takes precedence over an allow.

Evaluate a versioned draft against recorded events, then against live events in observe mode. For each evaluation show the policy version, the applicable rule or default, and one of **would allow**, **would deny**, or **insufficient evidence**. Use the last result when missing information could change the decision. For example, a hostname-only allow rule cannot be resolved from an IP address alone. Replaying the same evidence against the same policy version should yield the same result. All three outcomes leave traffic unaffected during this release.

## Acceptance scenarios

1. A child process contacts a previously unseen LAN endpoint. The audit shows the process, its parent, the observed destination and the owning run when known. A failed attempt and a transfer witnessed at a controlled receiver produce distinguishable records.
2. Two agents run concurrently. One process sends directly and another uses a shared helper. The direct transfer is attributed to its run. If the helper supplies no evidence of its caller, the record identifies the helper and leaves the run unknown.
3. Two instrumented RPCs use one connection. Both RPCs remain visible, connection bytes are counted once, and the record contains no request content or credentials.
4. Replay a draft against known allowed, known denied and IP-only events. Confirm all three outcomes, including an explicit deny overriding an allow. Repeat in live observe mode and confirm the controlled receiver still receives a transfer marked **would deny**.

Collector technology, supported platform coverage and operational retention settings remain implementation decisions. These scenarios describe future verification.
