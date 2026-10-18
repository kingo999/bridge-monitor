param([string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\windows-tasks.example.json'))
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'monitor-task-config.ps1')
$config = Get-MonitorTaskConfig $ConfigPath $project
New-Item -ItemType Directory -Force -Path (Split-Path $config.postgres_log) | Out-Null
& $config.pg_isready_executable -h $config.postgres_host -p $config.postgres_port `
    -U $config.postgres_probe_user -q
if ($LASTEXITCODE -eq 0) { throw 'Project PostgreSQL port is already accepting connections; refusing a second server.' }
$stdout = $config.postgres_log + '.stdout'
$stderr = $config.postgres_log + '.stderr'
$args = @('-D', ('"' + $config.data_dir + '"'), '-h', $config.postgres_host,
    '-p', [string]$config.postgres_port)
$server = Start-Process -FilePath $config.postgres_executable -ArgumentList $args `
    -WorkingDirectory $project -RedirectStandardOutput $stdout -RedirectStandardError $stderr `
    -WindowStyle Hidden -PassThru -Wait
if ($server.ExitCode -ne 0) { throw "Project PostgreSQL exited with code $($server.ExitCode)." }
