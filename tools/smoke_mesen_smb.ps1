[CmdletBinding()]
param(
    [string]$RomPath,
    [string]$DllPath
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$WorkspaceRoot = Split-Path $RepoRoot -Parent
$Planner = Join-Path $RepoRoot "examples\mesen_smb_checkpoint_planner_v7.py"
$DefaultRom = Join-Path $WorkspaceRoot "nek\roms\Super Mario Bros. (Japan, USA).nes"
$DefaultDll = Join-Path $RepoRoot "build\mesen\MesenCore.dll"
$SmokeHome = Join-Path $RepoRoot "build\mesen-home-smoke"
$SmokeState = Join-Path $RepoRoot "build\checkpoints\smb1-runtime-smoke.mss"
$SmokeCapture = Join-Path $RepoRoot "build\checkpoints\runtime-smoke-captures"

function Resolve-SmokePython {
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

    throw "Python was not found. Run .\tools\setup_python_env.ps1 first."
}

if ([string]::IsNullOrWhiteSpace($RomPath)) {
    $RomPath = $DefaultRom
}
if ([string]::IsNullOrWhiteSpace($DllPath)) {
    $DllPath = $DefaultDll
}

$RomPath = [System.IO.Path]::GetFullPath($RomPath)
$DllPath = [System.IO.Path]::GetFullPath($DllPath)

if (-not (Test-Path $Planner)) {
    throw "V7 planner was not found: $Planner"
}
if (-not (Test-Path $RomPath)) {
    throw @"
SMB1 ROM was not found.
Auto-detected path:
    $RomPath

Expected workspace layout:
    <workspace>\fami-pixel
    <workspace>\nek\roms\Super Mario Bros. (Japan, USA).nes

Override with:
    .\tools\smoke_mesen_smb.ps1 -RomPath <path-to-rom>
"@
}
if (-not (Test-Path $DllPath)) {
    throw @"
MesenCore.dll was not found:
    $DllPath

Build it first with tools\build_mesen.ps1, or override with -DllPath.
"@
}

$Python = Resolve-SmokePython

Write-Host "Fami Pixel SMB1 runtime smoke"
Write-Host "Repository : $RepoRoot"
Write-Host "Workspace  : $WorkspaceRoot"
Write-Host "Python     : $Python"
Write-Host "DLL        : $DllPath"
Write-Host "ROM        : $RomPath"
Write-Host
Write-Host "Scope      : load -> debugger -> title -> World 1-1 -> one authoritative decision"
Write-Host

# Keep the smoke bounded and deterministic enough for setup validation:
# - World 1-1 begins at X=40 in the known SMB1 path.
# - target X=41 requires at least one committed gameplay action.
# - max-decisions=2 gives the next loop a chance to observe the target and exit PASS.
& $Python $Planner `
    $RomPath `
    --dll $DllPath `
    --home $SmokeHome `
    --state-file $SmokeState `
    --capture-dir $SmokeCapture `
    --max-decisions 2 `
    --target-x 41

if ($LASTEXITCODE -ne 0) {
    throw "SMB1 runtime smoke failed with exit code $LASTEXITCODE."
}

Write-Host
Write-Host "SMB1 runtime smoke: PASS"
