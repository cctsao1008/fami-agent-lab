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

Unit-level contract tests: implemented for `0x08 -> 0x0B` death entry, `0x04 -> 0x05` level-complete entry, and duplicate suppression while a terminal routine remains unchanged.

The preceding non-terminal M1 contract slice was machine-independent and passed locally as `8 passed`. The newly added terminal-event tests still require a fresh local pytest run after pulling these commits.

Machine validation against an actual death and actual 1-1 completion remains required before these terminal events are considered fully machine-validated.
