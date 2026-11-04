# Independent administrator watchdog and immediate, run-scoped cleanup.
param([Parameter(Mandatory=$true)][string]$RunId,[Parameter(Mandatory=$true)][int]$TestPid,[switch]$WaitForController)
$ErrorActionPreference='Stop'
$root='C:\Program Files\BridgeMonitorAudit'
if($PSScriptRoot -notmatch '^C:\\Program Files\\BridgeMonitorAudit\.Setup-[a-f0-9]{32}$' -or $RunId -notmatch '^[a-f0-9]{32}$' -or $TestPid -le 0){throw 'Invalid protected cleanup scope'}
if(-not([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)){throw 'Administrator cleanup required'}
. (Join-Path $PSScriptRoot 'protected-file-utils.ps1')
$data=Join-Path $root ('verification\'+$RunId)
$receipt=Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw|ConvertFrom-Json
if($WaitForController){
    $deadline=[DateTime]::UtcNow.AddSeconds(60)
    while([DateTime]::UtcNow -lt $deadline -and -not(Test-Path -LiteralPath (Join-Path $data 'controller-result.json'))){Start-Sleep -Milliseconds 200}
}
$supervisors=@();$knownReaders=@()
foreach($phase in @('phase1.json','phase2.json')) {
    $marker=Join-Path $data $phase
    if(Test-Path -LiteralPath $marker){$m=Get-Content -LiteralPath $marker -Raw|ConvertFrom-Json;$supervisors+=@{pid=[int]$m.supervisor_pid;system_pid=[int]$m.system_pid};$knownReaders += [int]$m.reader_pid}
}
foreach($p in Get-CimInstance Win32_Process -Filter "Name='powershell.exe'") {
    if($p.ExecutablePath -eq 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -and $p.CommandLine -like ('*'+(Join-Path $root 'run-protected-reader.ps1')+'*') -and $p.CommandLine -match ('-VerificationRun\s+'+$RunId+'(?:\s|$)')) {
        if((Invoke-CimMethod -InputObject $p -MethodName GetOwnerSid).Sid -eq 'S-1-5-18'){$supervisors+=@{pid=[int]$p.ProcessId;system_pid=[int]$p.ParentProcessId}}
    }
}
foreach($sup in @($supervisors|Sort-Object pid -Unique)) {
    $p=Get-CimInstance Win32_Process -Filter "ProcessId=$($sup.pid)"
    if($p -and $p.ParentProcessId -eq $sup.system_pid -and $p.CommandLine -match ('-VerificationRun\s+'+$RunId+'(?:\s|$)')) {
        $process=Get-Process -Id $p.ProcessId -ErrorAction SilentlyContinue
        if($process -and $process.StartTime.ToUniversalTime().Ticks -eq $p.CreationDate.ToUniversalTime().Ticks){$process.Kill();$process.WaitForExit(5000)|Out-Null}
    }
    # Stop the supervisor before taking the child snapshot; it cannot spawn reader 3.
    foreach($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$($sup.pid)")) {
        if($child.ExecutablePath -eq (Join-Path $root 'bridge-outbound-reader.exe') -and $child.CommandLine -match ('--only-pid\s+'+$TestPid+'(?:\s|$)') -and (Invoke-CimMethod -InputObject $child -MethodName GetOwnerSid).Sid -eq 'S-1-5-18') {
            $knownReaders += [int]$child.ProcessId;$process=Get-Process -Id $child.ProcessId -ErrorAction SilentlyContinue
            if($process -and $process.StartTime.ToUniversalTime().Ticks -eq $child.CreationDate.ToUniversalTime().Ticks){$process.Kill();$process.WaitForExit(5000)|Out-Null}
        }
    }
}
foreach($readerPid in @($knownReaders|Where-Object {$_ -gt 0}|Sort-Object -Unique)) {
    if(-not(Get-Process -Id $readerPid -ErrorAction SilentlyContinue)){& 'C:\Windows\System32\logman.exe' stop "BridgeOutboundAudit-$readerPid" -ets *> $null}
}
$taskRemoved=$false
if($WaitForController){
    $scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
    $name='BridgeMonitor-AuditVerification-'+$RunId
    $tasks=@($folder.GetTasks(1)|Where-Object {$_.Name -eq $name})
    if($tasks.Count){
        $task=$tasks[0];$definition=$task.Definition
        if($definition.Principal.UserId -notin @('SYSTEM','S-1-5-18') -or $definition.Triggers.Count -ne 0 -or
           $definition.Actions.Count -ne 1 -or $definition.Actions.Item(1).Arguments -notlike ('*'+(Join-Path $data 'test-system-runtime.ps1')+'*')){throw 'Refusing to remove an unexpected task'}
        try{if($task.GetInstances(0).Count){$task.Stop(0)}}finally{$folder.DeleteTask($name,0)}
    }
    $taskRemoved=$true
}
$sessions=& 'C:\Windows\System32\logman.exe' query -ets 2>&1
$remaining=@($sessions|Where-Object {$_ -match '^BridgeOutboundAudit-\d+\s'})
$label=if($WaitForController){'watchdog-result.json'}else{'cleanup-result.json'}
Write-ProtectedText (Join-Path $data $label) (@{at=(Get-Date -Format o);run=$RunId;reader_pids=$knownReaders;supervisors=$supervisors;independent_deadline_seconds=60;temporary_task_absent=$taskRemoved;remaining_audit_sessions=$remaining}|ConvertTo-Json -Depth 5) $receipt.operator_sid
