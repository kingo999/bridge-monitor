# One-time elevated installation; registers DISABLED tasks and never starts them.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ReaderSource,
    [Parameter(Mandatory=$true)][string]$SupervisorSource,
    [Parameter(Mandatory=$true)][string]$ReaderSHA256,
    [Parameter(Mandatory=$true)][string]$SupervisorSHA256,
    [Parameter(Mandatory=$true)][string]$BuildEvidenceSource,
    [Parameter(Mandatory=$true)][string]$BuildEvidenceSHA256,
    [Parameter(Mandatory=$true)][string]$OperatorSid,
    [Parameter(Mandatory=$true)][string]$ProjectRoot
)
$ErrorActionPreference = 'Stop'
$root = 'C:\Program Files\BridgeMonitorAudit'
$taskName = 'BridgeMonitor-OutboundAudit'
$consumerName = 'BridgeMonitor-OutboundAudit-Ingest'
$system = [Environment]::GetFolderPath('System')
$admin = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
if ($OperatorSid -notmatch '^S-1-5-21-\d+-\d+-\d+-\d+$') { throw 'A named user account SID is required' }
$operator = New-Object Security.Principal.SecurityIdentifier($OperatorSid)
$account = Get-CimInstance Win32_UserAccount -Filter "SID='$OperatorSid'"
if (-not $account) { throw 'Operator SID must resolve to a Windows user account' }
$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole($admin)) { throw 'Administrator elevation required' }
if ($PSScriptRoot -notmatch '^C:\\Program Files\\BridgeMonitorAudit\.Setup-[a-f0-9]{32}$') { throw 'Run only a pinned protected staging copy' }
. (Join-Path $PSScriptRoot 'protected-file-utils.ps1')

function Assert-NoReparse([string]$path) {
    $item = Get-Item -LiteralPath $path -Force
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Reparse point rejected: $($item.FullName)" }
        $item = if ($item -is [IO.DirectoryInfo]) { $item.Parent } else { $item.Directory }
    }
}
foreach ($source in @($ReaderSource, $SupervisorSource, $BuildEvidenceSource)) { Assert-NoReparse $source }
Assert-NoReparse 'C:\Program Files'
Assert-NoReparse $ProjectRoot
if ((Get-FileHash -LiteralPath $ReaderSource -Algorithm SHA256).Hash -ne $ReaderSHA256) { throw 'Reader hash mismatch' }
if ((Get-FileHash -LiteralPath $SupervisorSource -Algorithm SHA256).Hash -ne $SupervisorSHA256) { throw 'Supervisor hash mismatch' }
if ((Get-FileHash -LiteralPath $BuildEvidenceSource).Hash -ne $BuildEvidenceSHA256) { throw 'Build manifest hash mismatch' }
$build = Get-Content -LiteralPath $BuildEvidenceSource -Raw | ConvertFrom-Json
if (-not $build.reproducible -or $build.ran_reader -or @($build.builds).Count -ne 2 -or
    $build.builds[0].executable_sha256 -ne $ReaderSHA256 -or $build.builds[1].executable_sha256 -ne $ReaderSHA256 -or
    $build.source_git_blob -ne $build.previously_reviewed_source_git_blob -or
    (Get-FileHash -LiteralPath (Join-Path $ProjectRoot 'scripts\outbound-audit\collector.cpp')).Hash -ne $build.source_sha256) { throw 'Reviewed-source reproducible build required' }
$old = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($old.State -ne 'Disabled' -or $old.Settings.Enabled) { throw 'Existing audit task must already be stopped and disabled' }
if (Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'bridge-outbound-reader.exe' -or ($_.Name -like '*python*' -and $_.CommandLine -like '*bridge.audit_cli*collect*') }) { throw 'An audit collector is still running' }
if (Test-Path -LiteralPath $root) { throw 'Protected destination already exists; inspect it instead of overwriting' }
if (Get-ScheduledTask -TaskName $consumerName -ErrorAction SilentlyContinue) { throw 'Consumer task already exists; inspect it first' }

# Program Files is a trusted parent. Protect the new directory before copying.
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;$OperatorSid)")
$directory = New-Object IO.DirectoryInfo($root)
$directory.Create($acl)
try {
    foreach ($name in @('data', 'scratch')) { New-ProtectedDirectory (Join-Path $root $name) $OperatorSid }
    Copy-ProtectedFile $ReaderSource (Join-Path $root 'bridge-outbound-reader.exe') $ReaderSHA256 $OperatorSid
    Copy-ProtectedFile $SupervisorSource (Join-Path $root 'run-protected-reader.ps1') $SupervisorSHA256 $OperatorSid
    Copy-ProtectedFile $BuildEvidenceSource (Join-Path $root 'build-evidence.json') $BuildEvidenceSHA256 $OperatorSid
    if ((Get-FileHash -LiteralPath (Join-Path $root 'bridge-outbound-reader.exe')).Hash -ne $ReaderSHA256) { throw 'Copied reader changed' }
    if ((Get-FileHash -LiteralPath (Join-Path $root 'run-protected-reader.ps1')).Hash -ne $SupervisorSHA256) { throw 'Copied supervisor changed' }
    if ((Get-FileHash -LiteralPath (Join-Path $root 'build-evidence.json')).Hash -ne $BuildEvidenceSHA256) { throw 'Copied build manifest changed' }
    Write-ProtectedText (Join-Path $root 'previous-disabled-task.xml') (Export-ScheduledTask -TaskName $taskName) $OperatorSid
    # Ownership matters: a named ordinary-user owner could rewrite a restrictive DACL.
    foreach ($item in @((Get-Item -LiteralPath $root)) + @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
        $itemAcl = Get-Acl -LiteralPath $item.FullName
        $itemAcl.SetOwner($admin)
        Set-Acl -LiteralPath $item.FullName -AclObject $itemAcl
    }
    $action = New-ScheduledTaskAction -Execute (Join-Path $system 'WindowsPowerShell\v1.0\powershell.exe') -WorkingDirectory $root -Argument ('-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -WindowStyle Hidden -File "' + (Join-Path $root 'run-protected-reader.ps1') + '"')
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId 'SYSTEM' -LogonType ServiceAccount -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $settings.Enabled = $false
    $triggers = @((New-ScheduledTaskTrigger -AtStartup), (New-ScheduledTaskTrigger -AtLogOn -User $OperatorSid))
    $definition = New-ScheduledTask -Action $action -Principal $taskPrincipal -Settings $settings -Trigger $triggers -Description 'Protected native metadata producer. Disabled pending security review; never executes checkout code.'
    Register-ScheduledTask -TaskName $taskName -InputObject $definition -Force | Out-Null
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    $registered = $folder.GetTask($taskName)
    $registered.SetSecurityDescriptor("O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;$OperatorSid)", 0)

    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $journal = Join-Path $ProjectRoot 'var\audit\outbound.sqlite'
    $cursor = Join-Path $ProjectRoot 'var\audit\spool-cursor.json'
    $arguments = '-m bridge.audit_spool --db "' + $journal + '" --spool "' + (Join-Path $root 'data\metadata.tsv') + '" --checkpoint "' + $cursor + '"'
    $consumerAction = New-ScheduledTaskAction -Execute $python -WorkingDirectory $ProjectRoot -Argument $arguments
    $consumerPrincipal = New-ScheduledTaskPrincipal -UserId $OperatorSid -LogonType Interactive -RunLevel Limited
    $consumerSettings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
    $consumerSettings.Enabled = $false
    $consumer = New-ScheduledTask -Action $consumerAction -Principal $consumerPrincipal -Settings $consumerSettings -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $OperatorSid) -Description 'Ordinary-user audit metadata ingestion. Disabled pending security review.'
    Register-ScheduledTask -TaskName $consumerName -InputObject $consumer | Out-Null
    $folder.GetTask($consumerName).SetSecurityDescriptor("O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;$OperatorSid)", 0)

    $evidence = @{
        installed = $true; activated = $false; at = (Get-Date -Format o)
        reader_sha256 = $ReaderSHA256; supervisor_sha256 = $SupervisorSHA256; build_evidence_sha256 = $BuildEvidenceSHA256
        root = $root; operator_sid = $OperatorSid; project_root = $ProjectRoot
        tasks = @($taskName, $consumerName)
    }
    Write-ProtectedText (Join-Path $root 'installation.json') ($evidence | ConvertTo-Json -Depth 5) $OperatorSid
    $receiptAcl = Get-Acl -LiteralPath (Join-Path $root 'installation.json')
    $receiptAcl.SetOwner($admin)
    Set-Acl -LiteralPath (Join-Path $root 'installation.json') -AclObject $receiptAcl
} finally {
    # Failure is fail-closed, too. No Start-ScheduledTask call exists in this installer.
    foreach ($name in @($taskName, $consumerName)) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task) { Disable-ScheduledTask -TaskName $name | Out-Null }
    }
}
foreach ($name in @($taskName, $consumerName)) {
    $task = Get-ScheduledTask -TaskName $name
    if ($task.State -ne 'Disabled' -or $task.Settings.Enabled) { throw "Task is not disabled: $name" }
}
Write-Output 'Installed protected producer and ordinary-user consumer. Both remain disabled.'
