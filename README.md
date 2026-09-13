# Fami Agent Lab 🎮🌱

A tiny machine-learning playground for Famicom / NES games.

> Let Python see the game, understand the world, and control the player.

The first workload is **Super Mario Bros.** running on **Mesen CE**. The initial research goal is a low-overhead, frame-aligned control loop between Python and the emulator core.

## First target

- Windows
- Mesen CE
- Super Mario Bros.
- Python
- Direct `MesenCore.dll` interop
- Native framebuffer observation
- RAM / PPU structured observation
- Direct controller input override
- Deterministic, frame-aligned stepping

No Lua. No screen scraping in the target architecture. No virtual gamepad in the target architecture.

## Architecture

```text
                     Python Agent
                          │
              ┌───────────┴───────────┐
              │                       │
              ▼                       ▼
        Observation                 Action
              │                       │
      ┌───────┴────────┐              │
      │                │              │
      ▼                ▼              ▼
Native framebuffer  RAM / PPU   Input override
      │                │              │
      └────────────┬───┴──────────────┘
                   ▼
              MesenCore.dll
                   │
                   ▼
                 NES / FC
                   │
                   ▼
             Super Mario Bros.
```

## Research modes

The environment is intended to support three observation modes:

- **Vision-only** — native emulator framebuffer → model → action
- **State-only** — RAM / PPU state → model → action
- **Hybrid** — framebuffer + structured state → model → action

This lets the project compare pure visual control against privileged emulator-state control without changing the game ROM.

## Milestone 0

Establish the smallest direct Python ↔ Mesen control loop:

```text
reset
→ RIGHT × 60 frames
→ RIGHT + A × 10 frames
→ RELEASE
```

For each step, record the frame identifier, action, native visual observation, and selected structured state.

## Project layout

```text
fami-agent-lab/
├─ README.md
├─ docs/
│  ├─ architecture.md
│  └─ mesen-interop.md
├─ fami_agent/
│  ├─ __init__.py
│  ├─ env.py
│  ├─ mesen.py
│  ├─ observation.py
│  ├─ input.py
│  └─ mario/
│     ├─ __init__.py
│     └─ state.py
├─ experiments/
│  └─ smb1/
├─ tests/
└─ tools/
```

## Design rule

Keep the emulator as the source of truth for machine state, keep Python as the experimentation layer, and keep game-specific semantics isolated from the generic NES environment.

## Status

🌱 Project just planted.

The first engineering task is to audit the existing Mesen CE interop surface and determine the minimum native path for frame-aligned observation and control.
