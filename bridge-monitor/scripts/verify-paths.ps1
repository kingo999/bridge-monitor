param([switch]$ReportOnly)
$project = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$root = $project.TrimEnd('\') + '\'
$paths = @('.venv', 'var', 'var\postgres', 'var\reports', 'dashboard\node_modules', 'dashboard\out')
$invalid = @()
foreach ($relative in $paths) {
    $resolved = [System.IO.Path]::GetFullPath((Join-Path $project $relative))
    if (-not $resolved.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        $invalid += $resolved
    }
    [pscustomobject]@{ Relative = $relative; Resolved = $resolved; WithinProject = ($invalid -notcontains $resolved) }
}
if ($invalid.Count -gt 0) { throw 'Planned path leaves project root.' }
