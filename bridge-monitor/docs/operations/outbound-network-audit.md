# Observe outbound agent traffic on Windows

The collector observes connections and sends on the local Windows host. Bridge hooks add logical application requests and run identities. Records live in a separate SQLite file. The operator selects its location, access permissions and retention. Use a directory accessible only to the collector, Bridge and authorized readers. The database, WAL and related files share that directory.

## Start an isolated observation

From the Bridge Monitor project directory, build the reader in an x64 Visual Studio developer prompt with the Windows SDK installed:

~~~cmd
scripts\outbound-audit\build.cmd var\audit-bin
~~~

In PowerShell, choose an existing private directory and start collection. The examples use a local Python environment with Bridge Monitor's dependencies installed.

~~~powershell
$db = 'C:\audit-lab\outbound.sqlite'
python -m bridge.audit_cli --db $db collect .\var\audit-bin\bridge-outbound-reader.exe
~~~

Start the collector before launching the test worker. Set the following variable in the environment of the Bridge instance you want to observe. Open a separate terminal for the collector. Stop it with Ctrl+C.

~~~powershell
$env:BRIDGE_OUTBOUND_AUDIT_DB = 'C:\audit-lab\outbound.sqlite'
~~~

Bridge registers its CLI worker process by PID and Windows creation time. The host collector follows process ancestry. To identify another running worker, register its PID before its workload starts:

~~~powershell
python -m bridge.audit_cli --db $db register --pid 1234 --agent research --run run-17
~~~

Host mode observes all processes, including unattributed traffic. The HTTP adapters and classifier attach application evidence where instrumented. Other clients can wrap an individual logical RPC with `bridge.audit_hooks.application_call`, supplying a static operation name, endpoint and known category. Operation names and operator labels must contain metadata only. Use `category=None` when unknown. Two RPCs on a shared connection have individual request IDs. Only host send events carry byte counts.

## Evaluate a policy

Save a draft such as the following as `draft.json`. Rules support exact agent, requested hostname, IP, port and transport. Null or omitted fields are unconstrained. Names are case-insensitive, and IP addresses use canonical forms.

~~~json
{
  "default": "deny",
  "rules": [
    {"id": "research-service", "effect": "allow", "agent": "research", "ip": "192.0.2.8", "port": 443, "transport": "tcp"},
    {"id": "blocked-host", "effect": "deny", "hostname": "blocked.example"}
  ]
}
~~~

~~~powershell
python -m bridge.audit_cli --db $db show
python -m bridge.audit_cli --db $db replay draft.json
python -m bridge.audit_cli --db $db policy draft.json
python -m bridge.audit_cli --db $db show --after 50
~~~

`replay` evaluates saved evidence against the specified draft. `policy` selects the draft for new events. Both report the same content-derived version. An explicit deny wins. In the example, an IP match alone may still give *insufficient evidence* because an unknown requested hostname could match the deny rule. A default of `allow` supports a denylist. Neither mode blocks a connection.

## Reading the evidence

- A host socket attempt records the emitting PID and destination. A TCP connect event and a kernel TCP or UDP send have different meanings. Send bytes are measured at the provider, with no claim of remote receipt. Multiple socket records can describe the same call. Use request IDs to count instrumented application calls.
- Local interface addresses (including loopback) are excluded from host records. Requested endpoints from the application layer describe intent; the host observation establishes whether traffic left. Cached DNS names are possible names for the observed IP. They are read from the local resolver cache without generating a lookup and cannot satisfy a requested-hostname rule. A remote machine's onward traffic requires a collector at that boundary.
- Windows provider access determines coverage. Full process-lifecycle and send evidence requires access to the kernel process and network providers. A restricted account may see Winsock attempts only. The reader checks still-live processes and verifies creation times when process events are unavailable, or when a sender or parent predates collection. Processes that ended before their identity could be verified may lack attribution. Reported tracing gaps clear cached identity evidence. The application hooks alone cannot identify every RPC in encrypted or third-party traffic.
- Look for collector heartbeat and gap records in `show`. A stopped or stale heartbeat indicates unknown coverage. Provider denial, decoding failures and reported ETW loss produce gaps. Kernel coverage here is TCP/UDP. Other IP protocols, provider-side calls and events lost before collection are outside this reader's coverage. Large traces may exceed the collector's throughput.
- Keep the query string, path parameters, headers, credentials, prompts and message bodies outside this journal. It does not perform packet capture or TLS interception. Files show host, process, endpoint and time metadata and require appropriate access controls. Stop writers before archiving or deleting a journal. Size and retention are operator-managed.

The collector records malformed reader input as a gap and continues collecting. On abnormal reader exit, the controller attempts to stop that reader's exact ETW session. Shutdown flushes pending evidence.

To check ETW installation, run `python scripts/outbound-audit/smoke.py var/audit-bin/bridge-outbound-reader.exe --require-kernel --result var/audit-smoke.json` with the path to your built executable. The smoke test restricts output to its own PID and uses a loopback receiver. It verifies sender identity and compares received bytes with kernel send counts. `--require-kernel` fails when any requested provider is unavailable. Without that switch, attempt-only evidence returns status `partial` and exit code 2. Full success returns 0; failure returns 1.

For an operator-selected plain HTTP test receiver, supply `--host test-host.example --port 8080 --path /health`. This sends a synthetic GET, requires an HTTP status response, and compares its request size with kernel send counts for that socket. The result records whether the peer is off-machine using the local interface inventory. No machine, provider or model name is built in. The test accepts a static path without credentials or query parameters and retains no response headers or body. Choose a reachable receiver you control, then compare its own access log before rollout.
