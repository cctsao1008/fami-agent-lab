[CmdletBinding()]
param(
    [ValidateSet("Release", "Debug")]
    [string]$Configuration = "Release",

    [ValidateSet("x64")]
    [string]$Platform = "x64",

    [string]$PortableVS,

    [switch]$LowMemory,

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

    throw "MSBuild.exe was not found. Install Visual Studio 2022/2026 with Desktop development with C++, or use -PortableVS <vsget-root>."
}

function Get-LatestVersionDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    return Get-ChildItem -Path $Path -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            try {
                [void][version]$_.Name
                $true
            } catch {
                $false
            }
        } |
        Sort-Object { [version]$_.Name } -Descending |
        Select-Object -First 1
}

function Get-PortableVSEnvironment {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $ResolvedRoot = (Resolve-Path $Root).Path
    $VcRoot = Join-Path $ResolvedRoot "VC"
    $MsvcRoot = Join-Path $VcRoot "Tools\MSVC"
    $MsvcDir = Get-LatestVersionDirectory $MsvcRoot
    if (-not $MsvcDir) {
        throw "Portable MSVC was not found under: $MsvcRoot"
    }

    $MsbuildAmd64 = Join-Path $ResolvedRoot "MSBuild\Current\Bin\amd64\MSBuild.exe"
    $MsbuildX64 = Join-Path $ResolvedRoot "MSBuild\Current\Bin\MSBuild.exe"
    $MSBuild = if (Test-Path $MsbuildAmd64) {
        $MsbuildAmd64
    } elseif (Test-Path $MsbuildX64) {
        $MsbuildX64
    } else {
        throw "Portable MSBuild.exe was not found under: $ResolvedRoot\MSBuild\Current\Bin"
    }

    $VcTargetsRoot = Join-Path $ResolvedRoot "MSBuild\Microsoft\VC"
    $VcTargets = Get-ChildItem -Path $VcTargetsRoot -Directory -Filter "v*" -ErrorAction SilentlyContinue |
        Sort-Object Name -Descending |
        Select-Object -First 1
    if (-not $VcTargets) {
        throw "Portable Microsoft.Cpp targets were not found under: $VcTargetsRoot"
    }

    $SdkRoot = Join-Path $ResolvedRoot "SDK\Windows Kits\10"
    $SdkIncludeRoot = Join-Path $SdkRoot "Include"
    $SdkLibRoot = Join-Path $SdkRoot "Lib"
    $IncludeVersions = Get-ChildItem -Path $SdkIncludeRoot -Directory -ErrorAction SilentlyContinue
    $LibVersions = Get-ChildItem -Path $SdkLibRoot -Directory -ErrorAction SilentlyContinue
    $SdkVersion = $IncludeVersions |
        Where-Object { $LibVersions.Name -contains $_.Name } |
        Where-Object {
            try {
                [void][version]$_.Name
                $true
            } catch {
                $false
            }
        } |
        Sort-Object { [version]$_.Name } -Descending |
        Select-Object -First 1
    if (-not $SdkVersion) {
        throw "A common portable Windows SDK Include/Lib version was not found under: $SdkRoot"
    }

    $SdkVersionName = $SdkVersion.Name
    $SdkBin = Join-Path $SdkRoot "bin\$SdkVersionName\x64"
    if (-not (Test-Path $SdkBin)) {
        throw "Portable Windows SDK x64 binaries were not found at: $SdkBin"
    }

    $VCToolsInstallDir = $MsvcDir.FullName
    $VcBin = Join-Path $VCToolsInstallDir "bin\Hostx64\x64"
    $VcInclude = Join-Path $VCToolsInstallDir "include"
    $VcLib = Join-Path $VCToolsInstallDir "lib\x64"

    $SdkIncludes = @(
        (Join-Path $SdkIncludeRoot "$SdkVersionName\ucrt"),
        (Join-Path $SdkIncludeRoot "$SdkVersionName\shared"),
        (Join-Path $SdkIncludeRoot "$SdkVersionName\um"),
        (Join-Path $SdkIncludeRoot "$SdkVersionName\winrt")
    )
    $CppWinRt = Join-Path $SdkIncludeRoot "$SdkVersionName\cppwinrt"
    if (Test-Path $CppWinRt) {
        $SdkIncludes += $CppWinRt
    }

    $SdkLibs = @(
        (Join-Path $SdkLibRoot "$SdkVersionName\ucrt\x64"),
        (Join-Path $SdkLibRoot "$SdkVersionName\um\x64")
    )

    $VCTargetsPath = $VcTargets.FullName
    $VCToolsVersionVariable = "VCToolsInstallDir_$($VcTargets.Name.Substring(1))"

    return [pscustomobject]@{
        Root = $ResolvedRoot
        MSBuild = $MSBuild
        MsvcVersion = $MsvcDir.Name
        SdkVersion = $SdkVersionName
        VcTargetsVersion = $VcTargets.Name
        VCTargetsPath = $VCTargetsPath
        VCToolsInstallDir = $VCToolsInstallDir
        VCToolsVersionVariable = $VCToolsVersionVariable
        WindowsSDKDir = $SdkRoot
        Include = ((@($VcInclude) + $SdkIncludes) -join ";")
        WindowsSDKIncludePath = ($SdkIncludes -join ";")
        Lib = ((@($VcLib) + $SdkLibs) -join ";")
        LibPath = $VcLib
        WindowsSDKLibraryPath = ($SdkLibs -join ";")
        PathPrefix = (@($VcBin, $SdkBin, (Split-Path -Parent $MSBuild)) -join ";")
    }
}

function Find-ProbePython {
    $VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $VenvPython) {
        return $VenvPython
    }

    $Python = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($Python) {
        return $Python.Source
    }

    $Py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($Py) {
        return $Py.Source
    }

    throw "Python was not found for the ABI probe. Run tools\setup_python_env.ps1 first."
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

$PortableEnvironment = $null
$MSBuild = $null
$EnvironmentNames = @(
    "CL",
    "CL_MPCount",
    "DisableRegistryUse",
    "VSINSTALLDIR",
    "VCINSTALLDIR",
    "VCToolsInstallDir",
    "VCToolsVersion",
    "WindowsSDKDir",
    "WindowsSDKVersion",
    "WindowsSDK_IncludePath",
    "WindowsSDK_LibraryPath_x64",
    "VCTargetsPath",
    "VSCMD_ARG_TGT_ARCH",
    "VSCMD_ARG_HOST_ARCH",
    "INCLUDE",
    "LIB",
    "LIBPATH",
    "PATH"
)
$OriginalEnvironment = @{}
foreach ($Name in $EnvironmentNames) {
    $OriginalEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}

try {
    if ([string]::IsNullOrWhiteSpace($PortableVS)) {
        $MSBuild = Find-MSBuild
    } else {
        $PortableEnvironment = Get-PortableVSEnvironment $PortableVS
        $MSBuild = $PortableEnvironment.MSBuild
        $EnvironmentNames += $PortableEnvironment.VCToolsVersionVariable
        $OriginalEnvironment[$PortableEnvironment.VCToolsVersionVariable] = [Environment]::GetEnvironmentVariable($PortableEnvironment.VCToolsVersionVariable, "Process")

        [Environment]::SetEnvironmentVariable("DisableRegistryUse", "true", "Process")
        [Environment]::SetEnvironmentVariable("VSINSTALLDIR", "$($PortableEnvironment.Root)\", "Process")
        [Environment]::SetEnvironmentVariable("VCINSTALLDIR", "$($PortableEnvironment.Root)\VC\", "Process")
        [Environment]::SetEnvironmentVariable("VCToolsInstallDir", "$($PortableEnvironment.VCToolsInstallDir)\", "Process")
        [Environment]::SetEnvironmentVariable("VCToolsVersion", $PortableEnvironment.MsvcVersion, "Process")
        [Environment]::SetEnvironmentVariable($PortableEnvironment.VCToolsVersionVariable, "$($PortableEnvironment.VCToolsInstallDir)\", "Process")
        [Environment]::SetEnvironmentVariable("WindowsSDKDir", "$($PortableEnvironment.WindowsSDKDir)\", "Process")
        [Environment]::SetEnvironmentVariable("WindowsSDKVersion", "$($PortableEnvironment.SdkVersion)\", "Process")
        [Environment]::SetEnvironmentVariable("WindowsSDK_IncludePath", $PortableEnvironment.WindowsSDKIncludePath, "Process")
        [Environment]::SetEnvironmentVariable("WindowsSDK_LibraryPath_x64", $PortableEnvironment.WindowsSDKLibraryPath, "Process")
        [Environment]::SetEnvironmentVariable("VCTargetsPath", "$($PortableEnvironment.VCTargetsPath)\", "Process")
        [Environment]::SetEnvironmentVariable("VSCMD_ARG_TGT_ARCH", "x64", "Process")
        [Environment]::SetEnvironmentVariable("VSCMD_ARG_HOST_ARCH", "x64", "Process")
        [Environment]::SetEnvironmentVariable("INCLUDE", $PortableEnvironment.Include, "Process")
        [Environment]::SetEnvironmentVariable("LIB", $PortableEnvironment.Lib, "Process")
        [Environment]::SetEnvironmentVariable("LIBPATH", $PortableEnvironment.LibPath, "Process")

        $ExistingPath = [Environment]::GetEnvironmentVariable("PATH", "Process")
        [Environment]::SetEnvironmentVariable("PATH", "$($PortableEnvironment.PathPrefix);$ExistingPath", "Process")
    }

    $OriginalCL = [Environment]::GetEnvironmentVariable("CL", "Process")
    $env:CL = if ([string]::IsNullOrWhiteSpace($OriginalCL)) {
        "/wd4819"
    } else {
        "/wd4819 $OriginalCL"
    }

    if ($LowMemory) {
        [Environment]::SetEnvironmentVariable("CL_MPCount", "1", "Process")
    }

    Write-Host "Repository : $RepoRoot"
    Write-Host "Mesen      : $MesenRoot"
    Write-Host "MSBuild    : $MSBuild"
    Write-Host "Solution   : $Solution"
    Write-Host "Target     : InteropDLL"
    Write-Host "Config     : $Configuration|$Platform"
    Write-Host "Charset    : upstream/default; suppress C4819 only"
    Write-Host "Build mode : clean + build"
    Write-Host "Parallel   : $(if ($LowMemory) { 'low-memory (/m:1, CL_MPCount=1)' } else { 'default (/m)' })"
    if ($PortableEnvironment) {
        Write-Host "Toolchain  : portable"
        Write-Host "PortableVS : $($PortableEnvironment.Root)"
        Write-Host "MSVC       : $($PortableEnvironment.MsvcVersion)"
        Write-Host "SDK        : $($PortableEnvironment.SdkVersion)"
        Write-Host "VC targets : $($PortableEnvironment.VcTargetsVersion)"
    } else {
        Write-Host "Toolchain  : installed Visual Studio / Build Tools"
    }
    Write-Host

    # Build through Mesen.sln rather than invoking InteropDLL.vcxproj directly.
    # Several Mesen projects derive their include paths and output directories from
    # $(SolutionDir); direct project invocation leaves that property without the
    # solution context and causes includes such as Utilities/... and Core/... to fail.
    #
    # Do not force /utf-8 globally here. At least one pinned upstream source file
    # contains legacy/mojibake byte sequences that MSVC rejects under code page
    # 65001 with C4828. On Traditional Chinese Windows, the default source code page
    # instead emits C4819 for characters that are not representable in CP950. Those
    # instances are in source text/comments, and Mesen's TreatWarningAsError setting
    # promotes C4819 to C2220. Suppress only C4819 for this child build while leaving
    # the upstream source interpretation otherwise unchanged.
    #
    # Portable vsget validation found that the SDK winrt include path must be added
    # explicitly for WRL headers. LowMemory limits both MSBuild project parallelism
    # and MSVC /MP worker count to avoid PCH/pagefile exhaustion on constrained PCs.
    $Multiprocess = if ($LowMemory) { "/m:1" } else { "/m" }

    Write-Host "Cleaning Mesen InteropDLL and dependencies..."
    & $MSBuild $Solution `
        $Multiprocess `
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
        $Multiprocess `
        /nologo `
        /verbosity:minimal `
        /t:InteropDLL `
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
        $ProbePython = Find-ProbePython
        Write-Host
        Write-Host "Running fami-pixel M0 ABI probe..."
        Write-Host "Python     : $ProbePython"
        & $ProbePython $Probe $StageDll
        if ($LASTEXITCODE -ne 0) {
            throw "MesenCore.dll built successfully, but the M0 ABI probe returned exit code $LASTEXITCODE."
        }
    }
}
finally {
    foreach ($Name in $EnvironmentNames | Select-Object -Unique) {
        [Environment]::SetEnvironmentVariable($Name, $OriginalEnvironment[$Name], "Process")
    }
}
