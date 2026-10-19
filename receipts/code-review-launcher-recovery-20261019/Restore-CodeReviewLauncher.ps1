param([switch]$CheckOnly)

$ErrorActionPreference = 'Stop'
$repairApp = 'C:\Program Files\WindowsApps\OpenAI.Codex_26.1013.4894.0_x64__2p2nqsd0c76g0\app'
$repairSource = Join-Path $repairApp 'resources\plugins\openai-bundled\plugins\code-review'
$repairCache = 'C:\Users\kian\.codex\plugins\cache\openai-bundled\code-review\26.1013.40934'
$repairLauncher = 'scripts\launch_code_review_mcp.cmd'
$repairSourceFile = Join-Path $repairSource $repairLauncher
$repairDestination = Join-Path $repairCache $repairLauncher
$repairExpectedHash = '678B1FD9F901EECF9C2A248C79D84C3C90120D3907B9E88F60DCB95CBCE814C6'
$repairPython = 'C:\AI\bridge-monitor\.venv\Scripts\python.exe'
$repairSmoke = Join-Path $PSScriptRoot 'smoke-mcp.py'
$repairSmokeHash = '34AA268681068F6D36A435E5CC8021A35B7E6271809718AD6ADD09A2A367B8FC'

function Get-RepairHash([string]$LiteralPath) {
    return (Get-FileHash -LiteralPath $LiteralPath -Algorithm SHA256).Hash
}

function Get-PluginHashes {
    $repairHashes = @{}
    Get-ChildItem -LiteralPath $repairCache -Recurse -File -Force | ForEach-Object {
        $repairHashes[$_.FullName.Substring($repairCache.Length + 1)] = Get-RepairHash $_.FullName
    }
    return $repairHashes
}

# Validate provenance before creating or copying anything.
$repairSignature = Get-AuthenticodeSignature -LiteralPath (Join-Path $repairApp 'ChatGPT.exe')
if ($repairSignature.Status.ToString() -ne 'Valid' -or
    $repairSignature.SignerCertificate.Subject -notmatch 'OpenAI OpCo, LLC') {
    throw 'The installed app does not have the expected valid OpenAI publisher signature.'
}
if ((Get-RepairHash $repairSourceFile) -ne $repairExpectedHash) {
    throw 'The shipped launcher differs from the previously verified 52-byte original.'
}
$repairVersion = (Get-Content -LiteralPath (Join-Path $repairSource '.codex-plugin\plugin.json') -Raw | ConvertFrom-Json).version
if ($repairVersion -ne '26.1013.40934') { throw 'Bundled plugin version changed; stop and recheck.' }
foreach ($repairRelative in @('server.mjs', '.codex-plugin\plugin.json')) {
    if ((Get-RepairHash (Join-Path $repairSource $repairRelative)) -ne
        (Get-RepairHash (Join-Path $repairCache $repairRelative))) {
        throw "Cached plugin differs from the original package: $repairRelative"
    }
}
if ((Get-RepairHash $repairSmoke) -ne $repairSmokeHash) {
    throw 'The local startup-test helper changed; stop and inspect it.'
}
if (-not (Test-Path -LiteralPath $repairPython -PathType Leaf)) { throw 'Existing Python runtime is missing.' }
$repairIsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($CheckOnly) {
    [pscustomobject]@{provenance='verified';source_version=$repairVersion;sha256=$repairExpectedHash;
        administrator=$repairIsAdmin;destination_exists=(Test-Path -LiteralPath $repairDestination);
        changes_made=$false} | ConvertTo-Json
    return
}
if (-not $repairIsAdmin) {
    throw 'Open PowerShell with Run as administrator, then run this script again. No permissions were changed.'
}

$repairBefore = Get-PluginHashes
$repairRun = Join-Path $PSScriptRoot ('elevated-' + (Get-Date -Format 'yyyyMMddTHHmmssfff'))
New-Item -ItemType Directory -Path $repairRun | Out-Null
$repairBefore | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repairRun 'plugin-before.json')

if (Test-Path -LiteralPath $repairDestination) {
    if ((Get-RepairHash $repairDestination) -ne $repairExpectedHash) {
        throw 'An existing launcher differs from the original. Refusing to overwrite it.'
    }
} else {
    [IO.Directory]::CreateDirectory((Split-Path -Parent $repairDestination)) | Out-Null
    # false means an existing file can never be overwritten by this copy.
    [IO.File]::Copy($repairSourceFile, $repairDestination, $false)
}
if ((Get-RepairHash $repairDestination) -ne $repairExpectedHash) { throw 'Restored file hash mismatch.' }

# This test uses a separate CODEX_HOME, forwards no API/GitHub credentials,
# and only sends initialize, notifications/initialized, ping and tools/list.
$repairSmokeReceipt = Join-Path $PSScriptRoot 'smoke-cache.json'
if (Test-Path -LiteralPath $repairSmokeReceipt) {
    Move-Item -LiteralPath $repairSmokeReceipt -Destination (Join-Path $repairRun 'previous-smoke-cache.json')
}
& $repairPython $repairSmoke cache
if ($LASTEXITCODE -ne 0) { throw 'The launcher was restored, but its MCP startup test failed. See smoke-cache.json.' }
$repairTest = Get-Content -LiteralPath $repairSmokeReceipt -Raw | ConvertFrom-Json
if (-not $repairTest.passed -or $repairTest.process_exit -ne 0) { throw 'MCP startup did not finish successfully.' }

$repairAfter = Get-PluginHashes
foreach ($repairRelative in $repairBefore.Keys) {
    if ($repairAfter[$repairRelative] -ne $repairBefore[$repairRelative]) {
        throw "An existing plugin file changed: $repairRelative"
    }
}
foreach ($repairRelative in $repairAfter.Keys) {
    if (-not $repairBefore.ContainsKey($repairRelative) -and $repairRelative -ne $repairLauncher) {
        throw "Unexpected new plugin file: $repairRelative"
    }
}
$repairResult = [pscustomobject]@{
    restored=$true; verified=$true; destination=$repairDestination; sha256=$repairExpectedHash;
    bytes=(Get-Item -LiteralPath $repairDestination).Length; advertised_tools=$repairTest.tool_count;
    existing_plugin_files_unchanged=$true; settings_changed=$false; permissions_changed=$false;
    security_settings_changed=$false; reviews_submitted=$false; test_exit=$repairTest.process_exit
}
$repairResult | ConvertTo-Json | Tee-Object -FilePath (Join-Path $repairRun 'result.json')
Write-Host "Restored and verified. Receipt: $repairRun"
