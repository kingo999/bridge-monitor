param([string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\windows-tasks.example.json'))
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'monitor-task-config.ps1')
$config = Get-MonitorTaskConfig $ConfigPath $project

function Clear-StaleDashboardReceipt {
    param($ProjectRoot, $DashboardConfig)
    $receipt = Resolve-ProjectChild (Join-Path $ProjectRoot 'var\run\dashboard-process.json') $ProjectRoot
    if (-not (Test-Path -LiteralPath $receipt)) { return }
    $record = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
    $expectedPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $expectedScript = Join-Path $ProjectRoot 'scripts\serve-dashboard.py'
    if ($record.executable -ne $expectedPython -or $record.script -ne $expectedScript -or
        $record.config -ne $DashboardConfig -or
        (Resolve-ProjectChild $record.server_pid_file $ProjectRoot) -ne $record.server_pid_file) {
        throw 'Dashboard receipt does not identify this project and config.'
    }
    foreach ($identity in @(@($record.server_pid, $record.server_started_at_utc_ticks),
            @($record.launcher_pid, $record.launcher_started_at_utc_ticks))) {
        $candidate = Get-Process -Id $identity[0] -ErrorAction SilentlyContinue
        if ($candidate -and $candidate.StartTime.ToUniversalTime().Ticks -eq [long]$identity[1]) {
            throw 'A recorded dashboard process is still running; refusing duplicate startup.'
        }
    }
    Remove-Item -LiteralPath $receipt
    if (Test-Path -LiteralPath $record.server_pid_file) {
        Remove-Item -LiteralPath $record.server_pid_file
    }
}

$desktop = Get-Content -LiteralPath $config.desktop_secrets -Raw | ConvertFrom-Json
$dashboard = Get-Content -LiteralPath $config.dashboard_secrets -Raw | ConvertFrom-Json
if (-not $desktop.dsn -or -not $dashboard.operator_token -or -not $dashboard.producer_tokens) {
    throw 'Protected dashboard credentials are incomplete.'
}
$env:BRIDGE_MONITOR_DSN = [string]$desktop.dsn
$env:BRIDGE_MONITOR_OPERATOR_TOKEN = [string]$dashboard.operator_token
$env:BRIDGE_MONITOR_PRODUCER_TOKENS_JSON = ConvertTo-Json -InputObject $dashboard.producer_tokens -Compress

$ready = $false
for ($second = 0; $second -lt [int]$config.database_ready_seconds; $second++) {
    & $config.pg_isready_executable -h $config.postgres_host -p $config.postgres_port `
        -U $config.postgres_probe_user -q
    if ($LASTEXITCODE -eq 0) { $ready = $true; break }
    Start-Sleep -Seconds 1
}
if (-not $ready) { throw 'Project PostgreSQL did not become ready before dashboard startup.' }
while ($true) {
    Clear-StaleDashboardReceipt $project $config.dashboard_config
    & (Join-Path $PSScriptRoot 'start-monitor.ps1') -ConfigPath $config.dashboard_config | Out-Null
    $receiptPath = Join-Path $project 'var\run\dashboard-process.json'
    $record = Get-Content -LiteralPath $receiptPath -Raw | ConvertFrom-Json
    $server = Get-Process -Id $record.server_pid -ErrorAction Stop
    $server.WaitForExit()
    Start-Sleep -Seconds ([int]$config.dashboard_restart_delay_seconds)
}
