$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
. (Join-Path $project 'scripts\monitor-task-config.ps1')

Describe 'Windows service task configuration' {
    It 'resolves the checked-in config to project-local data and secret files' {
        $config = Get-MonitorTaskConfig (Join-Path $project 'config\windows-tasks.example.json') $project
        $config.data_dir | Should Be (Join-Path $project 'var\postgres')
        $config.dashboard_secrets | Should Be (Join-Path $project 'var\secrets\dashboard.json')
        $config.postgres_port | Should Be 55481
    }

    It 'rejects a data directory outside the project' {
        $source = Get-Content (Join-Path $project 'config\windows-tasks.example.json') -Raw | ConvertFrom-Json
        $source.data_dir = '..\elsewhere'
        $path = Join-Path $project ('var\run\test-task-' + [guid]::NewGuid().ToString('N') + '.json')
        try {
            $source | ConvertTo-Json | Set-Content -LiteralPath $path
            $errorText = ''
            try { Get-MonitorTaskConfig $path $project | Out-Null } catch { $errorText = $_.Exception.Message }
            $errorText | Should Match 'leaves bridge-monitor'
        } finally { Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue }
    }

    It 'rejects non-loopback PostgreSQL binding' {
        $source = Get-Content (Join-Path $project 'config\windows-tasks.example.json') -Raw | ConvertFrom-Json
        $source.postgres_host = '0.0.0.0'
        $path = Join-Path $project ('var\run\test-task-' + [guid]::NewGuid().ToString('N') + '.json')
        try {
            $source | ConvertTo-Json | Set-Content -LiteralPath $path
            $errorText = ''
            try { Get-MonitorTaskConfig $path $project | Out-Null } catch { $errorText = $_.Exception.Message }
            $errorText | Should Match 'loopback'
        } finally { Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue }
    }

    It 'rejects a malformed database probe role' {
        $source = Get-Content (Join-Path $project 'config\windows-tasks.example.json') -Raw | ConvertFrom-Json
        $source.postgres_probe_user = 'role with spaces'
        $path = Join-Path $project ('var\run\test-task-' + [guid]::NewGuid().ToString('N') + '.json')
        try {
            $source | ConvertTo-Json | Set-Content -LiteralPath $path
            $errorText = ''
            try { Get-MonitorTaskConfig $path $project | Out-Null } catch { $errorText = $_.Exception.Message }
            $errorText | Should Match 'probe role'
        } finally { Remove-Item -LiteralPath $path -ErrorAction SilentlyContinue }
    }
}
