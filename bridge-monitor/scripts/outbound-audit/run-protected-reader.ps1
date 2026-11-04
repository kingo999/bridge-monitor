# Installed administrator-owned; never launch this script from a writable checkout.
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
$spool = Join-Path $root 'data\metadata.tsv'
$log = Join-Path $root 'data\supervisor.log'
$utf8 = New-Object Text.UTF8Encoding($false)
[Console]::OutputEncoding = $utf8
$OutputEncoding = $utf8
while ($true) {
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
    $file = New-Object IO.FileStream($spool, [IO.FileMode]::Append, [IO.FileAccess]::Write, [IO.FileShare]::ReadWrite)
    $writer = New-Object IO.StreamWriter($file, $utf8)
    $writer.AutoFlush = $true
    try {
        # Finish any torn line before the restart gap marker.
        $writer.WriteLine('')
        $writer.WriteLine('gap')
        $ErrorActionPreference = 'Continue'
        & $reader 2>> (Join-Path $root 'data\reader-errors.log') | ForEach-Object {
            try { $writer.WriteLine([string]$_) } catch { throw }
        }
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = 'Stop'
        $writer.Dispose()
        $file.Dispose()
    }
    [IO.File]::AppendAllText($log, ("{0:o} reader exited {1}; restart in 10 seconds`r`n" -f [DateTime]::UtcNow, $exitCode), $utf8)
    Start-Sleep -Seconds 10
}
