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
$Solution = Join-Path $MesenRoot "Mesen.sln"
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

if (-not (Test-Path $Solution) -or -not (Test-Path $Project)) {
    throw @"
Mesen CE submodule is not initialized.
Run:
    git submodule update --init --recursive
Expected solution:
    $Solution
Expected project:
    $Project
"@
}

$MSBuild = Find-MSBuild
Write-Host "Repository : $RepoRoot"
Write-Host "Mesen      : $MesenRoot"
Write-Host "MSBuild    : $MSBuild"
Write-Host "Solution   : $Solution"
Write-Host "Target     : InteropDLL"
Write-Host "Config     : $Configuration|$Platform"
Write-Host "Encoding   : UTF-8 (/utf-8)"
Write-Host "Build mode : clean + build"
Write-Host

# Build through Mesen.sln rather than invoking InteropDLL.vcxproj directly.
# Several Mesen projects derive their include paths and output directories from
# $(SolutionDir); direct project invocation leaves that property without the
# solution context and causes includes such as Utilities/... and Core/... to fail.
#
# Mesen source contains Unicode text. On Windows systems whose active ANSI code
# page is not UTF-8 (for example Traditional Chinese CP950), MSVC emits C4819.
# Mesen treats warnings as errors, which promotes that warning to C2220 and
# stops the build. Inject /utf-8 through the documented CL environment variable
# for this child build only, without modifying the upstream submodule.
#
# Because the compiler encoding switches are part of the precompiled-header
# compatibility contract, switching from the default code page to /utf-8 while
# reusing old PCH/object files produces C2855. Always clean the InteropDLL target
# and its dependencies before rebuilding with the injected encoding option.
$OriginalCL = [Environment]::GetEnvironmentVariable("CL", "Process")
try {
    $env:CL = if ([string]::IsNullOrWhiteSpace($OriginalCL)) {
        "/utf-8"
    } else {
        "/utf-8 $OriginalCL"
    }

    Write-Host "Cleaning Mesen InteropDLL and dependencies..."
    & $MSBuild $Solution `
        /m `
        /nologo `
        /verbosity:minimal `
        /t:InteropDLL:Clean `
        "/p:Configuration=$Configuration" `
        "/p:Platform=$Platform"

    if ($LASTEXITCODE -ne 0) {
        throw "Mesen InteropDLL clean failed with exit code $LASTEXITCODE."
    }

    Write-Host
    Write-Host "Building Mesen InteropDLL..."
    & $MSBuild $Solution `
        /m `
        /nologo `
        /verbosity:minimal `
        /t:InteropDLL `
        "/p:Configuration=$Configuration" `
        "/p:Platform=$Platform"

    if ($LASTEXITCODE -ne 0) {
        throw "Mesen InteropDLL build failed with exit code $LASTEXITCODE."
    }
}
finally {
    if ($null -eq $OriginalCL) {
        Remove-Item Env:CL -ErrorAction SilentlyContinue
    } else {
        $env:CL = $OriginalCL
    }
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
