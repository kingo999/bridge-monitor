# Draft upstream issue (sanitised; NOT submitted)

For the human to review and file (or not). Home paths are shortened to `~`; no credential content was read
or is quoted.

**Title:** Windows sandbox setup refresh fails with OS error 32 on the desktop app's `node_repl.exe`, so every
`codex sandbox` / sandboxed command fails while the desktop app is running

## Environment
| | |
|---|---|
| Codex CLI | 0.162.1 (npm launcher, native `codex-win32-x64` vendor binary) |
| Desktop app | package `OpenAI.Codex_26.1007.2314.0_x64` (running) |
| OS | Windows 11 Pro 10.0.26300 |
| Sandbox backend | elevated (as reported by `codex doctor`; provisioning "complete" with the desktop app closed, "failed" with it open) |
| Mode | `sandbox_mode="read-only"`, `approval_policy="never"` |

## Observed
With the desktop app running, a sandboxed command fails in every workspace tried:

    codex sandbox -c sandbox_mode="read-only" -- cmd /c cd
    -> exit 1: windows sandbox failed: helper_unknown_error: setup refresh had errors

`~/.codex/.sandbox/sandbox.<date>.log` (excerpt, sanitised):

    [<ts> codex.exe] setup refresh: spawning .../codex-resources/codex-windows-sandbox-setup.exe (cwd=<workspace>, payload_len=3808)
    [<ts>] runtime read/execute validation failed: validate runtime read/execute access on
           ~\AppData\Local\OpenAI\Codex\runtimes\cua_node\<hash>\bin\node_repl.exe:
           open ACL target for root-only update: The process cannot access the file because it is being used
           by another process. (os error 32)
    [<ts>] setup refresh: processed 0 write roots (read roots delegated); errors=["runtime read/execute validation failed: ..."]
    [<ts>] setup refresh completed with errors: [...]
    [<ts>] setup error: setup refresh had errors
    [<ts> codex.exe] setup refresh: exited with status ExitStatus(ExitStatus(1))

Two `node_repl.exe` instances from that same path were running: one a child of the desktop app's `codex.exe`
(itself a child of the app), the other a child of a `node.exe`. The same refresh succeeded
(`errors=[]`) for the same commands when the desktop app was closed.

## Expected
The setup refresh should not fail the whole sandbox launch because a runtime executable it only needs to
validate for read/execute access is open in another process (a sharing violation on an in-use file is
expected for a running runtime). Either open the ACL target with sharing that tolerates readers/executors,
skip validation of files that are in use, or report a non-fatal warning.

## Why it matters
Any client that must run sandboxed commands (here: a read-only, `approval_policy="never"` review bridge)
cannot work from, or alongside, the desktop app on this configuration. A model turn that executes no
command is unaffected; every command a turn runs goes through the same refresh.

## Reproduction
1. Windows 11, Codex desktop app running with its computer-use / node REPL runtime started.
2. In any directory: `codex sandbox -c sandbox_mode="read-only" -- cmd /c cd`.
3. Observe exit 1 and the log excerpt above.
4. Quit the desktop app (and its `node_repl.exe`); repeat; observe success.

## Not established
* That `codex exec` fails identically in a model turn on this exact build (the refresh log lines precede each
  command a turn runs, and an earlier observation by the implementer was that commands fail while review-only
  turns succeed; not re-verified in this investigation).
* Whether another sandbox backend avoids the validation.
