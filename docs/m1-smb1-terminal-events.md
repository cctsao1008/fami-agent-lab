# M1 SMB1 Terminal Event Sources

This note records the source basis for the first terminal events in the SMB1 environment layer.

The environment does not infer death or level completion from pixels. It derives them from SMB1's own game-engine state and keeps Mesen execution as ground truth.

## Source basis

The public SMB1 disassembly exposes `GameEngineSubroutine` through the `GameRoutines` dispatch table. The relevant entries are:

```text
0x04  FlagpoleSlide
0x05  PlayerEndLevel
0x06  PlayerLoseLife
0x08  PlayerCtrlRoutine
0x0B  PlayerDeath
```

The same disassembly shows:

- normal player control running through `PlayerCtrlRoutine` (`0x08`),
- transition into `PlayerDeath` (`0x0B`) for the death sequence,
- flagpole handling through `FlagpoleSlide` (`0x04`), followed by `PlayerEndLevel` (`0x05`) once the flagpole sequence finishes,
- `PlayerLoseLife` (`0x06`) as the later bookkeeping path that decrements lives and may switch to game-over mode.

Public source references used for this audit:

- https://gist.github.com/WillSams/678a2d8a49d3f01e1d6e0362f83d1fbc
- https://gist.github.com/dansalvato/ef1e3d34f6af710e57a876005d8b29a7

## Event definitions

For M1, terminal events are edge-triggered on entry into these authoritative engine routines:

```text
DIED
  previous GameEngineSubroutine != 0x0B
  current  GameEngineSubroutine == 0x0B

LEVEL_COMPLETED
  previous GameEngineSubroutine != 0x05
  current  GameEngineSubroutine == 0x05
```

The edge condition prevents the same event from being emitted again on every frame while the routine remains active.

## Why `PlayerLoseLife` is not the death event

`PlayerLoseLife` (`0x06`) is later life/accounting logic. It decrements the life counter and can transition to game-over mode. For an environment transition, `PlayerDeath` (`0x0B`) is the earlier and semantically direct witness that Mario has entered the death routine.

The later `0x06` path may still be useful for episode reset bookkeeping or life-count metrics.

## Why `PlayerEndLevel` is the level-complete event

The flagpole interaction starts earlier in `FlagpoleSlide` (`0x04`). That state means the completion sequence is in progress, not yet that the level transition has completed its flagpole stage. The next engine routine is `PlayerEndLevel` (`0x05`), which is therefore the first narrow terminal witness used by M1.

This definition is intentionally local to SMB1. Other games must provide their own game-specific terminal-state sources.

## Validation status

Source audit: complete.

Unit-level contract tests: added for `0x08 -> 0x0B` death entry, `0x04 -> 0x05` level-complete entry, and duplicate suppression while a terminal routine remains unchanged.

`DIED`: machine-validated against actual World 1-1 execution.

`LEVEL_COMPLETED`: still requires machine validation against an actual World 1-1 completion path.

### Death probe attempt 1 — stationary Mario

The first machine probe intentionally left Mario stationary after entering World 1-1. Result:

```text
GameEntry : PASS NativeFrame=196 X=40 Engine=0x08
DeathEdge : FAIL no DIED event within 900 frames; Engine=0x08 X=40
```

This is a useful negative result: standing at the initial X position does not guarantee an enemy activation/collision path. The event definition itself was not disproved; the stimulus failed to produce a death.

### Death probe attempt 2 — RIGHT, no jump

The revised machine probe held RIGHT after entering World 1-1 and did not jump. This produced a real collision/death path without fabricated RAM state:

```text
GameEntry : PASS NativeFrame=196 X=40 Engine=0x08
DeathDrive: RIGHT held, no jump; waiting for real collision/death
Progress  : frame=316 drive=120/900 X=189 Engine=0x08
EngineEdge: frame=387 0x08->0x0B X=295 Y=0xB0 State=1
DeathEdge : PASS frame=387 Engine=0x0B X=295 Y=0xB0
Duplicate : PASS no repeated DIED event
DeathEvent: PASS actual SMB1 execution emitted exactly one DIED edge
Supervisor: PASS
```

This establishes the M1 `DIED` event definition as machine-validated for the current SMB1 path:

```text
previous GameEngineSubroutine != 0x0B
current  GameEngineSubroutine == 0x0B
```

The observed authoritative transition was `0x08 -> 0x0B` at native frame 387. The edge-triggered event emitted exactly once, and a 12-frame duplicate-suppression window emitted no additional `DIED` events.