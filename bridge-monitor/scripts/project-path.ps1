function Resolve-ProjectChild {
    param([string]$Path, [string]$ProjectRoot)
    $root = [System.IO.Path]::GetFullPath($ProjectRoot).TrimEnd('\')
    $full = [System.IO.Path]::GetFullPath($Path)
    $prefix = $root + '\'
    if (-not $full.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Target path leaves bridge-monitor.'
    }
    $current = $root
    if ((Get-Item -LiteralPath $current -Force).Attributes -band
        [System.IO.FileAttributes]::ReparsePoint) {
        throw 'Project root is a reparse point.'
    }
    foreach ($part in $full.Substring($prefix.Length).Split('\')) {
        if (-not $part) { continue }
        $current = Join-Path $current $part
        if (Test-Path -LiteralPath $current) {
            $item = Get-Item -LiteralPath $current -Force
            if ($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) {
                throw 'Target path traverses a reparse point.'
            }
        }
    }
    return $full
}
