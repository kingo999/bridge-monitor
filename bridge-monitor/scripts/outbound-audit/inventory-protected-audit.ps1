# Read-only administrator inventory from a protected staging copy; writes only
# its protected evidence report. No collector start, stop, enable or policy edits.
param(
    [Parameter(Mandatory=$true)][string]$StagingDirectory,
    [Parameter(Mandatory=$true)][bool]$VerifierElevationRejected
)
$ErrorActionPreference = 'Stop'
$root = 'C:\Program Files\BridgeMonitorAudit'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not ([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) { throw 'Administrator inventory requires elevation' }
if ($PSScriptRoot -ne $StagingDirectory -or $PSScriptRoot -notmatch '^C:\\Program Files\\BridgeMonitorAudit\.Setup-[a-f0-9]{32}$') { throw 'Protected staging required' }
. (Join-Path $PSScriptRoot 'protected-file-utils.ps1')
$receipt = Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw | ConvertFrom-Json
function Command-Hash([string]$value) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($value)))).Replace('-', '') }
    finally { $sha.Dispose() }
}
$routes = @()
foreach ($task in Get-ScheduledTask) {
    $text = (($task.Actions | ForEach-Object { "$($_.Execute) $($_.Arguments) $($_.WorkingDirectory)" }) -join ' ')
    if ($text -match '(?i)C:\\AI(?:\\|\s|$)|BridgeMonitorAudit|bridge\.audit_(cli|spool)|run-collector\.ps1') {
        $routes += @{ kind='task'; name=($task.TaskPath+$task.TaskName); user=$task.Principal.UserId
            run_level=[string]$task.Principal.RunLevel; state=[string]$task.State; command_sha256=(Command-Hash $text)
            is_named_audit_task=($task.TaskName -in @('BridgeMonitor-OutboundAudit','BridgeMonitor-OutboundAudit-Ingest')) }
    }
}
foreach ($service in Get-CimInstance Win32_Service) {
    if ($service.PathName -match '(?i)C:\\AI(?:\\|\s|$)|BridgeMonitorAudit|bridge\.audit_(cli|spool)|run-collector\.ps1') {
        $routes += @{ kind='service'; name=$service.Name; user=$service.StartName; state=$service.State
            command_sha256=(Command-Hash $service.PathName); is_named_audit_task=$false }
    }
}
$registry = @('Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\CurrentVersion',
              'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion',
              ('Registry::HKEY_USERS\'+$receipt.operator_sid+'\SOFTWARE\Microsoft\Windows\CurrentVersion'))
foreach ($base in $registry) {
    foreach ($subkey in @('Run','RunOnce')) {
        $key = Join-Path $base $subkey
        if (Test-Path -LiteralPath $key) {
            $properties = Get-ItemProperty -LiteralPath $key
            foreach ($property in $properties.PSObject.Properties) {
                if ($property.Name -notlike 'PS*' -and [string]$property.Value -match '(?i)C:\\AI(?:\\|\s|$)|BridgeMonitorAudit|bridge\.audit_(cli|spool)|run-collector\.ps1') {
                    $routes += @{ kind='run-key'; name=($key+'\'+$property.Name); command_sha256=(Command-Hash ([string]$property.Value)); is_named_audit_task=$false }
                }
            }
        }
    }
}
$profileKey = 'Registry::HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows NT\CurrentVersion\ProfileList\'+$receipt.operator_sid
$profile = [Environment]::ExpandEnvironmentVariables((Get-ItemProperty -LiteralPath $profileKey).ProfileImagePath)
$startup = @('C:\ProgramData\Microsoft\Windows\Start Menu\Programs\Startup',
             (Join-Path $profile 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup'))
foreach ($directory in $startup) {
    if (Test-Path -LiteralPath $directory) {
        foreach ($item in Get-ChildItem -LiteralPath $directory -File -Force) {
            if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Startup reparse point requires separate inspection' }
            if ($item.Length -gt 1MB) { throw 'Oversized startup entry requires separate inspection' }
            $bytes = [IO.File]::ReadAllBytes($item.FullName)
            $text = [Text.Encoding]::Unicode.GetString($bytes) + [Text.Encoding]::UTF8.GetString($bytes)
            if ($text -match '(?i)C:\\AI(?:\\|\s|$)|BridgeMonitorAudit|bridge\.audit_(cli|spool)|run-collector\.ps1') {
                $routes += @{ kind='startup-file'; name=$item.FullName; command_sha256=(Get-FileHash -LiteralPath $item.FullName).Hash; is_named_audit_task=$false }
            }
        }
    }
}
$processes = @(Get-CimInstance Win32_Process)
$matching = @($processes | Where-Object {
    $_.Name -eq 'bridge-outbound-reader.exe' -or
    ($_.Name -like '*python*' -and ($_.CommandLine -like '*bridge.audit_cli*collect*' -or $_.CommandLine -like '*-m bridge.audit_spool*')) -or
    ($_.Name -like '*powershell*' -and ($_.CommandLine -like '*run-collector.ps1*' -or $_.CommandLine -like '*run-protected-reader.ps1*'))
} | ForEach-Object { @{pid=$_.ProcessId;parent_pid=$_.ParentProcessId;name=$_.Name} })
$unknown = @($processes | Where-Object { ($_.Name -like '*python*' -or $_.Name -like '*powershell*') -and -not $_.CommandLine } |
    ForEach-Object { @{pid=$_.ProcessId;name=$_.Name} })
$sessions = & 'C:\Windows\System32\logman.exe' query -ets 2>&1
$logmanExit = $LASTEXITCODE
if ($logmanExit -ne 0) { throw 'Elevated ETW session inventory failed' }
$auditSessions = @($sessions | Where-Object { $_ -match '^BridgeOutboundAudit-\d+\s' })
if ($matching.Count -or $auditSessions.Count) { throw 'Unexpected active audit process or ETW session; no activation requested' }
$tasks = @()
foreach ($name in @('BridgeMonitor-OutboundAudit','BridgeMonitor-OutboundAudit-Ingest')) {
    $task = Get-ScheduledTask -TaskName $name
    if ($task.State -ne 'Disabled' -or $task.Settings.Enabled) { throw 'Audit task is not disabled' }
    $tasks += @{name=$name;state=[string]$task.State;enabled=$task.Settings.Enabled;last_run=(Get-ScheduledTaskInfo -TaskName $name).LastRunTime.ToString('o')}
}
$staging = @()
foreach ($directory in Get-ChildItem -LiteralPath 'C:\Program Files' -Directory -Filter 'BridgeMonitorAudit.Setup-*') {
    foreach ($item in @($directory) + @(Get-ChildItem -LiteralPath $directory.FullName -Recurse -Force)) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Protected staging reparse point' }
        $acl = Get-Acl -LiteralPath $item.FullName
        if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted staging owner' }
        foreach ($rule in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
            if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544') -and
                ([long]$rule.FileSystemRights -band 0x500D0156)) { throw 'Untrusted staging write access' }
        }
        $staging += @{path=$item.FullName;owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value;sddl=$acl.Sddl
            sha256=$(if ($item -is [IO.FileInfo]) { (Get-FileHash -LiteralPath $item.FullName).Hash } else { $null })}
    }
}
$report = @{ at=(Get-Date -Format o); elevated=$true; user_sid=$identity.User.Value; activated=$false; tasks=$tasks
   verifier_elevation_rejected=$VerifierElevationRejected; audit_processes=$matching; unreadable_process_command_lines=$unknown
   logman_exit=$logmanExit; audit_etw_sessions=$auditSessions; checkout_routes=$routes; staging=$staging
   route_scope='Scheduled tasks, services, HKLM/HKU operator Run/RunOnce, common/operator Startup byte inventory; not a universal persistence scan'
   route_redaction='Other route command bodies omitted; only hashes retained. Unrelated routes are not modified.' } |
    ConvertTo-Json -Depth 8
Write-ProtectedText (Join-Path $root 'verification-admin.json') $report $receipt.operator_sid
