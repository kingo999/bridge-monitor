<#
.SYNOPSIS
START-01: start or reuse the local agent stack, then run model-free health checks.

.DESCRIPTION
1. Starts the installed Codex desktop app only if no process of that exact app executable
   is running (existing processes are never restarted).
2. Starts the OBS-01 dashboard (detached, no window) only if the port is free; reuses it
   when the actual OBS-01 dashboard already answers there; refuses any other occupant.
3. Runs stack_health.py: temporary MCP stdio clients probe the registered bridges
   (initialize + tools/list + two read-only Claude checks) and the Claude/Codex CLI
   sign-in status commands are run. No model call is ever made.

The MCP stdio bridges themselves stay owned by the Codex/Claude clients; nothing here
starts a detached stdio server. Nothing is installed, registered or stopped.

.PARAMETER Root
Trusted repository checkout. Defaults to the caller's current directory.
.PARAMETER HealthOnly
Launch nothing (no app, no dashboard); only report.
.PARAMETER SkipAppLaunch
Do not start the Codex app (use this from an IntelliJ terminal); still start/reuse the
dashboard and run the checks.
.PARAMETER DashboardPort
Loopback port for the dashboard (default 8089).
.PARAMETER PythonPath
Python 3.11+ to use. Default: the interpreter of the claude-implementer registration.
.PARAMETER AppExecutable
Explicit app executable instead of the installed OpenAI.Codex package.
.PARAMETER ConfigPath
Codex config to read (default <checkout>\.codex\config.toml). Never written.
.PARAMETER StateDir
Bridge state directory handed to the dashboard (default: its own default).
.PARAMETER TimeoutSeconds
Seconds allowed for each distinct bridge launch probe.
.PARAMETER HelperWaitSeconds
Overall bound on the health helper (default TimeoutSeconds + 60). On expiry the helper's
whole process tree is stopped, which also reaps its temporary probe trees.
.PARAMETER SkipCliAuth
Do not run the CLI sign-in status commands (isolated fixture runs only; normal startup
checks sign-in).

.EXAMPLE
powershell -NoProfile -File C:\AI\gpt-claude-mcp-bridge-v2\tools\agent-startup\start-agent-stack.ps1
#>
[CmdletBinding()]
param(
    [string]$Root = (Get-Location).Path,
    [switch]$HealthOnly,
    [switch]$SkipAppLaunch,
    [ValidateRange(1, 65535)][int]$DashboardPort = 8089,
    [string]$PythonPath,
    [string]$AppExecutable,
    [string]$ConfigPath,
    [string]$StateDir,
    [ValidateRange(5, 300)][int]$TimeoutSeconds = 45,
    [ValidateRange(0, 900)][int]$HelperWaitSeconds = 0,
    [switch]$SkipCliAuth
)

Set-StrictMode -Version 1.0
$ErrorActionPreference = 'Stop'

$Here = $PSScriptRoot
$Root = (Resolve-Path -LiteralPath $Root).Path
$Helper = Join-Path $Here 'stack_health.py'
$DashboardScript = Join-Path $Here '..\agent-observability\dashboard.py'
$Runtime = Join-Path $Here '.runtime'
if (-not $ConfigPath) { $ConfigPath = Join-Path $Root '.codex\config.toml' }
# Relative paths mean relative to the caller's directory, not the checkout.
$ConfigPath = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($ConfigPath)
if ($StateDir) { $StateDir = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($StateDir) }

if ($HealthOnly) { $Mode = 'health_only' }
elseif ($SkipAppLaunch) { $Mode = 'skip_app_launch' }
else { $Mode = 'startup' }
$LaunchApp = -not ($HealthOnly -or $SkipAppLaunch)
$LaunchDashboard = -not $HealthOnly
if ($HelperWaitSeconds -gt 0) { $HealthWait = $HelperWaitSeconds } else { $HealthWait = $TimeoutSeconds + 60 }
$MaxOutputBytes = 1048576


function ConvertTo-ArgText([string]$Text) {
    if ($Text -match '[\s"]') { return '"' + ($Text -replace '"', '\"') + '"' }
    return $Text
}

function Get-RegisteredCommand([string]$Config, [string]$Name) {
    if (-not (Test-Path -LiteralPath $Config -PathType Leaf)) { return $null }
    $inSection = $false
    foreach ($line in (Get-Content -LiteralPath $Config -TotalCount 400)) {
        if ($line -match '^\s*\[(.+?)\]\s*$') {
            $inSection = ($Matches[1].Trim().Trim('"') -eq "mcp_servers.$Name")
            continue
        }
        if ($inSection -and $line -match "^\s*command\s*=\s*'([^']+)'") { return $Matches[1] }
        if ($inSection -and $line -match '^\s*command\s*=\s*"((?:[^"\\]|\\.)*)"') {
            return ($Matches[1] -replace '\\\\', '\')
        }
    }
    return $null
}

function Resolve-Python {
    if ($PythonPath) {
        if (Test-Path -LiteralPath $PythonPath -PathType Leaf) { return (Resolve-Path -LiteralPath $PythonPath).Path }
        return $null
    }
    $candidates = @()
    foreach ($name in 'claude-implementer', 'gpt-architect-v2', 'gpt-architect-v1') {
        $command = Get-RegisteredCommand $ConfigPath $name
        if ($command) { $candidates += $command }
    }
    $onPath = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($onPath) { $candidates += $onPath.Source }
    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate }
    }
    return $null
}

# Stops a process this script itself started, and its descendants. Used only on our own
# helper after its deadline; never on pre-existing processes.
function Stop-OwnedTree([int]$ProcessId) {
    try { & taskkill.exe /PID $ProcessId /T /F 2>&1 | Out-Null } catch { }
}

function Read-Bounded([string]$Path) {
    try {
        $stream = [System.IO.File]::Open($Path, [System.IO.FileMode]::Open,
            [System.IO.FileAccess]::Read, [System.IO.FileShare]::ReadWrite)
        try {
            $buffer = New-Object byte[] $MaxOutputBytes
            $total = 0
            while ($total -lt $buffer.Length) {
                $count = $stream.Read($buffer, $total, $buffer.Length - $total)
                if ($count -le 0) { break }
                $total += $count
            }
            return [System.Text.Encoding]::UTF8.GetString($buffer, 0, $total)
        } finally { $stream.Dispose() }
    } catch { return '' }
}

# Runs stack_health.py hidden, with stdout/stderr going to files (never read unbounded), a
# hard deadline, and tree cleanup of the helper on expiry.
function Invoke-Helper([string[]]$HelperArgs, [int]$WaitSeconds) {
    $outFile = Join-Path $Runtime ('helper-' + [System.IO.Path]::GetRandomFileName() + '.out')
    $errFile = $outFile + '.err'
    $argText = ((@('-B', '-I', $Helper) + $HelperArgs) | ForEach-Object { ConvertTo-ArgText $_ }) -join ' '
    try {
        $proc = Start-Process -FilePath $script:Python -ArgumentList $argText -WorkingDirectory $Root `
            -WindowStyle Hidden -PassThru -RedirectStandardOutput $outFile -RedirectStandardError $errFile
        $null = $proc.Handle   # cache the handle so ExitCode is readable after exit
        if (-not $proc.WaitForExit($WaitSeconds * 1000)) {
            Stop-OwnedTree $proc.Id
            $null = $proc.WaitForExit(15000)
            return @{ ExitCode = 124; Output = '' }
        }
        return @{ ExitCode = $proc.ExitCode; Output = (Read-Bounded $outFile) }
    } catch {
        return @{ ExitCode = 125; Output = '' }
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

# ---- Codex app identity (installed package manifest, exact executable) -------------

function Get-CodexAppIdentity {
    if ($AppExecutable) {
        if (Test-Path -LiteralPath $AppExecutable -PathType Leaf) {
            return @{ Status = 'ok'; Path = (Resolve-Path -LiteralPath $AppExecutable).Path; Aumid = $null }
        }
        return @{ Status = 'override_missing'; Path = $null; Aumid = $null }
    }
    $package = $null
    try {
        $package = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction Stop |
            Sort-Object Version -Descending | Select-Object -First 1
    } catch { }
    if (-not $package) { return @{ Status = 'not_installed'; Path = $null; Aumid = $null } }
    try {
        $manifest = Get-AppxPackageManifest -Package $package -ErrorAction Stop
        foreach ($application in @($manifest.Package.Applications.Application)) {
            $relative = ([string]$application.Executable) -replace '/', '\'
            if ($relative -ieq 'app\ChatGPT.exe') {
                return @{
                    Status = 'ok'
                    Path = (Join-Path $package.InstallLocation $relative)
                    Aumid = ('{0}!{1}' -f $package.PackageFamilyName, $application.Id)
                }
            }
        }
    } catch { }
    return @{ Status = 'manifest_unrecognized'; Path = $null; Aumid = $null }
}

function Get-RunningApp([string]$ExePath) {
    $leaf = (Split-Path -Leaf $ExePath) -replace "'", "''"
    $found = Get-CimInstance -ClassName Win32_Process -Filter "Name = '$leaf'" -ErrorAction Stop
    return @($found | Where-Object { $_.ExecutablePath -and ($_.ExecutablePath -ieq $ExePath) })
}

function Invoke-AppStep {
    $identity = Get-CodexAppIdentity
    if ($identity.Status -ne 'ok') {
        if ($LaunchApp) { return $identity.Status }
        return 'unknown'
    }
    try { $running = @(Get-RunningApp $identity.Path) }
    catch { if ($LaunchApp) { return 'detection_failed' } else { return 'unknown' } }
    if ($running.Count -gt 0) {
        if ($LaunchApp) { return 'reused' } else { return 'running' }
    }
    if (-not $LaunchApp) { return 'not_running' }
    try {
        if ($identity.Aumid) {
            Start-Process -FilePath 'explorer.exe' -ArgumentList ('shell:AppsFolder\' + $identity.Aumid) -WindowStyle Hidden
        } else {
            Start-Process -FilePath $identity.Path
        }
    } catch { return 'launch_failed' }
    $deadline = (Get-Date).AddSeconds(25)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 1
        try { if (@(Get-RunningApp $identity.Path).Count -gt 0) { return 'started' } } catch { }
    }
    return 'launch_unconfirmed'
}

# ---- dashboard (reuse the identified OBS-01 or start it detached) -----------------

# The helper spawns dashboard.py itself with stdin/stdout from DEVNULL, a plain log file
# for stderr, no inherited handles and no console window. Starting it with Start-Process
# would hand it this script's redirected handles and pin any caller capturing our output.
function Invoke-DashboardStep {
    $startArgs = @('dashboard-start', '--port', "$DashboardPort", '--script', $DashboardScript,
        '--root', $Root, '--runtime-dir', $Runtime, '--wait', '20')
    if ($StateDir) { $startArgs += @('--state-dir', $StateDir) }
    $result = Invoke-Helper $startArgs 120
    try {
        $parsed = $result.Output | ConvertFrom-Json
        $pidValue = 0
        if ($parsed.pid) { $pidValue = [int]$parsed.pid }
        return @{ Action = [string]$parsed.action; Pid = $pidValue }
    } catch {
        return @{ Action = 'start_failed'; Pid = 0 }
    }
}

# ---- main ---------------------------------------------------------------------------

$script:Python = Resolve-Python
if (-not $script:Python) {
    Write-Output 'START-01: no usable Python 3.11+ was found (use -PythonPath). Nothing was started.'
    exit 2
}
if (-not (Test-Path -LiteralPath $Helper -PathType Leaf)) {
    Write-Output 'START-01: stack_health.py is missing. Nothing was started.'
    exit 2
}
New-Item -ItemType Directory -Force -Path $Runtime | Out-Null

$appStatus = 'not_checked'
try { $appStatus = Invoke-AppStep } catch { $appStatus = if ($LaunchApp) { 'detection_failed' } else { 'unknown' } }
if (@('started', 'reused', 'running') -contains $appStatus) { $checkout = 'manual_action_required' }
else { $checkout = 'not_applicable' }

$dashAction = 'no_launch_requested'
$dashPid = 0
if ($LaunchDashboard) {
    $step = Invoke-DashboardStep
    $dashAction = $step.Action
    $dashPid = $step.Pid
}

$healthArgs = @('health', '--root', $Root, '--config', $ConfigPath, '--timeout', "$TimeoutSeconds",
    '--dashboard-port', "$DashboardPort", '--mode', $Mode, '--app-status', $appStatus,
    '--app-checkout', $checkout, '--dashboard-action', $dashAction,
    '--report-file', (Join-Path $Runtime 'last-report.json'), '--format', 'both')
if ($dashPid -gt 0) { $healthArgs += @('--dashboard-pid', "$dashPid") }
if ($SkipCliAuth) { $healthArgs += @('--cli-auth', 'skip') }

$result = Invoke-Helper $healthArgs $HealthWait
if (@(0, 1, 2) -notcontains $result.ExitCode -or -not $result.Output) {
    Write-Output ("START-01: the health helper did not complete (exit {0}); its process tree was stopped. No readiness is claimed." -f $result.ExitCode)
    exit 2
}
Write-Output $result.Output
exit $result.ExitCode
