param([string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\router-shadow-task.example.json'))
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'router-shadow-task-config.ps1')
$config = Get-RouterShadowTaskConfig $ConfigPath $project
$desktop = Get-Content -LiteralPath $config.desktop_secrets -Raw | ConvertFrom-Json
if (-not $desktop.dsn -or -not $desktop.agent_token_file) {
    throw 'Protected router shadow credentials are incomplete.'
}
$tokenPath = if ([IO.Path]::IsPathRooted([string]$desktop.agent_token_file)) {
    [string]$desktop.agent_token_file
} else { Join-Path $project ([string]$desktop.agent_token_file) }
$tokenPath = Resolve-ProjectChild $tokenPath $project
if (-not (Test-Path -LiteralPath $tokenPath -PathType Leaf)) {
    throw 'Protected Skynet agent token file is unavailable.'
}
$token = (Get-Content -LiteralPath $tokenPath -Raw).Trim()
if (-not $token) { throw 'Protected Skynet agent token is empty.' }
$env:BRIDGE_MONITOR_DSN = [string]$desktop.dsn
[Environment]::SetEnvironmentVariable([string]$config.agent_token_env_name, $token, 'Process')
Remove-Variable token

foreach ($name in @('stdout_log', 'stderr_log', 'service_log')) {
    New-Item -ItemType Directory -Force -Path (Split-Path $config.$name) | Out-Null
}
while ($true) {
    $ready = $false
    for ($second = 0; $second -lt [int]$config.database_ready_seconds; $second++) {
        & $config.pg_isready_executable -h $config.postgres_host -p $config.postgres_port `
            -U $config.postgres_probe_user -q
        if ($LASTEXITCODE -eq 0) { $ready = $true; break }
        Start-Sleep -Seconds 1
    }
    if (-not $ready) {
        Add-Content -LiteralPath $config.service_log -Value "$(Get-Date -Format o) database not ready"
        Start-Sleep -Seconds ([int]$config.restart_delay_seconds)
        continue
    }
    $arguments = @('"' + $config.worker_script + '"', '--config',
        '"' + $config.runtime_config + '"')
    $worker = Start-Process -FilePath $config.python_executable -ArgumentList $arguments `
        -WorkingDirectory $project -WindowStyle Hidden -RedirectStandardOutput $config.stdout_log `
        -RedirectStandardError $config.stderr_log -PassThru -Wait
    Add-Content -LiteralPath $config.service_log -Value "$(Get-Date -Format o) worker exited $($worker.ExitCode)"
    Start-Sleep -Seconds ([int]$config.restart_delay_seconds)
}
