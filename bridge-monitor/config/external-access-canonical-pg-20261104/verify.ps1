[CmdletBinding()]
param(
    [string]$PostgresBin = 'C:\Program Files\PostgreSQL\18\bin',
    [ValidateRange(1024, 65535)]
    [int]$Port = 25432
)

$ErrorActionPreference = 'Stop'
if ($Port -eq 5432) { throw 'Use a separate test port; the default PostgreSQL port is not allowed.' }
function Invoke-LoggedCommand {
    param([string]$Executable, [string[]]$Arguments, [string]$LogPath)
    # Windows PowerShell wraps native stderr as error records even for successful commands.
    $ErrorActionPreference = 'Continue'
    & $Executable @Arguments *> $LogPath
    return $LASTEXITCODE
}
$repositoryRoot = (Resolve-Path -LiteralPath 'D:/IdeaProjects/mri/mri-java-services-3').Path
$postgresTools = @{}
foreach ($toolName in @('initdb', 'pg_ctl', 'createdb')) {
    $toolPath = Join-Path $PostgresBin "$toolName.exe"
    if (-not (Test-Path -LiteralPath $toolPath -PathType Leaf)) {
        throw "Required PostgreSQL executable is missing: $toolName"
    }
    $postgresTools[$toolName] = $toolPath
}
$mavenCommand = 'C:\ProgramData\chocolatey\lib\maven\apache-maven-3.9.12\bin\mvn.cmd'

# Never reuse the developer's running database or credentials.
$portProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, $Port)
try { $portProbe.Start() } finally { $portProbe.Stop() }
$runDirectory = Join-Path $repositoryRoot ('target\consolidation-postgres-' + [Guid]::NewGuid().ToString('N'))
$null = New-Item -ItemType Directory -Path $runDirectory
$dataDirectory = Join-Path $runDirectory 'data'
$passwordFile = Join-Path $runDirectory 'init-password.txt'
$buildLog = Join-Path $runDirectory 'maven.log'
$serverLog = Join-Path $runDirectory 'postgres.log'
$resultFile = Join-Path $runDirectory 'result.txt'
$databaseName = 'mri_consolidation_e2e'
$databaseUser = 'mri_e2e_owner'
$randomBytes = New-Object byte[] 32
$randomGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
try { $randomGenerator.GetBytes($randomBytes) } finally { $randomGenerator.Dispose() }
$databasePassword = [Convert]::ToBase64String($randomBytes)
[System.IO.File]::WriteAllText($passwordFile, $databasePassword, [System.Text.UTF8Encoding]::new($false))

$environmentNames = @('PGPASSWORD', 'PGCONNECT_TIMEOUT', 'PGHOSTADDR', 'PGSERVICE',
    'PGSERVICEFILE', 'PGOPTIONS', 'PGSSLMODE', 'PGREQUIRESSL', 'PGCHANNELBINDING', 'PGGSSENCMODE',
    'MRI_E2E_POSTGRES_URL', 'MRI_E2E_POSTGRES_USER', 'MRI_E2E_POSTGRES_PASSWORD')
$previousEnvironment = @{}
foreach ($name in $environmentNames) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
$clusterStarted = $false
$checkExitCode = 1
Set-Content -LiteralPath $resultFile -Value 'running'

try {
    foreach ($name in @('PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS', 'PGSSLMODE',
            'PGREQUIRESSL', 'PGCHANNELBINDING', 'PGGSSENCMODE')) {
        Remove-Item -LiteralPath ("Env:" + $name) -ErrorAction SilentlyContinue
    }
    Push-Location -LiteralPath $repositoryRoot
    try {
        # Logs and generated data remain under ignored target/. Never print credential values.
        $nativeExit = Invoke-LoggedCommand -Executable $postgresTools['initdb'] -Arguments @(
            '-D', $dataDirectory, '-U', $databaseUser, '--encoding=UTF8', '--locale=C',
            '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', "--pwfile=$passwordFile"
        ) -LogPath (Join-Path $runDirectory 'initdb.log')
        if ($nativeExit -ne 0) { throw 'Disposable PostgreSQL cluster initialization failed; inspect initdb.log.' }
        Remove-Item -LiteralPath $passwordFile

        # Mark cleanup necessary before startup, including a start that times out after spawning.
        $clusterStarted = $true
        # Wait only for pg_ctl, not its detached server. A PowerShell native-output pipeline
        # retains inherited handles; Start-Process -Wait also waits for descendant processes.
        $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
        $startInfo.FileName = $postgresTools['pg_ctl']
        $startInfo.Arguments = "-D `"$dataDirectory`" -l `"$serverLog`" -o `"-h 127.0.0.1 -p $Port`" -w start"
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startProcess = [System.Diagnostics.Process]::new()
        $startProcess.StartInfo = $startInfo
        try {
            $null = $startProcess.Start()
            $startProcess.WaitForExit()
            $nativeExit = $startProcess.ExitCode
        } finally {
            $startProcess.Dispose()
        }
        if ($nativeExit -ne 0) { throw 'Disposable PostgreSQL startup failed; inspect postgres.log.' }

        $env:PGPASSWORD = $databasePassword
        $env:PGCONNECT_TIMEOUT = '5'
        $nativeExit = Invoke-LoggedCommand -Executable $postgresTools['createdb'] -Arguments @(
            '-h', '127.0.0.1', '-p', "$Port", '-U', $databaseUser, '-O', $databaseUser, $databaseName
        ) -LogPath (Join-Path $runDirectory 'createdb.log')
        if ($nativeExit -ne 0) { throw 'Disposable test database creation failed; inspect createdb.log.' }

        $env:MRI_E2E_POSTGRES_URL = "jdbc:postgresql://127.0.0.1:$Port/$databaseName"
        $env:MRI_E2E_POSTGRES_USER = $databaseUser
        $env:MRI_E2E_POSTGRES_PASSWORD = $databasePassword
        # Maven already uses the repository's documented build environment; no application env file is loaded.
        $checkExitCode = Invoke-LoggedCommand -Executable $mavenCommand -Arguments @(
            '-B', '-ntp', '-pl', 'report-application', '-am', '-o', 'test',
            '-Dtest=EnrollmentChallengePostgresConcurrencyTest,ExternalExchangePostgresReplicasTest',
            '-Dmri.postgres.integration=true',
            '-Dsurefire.failIfNoSpecifiedTests=false', '-l', $buildLog
        ) -LogPath (Join-Path $runDirectory 'maven-console.log')
    } finally {
        Pop-Location
    }
} finally {
    if ($clusterStarted) {
        $stopExit = Invoke-LoggedCommand -Executable $postgresTools['pg_ctl'] -Arguments @(
            '-D', $dataDirectory, '-m', 'fast', '-w', 'stop'
        ) -LogPath (Join-Path $runDirectory 'stop.log')
        if ($stopExit -ne 0) {
            $checkExitCode = 1
            Write-Warning 'Disposable PostgreSQL did not stop cleanly; inspect stop.log before removing its data.'
        }
    }
    if (Test-Path -LiteralPath $passwordFile) { Remove-Item -LiteralPath $passwordFile }
    foreach ($name in $environmentNames) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    $databasePassword = $null
    Set-Content -LiteralPath $resultFile -Value $checkExitCode
    if (Test-Path -LiteralPath $buildLog) {
        (Select-String -LiteralPath $buildLog -Pattern '^\[(INFO|ERROR)\] (Tests run:|BUILD (SUCCESS|FAILURE)|Running com\.ca\.mri\.|.*<<< (ERROR|FAILURE)!)').Line | Select-Object -Last 18
    }
    if(Test-Path -LiteralPath $buildLog) {
        (Select-String -LiteralPath $buildLog -Pattern '^Replica evidence:|^\[ERROR\] [A-Z]:.*\.java:\[\d+,\d+\]|^\[ERROR\]\s+(symbol|location):|^(Caused by: )?(java\.lang\.(NullPointerException|AssertionError|IllegalStateException|IllegalArgumentException)|org\.opentest4j\.AssertionFailedError|org\.springframework\.|org\.postgresql\.)|^\s+at com\.ca\.mri\.reportapplication\.ExternalExchange(PostgresReplicasTest|ReplicaHost)' | Select-Object -Last 25).Line | ForEach-Object {
            if ($_ -match '(?i)(password|Authorization:|Bearer |mrx1\.|private.key|client.secret|assertion=)') { '[diagnostic containing credential fields omitted]' } else { $_ }
        }
    }
    Write-Output "PostgreSQL integration result: $checkExitCode"
    Write-Output "Run artifacts: $runDirectory"
}
exit $checkExitCode
