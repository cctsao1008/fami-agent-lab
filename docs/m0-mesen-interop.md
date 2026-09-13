# M0 Mesen CE interop notes

This document records the durable technical findings for the initial direct Python ↔ `MesenCore.dll` path.

## Verified upstream ABI surface

The current Mesen CE `InteropDLL` exports a small set of simple functions that can be bound safely without guessing native struct layouts:

```text
TestDll()
GetMesenVersion()
GetMesenBuildDate()
```

The M0 probe binds only verified signatures and uses symbol discovery for the remaining M0-relevant exports.

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

`tools/build_mesen.ps1` builds the `InteropDLL` target through `Mesen.sln`, stages the result at:

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

On Traditional Chinese Windows, the pinned Mesen source contains legacy/non-UTF-8 text that triggers MSVC encoding warnings. The build wrapper preserves the upstream/default source encoding and suppresses only warning `C4819`; forcing the entire tree to `/utf-8` is not valid for this pinned revision.

## Verified headless boot path

The following exact upstream signatures are bound and exercised:

```text
InitDll
InitializeEmu
LoadRom
IsRunning
IsPaused
Pause
Resume
Stop
Release
```

The current headless path is:

```text
InitDll
→ InitializeEmu(noAudio=true, noVideo=true, noInput=true)
→ LoadRom(local ROM)
→ IsRunning / IsPaused
→ Stop
→ Release
```

`examples/mesen_headless_boot.py` validates this path against a user-supplied local ROM. ROMs remain local and are not stored in this repository.

This path has been machine-validated on Windows against a local Super Mario Bros. NES ROM with:

```text
Init      : PASS
LoadRom   : PASS
IsRunning : True
IsPaused  : False
Stop      : PASS
Release   : PASS
```

## Verified debugger lifecycle

The pinned Mesen CE source defines and the adapter now binds:

```text
InitializeDebugger()
ReleaseDebugger()
IsDebuggerRunning() -> bool
IsExecutionStopped() -> bool
ResumeExecution()
```

`examples/mesen_debugger_smoke.py` exercises debugger initialization and release after a successful headless ROM load. It intentionally does not issue `Step()` yet.

The enum audit needed for the next slice is now partially complete:

```text
CpuType : uint8_t
CpuType::Nes = 8

StepType:
  Step             = 0
  StepOut          = 1
  StepOver         = 2
  CpuCycleStep     = 3
  PpuStep          = 4
  PpuScanline      = 5
  PpuFrame         = 6
  SpecificScanline = 7
  RunToNmi         = 8
  RunToIrq         = 9
  StepBack         = 10
```

Upstream `Step` is exported as:

```cpp
void __stdcall Step(CpuType cpuType, uint32_t count, StepType type)
```

The next machine-facing task is to validate debugger lifecycle first, then bind `Step` using the verified enum widths/values and determine its completion semantics for deterministic one-frame stepping.

## Target M0 control trace

The target remains a frame-aligned sequence:

```text
reset
→ RIGHT × 60 frames
→ RIGHT + A × 10 frames
→ RELEASE
```

with a trace that associates each logical/frame step with the requested action and native observation.
