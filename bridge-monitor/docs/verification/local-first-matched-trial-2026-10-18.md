# Local-first matched trial — 18 October 2026

This was an out-of-band feasibility experiment. It did not change the active bridge policy, dashboard data, database, or the original bridge sources. Raw receipts are in `var/probes/local-first-matched-20261018.json` and `var/probes/local-first-json-mode-20261018.json` (local, ignored by Git).

## Method

Eight fixed, read-only questions covered code reasoning, SQL, arithmetic, and bridge-style metrics. Each had an exact answer key written into the trial harness before any model call. The direct arm sent the same question to a fresh restricted Claude CLI invocation using the `sonnet` profile (reported `claude-sonnet-5-5`). The local-first arm sent it to pinned `qwen2.5-coder:14b-instruct-q4_K_M` on Skynet, accepted it only if a JSON `answer` string exactly matched the key, and otherwise invoked Sonnet as fallback. The Qwen model digest matched the pinned policy digest when checked after the run. Model outputs and per-call usage and elapsed time are in the receipts.

The first Qwen pass used a text response and the pre-registered strict JSON gate. An exploratory second pass repeated the same questions with Ollama `format: "json"`, retaining the same answer keys and fallback rule. This second pass is a format experiment on the same tasks, not an independent holdout. Calls went directly to Ollama and Claude CLI, outside the bridge agent, so production queueing and bridge overhead were not measured.

## Observations

| Measure | Sonnet direct | Local-first, plain output | Local-first, JSON mode |
| --- | ---: | ---: | ---: |
| Tasks | 8 | 8 | 8 |
| Qwen answers passing the exact gate | — | 1 | 4 |
| Sonnet calls required by that path | 8 | 7 | 4 |
| Sum of observed path elapsed times | 20.885 s | 27.447 s | 15.925 s |
| Qwen input / output tokens | — | 710 / 118 | 710 / 90 |

The first pass exposed a format issue: four Qwen answers had the correct substance but wrapped their JSON in Markdown fences; three were wrong. JSON mode removed the fence problem. It passed four exact keys; one further answer used the word “three” instead of the required `3`, and three were substantively wrong (`n or 10` with `n=0`, a queue balance, and Python list aliasing). The local-first JSON-mode path therefore skipped four Sonnet calls and fell back on four. All eight final answers were semantically correct, but one Sonnet fallback added prose outside the requested JSON, so only seven of eight final outputs met the strict machine-readable contract. Sonnet direct was semantically correct on all eight, but only five direct outputs met that same strict format contract.

The four skipped direct-arm Sonnet calls had observed usage of **8 ordinary input, 2,725 cache-creation input, and 115 output tokens**. Across all eight direct calls, usage was 16 ordinary input, 5,420 cache-creation input, and 283 output tokens. The CLI labeled the skipped calls' combined `$0.012066` estimate as *list* cost; this is not an observed bill or net saving. The second pass's fallback calls benefited from prompt caching after the direct-arm calls, so subtracting their reported dollar estimates from the direct arm would overstate savings. GPT/Codex parent tokens and actual subscription or invoice charges were not measured.

The JSON-mode path spent 2.815 seconds on all eight Qwen calls and 13.110 seconds on four Sonnet fallbacks. The aggregate 15.925 seconds versus 20.885 seconds direct is an illustrative latency result for these short tasks, not a production latency forecast. The sample was deliberately small and simple, and its known answer keys cannot be assumed for open-ended work.

## Decision supported by this trial

Local-first can avoid paid **worker invocations** when a task has a cheap, independent correctness check: four of eight in the exploratory JSON-mode pass. Generic Qwen delegation inside Sonnet cannot produce that saving, because Sonnet has already been invoked. Qwen made three substantive errors in this small sample, so an unchecked local-first route would be unsafe. Keep the live policy unchanged. A future narrow canary should use structured output, independently checkable task categories, automatic Sonnet fallback, and a separate holdout set; measure parent plus worker tokens, task quality, and end-to-end latency before claiming paid savings. Advisory execution model for that future implementation: Claude Sonnet, because it matches the existing paid worker and its fallback behavior can be checked against the current bridge contract; this does not change the user's selected model.
