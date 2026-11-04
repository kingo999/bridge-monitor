# Executed only from a hash-verified administrator-owned staging copy.
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ReaderSource,
    [Parameter(Mandatory=$true)][string]$SupervisorSource,
    [Parameter(Mandatory=$true)][string]$BuildEvidenceSource,
    [Parameter(Mandatory=$true)][string]$ReaderSHA256,
    [Parameter(Mandatory=$true)][string]$SupervisorSHA256,
    [Parameter(Mandatory=$true)][string]$BuildEvidenceSHA256
)
$ErrorActionPreference = 'Stop'
$root = 'C:\Program Files\BridgeMonitorAudit'
$trusted = @('S-1-5-18', 'S-1-5-32-544')
$admin = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole($admin)) { throw 'Elevation required' }
$staging = Get-Item -LiteralPath $PSScriptRoot
if ($staging.Parent.FullName -ne 'C:\Program Files' -or $staging.Name -notmatch '^BridgeMonitorAudit\.Setup-[a-f0-9]{32}$') { throw 'Protected staging required' }
. (Join-Path $PSScriptRoot 'protected-file-utils.ps1')
foreach ($item in @($staging, (Get-Item -LiteralPath $root)) + @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installation reparse point rejected' }
    $acl = Get-Acl -LiteralPath $item.FullName
    if ($acl.GetOwner([Security.Principal.SecurityIdentifier]).Value -notin $trusted) { throw 'Untrusted installation owner' }
    foreach ($rule in $acl.GetAccessRules($true, $true, [Security.Principal.SecurityIdentifier])) {
        if ($rule.AccessControlType -eq 'Allow' -and $rule.IdentityReference.Value -notin $trusted -and
            ([long]$rule.FileSystemRights -band 0x500D0156)) { throw 'Untrusted installation write access' }
    }
}
$names = @('BridgeMonitor-OutboundAudit', 'BridgeMonitor-OutboundAudit-Ingest')
foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskName $name
    if ($task.State -ne 'Disabled' -or $task.Settings.Enabled) { throw 'Both tasks must already be stopped and disabled' }
}
$running = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -eq 'bridge-outbound-reader.exe' -or
    ($_.Name -like '*python*' -and ($_.CommandLine -like '*bridge.audit_cli*collect*' -or $_.CommandLine -like '*-m bridge.audit_spool*'))
})
if ($running.Count) { throw 'An audit reader or consumer is running' }
$old = Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw | ConvertFrom-Json
if ((Get-FileHash -LiteralPath (Join-Path $root 'bridge-outbound-reader.exe')).Hash -ne $old.reader_sha256 -or
    (Get-FileHash -LiteralPath (Join-Path $root 'run-protected-reader.ps1')).Hash -ne $old.supervisor_sha256) { throw 'Installed files have drifted; inspect before updating' }
$inputs = @(
    @{source=$ReaderSource;name='bridge-outbound-reader.exe';hash=$ReaderSHA256},
    @{source=$SupervisorSource;name='run-protected-reader.ps1';hash=$SupervisorSHA256},
    @{source=$BuildEvidenceSource;name='build-evidence.json';hash=$BuildEvidenceSHA256}
)
foreach ($inputFile in $inputs) {
    $walk = Get-Item -LiteralPath $inputFile.source -Force
    while ($walk) {
        if ($walk.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Input reparse point rejected' }
        $walk = if ($walk -is [IO.DirectoryInfo]) { $walk.Parent } else { $walk.Directory }
    }
    if ((Get-FileHash -LiteralPath $inputFile.source).Hash -ne $inputFile.hash) { throw 'Pinned input hash mismatch' }
}
$build = Get-Content -LiteralPath $BuildEvidenceSource -Raw | ConvertFrom-Json
if (-not $build.reproducible -or $build.ran_reader -or @($build.builds).Count -ne 2 -or
    $build.builds[0].executable_sha256 -ne $ReaderSHA256 -or $build.builds[1].executable_sha256 -ne $ReaderSHA256 -or
    $build.source_git_blob -ne $build.previously_reviewed_source_git_blob) { throw 'Reproducible reviewed-source build evidence required' }
$project = 'C:\AI\bridge-monitor'
if ((Get-FileHash -LiteralPath (Join-Path $project 'scripts\outbound-audit\collector.cpp')).Hash -ne $build.source_sha256) { throw 'Native source differs from build evidence' }
$backup = Join-Path $root ('revisions\' + [Guid]::NewGuid().ToString('N'))
if (-not (Test-Path -LiteralPath (Join-Path $root 'revisions'))) { New-ProtectedDirectory (Join-Path $root 'revisions') $old.operator_sid }
New-ProtectedDirectory $backup $old.operator_sid
try {
    foreach ($name in @('installation.json', 'bridge-outbound-reader.exe', 'run-protected-reader.ps1', 'build-evidence.json')) {
        $existing = Join-Path $root $name
        if (Test-Path -LiteralPath $existing) { Copy-ProtectedFile $existing (Join-Path $backup $name) (Get-FileHash -LiteralPath $existing).Hash $old.operator_sid }
    }
    foreach ($inputFile in $inputs) {
        $temporary = Join-Path $root ($inputFile.name + '.new')
        if (Test-Path -LiteralPath $temporary) { throw 'Unreconciled update file; inspect previous failed update' }
        Copy-ProtectedFile $inputFile.source $temporary $inputFile.hash $old.operator_sid
        Move-Item -LiteralPath $temporary -Destination (Join-Path $root $inputFile.name) -Force
    }
    $scheduler = New-Object -ComObject 'Schedule.Service'; $scheduler.Connect(); $folder = $scheduler.GetFolder('\')
    foreach ($name in $names) {
        $folder.GetTask($name).SetSecurityDescriptor("O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;$($old.operator_sid))", 0)
    }
    $newReceipt = @{ installed=$true; activated=$false; at=(Get-Date -Format o); root=$root; project_root=$project
       operator_sid=$old.operator_sid; tasks=$names; reader_sha256=$ReaderSHA256
       supervisor_sha256=$SupervisorSHA256; build_evidence_sha256=$BuildEvidenceSHA256; previous_revision=$backup } |
        ConvertTo-Json -Depth 5
    Write-ProtectedText (Join-Path $root 'installation.json') $newReceipt $old.operator_sid
} finally {
    # Preserve partial updates as evidence; fail closed, with no implicit rollback/start.
    foreach ($name in $names) { Disable-ScheduledTask -TaskName $name | Out-Null }
    foreach ($item in @((Get-Item -LiteralPath $root)) + @(Get-ChildItem -LiteralPath $root -Recurse -Force)) {
        $acl = Get-Acl -LiteralPath $item.FullName; $acl.SetOwner($admin); Set-Acl -LiteralPath $item.FullName -AclObject $acl
    }
}
foreach ($name in $names) { if ((Get-ScheduledTask -TaskName $name).State -ne 'Disabled') { throw 'Task not disabled after update' } }
