# Protected administrator controller: one temporary, no-trigger, time-limited task.
param([Parameter(Mandatory=$true)][string]$RunId,[Parameter(Mandatory=$true)][int]$TestPid,[switch]$WatchdogDrill)
$ErrorActionPreference='Stop'
$root='C:\Program Files\BridgeMonitorAudit'
if($PSScriptRoot -notmatch '^C:\\Program Files\\BridgeMonitorAudit\.Setup-[a-f0-9]{32}$' -or $RunId -notmatch '^[a-f0-9]{32}$' -or $TestPid -le 0){throw 'Invalid protected test controller scope'}
. (Join-Path $PSScriptRoot 'protected-file-utils.ps1')
$receipt=Get-Content -LiteralPath (Join-Path $root 'installation.json') -Raw|ConvertFrom-Json
$scheduler=New-Object -ComObject Schedule.Service;$scheduler.Connect();$folder=$scheduler.GetFolder('\')
function Assert-ProductionOff {
    foreach($name in @('BridgeMonitor-OutboundAudit','BridgeMonitor-OutboundAudit-Ingest')) {
        $task=$folder.GetTask($name)
        if($task.Enabled -or $task.GetInstances(0).Count){throw 'Production task must remain stopped and disabled'}
    }
}
Assert-ProductionOff
$verification=Join-Path $root 'verification'
if(-not(Test-Path -LiteralPath $verification)){New-ProtectedDirectory $verification $receipt.operator_sid}
$data=Join-Path $verification $RunId;New-ProtectedDirectory $data $receipt.operator_sid
foreach($file in @('test-system-runtime.ps1','protected-file-utils.ps1')) {
    $source=Join-Path $PSScriptRoot $file
    Copy-ProtectedFile $source (Join-Path $data $file) (Get-FileHash -LiteralPath $source).Hash $receipt.operator_sid
}
$taskName='BridgeMonitor-AuditVerification-'+$RunId
$registered=$false;$failure=$null
$watchdog=Start-Process -FilePath 'C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe' -ArgumentList @('-NoProfile','-NonInteractive','-ExecutionPolicy','RemoteSigned','-File',('"'+(Join-Path $PSScriptRoot 'cleanup-runtime-test.ps1')+'"'),'-RunId',$RunId,'-TestPid',$TestPid,'-WaitForController') -WorkingDirectory $root -WindowStyle Hidden -PassThru
try {
    $definition=$scheduler.NewTask(0)
    $definition.RegistrationInfo.Description='Bounded audit verification only; no triggers; removed by protected controller'
    $definition.Principal.UserId='SYSTEM';$definition.Principal.LogonType=5;$definition.Principal.RunLevel=1
    $definition.Settings.Enabled=$true;$definition.Settings.ExecutionTimeLimit='PT90S';$definition.Settings.MultipleInstances=2
    $definition.Settings.DisallowStartIfOnBatteries=$false;$definition.Settings.StopIfGoingOnBatteries=$false
    $action=$definition.Actions.Create(0);$action.Path='C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe'
    $action.WorkingDirectory=$root
    $action.Arguments='-NoProfile -NonInteractive -ExecutionPolicy RemoteSigned -File "'+(Join-Path $data 'test-system-runtime.ps1')+'" -RunId '+$RunId+' -TestPid '+$TestPid
    if($WatchdogDrill){$action.Arguments+=' -WatchdogDrill'}
    $sddl="O:BAG:BAD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GR;;;$($receipt.operator_sid))"
    # Security descriptor is supplied AT REGISTRATION; no user-owner/DACL window.
    $task=$folder.RegisterTaskDefinition($taskName,$definition,2,'SYSTEM',$null,5,$sddl);$registered=$true
    $security=New-Object Security.AccessControl.RawSecurityDescriptor($task.GetSecurityDescriptor(7))
    if($security.Owner.Value -ne 'S-1-5-32-544'){throw 'Scheduler did not honor protected ownership'}
    Write-ProtectedText (Join-Path $data 'temporary-task.json') (@{name=$taskName;xml=$task.Xml;sddl=$task.GetSecurityDescriptor(7)}|ConvertTo-Json) $receipt.operator_sid
    $null=$task.Run($null)
    if($WatchdogDrill){
        $ready=[DateTime]::UtcNow.AddSeconds(20)
        while([DateTime]::UtcNow -lt $ready -and -not(Test-Path -LiteralPath (Join-Path $data 'phase1.json'))){Start-Sleep -Milliseconds 200}
        if(-not(Test-Path -LiteralPath (Join-Path $data 'phase1.json'))){throw 'Drill never reached live state'}
        # Intentional process termination bypasses this controller's finally.
        [Environment]::Exit(91)
    }
    $deadline=[DateTime]::UtcNow.AddSeconds(75)
    while([DateTime]::UtcNow -lt $deadline -and -not(Test-Path -LiteralPath (Join-Path $data 'system-result.json'))){Start-Sleep -Milliseconds 250}
    if(-not(Test-Path -LiteralPath (Join-Path $data 'system-result.json'))){throw 'System test deadline exceeded'}
    $result=Get-Content -LiteralPath (Join-Path $data 'system-result.json') -Raw|ConvertFrom-Json
    if($result.error -or -not $result.forced_reader_crash){throw 'System test did not complete its crash/restart check'}
} catch {$failure=$_.Exception.Message}
finally {
    if($registered){
        try { $existing=$folder.GetTask($taskName);if($existing.GetInstances(0).Count){$existing.Stop(0)} }
        finally { $folder.DeleteTask($taskName,0) }
    }
    & (Join-Path $PSScriptRoot 'cleanup-runtime-test.ps1') -RunId $RunId -TestPid $TestPid
    Assert-ProductionOff
    $sessions=& 'C:\Windows\System32\logman.exe' query -ets 2>&1
    $exit=$LASTEXITCODE;$audit=@($sessions|Where-Object {$_ -match '^BridgeOutboundAudit-\d+\s'})
    Write-ProtectedText (Join-Path $data 'controller-result.json') (@{at=(Get-Date -Format o);error=$failure;temporary_task_removed=$registered;production_tasks_started=$false;logman_exit=$exit;audit_sessions=$audit}|ConvertTo-Json -Depth 4) $receipt.operator_sid
    $watchdog.WaitForExit(5000)|Out-Null
}
if($failure){throw $failure}
