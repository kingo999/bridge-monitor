. (Join-Path $PSScriptRoot 'project-path.ps1')

function Get-RouterShadowTaskConfig {
    param([string]$ConfigPath, [string]$ProjectRoot)
    $root = (Resolve-Path -LiteralPath $ProjectRoot).Path
    $configFile = if ([IO.Path]::IsPathRooted($ConfigPath)) { $ConfigPath } else {
        Join-Path $root $ConfigPath
    }
    $configFile = Resolve-ProjectChild $configFile $root
    if (-not (Test-Path -LiteralPath $configFile -PathType Leaf)) {
        throw 'Router shadow task config is unavailable.'
    }
    $config = Get-Content -LiteralPath $configFile -Raw | ConvertFrom-Json
    if ($config.task_name -notmatch '^BridgeMonitor-[A-Za-z0-9_-]+$') {
        throw 'Invalid router shadow task name.'
    }
    foreach ($name in @('python_executable', 'worker_script', 'runtime_config',
            'desktop_secrets', 'stdout_log', 'stderr_log', 'service_log')) {
        $value = [string]$config.$name
        if (-not $value) { throw "Missing router shadow task path: $name" }
        $candidate = if ([IO.Path]::IsPathRooted($value)) { $value } else {
            Join-Path $root $value
        }
        $config.$name = Resolve-ProjectChild $candidate $root
    }
    foreach ($name in @('python_executable', 'worker_script', 'runtime_config',
            'desktop_secrets')) {
        if (-not (Test-Path -LiteralPath $config.$name -PathType Leaf)) {
            throw "Required router shadow task file is unavailable: $name"
        }
    }
    if (-not [IO.Path]::IsPathRooted([string]$config.pg_isready_executable) -or
        -not (Test-Path -LiteralPath $config.pg_isready_executable -PathType Leaf)) {
        throw 'Configured pg_isready executable is unavailable.'
    }
    if ($config.agent_token_env_name -notmatch '^[A-Z][A-Z0-9_]*$') {
        throw 'Invalid agent token environment name.'
    }
    if ($config.postgres_host -notin @('127.0.0.1', '::1') -or
        [int]$config.postgres_port -lt 1 -or [int]$config.postgres_port -gt 65535 -or
        $config.postgres_probe_user -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') {
        throw 'Invalid project PostgreSQL probe.'
    }
    if ([int]$config.database_ready_seconds -lt 1 -or
        [int]$config.database_ready_seconds -gt 300 -or
        [int]$config.restart_delay_seconds -lt 1 -or
        [int]$config.restart_delay_seconds -gt 60) {
        throw 'Invalid router shadow task timing.'
    }
    return $config
}
