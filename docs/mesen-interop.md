# Mesen CE Interop Notes

## Current direction

Fami Agent Lab should use the lowest practical integration layer exposed by Mesen CE on Windows: `MesenCore.dll`.

The upstream Mesen CE repository already contains an `InteropDLL` project with native exports for emulator lifecycle, debugger access, memory access, stepping, and input override. Before adding any patch, the project should inventory and validate what is already available.

## Existing areas to audit

Upstream files of interest include:

```text
InteropDLL/EmuApiWrapper.cpp
InteropDLL/DebugApiWrapper.cpp
InteropDLL/InputApiWrapper.cpp
Core/Shared/Video/
```

Known interop capabilities worth validating from Python include:

```text
InitDll / InitializeEmu
LoadRom
Pause / Resume
Step
GetMemoryValue / GetMemoryValues / GetMemoryState
GetCpuState
GetPpuState
GetTilemap
GetSpritePreviewInfo
SetInputOverrides
```

## Framebuffer question

The current audit must determine the ownership and lifetime of the native rendered frame buffer before defining a Python ABI.

Questions to answer:

1. Where is the canonical post-PPU frame buffer stored?
2. Is it already CPU-addressable in a stable format?
3. Is the buffer stable until the next frame, or reused during rendering?
4. What is its pixel format and row stride?
5. Can Python receive a pointer safely without forcing a copy?
6. What frame counter or notification can be paired with that buffer?
7. Can frame stepping and buffer retrieval share one deterministic synchronization point?

Only after these are answered should a new native export be proposed.

## Desired Python-facing contract

Conceptually:

```python
obs = env.reset()
obs = env.step(action)
```

with an observation shaped roughly as:

```python
{
    "frame_id": int,
    "frame": np.ndarray,       # native visual observation
    "ram": np.ndarray,         # optional structured observation
    "state": {...},            # decoded game-specific state
}
```

The first implementation should avoid hidden copies when practical, but correctness and buffer lifetime must be established before pursuing zero-copy access.

## Non-goals for the initial path

```text
Lua bridge
Windows screen capture
virtual gamepad
GUI automation
ROM modification
```

These can remain debugging or fallback techniques, but they are not the intended architecture.
