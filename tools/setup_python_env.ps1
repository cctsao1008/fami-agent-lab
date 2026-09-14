[CmdletBinding()]
param(
    [string]$VenvPath = ".venv"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = if ([System.IO.Path]::IsPathRooted($VenvPath)) {
    $VenvPath
} else {
    Join-Path $RepoRoot $VenvPath
}
$VenvPython = Join-Path $Venv "Scripts\python.exe"
$ActivateScript = Join-Path $Venv "Scripts\Activate.ps1"
$EditableSpec = "$($RepoRoot)[test]"

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

Write-Host "Repository : $RepoRoot"
Write-Host "Venv       : $Venv"
Write-Host

if (-not (Test-Path $VenvPython)) {
    $PyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    if (-not $PyLauncher) {
        throw "Python launcher 'py.exe' was not found. Install Python 3.11 or newer and retry."
    }

    & $PyLauncher.Source -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
    if ($LASTEXITCODE -ne 0) {
        throw "The default Python selected by py.exe is older than Python 3.11."
    }

    $BasePythonVersion = & $PyLauncher.Source --version
    Write-Host "Base Python: $BasePythonVersion"
    Write-Host "Creating virtual environment..."
    Invoke-Checked -Program $PyLauncher.Source -ArgumentList @("-m", "venv", $Venv)
} else {
    Write-Host "Existing virtual environment found."
}

if (-not (Test-Path $VenvPython)) {
    throw "Virtual-environment Python was not created at: $VenvPython"
}

& $VenvPython -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw "The virtual environment uses Python older than 3.11. Remove '$Venv' and rerun this script with a supported Python installation."
}

$VenvVersion = & $VenvPython --version
Write-Host "Venv Python: $VenvVersion"
Write-Host

Write-Host "Upgrading packaging tools..."
Invoke-Checked -Program $VenvPython -ArgumentList @("-m", "pip", "install", "--upgrade", "pip", "setuptools")

Write-Host
Write-Host "Installing fami-pixel with test dependencies..."
Invoke-Checked -Program $VenvPython -ArgumentList @("-m", "pip", "install", "-e", $EditableSpec)

Write-Host
Write-Host "Verifying fami-pixel import..."
Invoke-Checked -Program $VenvPython -ArgumentList @("-c", "import fami_pixel; print(fami_pixel.__file__)")

Write-Host
Write-Host "Verifying pytest..."
Invoke-Checked -Program $VenvPython -ArgumentList @("-m", "pytest", "--version")

Write-Host
Write-Host "Python environment ready."
Write-Host
Write-Host "Activate in the current PowerShell with:"
Write-Host "    .\.venv\Scripts\Activate.ps1"
Write-Host
Write-Host "Or run tools directly without activation, for example:"
Write-Host "    .\.venv\Scripts\python.exe -m pytest -q"
