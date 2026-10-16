# First real review of the MRI repository through `gpt-architect-v2` (PREPARED, NOT RUN)

Target: `D:\IdeaProjects\mri\mri-java-services` - a Maven multi-module Java services repository
(10 modules with a `pom.xml`: admin, authentication-management, capacity, context-security, emailer,
external-authentication, permission-management, product-management, security-essentials,
software-report-generator; plus fix-planner, pt, ptf-handler, security-ai, csr, utils, tools, ci, docs).
Surveyed read-only on 2026-10-14: branch `feature/plextrac-bin-msgfix`, HEAD
`29183d87cf7f150e2719afb5110d6bb78843a87d`, working tree clean, 1959 tracked files, 1404 `.java` files.

## Before you run it
1. Server: `gpt-architect-v2` (registered with `GPT_ARCHITECT_WINDOWS_SANDBOX=unelevated`, so the desktop app
   may stay open). Reconnect/restart the client so the tool list is current, and use ONLY
   `mcp__gpt-architect-v2__*`.
2. Pause edits, branch switches and builds in that repository for the duration (the bridge fingerprints the
   repo before and after and warns if it changed). Expected duration: several minutes per call; the bridge's
   default run limit is 900 s per call.
3. What leaves the machine: whatever the model reads from the repository is processed by OpenAI through your
   ChatGPT-authenticated Codex, exactly as any Codex use would be. Do not run this if that is not acceptable
   for this repository.
4. Cost: two calls against a repository this size can use a few hundred thousand input tokens each (the
   disposable-repo checks used 40-200K). It spends your ChatGPT subscription allowance.

## Message to paste into the client (Claude Code or the desktop session)

```text
Run a first review of this repository through the v2 GPT bridge. Use ONLY the MCP server "gpt-architect-v2"
(tools consult_architect and review_implementation); do not use "gpt-architect". Do not edit any file, run any
build, or switch branches.

Step 1 - call consult_architect with exactly this question:

"Read-only architecture review of this Maven multi-module Java repository. Rules: do not create, modify or delete
anything; do not run builds, tests or package managers; use only read-only commands (git, directory listing,
file reads, text search) and read at most 30 files in total. (1) Run `git rev-parse HEAD` and quote its exact
output on a line starting with CONTROL-OK:, and name the current branch. (2) Start from AGENTS.md, BUILD.md,
README.md and the root pom.xml and give the module map: for each top-level service module one line stating its
responsibility, the evidence file you based it on, and which other modules it depends on. (3) Identify the three
most important cross-module architectural risks (coupling, duplicated authentication or authorisation logic,
shared mutable state, inconsistent error handling, build or dependency hygiene), each with a severity
(High/Medium/Low), a confidence (High/Medium/Low), and the exact file paths that support it. If you could not
verify a claim from a file you read, say so explicitly instead of guessing. (4) State what you did NOT read or
could not assess. Keep the whole answer under 700 words and finish with the line END-OF-REVIEW-1."

Step 2 - call review_implementation with exactly this focus:

"Continuity and drill-down, read-only, same rules, at most 15 additional files. (1) Repeat the CONTROL-OK value
and the branch name from your previous answer exactly. (2) Take the single highest-severity risk you reported
and re-verify it from the source files: quote the minimum relevant lines (at most 10 lines per file) and say
whether you now confirm, weaken or retract it. (3) Run `git status --short` and say whether the working tree is
clean. (4) Give the one change you would make first and the test that would show it worked, without making it.
Keep it under 400 words and finish with the line END-OF-REVIEW-2."

Then show me both answers VERBATIM, including every "[bridge-warning]" line at the top, and run
`git rev-parse HEAD` and `git status --short` yourself so I can compare them. Do not paraphrase.
```

## What a good result looks like
* Both calls return text (no `[bridge-error:...]`). A single warning naming the `unelevated` backend override is
  expected and correct; any other warning (repository changed, fingerprint unavailable, uncertainty) must be
  read, not skipped.
* `CONTROL-OK` equals `29183d87cf7f150e2719afb5110d6bb78843a87d` in BOTH answers, and the branch name is
  `feature/plextrac-bin-msgfix` in both (continuity by recall).
* `END-OF-REVIEW-1` and `END-OF-REVIEW-2` present; answer 2 reports a clean tree.
* Every risk in answer 1 cites file paths; spot-check two of them yourself before acting on anything. The model
  is told to say what it could not verify: treat unsupported claims as hypotheses.
* Telemetry (`%USERPROFILE%\.gpt-architect-bridge-v2\telemetry\calls.jsonl`): two records, the second with
  `resumed: true` and the same `thread_id`, no `error_code`, no prompt/answer text.
* `git status --short` in the repository is still empty afterwards; no files were created.

## If it fails
| Symptom | Meaning | Action |
|---|---|---|
| `sandbox_unavailable/sandbox_setup_failed` naming `node_repl.exe` | the server ran WITHOUT the `unelevated` override (client cached an old registration) | restart the client; confirm `claude mcp get gpt-architect-v2` shows the env var |
| `sandbox_unavailable/repo_unreadable` | the sandbox cannot open the repository directory (ACL) | tell me the exact message; do not loosen anything |
| `timeout` | the review needed more than 900 s | narrow the question (fewer files); raise `GPT_ARCHITECT_TIMEOUT` only if you decide to |
| `turn_repo_access_failed` / `turn_sandbox_setup_failed` | an infrastructure failure inside the real turn; the answer was withheld on purpose | keep the message, no retry was made |
| a repository-changed warning | the tree changed during the call | re-run with edits paused |

## Not covered by this review
Write-blocking (covered in `docs/DESKTOP_COEXISTENCE.md` sections 11-12), correctness of the model's
architectural judgement (advisory, needs human validation), anything outside the files the model chose to read.
