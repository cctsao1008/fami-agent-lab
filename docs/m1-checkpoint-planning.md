# M1 Checkpoint Planning

This note records the first planning mechanism in Fami Pixel that evaluates alternative SMB1 actions against authoritative Mesen execution.

## Boundary

The planner is not a second game engine and is not a source of machine truth.

```text
Mesen checkpoint
    ↓
restore candidate A
    ↓
execute real frames
    ↓
observe result
    ↓
restore checkpoint
    ↓
execute candidate B
    ↓
observe result
    ↓
rank outcomes
    ↓
restore checkpoint
    ↓
commit one selected action sequence in Mesen
```

Every candidate outcome is therefore counterfactual only until the selected candidate is replayed and committed against Mesen.

## Validated prerequisite

The save/load/continue roundtrip has been machine-validated on SMB1 World 1-1:

```text
GameEntry : PASS NativeFrame=196 X=40
SaveState : PASS frame=316 X=189 bytes=14027
Advance   : PASS frame=396 X=295 delta=106
LoadState : PASS frame=316 X=189 Engine=0x08
Resume    : PASS frame=317 X=190
Checkpoint: PASS save/load/continue roundtrip
Supervisor: PASS
```

This establishes that a checkpoint can restore the authoritative frame, Mario position, and engine state and then continue synchronous execution.

## First bounded planner

`examples/mesen_smb_checkpoint_planner.py` implements a small receding-horizon search. At each decision it evaluates four equal 30-frame action macros:

```text
cruise
  RIGHT × 30

tap_jump
  RIGHT+A × 6
  RIGHT   × 24

medium_jump
  RIGHT+A × 14
  RIGHT   × 16

long_jump
  RIGHT+A × 24
  RIGHT   × 6
```

Equal horizon length matters: ordinary candidates are compared by observed forward progress per executed frame instead of rewarding a candidate merely for consuming more frames.

## Outcome ranking

The pure contract in `src/fami_pixel/games/smb1/planning.py` records:

```text
PlanCandidate
CandidateOutcome
CandidateTerminal
score_candidate(...)
select_best_candidate(...)
```

Ranking is deliberately small and auditable:

```text
LEVEL_COMPLETED  >> highest
FLAGPOLE_SLIDE   >> strong precursor bonus
safe progress    >> progress / elapsed frames
DIED             >> rejected by dominant negative score
```

`0x04 FlagpoleSlide` is used only as a planning preference because it is the verified precursor to the M1 terminal witness `0x05 PlayerEndLevel`; it does not redefine `LEVEL_COMPLETED`.

## First machine run of the one-ply planner

The first complete machine run validated that checkpoint search itself works. The planner avoided locally fatal choices at several points:

```text
Decision 007 @ X=279
  cruise    -> death
  tap_jump  -> safe +45
  selected  -> tap_jump

Decision 017 @ X=686
  cruise    -> death
  long_jump -> safe +45
  selected  -> long_jump
```

However, the run also exposed a genuine one-ply planning failure rather than an emulator or event-contract failure.

At `X=1226`, all four 30-frame candidates produced the same immediate progress (`dx=40`). Deterministic tie-breaking therefore selected `cruise`. The committed state reached `X=1266`, after which every 30-frame candidate produced zero immediate progress for several decisions. Eventually every candidate entered a terminal death path:

```text
Decision 029 frame=1036 X=1226
  cruise/tap/medium/long -> dx=40, all tied
  selected cruise

Decision 030..034 X=1266
  all candidates -> dx=0, terminal=none

Decision 035 X=1266
  all candidates -> death
```

This is a horizon/state-aliasing problem: equal immediate progress hid materially different future states. The one-ply planner could not distinguish an action that merely reaches the same X from one that reaches it with a better future trajectory.

The earlier transient `FamiPixelStepFrame` timeout seen immediately after the title screen did not reproduce on the following run; the full planner then executed through 35 decisions. It is therefore recorded separately from the planning failure and is not currently treated as the main blocker.

## Adaptive two-ply planner

`examples/mesen_smb_checkpoint_planner_v2.py` extends the experiment without adding hard-coded SMB1 hazard coordinates.

The default remains one-ply receding-horizon search. A second ply is enabled only when the current one-ply result is ambiguous and degraded relative to previously observed safe progress, or when no candidate makes progress:

```text
normal state
  → evaluate 4 first-ply candidates
  → commit best first-ply action

ambiguous/degraded state
  → evaluate 4 first-ply candidates
  → from each safe endpoint, checkpoint again
  → evaluate the same 4 candidate continuations
  → rank each first action by its best real second-ply continuation
  → restore root checkpoint
  → commit only the selected first action
```

This preserves the receding-horizon architecture and Mesen authority while giving the planner additional lookahead.

### Machine result: V2 still fails on delayed consequences

The corrected V2 machine run reached the same critical region cleanly and removed the frame-step regression from consideration. It proved that two-ply search itself works, but that 60 frames are still too short for this failure mode.

Key machine evidence:

```text
Decision 029 frame=1036 X=1226
  one-ply: all candidates dx=40
  two-ply: all first actions score=0.667, best continuation dx2=0
  selected cruise

Decision 030..033 X=1266
  all immediate candidates dx=0
  all two-ply branches remain non-terminal with zero progress

Decision 034 X=1266
  second-ply continuations finally see death

Decision 035 X=1266
  every first-ply candidate is death
```

The important result is that `DIED` arrives substantially later than the state first becomes unrecoverable. A terminal-only 30- or 60-frame evaluator can therefore enter a doomed trajectory while all visible short-horizon scores are still tied.

This is not evidence that checkpoint search is wrong; it is evidence that a fixed shallow horizon is the wrong abstraction for delayed terminal consequences.

## V3: multi-rate rollout-tail safety audit

`examples/mesen_smb_checkpoint_planner_v3.py` keeps the 30-frame committed control cadence but adds a slower bounded future rollout audit.

```text
fast loop
  checkpoint
  → evaluate four 30-frame action macros
  → normally commit one macro

safety audit
  checkpoint
  → candidate macro (30 frames)
  → fixed RIGHT-only rollout tail (default 120 frames)
  → observe delayed death / flagpole / future progress
  → restore checkpoint
  → repeat for each candidate
  → commit only the selected first 30-frame macro
```

The tail is counterfactual. It never becomes the authoritative episode trajectory. Its purpose is to ask a narrower planning question:

> If this first action is chosen, does a simple continuation expose a delayed terminal consequence that the immediate horizon cannot see?

The audit is multi-rate rather than continuous. By default it runs every four decisions and is also triggered by no-progress or tied-degraded outcomes. With a 30-frame commit cadence, the periodic audit occurs every 120 committed frames and itself looks 120 frames beyond each candidate macro.

### Machine result: V3 exposes open-loop tail policy mismatch

The V3 machine run was stable through 47 decisions and moved well beyond the V2 failure region, reaching `X=1532`. It also exposed a more important modeling error in the audit itself.

At `X=144`, every root candidate followed by the fixed RIGHT-only tail was predicted to die at `X=295`. That prediction is not useful for root-action ranking, because the real planner later reaches `X=279`, sees that `cruise` is fatal, and selects `tap_jump` successfully. The V3 tail therefore evaluates a future controller that the committed system would not actually use.

The same mismatch appears around the next major hazard:

```text
Decision 032 frame=1126 X=1068
  audited medium_jump/long_jump -> +225 over 150 frames
  planner selects medium_jump

Decision 042 frame=1426 X=1518
  all audited futures -> only +14 over 150 frames

Decision 043 frame=1456 X=1532
  all audited futures -> death after 144 frames

Decision 047 frame=1576 X=1532
  all immediate candidates -> death
```

So V3 proves that long-horizon audit can reveal delayed consequences, but also that an **open-loop fixed-input tail is the wrong future policy model**. The audit must preserve feedback if it is intended to estimate the future controllability of a root action.

## V4: closed-loop rollout-tail safety audit

`examples/mesen_smb_checkpoint_planner_v4.py` replaces V3's RIGHT-only tail with a bounded closed-loop checkpoint policy.

For each audited root candidate:

```text
root checkpoint
  → execute root candidate (30 frames)
  → tail checkpoint
      → evaluate cruise / tap / medium / long
      → commit best tail macro inside counterfactual branch
  → tail checkpoint
      → evaluate cruise / tap / medium / long
      → commit best tail macro inside counterfactual branch
  → repeat for bounded tail_decisions
  → score the resulting root candidate future
```

The default is four tail decisions. An audited root action is therefore evaluated through up to 150 frames total (`30 + 4×30`) while retaining feedback every 30 frames.

This remains model-free and Mesen-grounded. All nested tail transitions are counterfactual. After the four root candidates are ranked, the planner restores the original root checkpoint and commits only the selected first 30-frame macro to the authoritative episode.

The key distinction is:

```text
V3 audit: root action + open-loop RIGHT tail
V4 audit: root action + closed-loop checkpoint-policy tail
```

V4 therefore evaluates future **controllability under the planner's own action vocabulary**, rather than survival under a deliberately crippled fixed continuation.

This deliberately avoids:

- hard-coded World 1-1 X coordinates,
- redefining counterfactual branches as machine truth,
- requiring a learned world model,
- full-width exponential search at every committed decision.

## What this is not

These planners are not A*, MCTS, PPO, DQN, or a learned world model. They are the minimum real checkpoint-search mechanisms needed before those consumers can be evaluated cleanly.

Future planners may replace the ranking/search strategy while retaining the same architecture:

```text
checkpoint + candidate action
→ predicted or real rollout
→ CandidateOutcome
→ selection
→ committed Mesen transition
```

A future forward model may reduce the cost of candidate evaluation, but its predictions must remain distinguishable from Mesen-grounded transitions.
