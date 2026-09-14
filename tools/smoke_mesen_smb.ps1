[CmdletBinding()]
param(
    [string]$RomPath,
    [string]$DllPath,
    [int]$ShutdownGraceSeconds = 3
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
$SmokeLogDir = Join-Path $RepoRoot "build\test-reports"
$SmokeStdout = Join-Path $SmokeLogDir ".smb-runtime-smoke.stdout.log"
$SmokeStderr = Join-Path $SmokeLogDir ".smb-runtime-smoke.stderr.log"
$PassMarker = "PlannerV7: PASS target reached"

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

function Quote-ProcessArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Show-SmokeLogs {
    if (Test-Path $SmokeStdout) {
        Get-Content -Path $SmokeStdout
    }
    if (Test-Path $SmokeStderr) {
        Get-Content -Path $SmokeStderr
    }
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
New-Item -ItemType Directory -Force -Path $SmokeLogDir | Out-Null
Remove-Item -Force -ErrorAction SilentlyContinue $SmokeStdout, $SmokeStderr

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
# - max-decisions=2 gives the next loop a chance to observe the target and emit PASS.
#
# Mesen's native runtime can keep the Python host alive after the planner has already
# emitted a successful result. Run the planner as a child process, observe the explicit
# PASS marker, then allow a short natural-shutdown grace period. If the child is still
# alive after that point, terminate only that completed smoke child instead of hanging
# the caller's PowerShell session.
$Arguments = @(
    (Quote-ProcessArgument $Planner),
    (Quote-ProcessArgument $RomPath),
    "--dll", (Quote-ProcessArgument $DllPath),
    "--home", (Quote-ProcessArgument $SmokeHome),
    "--state-file", (Quote-ProcessArgument $SmokeState),
    "--capture-dir", (Quote-ProcessArgument $SmokeCapture),
    "--max-decisions", "2",
    "--target-x", "41"
)

$Process = Start-Process `
    -FilePath $Python `
    -ArgumentList $Arguments `
    -RedirectStandardOutput $SmokeStdout `
    -RedirectStandardError $SmokeStderr `
    -NoNewWindow `
    -PassThru

$PassObserved = $false
$LastStdoutCount = 0
$LastStderrCount = 0

while (-not $Process.HasExited -and -not $PassObserved) {
    Start-Sleep -Milliseconds 200

    $StdoutLines = if (Test-Path $SmokeStdout) { @(Get-Content -Path $SmokeStdout) } else { @() }
    if ($StdoutLines.Count -gt $LastStdoutCount) {
        $StdoutLines[$LastStdoutCount..($StdoutLines.Count - 1)] | ForEach-Object { Write-Host $_ }
        $LastStdoutCount = $StdoutLines.Count
    }
    if ($StdoutLines | Where-Object { $_ -like "$PassMarker*" }) {
        $PassObserved = $true
        break
    }

    $StderrLines = if (Test-Path $SmokeStderr) { @(Get-Content -Path $SmokeStderr) } else { @() }
    if ($StderrLines.Count -gt $LastStderrCount) {
        $StderrLines[$LastStderrCount..($StderrLines.Count - 1)] | ForEach-Object { Write-Host $_ }
        $LastStderrCount = $StderrLines.Count
    }
}

if ($PassObserved -and -not $Process.HasExited) {
    if (-not $Process.WaitForExit([Math]::Max(0, $ShutdownGraceSeconds) * 1000)) {
        Write-Host
        Write-Host "Runtime result : PASS marker observed; native host did not exit within ${ShutdownGraceSeconds}s."
        Write-Host "Shutdown       : terminating completed smoke child process."
        Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue
        $Process.WaitForExit()
    }
}

# Flush any lines written after the last polling iteration.
$StdoutLines = if (Test-Path $SmokeStdout) { @(Get-Content -Path $SmokeStdout) } else { @() }
if ($StdoutLines.Count -gt $LastStdoutCount) {
    $StdoutLines[$LastStdoutCount..($StdoutLines.Count - 1)] | ForEach-Object { Write-Host $_ }
}
$StderrLines = if (Test-Path $SmokeStderr) { @(Get-Content -Path $SmokeStderr) } else { @() }
if ($StderrLines.Count -gt $LastStderrCount) {
    $StderrLines[$LastStderrCount..($StderrLines.Count - 1)] | ForEach-Object { Write-Host $_ }
}

if (-not $PassObserved) {
    $ExitCode = if ($Process.HasExited) { $Process.ExitCode } else { -1 }
    throw "SMB1 runtime smoke failed before PASS (exit code $ExitCode)."
}

Write-Host
Write-Host "SMB1 runtime smoke: PASS"
