param([switch]$ReportOnly)
$project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
. (Join-Path $PSScriptRoot 'project-path.ps1')
$paths = @('.venv', 'var', 'var\postgres', 'var\reports', 'var\run', 'var\backups',
    'var\workspaces', 'dashboard\node_modules', 'dashboard\out')
$invalid = @()
foreach ($relative in $paths) {
    $candidate = Join-Path $project $relative
    try {
        $resolved = Resolve-ProjectChild $candidate $project
        $valid = $true
    } catch {
        $resolved = [System.IO.Path]::GetFullPath($candidate)
        $valid = $false
        $invalid += $resolved
    }
    [pscustomobject]@{ Relative = $relative; Resolved = $resolved; WithinProject = $valid }
}
if ($invalid.Count -gt 0) { throw 'Planned path leaves project root.' }
