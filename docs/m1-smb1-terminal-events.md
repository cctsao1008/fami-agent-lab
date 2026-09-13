# M1 SMB1 Terminal Event Sources

This note records the source basis and machine evidence for terminal events in the SMB1 environment layer.

The environment does not infer death or level completion from pixels. It derives them from SMB1 game-engine state and keeps Mesen execution as ground truth.

## Source basis

The public SMB1 disassembly exposes `GameEngineSubroutine` through the `GameRoutines` dispatch table. Relevant entries are:

```text
0x04  FlagpoleSlide
0x05  PlayerEndLevel
0x06  PlayerLoseLife
0x08  PlayerCtrlRoutine
0x0B  PlayerDeath
```

Public source references:

- https://gist.github.com/WillSams/678a2d8a49d3f01e1d6e0362f83d1fbc
- https://gist.github.com/dansalvato/ef1e3d34f6af710e57a876005d8b29a7

## Current event definitions

Machine evidence refined the original death definition. SMB1 has at least two observed authoritative death-entry paths:

```text
Enemy/collision death:
  previous engine != 0x0B
  current  engine == 0x0B

Pit/fall death:
  previous engine not in {0x0B, 0x06}
  current  engine == 0x06

Level complete:
  previous engine != 0x05
  current  engine == 0x05
```

The `0x0B -> 0x06` transition is bookkeeping for the same death and must not emit a second `DIED` event.

## Why the death definition changed

The first death probe, driven by RIGHT with no jump, machine-validated an enemy/collision path:

```text
EngineEdge: frame=387 0x08->0x0B X=295 Y=0xB0 State=1
DeathEdge : PASS frame=387 Engine=0x0B X=295 Y=0xB0
Duplicate : PASS no repeated DIED event
```

A later World 1-1 traversal produced a different path while Mario fell into hazards:

```text
EngineEdge: frame=1705 0x08->0x06 X=1542 Y=0x04 State=1
...
EngineEdge: frame=3013 0x08->0x06 X=2588 Y=0x02 State=1
```

There was no intermediate `0x0B` in those traces. Therefore `0x06 PlayerLoseLife` cannot be treated only as post-death bookkeeping; when entered directly from active play it is itself the first authoritative death witness for pit/fall deaths.

This is exactly why M1 keeps event semantics subordinate to machine evidence rather than freezing a source-only interpretation.

## Level-complete status

`LEVEL_COMPLETED` remains defined as entry into `0x05 PlayerEndLevel`. `0x04 FlagpoleSlide` means the flagpole sequence is in progress, not yet the selected M1 terminal witness.

Current validation status:

```text
DIED / enemy collision
  source-audited       yes
  unit-tested          yes
  machine-validated    yes (0x08 -> 0x0B)

DIED / pit-fall
  source-audited       yes
  unit-tested          yes
  machine-observed     yes (0x08 -> 0x06 at X=1542 and X=2588)

LEVEL_COMPLETED
  source-audited       yes
  unit-tested          yes
  machine-validated    pending
```

## World 1-1 completion probe history

### Attempt 1 — periodic grounded jumps

The first traversal stalled at `X=722`; this showed the baseline was insufficient and the original supervisor deadline was too short.

### Attempt 2 — stall recovery

The revised baseline added longer jumps when forward progress stalled. It successfully escaped the `X=722` plateau and reached substantially farther:

```text
X=722  -> stall recovery
X=1542 -> direct 0x08->0x06 pit/fall death
restart/checkpoint -> X=1320
X=2588 -> direct 0x08->0x06 pit/fall death
restart/checkpoint -> X=1320
later third-life/game-over path eventually returned to X=40
```

The important result is not merely that the baseline failed to finish. It exposed two repeatable hazard regions around the observed death coordinates and, more importantly, discovered the direct `0x06` death path.

### Attempt 3 — hazard-window long jumps

The next revision added pre-emptive long-jump windows derived from the previous machine trace. That did not improve the early traversal reliably. A stall-triggered long jump at `X=722` produced an enemy/collision death shortly afterwards:

```text
LongJump  : frame=792 X=722 hold=34 reason=stall recoveries=1
EngineEdge: frame=889 0x08->0x0B X=813 Y=0xAC State=1
LevelRun  : FAIL death before completion frame=889 engine=0x0B x=813 max_x=813
Supervisor: FAIL
```

This is evidence that adding more hand-authored jump heuristics is becoming counterproductive: action timing can trade one failure mode for another, and failure coordinates alone are not a sufficient planning state.

## Planning pivot

The next step is therefore not another heuristic tweak. M1 now pivots to checkpointed action search:

```text
known-good machine state
→ save Mesen state file
→ try bounded candidate action sequence
→ measure authoritative SMB1 result
→ restore checkpoint
→ try next candidate
→ commit only the best safe transition
```

Mesen already exposes `SaveStateFile` / `LoadStateFile`; their ABI was verified directly in the pinned `InteropDLL/EmuApiWrapper.cpp`. Fami Pixel now binds those functions in the Python Mesen adapter and adds a dedicated state-file roundtrip smoke probe before any planner depends on them.

This preserves the architecture boundary:

```text
Mesen save state = machine checkpoint authority
planner          = experiment/search policy
SMB1 decoder     = result interpretation
```

The checkpoint mechanism must be machine-validated before it is used to close `LEVEL_COMPLETED`.