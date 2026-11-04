# Management helpers, loaded only from a pinned administrator-owned staging copy.
function New-ProtectedDirectory([string]$Path, [string]$OperatorSid) {
    if (Test-Path -LiteralPath $Path) { throw 'Protected directory already exists' }
    $security = New-Object Security.AccessControl.DirectorySecurity
    $security.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;GRGX;;;$OperatorSid)")
    (New-Object IO.DirectoryInfo($Path)).Create($security)
}

function Open-NewProtectedFile([string]$Path, [string]$OperatorSid) {
    $security = New-Object Security.AccessControl.FileSecurity
    $security.SetSecurityDescriptorSddlForm("O:BAG:BAD:P(A;;FA;;;SY)(A;;FA;;;BA)(A;;GRGX;;;$OperatorSid)")
    # Owner and DACL are supplied to the create operation, not repaired afterward.
    New-Object IO.FileStream($Path, [IO.FileMode]::CreateNew, [Security.AccessControl.FileSystemRights]::FullControl,
        [IO.FileShare]::None, 4096, [IO.FileOptions]::None, $security)
}

function Copy-ProtectedFile([string]$Source, [string]$Destination, [string]$SHA256, [string]$OperatorSid) {
    $inputStream = New-Object IO.FileStream($Source, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::Read)
    try {
        $outputStream = Open-NewProtectedFile $Destination $OperatorSid
        try { $inputStream.CopyTo($outputStream); $outputStream.Flush($true) } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    if ((Get-FileHash -LiteralPath $Destination).Hash -ne $SHA256) { throw 'Protected file hash mismatch' }
}

function Write-ProtectedText([string]$Path, [string]$Text, [string]$OperatorSid) {
    $temporary = $Path + '.new-' + [Guid]::NewGuid().ToString('N')
    $stream = Open-NewProtectedFile $temporary $OperatorSid
    $writer = New-Object IO.StreamWriter($stream, (New-Object Text.UTF8Encoding($false)), 4096, $true)
    try { $writer.Write($Text); $writer.Flush(); $stream.Flush($true) }
    finally { $writer.Dispose(); $stream.Dispose() }
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}
