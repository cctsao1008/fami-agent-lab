[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,

    [string]$VsgetRevision = "b11726cfcebc66cb68a0bf564a017e54ffc22fc3",

    [string]$CacheRoot = (Join-Path $env:TEMP "fami-pixel-tools"),

    [switch]$ForceProvision
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$VsgetRepository = "https://github.com/reksar/vsget.git"
$Destination = [System.IO.Path]::GetFullPath($Destination)
$RevisionShort = $VsgetRevision.Substring(0, [Math]::Min(12, $VsgetRevision.Length))
$VsgetRoot = Join-Path ([System.IO.Path]::GetFullPath($CacheRoot)) "vsget-$RevisionShort"

function Get-PortableVsLayout {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root
    )

    $MSBuild = Join-Path $Root "MSBuild\Current\Bin\amd64\MSBuild.exe"
    if (-not (Test-Path $MSBuild)) {
        $MSBuild = Join-Path $Root "MSBuild\Current\Bin\MSBuild.exe"
    }

    $CppRoot = Join-Path $Root "MSBuild\Microsoft\VC\v170"
    $CppDefaultProps = Join-Path $CppRoot "Microsoft.Cpp.Default.props"
    $CppProps = Join-Path $CppRoot "Microsoft.Cpp.props"
    $CppTargets = Join-Path $CppRoot "Microsoft.Cpp.targets"
    $VcVars = Join-Path $Root "vcvars-x64-x64.bat"

    $Cl = Get-ChildItem -Path (Join-Path $Root "VC\Tools\MSVC") `
        -Filter cl.exe -File -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\bin\\Hostx64\\x64\\cl\.exe$' } |
        Sort-Object FullName -Descending |
        Select-Object -First 1

    $SdkRoot = Join-Path $Root "SDK\Windows Kits\10"
    $SdkIncludeRoot = Join-Path $SdkRoot "Include"
    $SdkVersionDir = Get-ChildItem -Path $SdkIncludeRoot -Directory -ErrorAction SilentlyContinue |
        Where-Object {
            Test-Path (Join-Path $_.FullName "um\Windows.h") -and
            Test-Path (Join-Path $_.FullName "shared\SDKDDKVer.h") -and
            Test-Path (Join-Path $_.FullName "ucrt\stdio.h") -and
            Test-Path (Join-Path $_.FullName "winrt\wrl.h") -and
            Test-Path (Join-Path $_.FullName "winrt\wrl\client.h")
        } |
        Sort-Object Name -Descending |
        Select-Object -First 1

    $SdkVersion = if ($SdkVersionDir) { $SdkVersionDir.Name } else { $null }
    $SdkLib = if ($SdkVersion) { Join-Path $SdkRoot "Lib\$SdkVersion" } else { $null }
    $SdkBin = if ($SdkVersion) { Join-Path $SdkRoot "bin\$SdkVersion\x64" } else { $null }

    [pscustomobject]@{
        Root             = $Root
        Cl               = if ($Cl) { $Cl.FullName } else { $null }
        MSBuild          = $MSBuild
        CppDefaultProps  = $CppDefaultProps
        CppProps         = $CppProps
        CppTargets       = $CppTargets
        VcVars           = $VcVars
        SdkRoot          = $SdkRoot
        SdkVersion       = $SdkVersion
        SdkLib           = $SdkLib
        SdkBin           = $SdkBin
        WinRTWrl         = if ($SdkVersionDir) { Join-Path $SdkVersionDir.FullName "winrt\wrl.h" } else { $null }
        WinRTClient      = if ($SdkVersionDir) { Join-Path $SdkVersionDir.FullName "winrt\wrl\client.h" } else { $null }
    }
}

function Test-PortableVsLayout {
    param(
        [Parameter(Mandatory = $true)]
        $Layout,

        [switch]$Quiet
    )

    $Checks = [ordered]@{
        "cl.exe"                     = $Layout.Cl
        "MSBuild.exe"                = $Layout.MSBuild
        "Microsoft.Cpp.Default.props" = $Layout.CppDefaultProps
        "Microsoft.Cpp.props"         = $Layout.CppProps
        "Microsoft.Cpp.targets"       = $Layout.CppTargets
        "vcvars-x64-x64.bat"          = $Layout.VcVars
        "SDK Lib"                      = $Layout.SdkLib
        "SDK Bin x64"                  = $Layout.SdkBin
        "winrt\wrl.h"                 = $Layout.WinRTWrl
        "winrt\wrl\client.h"          = $Layout.WinRTClient
    }

    $AllPass = $true
    foreach ($Name in $Checks.Keys) {
        $Path = $Checks[$Name]
        $Pass = -not [string]::IsNullOrWhiteSpace($Path) -and (Test-Path $Path)
        if (-not $Pass) {
            $AllPass = $false
        }

        if (-not $Quiet) {
            $Status = if ($Pass) { "PASS" } else { "FAIL" }
            Write-Host ("{0,-30} {1}" -f $Name, $Status)
            if ($Pass) {
                Write-Host "    $Path"
            }
        }
    }

    return $AllPass
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Program,

        [Parameter(Mandatory = $true)]
        [string[]]$ArgumentList
    )

    & $Program @ArgumentList
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code ${LASTEXITCODE}: $Program $($ArgumentList -join ' ')"
    }
}

Write-Host "Portable Visual Studio provisioning"
Write-Host "Destination : $Destination"
Write-Host "vsget repo  : $VsgetRepository"
Write-Host "vsget pin   : $VsgetRevision"
Write-Host "Cache       : $VsgetRoot"
Write-Host

$ExistingLayout = Get-PortableVsLayout -Root $Destination
if (-not $ForceProvision -and (Test-PortableVsLayout -Layout $ExistingLayout -Quiet)) {
    Write-Host "Existing portable toolchain is complete; provisioning skipped."
    Write-Host
    Test-PortableVsLayout -Layout $ExistingLayout | Out-Null
    Write-Host
    Write-Host "Portable Visual Studio toolchain ready."
    Write-Host "Build with:"
    Write-Host "    .\tools\build_mesen.ps1 -PortableVS `"$Destination`" -LowMemory"
    exit 0
}

$Git = Get-Command git.exe -ErrorAction SilentlyContinue
if (-not $Git) {
    throw "git.exe was not found. Install Git for Windows and retry."
}

New-Item -ItemType Directory -Force -Path ([System.IO.Path]::GetFullPath($CacheRoot)) | Out-Null

if (-not (Test-Path (Join-Path $VsgetRoot ".git"))) {
    if (Test-Path $VsgetRoot) {
        Remove-Item -Recurse -Force $VsgetRoot
    }

    Write-Host "Cloning vsget provisioning helper..."
    Invoke-Checked -Program $Git.Source -ArgumentList @(
        "clone",
        "--no-checkout",
        $VsgetRepository,
        $VsgetRoot
    )
}

Write-Host "Checking out pinned vsget revision..."
Invoke-Checked -Program $Git.Source -ArgumentList @(
    "-C", $VsgetRoot,
    "fetch", "--depth", "1", "origin", $VsgetRevision
)
Invoke-Checked -Program $Git.Source -ArgumentList @(
    "-C", $VsgetRoot,
    "checkout", "--detach", "--force", "FETCH_HEAD"
)

$ResolvedRevision = (& $Git.Source -C $VsgetRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $ResolvedRevision -ne $VsgetRevision) {
    throw "vsget revision verification failed. Expected $VsgetRevision, got $ResolvedRevision"
}

$VsgetBat = Join-Path $VsgetRoot "vsget.bat"
if (-not (Test-Path $VsgetBat)) {
    throw "Pinned vsget checkout does not contain vsget.bat: $VsgetBat"
}

New-Item -ItemType Directory -Force -Path $Destination | Out-Null

Write-Host
Write-Host "Running pinned vsget provisioning. This downloads MSVC, MSBuild, and the Windows SDK."
Write-Host "No permanent PATH or registry changes are made by this wrapper."
Write-Host

$Cmd = Get-Command cmd.exe -ErrorAction Stop
Invoke-Checked -Program $Cmd.Source -ArgumentList @(
    "/d",
    "/c",
    "call `"$VsgetBat`" `"$Destination`""
)

Write-Host
Write-Host "Validating portable toolchain..."
$Layout = Get-PortableVsLayout -Root $Destination
$Valid = Test-PortableVsLayout -Layout $Layout
if (-not $Valid) {
    throw "Portable toolchain provisioning completed, but one or more required fami-pixel build components are missing."
}

Write-Host
Write-Host "Portable Visual Studio toolchain ready."
Write-Host "SDK version : $($Layout.SdkVersion)"
Write-Host "MSBuild     : $($Layout.MSBuild)"
Write-Host "Compiler    : $($Layout.Cl)"
Write-Host
Write-Host "Build MesenCore with:"
Write-Host "    .\tools\build_mesen.ps1 -PortableVS `"$Destination`" -LowMemory"
