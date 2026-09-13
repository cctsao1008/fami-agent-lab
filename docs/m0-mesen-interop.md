# M0 Mesen CE interop notes

This document records the durable technical findings for the initial direct Python ↔ `MesenCore.dll` path.

## Verified upstream ABI surface

The pinned Mesen CE `InteropDLL` exposes a set of simple functions whose signatures can be bound safely without guessing native struct layouts:

```text
TestDll()
GetMesenVersion()
GetMesenBuildDate()
InitDll()
InitializeEmu(...)
LoadRom(...)
IsRunning()
Pause()
Resume()
IsPaused()
Stop()
Release()
```

The exact pinned upstream signatures used by the first headless lifecycle are:

```cpp
void __stdcall InitDll();

void __stdcall InitializeEmu(
    const char* homeFolder,
    void* windowHandle,
    void* viewerHandle,
    bool softwareRenderer,
    bool noAudio,
    bool noVideo,
    bool noInput
);

bool __stdcall LoadRom(char* filename, char* patchFile);
bool __stdcall IsRunning();
bool __stdcall IsPaused();
void __stdcall Stop();
void __stdcall Release();
```

For headless M0 bring-up, both native window handles are null and audio, video, and host input are disabled.

The remaining M0-relevant exports are still discovered by name before any deeper binding work:

```text
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

Mesen CE is pinned as the `modules/mesen` Git submodule. The native DLL is produced by the upstream `InteropDLL` project, but fami-pixel intentionally builds that target through:

```text
modules/mesen/Mesen.sln
```

rather than invoking `InteropDLL.vcxproj` directly. Several upstream projects derive include paths from `$(SolutionDir)`, so direct project invocation breaks dependency include resolution.

For `Release|x64`, the upstream output is:

```text
modules/mesen/bin/win-x64/Release/MesenCore.dll
```

`tools/build_mesen.ps1` performs a clean solution-target build, stages the result at:

```text
build/mesen/MesenCore.dll
```

and runs the M0 ABI probe unless `-SkipProbe` is specified.

On Traditional-Chinese Windows hosts, the pinned upstream source includes text that triggers MSVC `C4819` under the local code page. The build wrapper preserves the upstream/default source encoding and suppresses only warning `C4819`; it does not force the whole tree to UTF-8 because some pinned source files contain legacy/non-UTF-8 bytes.

On a fresh clone:

```powershell
git submodule update --init --recursive
.\tools\build_mesen.ps1
```

The script requires Visual Studio 2022/2026 with the C++ desktop toolchain and locates `MSBuild.exe` through PATH or `vswhere.exe`.

## ABI probe

Install the package in editable mode, then run on Windows:

```powershell
py -m pip install -e .
py tools\inspect_mesen_exports.py build\mesen\MesenCore.dll
```

The probe reports the DLL path, `TestDll()` result, Mesen numeric version, native build date, and presence/absence of each M0-relevant export.

The project-controlled build has been verified to produce a loadable `MesenCore.dll` whose M0 export probe passes.

## Headless ROM boot smoke test

The next M0 slice validates only the native emulator lifecycle before debugger stepping or controller injection:

```text
InitDll
→ InitializeEmu(headless)
→ LoadRom(local ROM)
→ IsRunning / IsPaused
→ Stop
→ Release
```

Run it with a local ROM path:

```powershell
py examples\mesen_headless_boot.py "D:\path\to\game.nes"
```

The default DLL and isolated Mesen home paths are:

```text
build/mesen/MesenCore.dll
build/mesen-home/
```

ROM images remain local and are not committed to fami-pixel.

## Next ABI audit

After the headless ROM boot is confirmed on the target machine, continue in this order:

1. debugger lifecycle;
2. exact `CpuType` and `StepType` declarations;
3. deterministic frame-step semantics;
4. exact controller override layout;
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
