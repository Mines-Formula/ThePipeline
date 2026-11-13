param (
    [string]$RemoteUser = "austin_t",
    [string]$RemoteHost = "fsaelinux",
    [string]$RemoteDir = "/srv/formula/ThePipeline/data",
    [string]$LocalDir = (Join-Path $PSScriptRoot "..\data")
)

$ErrorActionPreference = "Stop"

$ResolvedLocal = (Resolve-Path -Path $LocalDir -ErrorAction SilentlyContinue)
if (-not $ResolvedLocal) {
    New-Item -ItemType Directory -Path $LocalDir -Force | Out-Null
    $ResolvedLocal = (Resolve-Path -Path $LocalDir)
}

Write-Host "Syncing data from ${RemoteUser}@${RemoteHost}:${RemoteDir} to ${ResolvedLocal}..." -ForegroundColor Cyan
Write-Host "Preserving file modification timestamps for heatmaps (-p flag)..." -ForegroundColor Gray

# Use -p to preserve mtimes (critical for heatmap timestamps), -r for recursion
$source = "${RemoteUser}@${RemoteHost}:${RemoteDir}/*"
scp -rp $source $ResolvedLocal

if ($LASTEXITCODE -eq 0) {
    Write-Host "`nSync completed successfully!" -ForegroundColor Green
    Get-ChildItem -Path $ResolvedLocal -Recurse -File | Select-Object Name, Length, LastWriteTime | Format-Table -AutoSize
} else {
    Write-Warning "SCP exited with code $LASTEXITCODE. If permissions were denied on the server, see troubleshooting in the instructions."
}
