# Fami Pixel — Fresh PC Clone & Build Guide

This guide is for setting up **fami-pixel** on a new Windows PC from a clean checkout.

Repository:

```text
https://github.com/cctsao1008/fami-pixel
```

The project uses a pinned **Mesen CE** fork as a Git submodule:

```text
modules/mesen
→ https://github.com/cctsao1008/MesenCE.git
```

The intended environment is **Windows + Python 3.11+** plus either:

```text
A. Visual Studio / Visual Studio Build Tools with C++ support
B. a portable vsget MSVC + MSBuild + Windows SDK tree
```

The Visual Studio path remains the normal installed-toolchain path. The portable path is for machines where a full Visual Studio installation is undesirable.

## 1. Prerequisites

Install the following first.

### Git

```powershell
git --version
```

### Python 3.11 or newer

```powershell
py --version
```

The project currently declares:

```text
Python >= 3.11
```

### Native build toolchain — choose one

#### Option A — Visual Studio / Build Tools

Install Visual Studio 2022/2026 or the corresponding Build Tools with:

```text
Desktop development with C++
MSBuild
MSVC x64/x86 build tools
Windows SDK
```

The repository build script automatically tries to locate `MSBuild.exe` from `PATH` or through `vswhere.exe`.

#### Option B — portable vsget toolchain

A portable Windows build has been machine-validated with `reksar/vsget`.

The portable destination must contain the equivalent of:

```text
<PortableVS>\
├─ VC\Tools\MSVC\...
├─ MSBuild\Current\Bin\amd64\MSBuild.exe
├─ MSBuild\Microsoft\VC\v170\Microsoft.Cpp.Default.props
├─ MSBuild\Microsoft\VC\v170\Microsoft.Cpp.props
├─ MSBuild\Microsoft\VC\v170\Microsoft.Cpp.targets
├─ SDK\Windows Kits\10\...
└─ vcvars-x64-x64.bat
```

Example validated destination:

```text
E:\PortableVS
```

The fami-pixel build wrapper constructs the required process-local MSVC/MSBuild/SDK environment itself when `-PortableVS` is supplied. It does not require permanent PATH or registry changes.

## 2. Clone the repository

Recommended example:

```powershell
D:
cd D:\my-github

git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel
```

If the repository was cloned without `--recurse-submodules`:

```powershell
git submodule update --init --recursive
```

Verify:

```powershell
git status
git submodule status
```

The following paths must exist:

```text
modules\mesen\Mesen.sln
modules\mesen\InteropDLL\InteropDLL.vcxproj
```

## 3. Update an existing checkout

```powershell
cd D:\my-github\fami-pixel

git pull
git submodule sync --recursive
git submodule update --init --recursive
```

Do not manually move the Mesen submodule to another commit unless the main repository explicitly changes its pinned gitlink.

## 4. Create the Python environment

From the repository root, use the repository-owned bootstrap script:

```powershell
.\tools\setup_python_env.ps1
```

The script:

```text
checks Python >= 3.11
creates .venv when missing
upgrades pip/setuptools
installs fami-pixel in editable mode
installs the declared test dependency extra
verifies import fami_pixel
verifies pytest availability
```

Python test dependencies are declared in `pyproject.toml` under the `test` optional dependency group. The bootstrap script does not hard-code individual test packages.

Activate the environment in the current PowerShell when desired:

```powershell
.\.venv\Scripts\Activate.ps1
```

Activation is intentionally separate because a child PowerShell script cannot persist environment changes into its parent shell.

You can also use the virtual-environment interpreter directly without activation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

## 5. Build the fami-pixel Mesen CE Interop DLL

The repository contains the canonical Windows build wrapper:

```text
tools\build_mesen.ps1
```

### Installed Visual Studio / Build Tools

Run:

```powershell
.\tools\build_mesen.ps1
```

### Portable vsget toolchain

Run:

```powershell
.\tools\build_mesen.ps1 -PortableVS E:\PortableVS -LowMemory
```

`-PortableVS` makes the wrapper discover and use the portable MSVC, MSBuild, VC targets, and Windows SDK from the supplied root.

`-LowMemory` changes build parallelism to:

```text
MSBuild /m:1
CL_MPCount=1
```

This mode was required on a 16 GB validation machine with limited commit/pagefile headroom. It avoids changing the machine pagefile and is safe to use on fresh or constrained PCs, at the cost of build speed.

The portable wrapper also adds the Windows SDK `winrt` include directory explicitly. This is required for WRL headers such as:

```text
wrl.h
wrl/client.h
```

The script performs a clean `Release|x64` build of the Mesen CE `InteropDLL` target through `Mesen.sln`.

It suppresses only MSVC warning `C4819`, because the pinned upstream Mesen sources contain source-text characters that can conflict with the Traditional Chinese Windows code page.

Do **not** globally force `/utf-8` as a workaround.

Successful output is staged to:

```text
build\mesen\MesenCore.dll
```

The script then automatically runs the fami-pixel ABI probe. When `.venv` exists, the repo-local virtual-environment Python is preferred for the probe.

Verify:

```powershell
Test-Path .\build\mesen\MesenCore.dll
```

Expected:

```text
True
```

If PowerShell execution policy blocks the installed-toolchain path:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\build_mesen.ps1
```

For the portable path:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\build_mesen.ps1 -PortableVS E:\PortableVS -LowMemory
```

## 6. Run the ABI probe manually

Normally the build script already does this.

With the repo-local virtual environment:

```powershell
.\.venv\Scripts\python.exe .\tools\inspect_mesen_exports.py .\build\mesen\MesenCore.dll
```

The DLL must expose the fami-pixel native extensions used by the Python adapter, including deterministic frame stepping and direct NES controller/framebuffer access.

Do not substitute a stock upstream Mesen CE DLL that lacks the fami-pixel interop additions.

## 7. Run the Python test suite

With the virtual environment activated:

```powershell
python -m pytest -q
```

Or without activation:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Current known-good baseline as of 2026-09-14:

```text
21 passed
```

Treat the machine's actual pytest result as authoritative.

## 8. ROM setup

ROMs are **not** stored in the fami-pixel repository.

Use a legally obtained local copy of Super Mario Bros.

Example local path:

```text
D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes
```

Do not commit ROM files into the repository.

## 9. Quick runtime smoke test

After the DLL is built and the ROM path is available, run the current B-aware planner:

```powershell
python .\examples\mesen_smb_checkpoint_planner_v7_cast.py `
  "D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes"
```

Typical startup sequence:

```text
DLL       : ...\build\mesen\MesenCore.dll
ROM       : ...\Super Mario Bros. (Japan, USA).nes
Init      : PASS
NesConfig : PASS
LoadRom   : PASS
Debugger  : PASS
```

A warning such as:

```text
[CPU] Uninitialized memory read: $07DC
```

has previously been observed and is not by itself a startup failure.

## 10. Replay a captured regression fixture

If the previous machine's V5 test-report ZIP has also been copied to the new PC, for example:

```text
D:\my-github\fami-pixel\build\test-reports\fami-pixel-v5-test-report-20260914-064240.zip
```

run:

```powershell
python .\examples\mesen_smb_checkpoint_planner_v7_cast.py `
  "D:\my-github\nek\roms\Super Mario Bros. (Japan, USA).nes" `
  --report-zip "D:\my-github\fami-pixel\build\test-reports\fami-pixel-v5-test-report-20260914-064240.zip" `
  --fixture pre-collapse `
  --target-x 1267
```

A correctly synchronized fixture replay should report:

```text
FixtureSync: PASS exact frame=946 X=1091
```

The regression ZIP is a local research artifact and is not required for a fresh normal run.

## 11. Expected local directories after build/run

```text
fami-pixel\
├─ .venv\
├─ build\
│  ├─ mesen\
│  │  └─ MesenCore.dll
│  ├─ mesen-home\
│  ├─ checkpoints\
│  └─ test-reports\
├─ modules\
│  └─ mesen\
├─ examples\
├─ src\
├─ tests\
└─ tools\
```

`.venv\` and `build\` are local generated artifacts and are ignored by Git.

## 12. Fast setup checklist

### Installed Visual Studio / Build Tools

```powershell
git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel

.\tools\setup_python_env.ps1
.\.venv\Scripts\Activate.ps1

.\tools\build_mesen.ps1
python -m pytest -q
```

### Portable vsget toolchain

Assuming vsget has already populated `E:\PortableVS`:

```powershell
git clone --recurse-submodules https://github.com/cctsao1008/fami-pixel.git
cd fami-pixel

.\tools\setup_python_env.ps1
.\.venv\Scripts\Activate.ps1

.\tools\build_mesen.ps1 -PortableVS E:\PortableVS -LowMemory
python -m pytest -q
```

Then run a Mesen-based example with the local ROM path.

## Troubleshooting

### `Mesen CE submodule is not initialized`

```powershell
git submodule update --init --recursive
```

### `MSBuild.exe was not found`

Use one of the two supported paths:

```text
- install Visual Studio / Build Tools with Desktop development with C++
- supply a complete portable vsget tree with -PortableVS
```

### Missing `wrl.h` / `wrl/client.h`

Use the repository wrapper with `-PortableVS`. It adds the SDK `winrt` include directory to the process-local portable environment.

Confirm the file exists under the portable SDK tree, for example:

```text
E:\PortableVS\SDK\Windows Kits\10\Include\<version>\winrt\wrl.h
```

### `C3859`, `C1076`, `C1060`, or paging-file / PCH exhaustion

Retry with low-memory mode:

```powershell
.\tools\build_mesen.ps1 -PortableVS E:\PortableVS -LowMemory
```

This intentionally trades build speed for lower peak commit usage.

### `MesenCore.dll not found`

Re-run the appropriate build command. Expected staged output:

```text
build\mesen\MesenCore.dll
```

### Python cannot import `fami_pixel` or pytest is missing

Re-run the repository bootstrap:

```powershell
.\tools\setup_python_env.ps1
```

Then either activate the environment:

```powershell
.\.venv\Scripts\Activate.ps1
```

or invoke its Python directly:

```powershell
.\.venv\Scripts\python.exe -c "import fami_pixel"
.\.venv\Scripts\python.exe -m pytest --version
```

### Submodule appears wrong or incomplete

```powershell
git submodule sync --recursive
git submodule update --init --recursive
git submodule status
```

### `FamiPixelStepFrame failed (4: timeout waiting for native frame advance)`

This is a native frame-step synchronization timeout, not a normal Python exception to hide by simply increasing timeouts indefinitely.

First confirm:

```text
- correct pinned Mesen submodule
- freshly rebuilt MesenCore.dll
- ABI probe passes
- pytest passes
```

Then preserve the exact runtime log for diagnosis.

## Authority rules

```text
Git repository
    = source/code/config authority

Pinned Mesen submodule
    = native emulator implementation authority

Mesen runtime
    = machine-state authority

Python planner/environment
    = experiment and control layer

Local ROM
    = user-provided external game image
```

Do not replace machine-observed behavior with assumptions from documentation or planner heuristics.
