param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\router-shadow-task.example.json'),
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'router-shadow-task-config.ps1')
$config = Get-RouterShadowTaskConfig $ConfigPath $project
$configCandidate = if ([IO.Path]::IsPathRooted($ConfigPath)) {
    $ConfigPath
} else { Join-Path $project $ConfigPath }
$configFile = Resolve-ProjectChild $configCandidate $project
$script = Join-Path $PSScriptRoot 'run-router-shadow-task.ps1'
if (-not $Install) {
    [pscustomobject]@{ name = $config.task_name; script = $script }
    return
}
if (Get-ScheduledTask -TaskName $config.task_name -ErrorAction SilentlyContinue) {
    throw "Task already exists; inspect before replacing: $($config.task_name)"
}
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
$arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
    $script + '" -ConfigPath "' + $configFile + '"'
$action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $project
Register-ScheduledTask -TaskName $config.task_name -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description 'Bridge Monitor shadow Qwen pilot worker' | Out-Null
[pscustomobject]@{ name = $config.task_name; script = $script }
