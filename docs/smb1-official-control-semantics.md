# SMB1 Official Control Semantics

This note distills controller semantics from the official *Super Mario Bros.* instruction booklet (`CLV-P-NAAAE.pdf`) into durable guidance for Fami Pixel.

It is intentionally a **technical summary**, not a transcription of the manual. Mesen remains the execution authority; this document only records player-control semantics that are useful when designing action spaces, planners, and future environment adapters.

## Why this matters

The current planner work exposed a structural limitation in fixed Mario action macros: several 30-frame candidates can produce the same forward progress while reaching materially different machine states, and later collapse into the same doomed trajectory.

The official control description is useful because it clarifies that SMB1 movement is not naturally organized as a few fixed jump macros. It is a short-horizon control problem with independently meaningful horizontal, jump-hold, and acceleration inputs.

## Source-derived control semantics

### Jump height depends on A-button hold duration

The booklet explains that Mario's jump height depends on how long the A button is held.

Engineering implication:

```text
jump control != {short, medium, long} only
jump control = A hold duration over time
```

A planner should therefore prefer frame-explicit A control and adaptive durations over a permanently fixed set of 6/14/24-frame jump macros.

### Horizontal steering remains available while airborne

The booklet explains that LEFT/RIGHT can continue to influence Mario while he is in the air.

Engineering implication:

```text
jump initiation
    !=
full jump trajectory
```

A jump should be modeled as a sequence in which horizontal intent may change after takeoff.

For example:

```text
RIGHT+A 8f
→ RIGHT 4f
→ LEFT 4f
```

is meaningfully different from a single named `medium_jump` macro even if both initially press A for a similar amount of time.

### B controls running / acceleration state

The booklet describes B as the run/speed control and links increased speed with improved jumping capability.

Engineering implication:

The reachable future set depends on horizontal speed state. `RIGHT+B` and `RIGHT+A+B` are therefore not cosmetic variants; they can change which trajectories are physically reachable.

This suggests treating acceleration state as part of the control model rather than merely as a scoring side effect.

### DOWN is primarily crouch control

The booklet associates DOWN with crouching for Super Mario.

Engineering implication:

`DOWN` is not a high-priority primitive for the current World 1-1 planner because current machine evidence does not indicate a crouch-dependent failure mode. Add it when a validated scene or interaction requires it.

### Goal / flagpole semantics

The booklet describes completion by reaching the end-of-level flagpole and also distinguishes better flagpole contact as a scoring objective.

Engineering implication:

Fami Pixel should continue to separate:

```text
machine completion truth
    from
experiment quality objectives
```

`PlayerEndLevel` / validated engine-state transitions remain the authoritative terminal condition. Flagpole height, score, elapsed time, or stylistic quality may be planner/reward objectives above that machine truth.

## Control model suggested by the manual

A more faithful controller model is:

```text
Horizontal intent
  LEFT / NEUTRAL / RIGHT

Acceleration intent
  B released / B held

Jump intent
  A released / A held

Duration
  explicit number of emulator frames
```

This is better represented as short sequences of `ActionCommand` values than as an ever-growing catalog of named macros.

Conceptually:

```text
controller state u(t)
    = horizontal
    + A state
    + B state

current machine state x(t)
    ↓
short control sequence
    ↓
reachable future states
```

## Implication for V6 and later planners

The immediate V6 direction remains valid:

- coarse normal mode,
- precision mode when progress degrades or candidate states converge,
- shorter frame durations,
- NOOP / LEFT / A / LEFT+A / RIGHT / RIGHT+A primitives.

However, this manual suggests an important next refinement: avoid solving every new situation by adding another fixed macro.

The longer-term candidate generator should search short controller-state sequences such as:

```text
RIGHT+B 8f
→ RIGHT+A+B 8f
→ RIGHT+B 4f
→ RIGHT 4f
```

or:

```text
RIGHT+A 8f
→ RIGHT 4f
→ LEFT 4f
```

The exact sequences must still be evaluated against authoritative Mesen state.

## Planner design rule

Do not encode manual statements as machine truth.

Use them to constrain and organize the action model:

```text
Official manual
  = player-control semantics

Mesen + SMB1 decoder
  = actual machine/game truth

Planner
  = search over admissible controller sequences
```

This keeps the control model faithful to documented gameplay while preserving the project's authority boundary.

## Current design takeaway

For Fami Pixel, the strongest durable takeaway is:

```text
SMB1 planning should evolve from
fixed macro-action search

toward
short-horizon controller-sequence search
```

because jump height, airborne steering, and running speed are independently controllable and jointly determine Mario's reachable future states.
