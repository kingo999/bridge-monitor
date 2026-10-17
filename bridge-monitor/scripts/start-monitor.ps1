param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\dashboard-service.example.toml')
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'project-path.ps1')
$config = Resolve-ProjectChild $ConfigPath $project
if (-not (Test-Path -LiteralPath $config -PathType Leaf)) { throw 'Dashboard config is unavailable.' }
$python = Join-Path $project '.venv\Scripts\python.exe'
$service = Join-Path $project 'scripts\serve-dashboard.py'
if (-not (Test-Path -LiteralPath $python) -or -not (Test-Path -LiteralPath $service)) {
    throw 'Project-local Python and dashboard service are required.'
}
if (-not $env:BRIDGE_MONITOR_DSN -or -not $env:BRIDGE_MONITOR_OPERATOR_TOKEN -or
    -not $env:BRIDGE_MONITOR_PRODUCER_TOKENS_JSON) {
    throw 'Database, operator and producer secret environment references must be set.'
}
$run = Resolve-ProjectChild (Join-Path $project 'var\run') $project
$logs = Resolve-ProjectChild (Join-Path $project 'var\logs') $project
New-Item -ItemType Directory -Force -Path $run, $logs | Out-Null
$receipt = Join-Path $run 'dashboard-process.json'
if (Test-Path -LiteralPath $receipt) {
    throw 'A dashboard process receipt already exists; inspect it before starting another.'
}
$stdout = Join-Path $logs 'dashboard.stdout.log'
$stderr = Join-Path $logs 'dashboard.stderr.log'
$arguments = @('"' + $service + '"', '--config', '"' + $config + '"')
$pidFile = Resolve-ProjectChild (Join-Path $run ('dashboard-server-' + [guid]::NewGuid().ToString('N') + '.json')) $project
$priorPidFile = $env:BRIDGE_MONITOR_SERVER_PID_FILE
try {
    $env:BRIDGE_MONITOR_SERVER_PID_FILE = $pidFile
    $process = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $project `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -WindowStyle Hidden -PassThru
} finally {
    $env:BRIDGE_MONITOR_SERVER_PID_FILE = $priorPidFile
}
$serverRecord = $null
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    if (Test-Path -LiteralPath $pidFile) {
        try {
            $serverRecord = Get-Content -LiteralPath $pidFile -Raw | ConvertFrom-Json
            break
        } catch { }
    }
    Start-Sleep -Milliseconds 250
}
if (-not $serverRecord -or $serverRecord.script -ne $service -or
    $serverRecord.config -ne $config) {
    throw "Dashboard server identity was not recorded; inspect $stderr"
}
$server = Get-Process -Id $serverRecord.pid -ErrorAction SilentlyContinue
if (-not $server) { throw "Dashboard server exited during startup; inspect $stderr" }
$record = [ordered]@{
    launcher_pid = $process.Id
    launcher_started_at_utc_ticks = $process.StartTime.ToUniversalTime().Ticks
    server_pid = $server.Id
    server_started_at_utc_ticks = $server.StartTime.ToUniversalTime().Ticks
    server_pid_file = $pidFile
    executable = $python
    script = $service
    config = $config
    stdout = $stdout
    stderr = $stderr
}
$temporary = Join-Path $run ('dashboard-process.' + [guid]::NewGuid().ToString('N') + '.tmp')
try {
    $record | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $receipt
} finally {
    Remove-Item -LiteralPath $temporary -ErrorAction SilentlyContinue
}
$record | ConvertTo-Json -Depth 3
