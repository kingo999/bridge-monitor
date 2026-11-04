# Read-only configuration verification. Does not start, enable or modify tasks.
[CmdletBinding()]
param([Parameter(Mandatory=$true)][string]$ReportPath)
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (([Security.Principal.WindowsPrincipal]$identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this verifier from an ordinary, non-elevated PowerShell window'
}
$project = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$report = [IO.Path]::GetFullPath($ReportPath)
$allowed = (Join-Path $project 'var') + '\'
if (-not $report.StartsWith($allowed, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetExtension($report) -ne '.json') {
    throw 'Report must be a new JSON file beneath this project var directory'
}
if (Test-Path -LiteralPath $report) { throw 'Report already exists; choose a new evidence filename' }
$ancestor = Get-Item -LiteralPath ([IO.Path]::GetDirectoryName($report)) -Force
while ($ancestor) {
    if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Report ancestry contains a reparse point' }
    $ancestor = $ancestor.Parent
}
$root = 'C:\Program Files\BridgeMonitorAudit'
$receipt = Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw | ConvertFrom-Json
$trusted = @('S-1-5-18', 'S-1-5-32-544')
$files = @()
foreach ($item in @((Get-Item -LiteralPath $root)) + @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point in installation: $($item.FullName)" }
    $acl = Get-Acl -LiteralPath $item.FullName
    $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin $trusted) { throw "Untrusted owner: $($item.FullName)" }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and
            (([long]$rule.FileSystemRights -band 0x500D0156) -ne 0)) {
            throw "Untrusted write permission: $($item.FullName)"
        }
    }
    $files += @{ path = $item.FullName; owner = $owner; sddl = $acl.Sddl }
}
$walk = Get-Item -LiteralPath $root
while ($walk) {
    if ($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Reparse point in installation ancestry' }
    $walk = $walk.Parent
}
if (-not (Get-Acl -LiteralPath $root).AreAccessRulesProtected) { throw 'Installation DACL is not protected' }
$hashes = @{
    'bridge-outbound-reader.exe' = $receipt.reader_sha256
    'run-protected-reader.ps1' = $receipt.supervisor_sha256
    'build-evidence.json' = $receipt.build_evidence_sha256
}
foreach ($name in $hashes.Keys) {
    if ((Get-FileHash -LiteralPath (Join-Path $root $name)).Hash -ne $hashes[$name]) { throw "Installed hash mismatch: $name" }
}
if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'run-protected-reader.ps1')).Hash -ne $receipt.supervisor_sha256) {
    throw 'Reviewed supervisor source differs from installed supervisor'
}
$build = Get-Content -LiteralPath (Join-Path $root 'build-evidence.json') -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath (Join-Path $PSScriptRoot 'collector.cpp')).Hash -ne $build.source_sha256 -or
    -not $build.reproducible -or @($build.builds).Count -ne 2 -or $build.ran_reader -or
    $build.builds[0].executable_sha256 -ne $receipt.reader_sha256 -or $build.builds[1].executable_sha256 -ne $receipt.reader_sha256 -or
    $build.source_git_blob -ne $build.previously_reviewed_source_git_blob) { throw 'Installed build provenance mismatch' }
$scheduler = New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder = $scheduler.GetFolder('\')
$tasks = @()
foreach ($name in @('BridgeMonitor-OutboundAudit', 'BridgeMonitor-OutboundAudit-Ingest')) {
    $task = Get-ScheduledTask -TaskName $name
    $registered = $folder.GetTask($name)
    if ($registered.Enabled -or $registered.State -ne 1 -or $task.State -ne 'Disabled') { throw "Task is not stopped and disabled: $name" }
    $sddl = $registered.GetSecurityDescriptor(7)
    $security = New-Object Security.AccessControl.RawSecurityDescriptor($sddl)
    if ($security.Owner.Value -notin $trusted) { throw 'Untrusted task owner' }
    foreach ($ace in $security.DiscretionaryAcl) {
        if ($ace.AceType -ne 'AccessAllowed') { throw 'Unexpected task ACE' }
        if ($ace.SecurityIdentifier.Value -notin $trusted) {
            if ($ace.SecurityIdentifier.Value -ne $receipt.operator_sid -or $ace.AccessMask -notin @(-2147483648, 0x120089)) {
                throw 'Nonadministrator can modify or execute an audit task'
            }
        }
    }
    if ($name -eq 'BridgeMonitor-OutboundAudit') {
        $expectedExe = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\powershell.exe'
        $expectedArgs = '-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + (Join-Path $root 'run-protected-reader.ps1') + '"'
        if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne $expectedExe -or
            $task.Actions[0].Arguments -ne $expectedArgs -or $task.Actions[0].WorkingDirectory -ne $root -or
            $task.Principal.UserId -notin @('SYSTEM', 'S-1-5-18') -or $task.Principal.RunLevel -ne 'Highest') {
            throw 'Unexpected privileged execution chain'
        }
    } else {
        $expectedArgs = '-m bridge.audit_spool --db "' + (Join-Path $project 'var\audit\outbound.sqlite') + '" --spool "' + (Join-Path $root 'data\metadata.tsv') + '" --checkpoint "' + (Join-Path $project 'var\audit\spool-cursor.json') + '"'
        if (@($task.Actions).Count -ne 1 -or $task.Actions[0].Execute -ne (Join-Path $project '.venv\Scripts\python.exe') -or
            $task.Actions[0].Arguments -ne $expectedArgs -or $task.Actions[0].WorkingDirectory -ne $project) { throw 'Unexpected consumer action' }
        $userSid = $task.Principal.UserId
        if ($userSid -notlike 'S-1-*') {
            $account = New-Object Security.Principal.NTAccount($userSid)
            $userSid = $account.Translate([Security.Principal.SecurityIdentifier]).Value
        }
        if ($userSid -ne $receipt.operator_sid -or $task.Principal.RunLevel -ne 'Limited' -or
            $task.Principal.LogonType -ne 'Interactive') { throw 'Consumer is not limited to the ordinary interactive user' }
    }
    [xml]$xml = $registered.Xml
    $settings = $xml.Task.Settings
    if ($settings.Enabled -ne 'false' -or $settings.ExecutionTimeLimit -ne 'PT0S' -or
        $settings.MultipleInstancesPolicy -ne 'IgnoreNew' -or $settings.RestartOnFailure.Count -ne '999' -or
        $settings.RestartOnFailure.Interval -ne 'PT1M' -or $settings.StartWhenAvailable -ne 'true' -or
        $settings.DisallowStartIfOnBatteries -ne 'false' -or $settings.StopIfGoingOnBatteries -ne 'false') { throw 'Unexpected scheduler settings' }
    $triggerNodes = @($xml.Task.Triggers.ChildNodes | Where-Object { $_.NodeType -eq 'Element' })
    $logon = @($triggerNodes | Where-Object { $_.LocalName -eq 'LogonTrigger' })
    $boot = @($triggerNodes | Where-Object { $_.LocalName -eq 'BootTrigger' })
    $expectedCount = if ($name -eq 'BridgeMonitor-OutboundAudit') { 2 } else { 1 }
    if ($triggerNodes.Count -ne $expectedCount -or $logon.Count -ne 1 -or $boot.Count -ne ($expectedCount-1)) { throw 'Unexpected audit triggers' }
    $logonSid = $logon[0].UserId
    if ($logonSid -notlike 'S-1-*') { $logonSid = (New-Object Security.Principal.NTAccount($logonSid)).Translate([Security.Principal.SecurityIdentifier]).Value }
    if ($logonSid -ne $receipt.operator_sid) { throw 'Unexpected logon trigger user' }
    foreach ($trigger in $triggerNodes) {
        # Windows omits the schema-default Enabled=true element when exporting.
        if (($trigger.Enabled -and $trigger.Enabled -ne 'true') -or $trigger.EndBoundary) { throw 'Unexpected disabled or expiring trigger' }
    }
    $tasks += @{ name = $name; state = [string]$task.State; enabled = $registered.Enabled; sddl = $sddl
        user = $task.Principal.UserId; run_level = [string]$task.Principal.RunLevel
        logon_type = [string]$task.Principal.LogonType
        execute = $task.Actions[0].Execute; arguments = $task.Actions[0].Arguments
        working_directory = $task.Actions[0].WorkingDirectory; xml = $registered.Xml }
}
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'bridge-outbound-reader.exe' -or
    ($_.Name -like '*python*' -and ($_.CommandLine -like '*bridge.audit_cli*collect*' -or $_.CommandLine -like '*-m bridge.audit_spool*'))
})
if ($running.Count) { throw 'An audit producer or consumer is running' }
$unreadable = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like '*python*' -and -not $_.CommandLine })
$result = @{ verified = $true; at = (Get-Date -Format o); activated = $false; tasks = $tasks
    files = $files; hashes = $hashes; visible_collector_processes = $running.Count
    process_scope = 'ordinary-token visible processes; use protected administrator inventory for full command-line coverage'
    unreadable_python_command_lines = $unreadable.Count
    user_sid = $identity.User.Value; elevated = $false }
$json = $result | ConvertTo-Json -Depth 7
$output = New-Object IO.FileStream($report, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
$writer = New-Object IO.StreamWriter($output, (New-Object Text.UTF8Encoding($false)))
try { $writer.Write($json) } finally { $writer.Dispose(); $output.Dispose() }
Write-Output 'Verified hashes, full task definitions/DACLs, disabled state and ordinary-token process visibility.'
