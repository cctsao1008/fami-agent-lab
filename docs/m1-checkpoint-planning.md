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

## What this is not

This first planner is not A*, MCTS, PPO, DQN, or a learned world model. It is the minimum real checkpoint-search mechanism needed before those consumers can be evaluated cleanly.

Future planners may replace the ranking/search strategy while retaining the same architecture:

```text
checkpoint + candidate action
→ predicted or real rollout
→ CandidateOutcome
→ selection
→ committed Mesen transition
```

A future forward model may reduce the cost of candidate evaluation, but its predictions must remain distinguishable from Mesen-grounded transitions.
