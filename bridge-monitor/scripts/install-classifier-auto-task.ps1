param(
    [Parameter(Mandatory=$true)][string]$ConfigPath,
    [string]$SecretsPath = 'var\secrets\desktop-mcp.json',
    [string]$Database = 'bridge_monitor_pilot_m5_20261017',
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
function Resolve-LocalPath([string]$Path) {
    $candidate = if ([IO.Path]::IsPathRooted($Path)) { $Path } else { Join-Path $project $Path }
    $full = [IO.Path]::GetFullPath($candidate)
    if (-not $full.StartsWith($project + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Automatic classifier paths must stay inside the project.'
    }
    return $full
}
function Escape-Xml([string]$Text) { return [Security.SecurityElement]::Escape($Text) }
$config = Resolve-LocalPath $ConfigPath
$secrets = Resolve-LocalPath $SecretsPath
$python = Join-Path $project '.venv\Scripts\pythonw.exe'
$worker = Join-Path $project 'scripts\classifier-auto.py'
foreach ($path in @($config, $python, $worker)) {
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) { throw "Required file is missing: $path" }
}
if ($Install -and -not (Test-Path -LiteralPath $secrets -PathType Leaf)) {
    throw 'Protected desktop credentials are missing.'
}
if ($Database -notmatch '^[a-zA-Z0-9_]+$') { throw 'Invalid database name.' }
$hash = (Get-FileHash -LiteralPath $config -Algorithm SHA256).Hash.ToLowerInvariant()
$name = 'BridgeMonitor-GemmaClassifier'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$arguments = '-u "' + $worker + '" --config "' + $config + '" --config-sha256 "' +
    $hash + '" --secrets "' + $secrets + '" --database "' + $Database + '"'
$boundary = (Get-Date).AddMinutes(5).ToString('yyyy-MM-ddTHH:mm:ss')
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>Bridge Monitor advisory Gemma classifier; no live routing changes.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled><UserId>$(Escape-Xml $user)</UserId><Delay>PT30S</Delay></LogonTrigger>
    <TimeTrigger><Repetition><Interval>PT5M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition><StartBoundary>$boundary</StartBoundary><Enabled>true</Enabled></TimeTrigger>
  </Triggers>
  <Principals><Principal id="Author"><UserId>$(Escape-Xml $user)</UserId><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal></Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries><StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate><StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable><AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled><Hidden>true</Hidden><ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>3</Count></RestartOnFailure>
  </Settings>
  <Actions Context="Author"><Exec><Command>$(Escape-Xml $python)</Command><Arguments>$(Escape-Xml $arguments)</Arguments><WorkingDirectory>$(Escape-Xml $project)</WorkingDirectory></Exec></Actions>
</Task>
"@
if ($Install) {
    if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
        throw "Task already exists; inspect before replacing: $name"
    }
    Register-ScheduledTask -TaskName $name -Xml $xml -ErrorAction Stop | Out-Null
    $state = Join-Path $project 'var\run\classifier-shadow-auto'
    New-Item -ItemType Directory -Path $state -Force | Out-Null
    $xml | Set-Content -LiteralPath (Join-Path $state 'task-definition.xml') -Encoding Unicode
}
[pscustomobject]@{ name=$name; installed=[bool]$Install; config_sha256=$hash; xml=$xml } | ConvertTo-Json -Compress
