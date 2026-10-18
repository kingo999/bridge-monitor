# Test-backed code-fix shadow design

The first candidate class is a small, single-file Python fix with trusted tests supplied before any model call. The user selected this class after the existing 30 observed work items were found to be mostly controlled probes. Two retrospective fixes from this project's recent history can validate the harness, but they are not natural live traffic and cannot justify a live canary by themselves.

## Acceptance boundary

Each case contains a natural-language request, broken source, a known-good reference source, and host-held trusted input/expected-output vectors. The reference must pass and the broken source must fail before dispatch. Qwen and Sonnet receive the same request and broken source, but never the reference or expected outputs. Each returns a JSON object with one string field, `answer`, containing the complete replacement file. A candidate is eligible only if its JSON parses, its source fits the configured byte limit, and its outputs match the host-held expected values. Candidate code runs only in a disposable Docker container; the host compares the bounded results after the container exits. A passing suite is evidence for this bounded task, not a proof of all unseen behavior. The live bridge does not deliver a shadow answer.

The container uses a digest-pinned Python image with `--pull never`, no network, read-only root and input mount, dropped capabilities, no privilege escalation, a non-root UID, and configurable CPU, memory, process, and wall-clock limits. The runner and candidate are read-only mounts; a size-limited result file is the only writable mount. The expected outputs are never mounted. A missing runtime, image mismatch, timeout, malformed answer, or test failure is a failed local verification. An uncertain Qwen dispatch is never repeated automatically.

## Measurement

A matched run invokes restricted Sonnet directly and Qwen locally; a failed local candidate invokes Sonnet fallback. It records exact prompts, raw and parsed model outputs, requested and reported models, token categories, elapsed time, test results, route, and fallback reason in an ignored project-local receipt. A controlled read-only Codex CLI parent receives only the trusted task, test state, source byte count and hash for each arm. It never receives worker source. It records reported input, cached, cache-write, output, and reasoning tokens and elapsed time. This metadata-only parent is a measurement proxy, not the desktop conversation or a full downstream review. CLI workers run in empty project-local temporary directories. No token categories from different providers are summed as if they had the same price. Absent actual billing and full live-parent usage, the report must not claim net billed savings.

The receipt hashes the fixture, config, image digest, and runner code before dispatch. It retains completed-arm evidence after interruption and refuses automatic replay of an uncertain case. The current production event store and dashboard cohort remain unchanged.

## Shadow decision

First run the two retrospective code-fix cases to validate the path. A genuine live shadow cohort starts only when new user work naturally supplies a small Python fix and trusted tests. The live Claude result remains authoritative; Qwen's candidate is inspected offline. Before any canary, review the real cohort's test pass rate, model and parent token categories, latency, fallback safety, test coverage and isolation, and whether a paid worker is actually avoided. If there are no genuine eligible tasks or no credible cost advantage, record a no-go and leave routing disabled. Any later canary needs an explicit reviewed activation policy and rollback; this shadow design does not activate one.

**Suggested execution model: Claude Opus.** The verifier and code-execution isolation set an answer-quality and security trust boundary. Advisory only; the user's chosen implementation model remains in use.
