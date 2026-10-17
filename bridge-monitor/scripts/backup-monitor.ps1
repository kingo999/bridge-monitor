param(
    [string]$DsnEnvName = 'BRIDGE_MONITOR_DSN',
    [string]$OutputPath = ''
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'project-path.ps1')
$backups = Resolve-ProjectChild (Join-Path $project 'var\backups') $project
New-Item -ItemType Directory -Force -Path $backups | Out-Null
if (-not $OutputPath) {
    $OutputPath = Join-Path $backups ('bridge-monitor-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '.dump')
}
$output = Resolve-ProjectChild $OutputPath $project
if (-not $output.StartsWith($backups.TrimEnd('\') + '\',
    [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Backup output must stay under bridge-monitor\var\backups.'
}
if (Test-Path -LiteralPath $output) { throw 'Backup output already exists.' }
$dsn = [Environment]::GetEnvironmentVariable($DsnEnvName)
if (-not $dsn) { throw 'Named database secret environment reference is missing.' }
if ($dsn -match '(?i)(password\s*=|://[^@/]+:[^@/]+@)') {
    throw 'Inline database passwords expose credentials in pg_dump process arguments; use a protected PostgreSQL passfile.'
}
$pgDump = (Get-Command pg_dump -ErrorAction Stop).Source
& $pgDump --format=custom --no-owner --no-acl --file=$output $dsn
if ($LASTEXITCODE -ne 0) {
    Remove-Item -LiteralPath $output -ErrorAction SilentlyContinue
    throw 'pg_dump failed.'
}
$hash = (Get-FileHash -LiteralPath $output -Algorithm SHA256).Hash
$record = [ordered]@{ path = $output; sha256 = $hash;
    byte_length = (Get-Item -LiteralPath $output).Length;
    completed_at_utc = [DateTime]::UtcNow.ToString('o'); format = 'pg_dump_custom' }
$record | ConvertTo-Json -Depth 3 | Set-Content -LiteralPath ($output + '.json') -Encoding utf8
$record | ConvertTo-Json -Depth 3
