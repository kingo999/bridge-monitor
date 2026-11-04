[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$python = Join-Path $project '.venv\Scripts\pythonw.exe'
$settings = Join-Path $project 'var\secrets\outbound-pg-sync.json'
if (!(Test-Path -LiteralPath $settings -PathType Leaf) -or !(Test-Path -LiteralPath $python -PathType Leaf)) {
    throw 'Missing protected import settings or project pythonw.exe.'
}
$name = 'BridgeMonitor-OutboundAudit-PostgresSync'
if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
    throw 'Sync task already exists. Inspect it before changing its registration.'
}
$operator = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $operator -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1)
$action = New-ScheduledTaskAction -Execute $python -WorkingDirectory $project -Argument (
    '-B -m bridge.audit_pg_sync --settings "' + $settings + '"'
)
$options = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $name -Principal $principal -Trigger $trigger -Action $action -Settings $options -Description 'Drain local audit evidence into the retained PostgreSQL store while the operator is logged in.' | Out-Null
