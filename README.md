# Fami Agent Lab 🎮🌱

A tiny machine-learning playground for Famicom / NES games.

> Let Python see the game, understand the world, and control the player.

The first workload is **Super Mario Bros.** running on **Mesen CE**. The project focuses on a low-overhead, frame-aligned control loop between Python and the emulator core.

## Focus

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

The environment is designed to support three observation modes:

- **Vision-only** — native emulator framebuffer → model → action
- **State-only** — RAM / PPU state → model → action
- **Hybrid** — framebuffer + structured state → model → action

This makes it possible to compare pure visual control against privileged emulator-state control without modifying the game ROM.

## Design rule

Keep the emulator as the source of truth for machine state, keep Python as the experimentation layer, and keep game-specific semantics isolated from the generic NES environment.
