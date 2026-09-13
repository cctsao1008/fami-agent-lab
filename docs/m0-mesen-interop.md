# M0 Mesen CE interop notes

This document records the durable technical findings for the initial direct Python ↔ `MesenCore.dll` path.

## Verified upstream ABI surface

The current Mesen CE `InteropDLL` exports a small set of simple functions that can be bound safely without guessing native struct layouts:

```text
TestDll()
GetMesenVersion()
GetMesenBuildDate()
```

The M0 probe binds only these signatures initially and uses symbol discovery for the remaining M0-relevant exports.

The following exports are expected from the upstream interop surface and are checked by name before any deeper binding work:

```text
InitDll
InitializeEmu
LoadRom
Pause
Resume
IsPaused
Stop
Release
InitializeDebugger
ReleaseDebugger
IsDebuggerRunning
IsExecutionStopped
ResumeExecution
Step
SetInputOverrides
GetAvailableInputOverrides
GetMemorySize
GetMemoryState
GetMemoryValue
GetMemoryValues
GetCpuState
GetPpuState
SaveState
LoadState
SaveStateFile
LoadStateFile
```

## Binding rule

Do not bind enum- or struct-heavy APIs until their exact upstream declarations and layout are audited. In particular, `DebugControllerState`, `CpuType`, `StepType`, `MemoryType`, and framebuffer ownership/pixel-format details must be verified before Python `ctypes` definitions are added.

This avoids creating an ABI that merely appears to work on one build.

## Project-controlled Mesen build

Mesen CE is pinned as the `modules/mesen` Git submodule. The pinned upstream project that produces the native DLL is:

```text
modules/mesen/InteropDLL/InteropDLL.vcxproj
```

For `Release|x64`, the upstream project explicitly defines:

```text
TargetName = MesenCore
OutDir     = <Mesen solution>/bin/win-x64/Release/
```

Therefore the expected upstream build artifact is:

```text
modules/mesen/bin/win-x64/Release/MesenCore.dll
```

`tools/build_mesen.ps1` builds that project with MSBuild, stages the result at:

```text
build/mesen/MesenCore.dll
```

and runs the M0 ABI probe unless `-SkipProbe` is specified.

On a fresh clone:

```powershell
git submodule update --init --recursive
.\tools\build_mesen.ps1
```

The script requires Visual Studio 2022/2026 with the C++ desktop toolchain and locates `MSBuild.exe` through PATH or `vswhere.exe`.

## First probe

Install the package in editable mode, then run on Windows:

```powershell
py -m pip install -e .
py tools\inspect_mesen_exports.py C:\path\to\MesenCore.dll
```

The probe reports:

```text
DLL path
TestDll result
Mesen numeric version
native build date
presence/absence of each M0-relevant export
```

It does **not** initialize emulation, load a ROM, or issue controller input yet.

## Next ABI audit

Before implementing the first control trace, audit and bind, in this order:

1. emulator initialization and ROM loading;
2. debugger lifecycle;
3. exact controller override layout;
4. deterministic frame-step semantics;
5. one structured memory observation path;
6. native framebuffer ownership and export path.

The target remains a frame-aligned sequence:

```text
reset
→ RIGHT × 60 frames
→ RIGHT + A × 10 frames
→ RELEASE
```

with a trace that associates each logical/frame step with the requested action and native observation.
