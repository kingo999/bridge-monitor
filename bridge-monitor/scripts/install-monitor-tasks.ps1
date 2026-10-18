param(
    [string]$ConfigPath = (Join-Path $PSScriptRoot '..\config\windows-tasks.example.json'),
    [switch]$Install
)
$ErrorActionPreference = 'Stop'
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
. (Join-Path $PSScriptRoot 'monitor-task-config.ps1')
$config = Get-MonitorTaskConfig $ConfigPath $project
$path = Resolve-ProjectChild $ConfigPath $project
$powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$definition = @(
    [pscustomobject]@{ name = $config.postgres_task; script = (Join-Path $PSScriptRoot 'run-postgres-task.ps1') },
    [pscustomobject]@{ name = $config.dashboard_task; script = (Join-Path $PSScriptRoot 'run-dashboard-task.ps1') }
)
foreach ($item in $definition) {
    $existing = Get-ScheduledTask -TaskName $item.name -ErrorAction SilentlyContinue
    if ($existing) { throw "Task already exists; inspect before replacing: $($item.name)" }
}
if (-not $Install) {
    $definition | Select-Object name,script
    return
}
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
foreach ($item in $definition) {
    $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -WindowStyle Hidden -File "' +
        $item.script + '" -ConfigPath "' + $path + '"'
    $action = New-ScheduledTaskAction -Execute $powershell -Argument $arguments -WorkingDirectory $project
    Register-ScheduledTask -TaskName $item.name -Action $action -Trigger $trigger `
        -Principal $principal -Settings $settings -Description 'Bridge Monitor project-local pilot service' | Out-Null
}
$definition | Select-Object name,script
