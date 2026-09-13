# SMB1 Official Control Semantics

This note distills controller semantics from the official *Super Mario Bros.* instruction booklet (`CLV-P-NAAAE.pdf`) into durable guidance for Fami Pixel.

It is intentionally a **technical summary**, not a transcription of the manual. Mesen remains the execution authority; this document only records player-control semantics that are useful when designing action spaces, planners, and future environment adapters.

## Why this matters

The planner work exposed a structural limitation in fixed Mario action macros: several 30-frame candidates can produce the same forward progress while reaching materially different machine states, and later collapse into the same doomed trajectory.

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

A faithful controller model is:

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

## Current implementation: V7

V7 now implements this control model directly rather than postponing B support to a later experiment.

The durable action contract includes:

```text
NOOP
A
B
RIGHT
RIGHT+A
RIGHT+B
RIGHT+A+B
LEFT
LEFT+A
LEFT+B
LEFT+A+B
```

The planner uses two rates:

```text
coarse mode
  30-frame walk/run and jump/run-jump macros

precision mode
  4/8/12-frame controller primitives
  spanning horizontal intent, A state, and B state
```

Representative V7 coarse candidates include:

```text
RIGHT 30f
RIGHT+A 6f  -> RIGHT 24f
RIGHT+B 30f
RIGHT+A+B 6f -> RIGHT+B 24f
RIGHT+A+B 14f -> RIGHT+B 16f
RIGHT+A+B 24f -> RIGHT+B 6f
```

Precision search additionally covers short `NOOP`, `A`, `B`, LEFT/RIGHT, LEFT/RIGHT+A, LEFT/RIGHT+B, and LEFT/RIGHT+A+B commands. This allows run-up, jump-hold, and airborne steering to be searched as independent control dimensions.

V7 also accepts a prior test-report ZIP directly and can locate named regression fixtures (`pre-collapse`, `collapse-boundary`, `doomed`) without manually extracting or renaming `.mss` files.

All candidate trajectories remain counterfactual until their first root command is selected and committed against authoritative Mesen state.

## Secondary commentary references

A useful secondary reading is David Oxford's 2015 PoisonMushroom.Org article, “Let's Read the Original Super Mario Bros. Manual”:

https://poisonmushroom.org/2015/09/lets-read-the-original-super-mario-bros-manual/

This article is **not normative**. It is a retrospective walkthrough and commentary on the original manual, useful mainly for:

- navigating the manual by topic,
- preserving historical naming and context,
- noticing sections that deserve primary-source follow-up,
- identifying richer gameplay interactions for later environment tests.

Examples highlighted in the article include:

- the manual's control and world-layout explanations,
- Mario forms and historical terminology,
- explicit discussion of death conditions,
- enemy descriptions and behavior context,
- the “Bulldozer Attack” shell-interaction sequence,
- off-screen interaction quirks described in the manual.

Engineering implication:

The article reinforces that many SMB1 behaviors are **multi-stage interaction sequences**, not single button presses. The “Bulldozer Attack” is especially useful as a future regression or capability test because it requires timed contact, shell state, relative position, follow-up movement, and environmental interaction.

Authority remains:

```text
Official manual
  = primary documentary source for stated controls / rules

Secondary commentary
  = context, navigation, historical interpretation

Mesen + SMB1 decoder
  = executable machine truth
```

Any behavior inferred from the commentary should be checked against either the official manual or actual machine evidence before entering a planner contract.

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

For Fami Pixel, the durable direction is now implemented in V7:

```text
fixed macro-action search
        ↓
adaptive coarse / precision control
        ↓
short-horizon controller-sequence search
```

Jump hold, airborne steering, and running speed are independently controllable and jointly determine Mario's reachable future states.
