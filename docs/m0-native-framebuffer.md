# M0 Native NES Framebuffer Contract

This note records the durable framebuffer contract used by fami-pixel M0.

## Canonical source

For NES, Mesen CE exposes the current PPU frame internally through:

```text
NesConsole::GetPpuFrame()
  -> NesPpu::GetScreenBuffer(false)
```

`NesConsole::GetPpuFrame()` reports:

- width: 256
- height: 240
- storage: `uint16_t` per pixel
- frame-buffer size: `256 * 240 * sizeof(uint16_t)`
- frame count: native PPU frame counter

This is emulator-owned PPU state. It is not a Windows renderer capture and does not depend on the desktop or a visible emulator window.

## Ownership and lifetime

`NesPpu` owns two `uint16_t[256 * 240]` output buffers. `_currentOutputBuffer` alternates between them at the pre-render scanline so the video decoder can still consume the previous frame while the PPU renders the next one.

Because the native pointer can change ownership role on the next frame, fami-pixel does not expose that pointer to Python.

The fork-side export instead performs a copy into caller-owned memory:

```text
FamiPixelCopyNesFrame(...)
```

The copy is allowed only while debugger execution is stopped. In the M0 runtime this stop is established by `FamiPixelStepFrame`, preventing Python from observing a buffer while the PPU is drawing or swapping it.

## Raw pixel layout

The copied image is row-major `uint16_t` data.

For the normal NES PPU path:

```text
bits 0..5 : NES palette color value
bits 6..8 : emphasis/intensify bits
```

Mesen applies grayscale and emphasis state to this raw output before a completed frame is sent to the video decoder. This buffer is therefore a canonical emulator observation but is not RGB/RGBA.

Color conversion belongs above the Mesen adapter and must not be confused with the canonical raw framebuffer contract.

## Stock interop audit

The pinned stock `InteropDLL` exposes screenshot and display-related operations such as `TakeScreenshot()` and `GetBaseScreenSize()`, but it does not expose a safe raw framebuffer-copy ABI suitable for Python ownership.

For M0 the minimum fork extension is therefore a single copy operation rather than a renderer API or raw native pointer export.

## fami-pixel API

Python uses:

```python
frame = copy_nes_raw_frame(core)
```

The returned `NesRawFrame` owns its data in Python and records:

```text
frame_count
width
height
pixels
```

The expected geometry is always 256 x 240 for this NES-specific API.

## M0 validation criteria

The supervised framebuffer probe requires:

```text
width == 256
height == 240
pixel_count == 61440
all raw words <= 0x01FF
copied frame_count == native frame_count
one synchronous native step -> copied frame_count delta == 1
```

A non-uniform image after SMB boot warmup is also used as a practical smoke witness that the copied buffer contains rendered content rather than an untouched allocation.
