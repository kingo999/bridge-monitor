# Installed administrator-owned; never launch this script from a writable checkout.
[CmdletBinding()]
param([string]$VerificationRun, [int]$OnlyPid=0)
$ErrorActionPreference = 'Stop'
$root = 'C:\Program Files\BridgeMonitorAudit'
if ($PSScriptRoot -ne $root) { throw 'Run only the protected installed supervisor' }
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.User.Value -ne 'S-1-5-18') { throw 'The producer requires its SYSTEM task identity' }
$system = [Environment]::GetFolderPath('System')
$env:PATH = $system
$env:PSModulePath = Join-Path $system 'WindowsPowerShell\v1.0\Modules'
$env:TEMP = Join-Path $root 'scratch'
$env:TMP = $env:TEMP
$env:BRIDGE_OUTBOUND_AUDIT_DB = $null
Set-Location -LiteralPath $root
$reader = Join-Path $root 'bridge-outbound-reader.exe'
$data = Join-Path $root 'data'
if ($VerificationRun -or $OnlyPid) {
    if ($VerificationRun -notmatch '^[a-f0-9]{32}$' -or $OnlyPid -le 0) { throw 'Invalid bounded verification scope' }
    $data = Join-Path $root ('verification\'+$VerificationRun)
    $directory = Get-Item -LiteralPath $data
    if ($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Verification reparse point rejected' }
    if ((Get-Acl -LiteralPath $data).GetOwner([Security.Principal.SecurityIdentifier]).Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted verification directory' }
}
$readerArguments = if ($VerificationRun) { @('--only-pid',[string]$OnlyPid) } else { @() }
$spool = Join-Path $data 'metadata.tsv'
$log = Join-Path $data 'supervisor.log'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
$verificationClock=[Diagnostics.Stopwatch]::StartNew()
while ($true) {
    if($VerificationRun -and $verificationClock.Elapsed.TotalSeconds -ge 55){break}
    # Match only this reader's fixed namespace, and only dead producer PIDs.
    $sessions = & (Join-Path $system 'logman.exe') query -ets 2>$null
    foreach ($line in $sessions) {
        $readerPid = 0
        if ($line -match '^BridgeOutboundAudit-([0-9]+)\s' -and
            [int]::TryParse($Matches[1], [ref]$readerPid) -and $readerPid -gt 0) {
            if (-not (Get-Process -Id $readerPid -ErrorAction SilentlyContinue)) {
                & (Join-Path $system 'logman.exe') stop "BridgeOutboundAudit-$readerPid" -ets *> $null
            }
        }
    }
    # SYSTEM writes protected metadata only. No Python, SQLite, repo imports or
    # user-writable configuration is read in this privileged process chain.
    $file = New-Object IO.FileStream($spool, [IO.FileMode]::OpenOrCreate, [IO.FileAccess]::ReadWrite, [IO.FileShare]::ReadWrite)
    $needsBoundary = $false
    if ($file.Length -gt 0) { $null=$file.Seek(-1,[IO.SeekOrigin]::End); $needsBoundary=($file.ReadByte() -ne 10) }
    $null=$file.Seek(0,[IO.SeekOrigin]::End)
    $writer = New-Object IO.StreamWriter($file, $utf8)
    $writer.AutoFlush = $true
    try {
        # Finish any torn line before the restart gap marker.
        if ($needsBoundary) { $writer.WriteLine('') }
        $writer.WriteLine('gap')
        $ErrorActionPreference = 'Continue'
        & $reader @readerArguments 2>> (Join-Path $data 'reader-errors.log') | ForEach-Object {
            if($VerificationRun -and $verificationClock.Elapsed.TotalSeconds -ge 55){throw 'Bounded verification deadline reached'}
            try { $writer.WriteLine([string]$_) } catch { throw }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
        $writer.Dispose()
        $file.Dispose()
    }
    [IO.File]::AppendAllText($log, ("{0:o} reader exited {1}; restart in 10 seconds`r`n" -f [DateTime]::UtcNow, $exitCode), $utf8)
    if($VerificationRun -and $verificationClock.Elapsed.TotalSeconds -ge 55){break}
    Start-Sleep -Seconds 10
}
