# This complete text is supplied in the UAC-approved encoded command, never
# executed elevated by its mutable checkout path. Payload is base64 JSON data.
param([Parameter(Mandatory=$true)][string]$PayloadBase64)
$ErrorActionPreference = 'Stop'
$env:PSModulePath = Join-Path ([Environment]::GetFolderPath('System')) 'WindowsPowerShell\v1.0\Modules'
$payload = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($PayloadBase64)) | ConvertFrom-Json
if ($payload.setup -notmatch '^C:\\Program Files\\BridgeMonitorAudit\.Setup-[a-f0-9]{32}$') { throw 'Invalid staging location' }
if ($payload.operator_sid -notmatch '^S-1-5-21-\d+-\d+-\d+-\d+$') { throw 'Invalid operator SID' }
$admin = New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole($admin)) { throw 'Administrator token required' }
$parent = Get-Item -LiteralPath 'C:\Program Files'
while ($parent) {
    if ($parent.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Staging parent reparse point' }
    $parent = $parent.Parent
}
if (Test-Path -LiteralPath $payload.setup) { throw 'Staging already exists' }
$acl = New-Object Security.AccessControl.DirectorySecurity
$acl.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;$($payload.operator_sid))")
(New-Object IO.DirectoryInfo($payload.setup)).Create($acl)
$ok = $false; $errorText = $null
function Copy-PinnedManagementFile([string]$Source, [string]$Destination, [string]$Hash) {
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$($payload.operator_sid))")
    $output = New-Object IO.FileStream($Destination, [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::FullControl,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $security)
    try {
        $inputStream = New-Object IO.FileStream($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
        try { $inputStream.CopyTo($output); $output.Flush($true) } finally { $inputStream.Dispose() }
    } finally { $output.Dispose() }
    if ((Get-FileHash -LiteralPath $Destination).Hash -ne $Hash) { throw 'Protected management script hash mismatch' }
}
try {
    # Only these fixed script roles can execute. Each copied byte stream is pinned.
    $roles=@('update-protected-reader.ps1','inventory-protected-audit.ps1','verify-protected-reader.ps1','protected-file-utils.ps1')
    if($payload.runtime_run) {
        if($payload.runtime_run -notmatch '^[a-f0-9]{32}$' -or $payload.test_pid -le 0){throw 'Invalid runtime scope'}
        $roles+=@('run-runtime-tests.ps1','test-system-runtime.ps1','cleanup-runtime-test.ps1')
    }
    foreach ($name in $roles) {
        $source = @($payload.scripts | Where-Object { $_.name -eq $name })
        if ($source.Count -ne 1) { throw 'Missing or ambiguous pinned management script' }
        $destination = Join-Path $payload.setup $name
        Copy-PinnedManagementFile $source[0].source $destination $source[0].sha256
    }
    . (Join-Path $payload.setup 'protected-file-utils.ps1')
    & (Join-Path $payload.setup 'update-protected-reader.ps1') -ReaderSource $payload.reader -SupervisorSource $payload.supervisor -BuildEvidenceSource $payload.build -ReaderSHA256 $payload.reader_sha256 -SupervisorSHA256 $payload.supervisor_sha256 -BuildEvidenceSHA256 $payload.build_sha256
    $guardRejected = $false
    try { & (Join-Path $payload.setup 'verify-protected-reader.ps1') -ReportPath 'C:\Program Files\BridgeMonitorAudit\must-not-be-created.json' }
    catch { if ($_.Exception.Message -like '*non-elevated*') { $guardRejected = $true } else { throw } }
    if (-not $guardRejected -or (Test-Path -LiteralPath 'C:\Program Files\BridgeMonitorAudit\must-not-be-created.json')) { throw 'Verifier elevation guard failed' }
    & (Join-Path $payload.setup 'inventory-protected-audit.ps1') -StagingDirectory $payload.setup -VerifierElevationRejected $guardRejected
    if($payload.runtime_run) {
        & (Join-Path $payload.setup 'run-runtime-tests.ps1') -RunId $payload.runtime_run -TestPid $payload.test_pid -WatchdogDrill:([bool]$payload.watchdog_drill)
        & (Join-Path $payload.setup 'inventory-protected-audit.ps1') -StagingDirectory $payload.setup -VerifierElevationRejected $guardRejected
    }
    $ok = $true
} catch { $errorText = $_.Exception.Message }
$result = @{ installed=$ok; activated=$false; at=(Get-Date -Format o); error=$errorText; verifier_elevation_rejected=$guardRejected } | ConvertTo-Json
if (Get-Command Write-ProtectedText -ErrorAction SilentlyContinue) {
    Write-ProtectedText (Join-Path $payload.setup 'result.json') $result $payload.operator_sid
} else { throw $errorText }
if (-not $ok) { exit 1 }
