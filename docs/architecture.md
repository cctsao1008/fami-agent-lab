# Architecture

## Goal

Build a low-overhead machine-learning environment around Mesen CE without using Lua and without treating the Windows desktop as the primary observation surface.

The target loop is:

```text
observation_t → Python agent → action_t → emulator step → observation_t+1
```

The emulator remains responsible for correct NES execution. Python remains responsible for experimentation, models, logging, and policy logic.

## Observation paths

### Native visual observation

Preferred path:

```text
NES PPU
  ↓
Mesen video pipeline
  ↓
native frame buffer
  ↓
Python / NumPy
```

The desired interface should expose at least:

```text
frame_buffer
width
height
pixel_format
frame_id
```

A direct native buffer is preferred over window capture because it avoids desktop composition, scaling, overlays, focus state, and capture latency.

### Structured observation

Use the existing Mesen debugger / interop APIs for machine state where possible:

```text
RAM
CPU state
PPU state
tilemap
sprite state
```

Game-specific semantics such as Mario position, velocity, scroll position, or enemy state should be decoded in the `fami_agent/mario/` layer rather than embedded in the generic emulator wrapper.

## Action path

Preferred path:

```text
Python
  ↓
Mesen native interop
  ↓
controller input override
  ↓
NES controller port
```

Avoid virtual HID/gamepad injection in the target architecture because it adds unnecessary operating-system layers and weakens frame alignment.

## Synchronization

The environment should ultimately support deterministic frame-level stepping:

```text
apply action
run exactly one frame
stop at frame boundary
collect observation
return to Python
```

Wall-clock sleeps are not considered a deterministic synchronization mechanism.

## Research modes

### Vision-only

```text
framebuffer → visual model → controller action
```

### State-only

```text
RAM / PPU → structured model → controller action
```

### Hybrid

```text
framebuffer + structured state → model → controller action
```

Keeping all three modes available allows controlled comparison of representation efficiency and learning behavior.

## Boundary rules

- Do not modify the game ROM for agent control.
- Do not use Lua in the control path.
- Do not use screen scraping as the target visual interface.
- Prefer existing `MesenCore.dll` exports before adding any new native export.
- If a new export is required, keep it minimal and observation-oriented.
- Keep Mario-specific knowledge outside the generic Mesen/NES wrapper.
