# Forward-Model Trajectory Planning

Fami Pixel should plan **outcomes**, not accumulate more local `if hazard -> macro` rules.

The durable authority boundary is:

> Semantic perception proposes what matters. Learned models rank cheaply. Mesen proves what actually happens.

This design is the implementation contract for GitHub issue #32.

## Why change the planner

The V15–V22 line proved that live native perception, parallel shadow Mesen workers, a tiny learned surrogate, watchdog recovery, evidence recording, and Windows process containment all work as useful components.

It also exposed the limit of short fixed macros:

- a reward can be recognized but still be ignored because every candidate mostly moves right;
- an enemy-only landing corridor can be called safe while the real trajectory falls into a pit;
- adding another threshold does not answer what happens at the end of a jump.

The planner therefore moves from **macro scoring** toward **receding-horizon forward-model search**.

## Architecture

```text
Authoritative Mesen state
        |
        v
Semantic world model
  - Mario state/capabilities
  - hostile enemies/clusters
  - terrain/gap validity
  - reward objects/targets
        |
        v
Objective manager
  SURVIVE / COLLECT / PROGRESS / RECOVER
        |
        v
Bounded action-chunk generator
        |
        v
Cheap learned ordering/pruning
        |
        v
Parallel Mesen save-state branches
        |
        v
Event-horizon trajectory evaluation
        |
        v
Lexicographic outcome selection
        |
        v
Execute only first 2-4 frames
        |
        +---- observe authoritative state and replan
```

## Mesen is the forward model

Fami Pixel already has exact native save/load-state support in the pinned Mesen CE ABI. A branch is therefore evaluated by:

1. restoring one authoritative checkpoint;
2. applying a bounded action sequence;
3. stepping real emulation frames;
4. reading SMB1 structured observations and semantic radar;
5. stopping on a meaningful event rather than an arbitrary fixed score horizon.

No hand-written ballistic model is allowed to override the branch outcome.

## Event horizon

A trajectory evaluation terminates when the first authoritative event of interest occurs:

- death;
- level completion;
- target reward collection / capability transition;
- landing after the branch has been airborne;
- hard maximum horizon.

A candidate may contain a short planned prefix followed by a bounded tail action (initially neutral/coast). The hard horizon prevents an unresolved branch from running indefinitely.

This changes the question from:

> How much X progress did this macro make after 12 frames?

into:

> If I begin this maneuver now, do I die, land safely, collect the target, or remain unresolved?

## Outcome classes

Selection should be lexicographic before scalar tie-breaking. A first ordering is:

```text
WIN
SAFE_REWARD_COLLECTED
SAFE_CAPABILITY_GAIN
SAFE_LANDING_WITH_PROGRESS
SAFE_PROGRESS
HORIZON_UNRESOLVED
DEATH
```

Within the same class, progress, target-distance reduction, landing state, model risk, and compute cost may be used as tie-breakers.

A learned risk estimate must never override direct Mesen evidence that another trajectory is safe and superior.

## Action vocabulary

The old forward-only candidate pool is insufficient for interception. Keep branching bounded, but include maneuver primitives such as:

- `RIGHT+B` run;
- `RIGHT` / coast;
- `NOOP` / release;
- jump press/hold/release chunks;
- short `LEFT` / braking/backtrack chunks.

Do not expand every possible NES button combination. Branching factor must stay explicit and measurable.

## Reward interception

Reward awareness is not reward seeking until the simulated branch proves useful target interaction.

For a selected reward target, trajectory evaluation should prefer in this order:

1. authoritative collection / capability change;
2. safe target-distance reduction;
3. safe landing that preserves another interception opportunity;
4. ordinary forward progress.

Temporary loss of X progress is valid for a bounded `COLLECT` objective.

The objective must be sticky for a short bounded interval so the controller does not notice a Star, choose one corrective frame, then immediately revert to pure progress.

## Landing and pits

The fixed `+96..160 px` enemy corridor remains useful telemetry, not final truth.

True trajectory safety comes from the branch outcome:

- did Mario die or enter lose-life?
- did he become airborne and then land?
- what was the actual landing X/Y/state?
- was the target reward collected?

Terrain radar still matters as a cheap prior and diagnostic. Its state should eventually be explicit `SAFE / GAP / UNKNOWN`; `UNKNOWN` must not silently become safe.

## Learned surrogate

The current tiny surrogate remains useful as a search heuristic:

```text
many candidate prefixes
        -> tiny model ordering/pruning
        -> top-K Mesen branches
        -> authoritative selection
```

This is intentionally different from letting the model decide the final action.

## Regression scenarios

Development should stop replaying all of World 1-1 for every narrow bug. Local save states remain untracked under `build/` and define deterministic scenario roots such as:

```text
first-goomba
multi-goomba-landing
small-gap
wide-gap
pit-x2587
staircase-stall
star-intercept
mushroom-intercept
level-opening
```

Each scenario needs a machine pass condition. Examples:

```text
star-intercept:
  star capability transition observed
  death == false

mushroom-intercept:
  player status increased
  death == false

pit-x2587:
  previous fatal region crossed
  death == false
```

Save-state binaries and ROM content must remain local and untracked.

## Rollout evidence

For every evaluated branch, persist enough metadata to reconstruct the decision:

- objective and target;
- action chunks;
- branch root frame/X;
- event-horizon reason;
- frames simulated;
- terminal outcome;
- start/end/max X;
- airborne/landing evidence;
- capability transition;
- reward collection evidence;
- model heuristic values;
- selected prefix and replanning reason.

## Implementation order

1. **Event-horizon branch evaluator** with unit tests.
2. **Local scenario manifest/harness** using Mesen save-state files under `build/`.
3. **Bounded beam search** over small action chunks.
4. Learned surrogate ordering/pruning.
5. Parallelize branch evaluation using the existing shadow-worker/process-isolation machinery.
6. Integrate into live receding-horizon authority with short-prefix execution.
7. Compare beam vs best-first/A*-like vs MCTS only after the bounded baseline is measurable.

## Relationship to existing tracks

- #28 is the reward-interception acceptance track.
- #30 is the multi-enemy/landing acceptance track.
- #31 is the terrain-validity/pit acceptance track.
- #32 owns the shared model-based planning architecture.

A World 1-1 run is integration evidence, not sufficient proof by itself. Narrow deterministic scenarios must pass before a behavior is called solved.
