$ErrorActionPreference = 'Stop'
$project = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$backupScript = Join-Path $project 'scripts\backup-monitor.ps1'
$backupRoot = Join-Path $project 'var\backups'

Describe 'Backup access boundary' {
    It 'refuses to write under a backup subdirectory readable by other users' {
        $testDir = Join-Path $backupRoot ('acl-test-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $testDir | Out-Null
        try {
            & icacls.exe $testDir /grant '*S-1-5-11:(OI)(CI)R' | Out-Null
            if ($LASTEXITCODE -ne 0) { throw 'Could not prepare test ACL.' }

            $output = Join-Path $testDir 'should-not-exist.dump'
            $errorText = ''
            try {
                & $backupScript -OutputPath $output -DsnEnvName 'BRIDGE_MONITOR_TEST_MISSING' | Out-Null
            } catch { $errorText = $_.Exception.Message }

            $errorText | Should Match 'Backup directory access is not private'
            (Test-Path -LiteralPath $output) | Should Be $false
        } finally {
            Remove-Item -LiteralPath $testDir -Force -ErrorAction SilentlyContinue
        }
    }
}
