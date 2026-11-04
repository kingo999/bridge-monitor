# One-shot SYSTEM verification. Launched only by a temporary protected task.
param([Parameter(Mandatory=$true)][string]$RunId,[Parameter(Mandatory=$true)][int]$TestPid,[switch]$WatchdogDrill)
$ErrorActionPreference='Stop'
$root='C:\Program Files\BridgeMonitorAudit'
if ($RunId -notmatch '^[a-f0-9]{32}$' -or $TestPid -le 0) {throw 'Invalid test scope'}
$data=Join-Path $root ('verification\'+$RunId)
if ($PSScriptRoot -ne $data -or [Security.Principal.WindowsIdentity]::GetCurrent().User.Value -ne 'S-1-5-18') {throw 'Protected SYSTEM verification required'}
. (Join-Path $data 'protected-file-utils.ps1')
$receipt=Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw|ConvertFrom-Json
$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
foreach($name in @('BridgeMonitor-OutboundAudit','BridgeMonitor-OutboundAudit-Ingest')) {
    $task=$folder.GetTask($name)
    if ($task.Enabled -or $task.GetInstances(0).Count) {throw 'Production audit task active'}
}
$sup=$null;$children=@();$errorText=$null;$forced=$false
function Wait-Reader([int]$Exclude) {
    $deadline=[DateTime]::UtcNow.AddSeconds(28)
    while ([DateTime]::UtcNow -lt $deadline) {
        $found=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($sup.Id)"|Where-Object {
            $_.Name -eq 'bridge-outbound-reader.exe' -and $_.ExecutablePath -eq (Join-Path $root 'bridge-outbound-reader.exe') -and $_.ProcessId -ne $Exclude
        })
        if($found.Count -eq 1){return $found[0]}
        if($sup.HasExited){throw 'Supervisor exited'}
        Start-Sleep -Milliseconds 200
    }
    throw 'Reader/restart deadline exceeded'
}
try {
    $sup=Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','RemoteSigned','-File',('"'+(Join-Path $root 'run-protected-reader.ps1')+'"'),'-VerificationRun',$RunId,'-OnlyPid',$TestPid) -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $first=Wait-Reader 0;$children+=@{pid=[int]$first.ProcessId;birth=$first.CreationDate.ToString('o')}
    Write-ProtectedText (Join-Path $data 'phase1.json') (@{reader_pid=[int]$first.ProcessId;supervisor_pid=$sup.Id;system_pid=$PID;system_sid='S-1-5-18'}|ConvertTo-Json) $receipt.operator_sid
    if($WatchdogDrill){Start-Sleep -Seconds 120}
    Start-Sleep -Seconds 12
    $current=Get-CimInstance Win32_Process -Filter "ProcessId=$($first.ProcessId)"
    if($current -and $current.ParentProcessId -eq $sup.Id -and $current.CreationDate -eq $first.CreationDate) {
        Stop-Process -Id $first.ProcessId -Force;$forced=$true
    } else {throw 'Initial reader exited before forced-crash check'}
    $second=Wait-Reader $first.ProcessId;$children+=@{pid=[int]$second.ProcessId;birth=$second.CreationDate.ToString('o')}
    Write-ProtectedText (Join-Path $data 'phase2.json') (@{reader_pid=[int]$second.ProcessId;supervisor_pid=$sup.Id;system_pid=$PID;restarted=$true;system_sid='S-1-5-18'}|ConvertTo-Json) $receipt.operator_sid
    Start-Sleep -Seconds 10
} catch {$errorText=$_.Exception.Message}
finally {
    if($sup) {
        if(-not $sup.HasExited){$sup.Kill();$sup.WaitForExit(5000)|Out-Null}
        $native=@(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($sup.Id)"|Where-Object {$_.ExecutablePath -eq (Join-Path $root 'bridge-outbound-reader.exe')})
        foreach($item in $native){$children+=@{pid=[int]$item.ProcessId;birth=$item.CreationDate.ToString('o')}}
        foreach($item in $native){Stop-Process -Id $item.ProcessId -Force -ErrorAction SilentlyContinue}
    }
    foreach($child in $children) {
        if(-not (Get-Process -Id $child.pid -ErrorAction SilentlyContinue)) {
            & 'C:\Windows\System32\logman.exe' stop "BridgeOutboundAudit-$($child.pid)" -ets *> $null
        }
    }
    Write-ProtectedText (Join-Path $data 'system-result.json') (@{at=(Get-Date -Format o);system_sid='S-1-5-18';forced_reader_crash=$forced;reader_generations=$children;error=$errorText;production_tasks_started=$false}|ConvertTo-Json -Depth 5) $receipt.operator_sid
}
if($errorText){exit 1}
