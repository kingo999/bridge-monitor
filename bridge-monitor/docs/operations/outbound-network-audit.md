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
- Windows provider access determines coverage. Full process-lifecycle and send evidence requires access to the kernel process and network providers. A restricted account may see Winsock attempts only. When process events are unavailable, the reader checks still-live processes and verifies their creation times before linking ancestors. Short-lived or already-running processes may lack attribution. The application hooks alone cannot identify every RPC in encrypted or third-party traffic.
- Look for collector heartbeat and gap records in `show`. A stopped or stale heartbeat indicates unknown coverage. Provider denial, decoding failures and reported ETW loss produce gaps. Kernel coverage here is TCP/UDP. Other IP protocols, provider-side calls and events lost before collection are outside this reader's coverage. Large traces may exceed the collector's throughput.
- Keep the query string, path parameters, headers, credentials, prompts and message bodies outside this journal. It does not perform packet capture or TLS interception. Files show host, process, endpoint and time metadata and require appropriate access controls. Stop writers before archiving or deleting a journal. Size and retention are operator-managed.

To check ETW installation, run `python scripts/outbound-audit/smoke.py var/audit-bin/bridge-outbound-reader.exe` with the path to your built executable. The smoke test restricts output to its own PID and uses a loopback receiver. Before rollout, repeat it with kernel-provider access, then run a separate controlled off-machine transfer and compare its receiver log with the audit evidence.
