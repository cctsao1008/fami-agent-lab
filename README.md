# Fami Pixel

A tiny machine-learning playground for Famicom / NES games.

> Let Python see the game, understand the world, and control the player.

The first workload is **Super Mario Bros.** running on **Mesen CE**. The project focuses on a low-overhead, frame-aligned control loop between Python and the emulator core.

## Focus

- Windows
- Mesen CE
- Python
- Native framebuffer access
- RAM / PPU observation
- Direct controller input
- Deterministic frame stepping

## Architecture

```text
Python / ML
   │
   ├── observation
   │      ├── native framebuffer
   │      └── RAM / PPU state
   │
   └── action
          └── controller input
                 │
                 ▼
              Mesen CE
                 │
                 ▼
            Famicom / NES
```

The emulator remains the authority for machine state. Python provides the experimentation layer above a small native adapter boundary.

## Research modes

The environment is designed to support three observation modes:

- **Vision-only** — native emulator framebuffer → model → action
- **State-only** — RAM / PPU state → model → action
- **Hybrid** — framebuffer + structured state → model → action

This makes it possible to compare pure visual control against privileged emulator-state control without modifying the game ROM.

## Design notes

- [Reward-aware SMB1 planning](docs/architecture/reward-aware-planning.md) — separate hazard avoidance from state-dependent power-up pursuit while keeping Mesen authoritative.

## Design rule

Keep the emulator as the source of truth for machine state, keep Python as the experimentation layer, and keep game-specific semantics isolated from the generic NES environment.
