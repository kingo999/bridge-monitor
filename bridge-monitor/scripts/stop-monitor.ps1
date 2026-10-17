$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'project-path.ps1')
$receipt = Resolve-ProjectChild (Join-Path $project 'var\run\dashboard-process.json') $project
if (-not (Test-Path -LiteralPath $receipt)) {
    throw 'No project-owned dashboard process receipt exists.'
}
$record = Get-Content -LiteralPath $receipt -Raw | ConvertFrom-Json
$expectedPython = Join-Path $project '.venv\Scripts\python.exe'
$expectedScript = Join-Path $project 'scripts\serve-dashboard.py'
if ($record.executable -ne $expectedPython -or $record.script -ne $expectedScript -or
    -not $record.config.StartsWith($project.TrimEnd('\') + '\',
        [StringComparison]::OrdinalIgnoreCase) -or
    (Resolve-ProjectChild $record.server_pid_file $project) -ne $record.server_pid_file) {
    throw 'Process receipt names a target outside this project.'
}
$server = Get-Process -Id $record.server_pid -ErrorAction SilentlyContinue
if (-not $server) {
    throw 'Recorded dashboard process is absent; inspect receipt before removal.'
}
if ($server.StartTime.ToUniversalTime().Ticks -ne [long]$record.server_started_at_utc_ticks) {
    throw 'Recorded PID no longer identifies the project dashboard process.'
}
$pidEvidence = Get-Content -LiteralPath $record.server_pid_file -Raw | ConvertFrom-Json
if ($pidEvidence.pid -ne $record.server_pid -or
    $pidEvidence.script -ne $expectedScript -or $pidEvidence.config -ne $record.config) {
    throw 'Server PID receipt does not match the project dashboard command.'
}
Stop-Process -Id $record.server_pid -ErrorAction Stop
$server.WaitForExit(10000) | Out-Null
if (-not $server.HasExited) {
    throw 'Dashboard did not stop; receipt retained.'
}
$launcher = Get-Process -Id $record.launcher_pid -ErrorAction SilentlyContinue
if ($launcher -and $launcher.Id -ne $record.server_pid -and
    $launcher.StartTime.ToUniversalTime().Ticks -eq [long]$record.launcher_started_at_utc_ticks -and
    $launcher.Path -eq $expectedPython) {
    Stop-Process -Id $launcher.Id -ErrorAction Stop
}
$stopped = [ordered]@{ server_pid = $record.server_pid;
    launcher_pid = $record.launcher_pid; stopped_at_utc = [DateTime]::UtcNow.ToString('o');
    outcome = 'dashboard_process_stopped'; database = 'left_running'; model_executions = 'unchanged' }
$stopReceipt = Resolve-ProjectChild (Join-Path $project 'var\run\dashboard-last-stop.json') $project
$stopped | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath $stopReceipt -Encoding utf8
Remove-Item -LiteralPath $receipt
Remove-Item -LiteralPath $record.server_pid_file -ErrorAction SilentlyContinue
$stopped | ConvertTo-Json -Depth 3
