# -----------------------------------------------------------------------------
#  build-releases.ps1
#  Stamps the version across all project files and assembles distributable ZIPs.
#
#  Usage (from the repo root):
#    .\build-releases.ps1                        # build using current version
#    .\build-releases.ps1 -Version v2.6.0        # stamp new version then build
#
#  Output:
#    dist\ReviewSentinel-<Version>-Docker.zip
#    dist\ReviewSentinel-<Version>-Python.zip
# -----------------------------------------------------------------------------
#Requires -Version 5.1
[CmdletBinding()]
param(
    [string]$Version = "",   # e.g. "v2.6.0"  - leave empty to keep current
    [switch]$Yes             # skip the confirmation prompt (CI / non-interactive)
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$Root        = $PSScriptRoot
$Dist        = Join-Path $Root "dist"
$CurrentVersion = "v2.1.3" # single source of truth

# -- Validate version format --------------------------------------------------
if ($Version -ne "" -and $Version -notmatch '^v\d+\.\d+\.\d+$') {
    Write-Host "  [ERROR] Invalid version format: '$Version'"
    Write-Host "          Expected: vX.Y.Z  (e.g. v2.6.0)"
    exit 1
}

# -- Stamp version into a file using a regex replace -------------------------
function Update-VersionInFile {
    param([string]$FilePath, [string]$Pattern, [string]$Replacement)
    if (-not (Test-Path $FilePath)) {
        Write-Host "    [!] Not found - skipped: $FilePath"
        return
    }
    $content = [System.IO.File]::ReadAllText($FilePath, [System.Text.UTF8Encoding]::new($false))
    $updated = [System.Text.RegularExpressions.Regex]::Replace($content, $Pattern, $Replacement)
    if ($content -ne $updated) {
        [System.IO.File]::WriteAllText($FilePath, $updated, [System.Text.UTF8Encoding]::new($false))
    }
    $rel = $FilePath.Replace($Root + "\", "").Replace($Root + "/", "")
    Write-Host "    [OK] $rel"
}

# -- Perform version stamp -----------------------------------------------------
function Invoke-VersionStamp {
    param([string]$OldVer, [string]$NewVer)

    Write-Host ""
    Write-Host "  -- Stamping version: $OldVer -> $NewVer ----------------------"

    $verPattern = [System.Text.RegularExpressions.Regex]::Escape($OldVer)

    # build-releases.sh  (VERSION="vX.Y.Z")
    Update-VersionInFile `
        -FilePath (Join-Path $Root "build-releases.sh") `
        -Pattern  ('VERSION="' + $verPattern + '"') `
        -Replacement ('VERSION="' + $NewVer + '"')

    # build-releases.ps1  ($CurrentVersion = "vX.Y.Z")
    Update-VersionInFile `
        -FilePath (Join-Path $Root "build-releases.ps1") `
        -Pattern  ('\$CurrentVersion = "' + $verPattern + '"') `
        -Replacement ('$CurrentVersion = "' + $NewVer + '"')

    # README.md  (<code>vX.Y.Z</code>)
    Update-VersionInFile `
        -FilePath (Join-Path $Root "README.md") `
        -Pattern  ('^v\d+\.\d+\.\d+ · Python 3\.12 · Flask 3\.1 · Docker ready') `
        -Replacement ($NewVer + ' · Python 3.12 · Flask 3.1 · Docker ready')

    # app.py  (RSP_VERSION = "vX.Y.Z") — the runtime source of truth rendered
    # in the footer and About page via the rsp_version context processor
    Update-VersionInFile `
        -FilePath (Join-Path $Root "app.py") `
        -Pattern  ('RSP_VERSION = "' + $verPattern + '"') `
        -Replacement ('RSP_VERSION = "' + $NewVer + '"')

    # releases/docker/README.md  (all occurrences of "Review Sentinel Prime vX.Y.Z")
    Update-VersionInFile `
        -FilePath (Join-Path $Root "releases\docker\README.md") `
        -Pattern  ('Review Sentinel Prime ' + $verPattern) `
        -Replacement ('Review Sentinel Prime ' + $NewVer)

    # releases/python/README.md  (title, footer, and zip example filename)
    Update-VersionInFile `
        -FilePath (Join-Path $Root "releases\python\README.md") `
        -Pattern  ('Review Sentinel Prime ' + $verPattern) `
        -Replacement ('Review Sentinel Prime ' + $NewVer)

    # releases/python/README.md  (zip example filename: ReviewSentinelPrime_vX.Y.Z)
    Update-VersionInFile `
        -FilePath (Join-Path $Root "releases\python\README.md") `
        -Pattern  ('ReviewSentinelPrime_' + $verPattern) `
        -Replacement ('ReviewSentinelPrime_' + $NewVer)

    Write-Host ""
    Write-Host "  Version stamped. All files updated to $NewVer."
    Write-Host ""
}

# -- Run stamp if a new version was requested ---------------------------------
$BuildVersion = $CurrentVersion

if ($Version -ne "") {
    if ($Version -eq $CurrentVersion) {
        Write-Host "  [INFO] Requested version ($Version) matches current version. No stamp needed."
    } else {
        Write-Host ""
        Write-Host "  +-----------------------------------------------------------------+"
        Write-Host "  |  Version update: $CurrentVersion  ->  $Version"
        Write-Host "  |"
        Write-Host "  |  Files to be updated:"
        Write-Host "  |    * build-releases.sh"
        Write-Host "  |    * build-releases.ps1"
        Write-Host "  |    * README.md"
        Write-Host "  |    * app.py"
        Write-Host "  |    * releases\docker\README.md"
        Write-Host "  |    * releases\python\README.md"
        Write-Host "  +-----------------------------------------------------------------+"
        Write-Host ""
        if ($Yes) { $confirm = "y" } else { $confirm = Read-Host "  Proceed? [y/N]" }
        if ($confirm -match '^[yY]') {
            Invoke-VersionStamp -OldVer $CurrentVersion -NewVer $Version
            $BuildVersion = $Version
        } else {
            Write-Host "  Aborted. No files were changed."
            exit 0
        }
    }
}

# App source directories/files that go into EVERY release
$AppItems = @("app.py", "requirements.txt", "templates", "static", "LICENSE")

# Minimal data skeleton (no credentials, no results)
$DataSkeleton = @{
    "data\searches.json"  = "[]"
    "data\smtp_config.json" = '{}'
}

# -- Helper: copy a directory tree into a staging folder ---------------------
function Copy-Tree ($Source, $Dest) {
    if (Test-Path $Source -PathType Container) {
        $name = Split-Path $Source -Leaf
        Copy-Item $Source (Join-Path $Dest $name) -Recurse -Force
    } else {
        Copy-Item $Source $Dest -Force
    }
}

# -- Helper: zip a staging folder and clean it up ----------------------------
function New-ReleaseZip ($StageDir, $ZipPath) {
    if (Test-Path $ZipPath) { Remove-Item $ZipPath -Force }
    # Entry names are normalised to forward slashes: .NET Framework's
    # CreateFromDirectory writes backslashes on Windows, which macOS/Linux
    # unzip treats as literal filenames instead of folders.
    $zip = [System.IO.Compression.ZipFile]::Open($ZipPath, [System.IO.Compression.ZipArchiveMode]::Create)
    try {
        $base = (Resolve-Path $StageDir).Path.TrimEnd('\')
        function Set-UnixExecModeIfNeeded {
            param([System.IO.Compression.ZipArchiveEntry]$Entry, [string]$RelativePath)

            $lower = $RelativePath.ToLowerInvariant()
            if ($lower.EndsWith('.command') -or $lower.EndsWith('.sh')) {
                # Preserve executable mode in the ZIP entry so macOS/Linux users
                # can run launchers without a manual chmod step.
                $Entry.ExternalAttributes = (33261 -shl 16)
            }
        }
        foreach ($file in Get-ChildItem $StageDir -Recurse -File -Force) {
            $rel = $file.FullName.Substring($base.Length + 1).Replace('\', '/')
            $entry = [System.IO.Compression.ZipFileExtensions]::CreateEntryFromFile(
                $zip, $file.FullName, $rel,
                [System.IO.Compression.CompressionLevel]::Optimal)
            Set-UnixExecModeIfNeeded -Entry $entry -RelativePath $rel
        }
    } finally {
        $zip.Dispose()
    }
    Remove-Item $StageDir -Recurse -Force
}

# -- Prepare dist/ folder ----------------------------------------------------
if (Test-Path $Dist) { Remove-Item $Dist -Recurse -Force }
New-Item -ItemType Directory -Path $Dist | Out-Null

# -- Shared: write data skeleton into a staging folder -----------------------
function Add-DataSkeleton ($StageDir) {
    New-Item -ItemType Directory -Path (Join-Path $StageDir "data\results") -Force | Out-Null
    foreach ($entry in $DataSkeleton.GetEnumerator()) {
        # Use WriteAllText with no-BOM UTF-8 - PowerShell 5.1's Set-Content -Encoding UTF8
        # writes a BOM that Python's json.load rejects.
        $path = Join-Path $StageDir $entry.Key
        [System.IO.File]::WriteAllText($path, $entry.Value, [System.Text.UTF8Encoding]::new($false))
    }
    # Relay configs live in the tracked releases/relay/ folder so every release ships them.
    # They hold public form URLs and field IDs only - never credentials.
    foreach ($cfg in @("google_form_config.json", "google_form_feedback_config.json", "google_form_feature_config.json")) {
        $src = Join-Path $Root "releases\relay\$cfg"
        if (-not (Test-Path $src)) {
            Write-Host "    [ERROR] Relay config missing: releases\relay\$cfg"
            exit 1
        }
        Copy-Item $src (Join-Path $StageDir "data\$cfg") -Force
    }
}

# ════════════════════════════════════════════════════════════════════════════
#  RELEASE 1 - Docker
# ════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "  Building: ReviewSentinel-$BuildVersion-Docker.zip ..."

$DockerStage = Join-Path $Dist "_stage_docker"
New-Item -ItemType Directory -Path $DockerStage | Out-Null

# App source
foreach ($item in $AppItems) { Copy-Tree (Join-Path $Root $item) $DockerStage }

# Data skeleton
Add-DataSkeleton $DockerStage

# Release-specific files (Dockerfile, docker-compose.yml, scripts, README)
$DockerSrc = Join-Path $Root "releases\docker"
foreach ($item in Get-ChildItem $DockerSrc -Force) {
    Copy-Item $item.FullName (Join-Path $DockerStage $item.Name) -Recurse -Force
}

$DockerZip = Join-Path $Dist "ReviewSentinel-$BuildVersion-Docker.zip"
New-ReleaseZip $DockerStage $DockerZip
Write-Host "  [OK] dist\ReviewSentinel-$BuildVersion-Docker.zip"

# ════════════════════════════════════════════════════════════════════════════
#  RELEASE 2 - Plain Python
# ════════════════════════════════════════════════════════════════════════════
Write-Host ""
Write-Host "  Building: ReviewSentinel-$BuildVersion-Python.zip ..."

$PythonStage = Join-Path $Dist "_stage_python"
New-Item -ItemType Directory -Path $PythonStage | Out-Null

# App source
foreach ($item in $AppItems) { Copy-Tree (Join-Path $Root $item) $PythonStage }

# Data skeleton
Add-DataSkeleton $PythonStage

# Release-specific files (START.bat, STOP.bat, start.command, stop.command, README)
$PythonSrc = Join-Path $Root "releases\python"
foreach ($item in Get-ChildItem $PythonSrc -Force) {
    Copy-Item $item.FullName (Join-Path $PythonStage $item.Name) -Recurse -Force
}

$PythonZip = Join-Path $Dist "ReviewSentinel-$BuildVersion-Python.zip"
New-ReleaseZip $PythonStage $PythonZip
Write-Host "  [OK] dist\ReviewSentinel-$BuildVersion-Python.zip"

# -- Summary -----------------------------------------------------------------
Write-Host ""
Write-Host "  -----------------------------------------"
Write-Host "  Release ZIPs ready in:  dist\"
Write-Host ""
Get-ChildItem $Dist | ForEach-Object {
    $mb = [math]::Round($_.Length / 1MB, 2)
    Write-Host ("    {0,-40} {1,6} MB" -f $_.Name, $mb)
}
Write-Host "  -----------------------------------------"
Write-Host ""

