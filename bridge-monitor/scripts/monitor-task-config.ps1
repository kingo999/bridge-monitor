. (Join-Path $PSScriptRoot 'project-path.ps1')

function Get-MonitorTaskConfig {
    param([string]$ConfigPath, [string]$ProjectRoot)
    $root = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $path = Resolve-ProjectChild $ConfigPath $root
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw 'Task config is unavailable.' }
    $raw = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if (-not $raw) { throw 'Task config is empty.' }
    foreach ($name in @('postgres_task', 'dashboard_task')) {
        if ($raw.$name -notmatch '^BridgeMonitor-[A-Za-z0-9_-]+$') {
            throw "Invalid task name: $name"
        }
    }
    if ($raw.postgres_task -eq $raw.dashboard_task) { throw 'Task names must differ.' }
    foreach ($name in @('postgres_executable', 'pg_isready_executable')) {
        $binary = [string]$raw.$name
        if (-not [IO.Path]::IsPathRooted($binary) -or
            -not (Test-Path -LiteralPath $binary -PathType Leaf)) {
            throw "Configured PostgreSQL executable is unavailable: $name"
        }
    }
    foreach ($name in @('data_dir', 'postgres_log', 'dashboard_config',
            'dashboard_secrets', 'desktop_secrets')) {
        $value = [string]$raw.$name
        if (-not $value) { throw "Missing project path: $name" }
        $candidate = if ([IO.Path]::IsPathRooted($value)) { $value } else { Join-Path $root $value }
        $raw.$name = Resolve-ProjectChild $candidate $root
    }
    if ($raw.postgres_host -ne '127.0.0.1' -and $raw.postgres_host -ne '::1') {
        throw 'Project PostgreSQL task must bind to loopback.'
    }
    if ([int]$raw.postgres_port -lt 1 -or [int]$raw.postgres_port -gt 65535) {
        throw 'Invalid PostgreSQL port.'
    }
    if ($raw.postgres_probe_user -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw 'Invalid PostgreSQL probe role.'
    }
    if ([int]$raw.database_ready_seconds -lt 1 -or
        [int]$raw.database_ready_seconds -gt 300) {
        throw 'Invalid database readiness deadline.'
    }
    if ([int]$raw.dashboard_restart_delay_seconds -lt 1 -or
        [int]$raw.dashboard_restart_delay_seconds -gt 60) {
        throw 'Invalid dashboard restart delay.'
    }
    foreach ($name in @('data_dir', 'dashboard_config', 'dashboard_secrets', 'desktop_secrets')) {
        if (-not (Test-Path -LiteralPath $raw.$name)) { throw "Required path unavailable: $name" }
    }
    return $raw
}
