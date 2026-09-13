[CmdletBinding()]
param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release",

    [ValidateSet("x64")]
    [string]$Platform = "x64",

    [switch]$SkipProbe
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$MesenRoot = Join-Path $RepoRoot "modules\mesen"
$Project = Join-Path $MesenRoot "InteropDLL\InteropDLL.vcxproj"
$StageDir = Join-Path $RepoRoot "build\mesen"
$SourceDll = Join-Path $MesenRoot "bin\win-x64\$Configuration\MesenCore.dll"
$StageDll = Join-Path $StageDir "MesenCore.dll"
$Probe = Join-Path $RepoRoot "tools\inspect_mesen_exports.py"

function Find-MSBuild {
    $cmd = Get-Command msbuild.exe -ErrorAction SilentlyContinue
    if ($cmd) {
        return $cmd.Source
    }

    $vswhere = Join-Path ${env:ProgramFiles(x86)} "Microsoft Visual Studio\Installer\vswhere.exe"
    if (Test-Path $vswhere) {
        $found = & $vswhere `
            -latest `
            -products * `
            -requires Microsoft.Component.MSBuild `
            -find "MSBuild\**\Bin\MSBuild.exe" |
            Select-Object -First 1

        if ($found) {
            return $found
        }
    }

    throw "MSBuild.exe was not found. Install Visual Studio 2022/2026 with Desktop development with C++."
}

if (-not (Test-Path $Project)) {
    throw @"
Mesen CE submodule is not initialized.
Run:
    git submodule update --init --recursive
Expected project:
    $Project
"@
}

$MSBuild = Find-MSBuild
Write-Host "Repository : $RepoRoot"
Write-Host "Mesen      : $MesenRoot"
Write-Host "MSBuild    : $MSBuild"
Write-Host "Project    : $Project"
Write-Host "Config     : $Configuration|$Platform"
Write-Host

& $MSBuild $Project `
    /m `
    /nologo `
    /verbosity:minimal `
    "/p:Configuration=$Configuration" `
    "/p:Platform=$Platform"

if ($LASTEXITCODE -ne 0) {
    throw "Mesen InteropDLL build failed with exit code $LASTEXITCODE."
}

if (-not (Test-Path $SourceDll)) {
    throw "Build completed but MesenCore.dll was not found at expected path: $SourceDll"
}

New-Item -ItemType Directory -Force -Path $StageDir | Out-Null
Copy-Item -Force $SourceDll $StageDll

Write-Host
Write-Host "MesenCore.dll staged:"
Write-Host "    $StageDll"

if (-not $SkipProbe) {
    Write-Host
    Write-Host "Running fami-pixel M0 ABI probe..."
    & py $Probe $StageDll
    if ($LASTEXITCODE -ne 0) {
        throw "MesenCore.dll built successfully, but the M0 ABI probe returned exit code $LASTEXITCODE."
    }
}
