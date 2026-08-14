# scripts/windows-backfill.ps1
#
# Windows-side blurhash / fingerprint backfill against the in-cluster
# Qdrant via Tailscale. Run from your Windows box, no k8s needed.
#
# What it does
# ------------
# Walks the existing Qdrant `images` collection. For every point whose
# `payload.source` matches one of `-SourceNames`, it re-reads the
# source file from disk, recomputes the requested payload field
# (blurhash or fingerprint), and `set_payload`s the result.
#
# No re-embed. The 1536-dim SigLIP2 vector is never touched. Idempotent:
# points that already have the right value are skipped.
#
# For the kpop library, expect ~30-60 min on a fast disk; the
# underlying local_sync prints progress every batch. Re-runnable: a
# second pass is cheap.
#
# Usage
# -----
#   $env:QDRANT_API_KEY = "***"               # one time per shell
#
#   # Blurhash backfill (default; kpop library)
#   .\scripts\windows-backfill.ps1
#
#   # Same but only one source
#   .\scripts\windows-backfill.ps1 `
#       -Sources "Z:/kpop/collections" `
#       -SourceNames "kpop/collections"
#
#   # Fingerprints instead of blurhash
#   .\scripts\windows-backfill.ps1 -Field fingerprint
#
#   # Different mount / UNC
#   .\scripts\windows-backfill.ps1 `
#       -PathPrefix "\\nas01\images" `
#       -NasImagesBase "Y:/"
#
# Notes
# -----
# * -PathPrefix is what gets STORED in Qdrant payload['path']. The
#   k8s search pods resolve this prefix against NAS_IMAGES_BASE,
#   so it must match what the cluster expects (\\192.168.250.108\...).
#
# * -NasImagesBase is the local mount that corresponds to the prefix.
#   The script translates local Z:\kpop\a.jpg -> the canonical
#   \\192.168.250.108\files\images\kpop\a.jpg form before reading,
#   so the backfill works even if you mount the NAS at a different
#   drive letter than Z:.
#
# * If your Windows box accesses the NAS via the same UNC directly
#   (\\192.168.250.108\... works), pass -NasImagesBase "" and the
#   local path stored in the payload is used as-is.

[CmdletBinding()]
param(
    [Parameter(Mandatory = $false)]
    [string[]]$Sources = @("Z:/kpop/collections", "Z:/kpop/data"),

    [Parameter(Mandatory = $false)]
    [string[]]$SourceNames = @("kpop/collections", "kpop/data"),

    [Parameter(Mandatory = $false)]
    [ValidateSet("blurhash", "fingerprint")]
    [string]$Field = "blurhash",

    [Parameter(Mandatory = $false)]
    [string]$QdrantUrl = "http://qdrant:6333",

    [Parameter(Mandatory = $false)]
    [string]$QdrantApiKey = $env:QDRANT_API_KEY,

    [Parameter(Mandatory = $false)]
    [string]$QdrantCollection = "images",

    [Parameter(Mandatory = $false)]
    [string]$PathPrefix = "\\192.168.250.108\files\images",

    [Parameter(Mandatory = $false)]
    [string]$NasImagesBase = "Z:/",

    [Parameter(Mandatory = $false)]
    [int]$BatchSize = 64,

    [Parameter(Mandatory = $false)]
    [int]$Limit = 0
)

$ErrorActionPreference = "Stop"

# --- sanity checks --------------------------------------------------------

if (-not $QdrantApiKey) {
    throw "QDRANT_API_KEY is not set. Run: `$env:QDRANT_API_KEY = 'your-key' (or pass -QdrantApiKey)"
}
$env:QDRANT_API_KEY = $QdrantApiKey

if ($Sources.Count -ne $SourceNames.Count) {
    throw "-Sources count ($($Sources.Count)) must equal -SourceNames count ($($SourceNames.Count))"
}

foreach ($src in $Sources) {
    if (-not (Test-Path -LiteralPath $src)) {
        throw "Source does not exist on this box: $src"
    }
    if (-not (Get-Item -LiteralPath $src).PSIsContainer) {
        throw "Source is not a directory: $src"
    }
}

# --- build the python -m invocation ---------------------------------------

$cliArgs = @(
    "-m", "indexer.local_sync"
)

for ($i = 0; $i -lt $Sources.Count; $i++) {
    $cliArgs += @("--source", $Sources[$i], "--source-name", $SourceNames[$i])
}

$cliArgs += @(
    "--qdrant-url", $QdrantUrl
    "--qdrant-api-key", $QdrantApiKey
    "--qdrant-collection", $QdrantCollection
    "--prefix", $PathPrefix
    "--base", $NasImagesBase
    "--batch-size", $BatchSize
)

if ($Limit -gt 0) {
    $cliArgs += @("--limit", $Limit)
}

# --reblurhash or --refingerprint
$cliArgs += @("--$Field")

# --- run -----------------------------------------------------------------

Write-Host ""
Write-Host "==> backfill: $Field" -ForegroundColor Cyan
Write-Host "    qdrant:        $QdrantUrl" -ForegroundColor DarkGray
Write-Host "    collection:    $QdrantCollection" -ForegroundColor DarkGray
Write-Host "    sources:       $($Sources -join ', ')" -ForegroundColor DarkGray
Write-Host "    source-names:  $($SourceNames -join ', ')" -ForegroundColor DarkGray
Write-Host "    path-prefix:   $PathPrefix" -ForegroundColor DarkGray
Write-Host "    nas-base:      $NasImagesBase" -ForegroundColor DarkGray
Write-Host "    batch-size:    $BatchSize" -ForegroundColor DarkGray
if ($Limit -gt 0) {
    Write-Host "    limit:         $Limit (per source)" -ForegroundColor DarkGray
}
Write-Host ""

# Inherit stdio so progress prints to your shell live.
# Use `python` from PATH; on Windows installs with the launcher you
# can change to `py -3` if needed.
& python @cliArgs
$exit = $LASTEXITCODE

Write-Host ""
if ($exit -eq 0) {
    Write-Host "==> backfill complete" -ForegroundColor Green
} else {
    Write-Host "==> backfill exited with code $exit" -ForegroundColor Red
    exit $exit
}
