# Reward-aware SMB1 planning

> Fami Pixel should not only learn what to avoid; it should also understand what is worth pursuing.

## Goal

Extend the live SMB1 controller from hazard-aware reactive control to reward-aware goal-directed behavior without changing the authority boundary.

The planner should continuously answer two different questions:

1. **What must Mario avoid?**
2. **What is worth pursuing?**

Those questions are related, but they are not the same problem and should not be collapsed into one opaque score.

```text
Authoritative Mesen state
        |
        +--> hazard radar ------+
        |                       |
        +--> reward radar ------+--> state-aware utility --> shadow Mesen verify --> action
        |                       |
        +--> learned surrogate -+
```

Mesen remains authoritative for world state, collision, collection, player capability changes, terminal events, and framebuffer output.

## Why hazard-only control is incomplete

The live radar introduced for V15-V18 can already identify near-field hazards such as enemies, gaps, and raised obstacles. That supports a strong reactive safety loop, but it is incomplete as an agent model.

A human player does not only avoid danger. They also make small local detours to gain future capability or reduce later risk.

Examples:

- Small Mario should usually value a safely reachable Mushroom highly.
- A Star can justify a short safe detour because temporary invincibility changes the meaning of nearby enemies.
- A Fire Flower is more valuable when Mario is not already fiery.
- A 1-Up is useful, but it should not justify a dangerous route that threatens the current run.
- A reward that requires entering a known pit or accepting an immediate Mesen death outcome must be rejected.

This is the transition from **reactive safety** to **goal-directed behavior**.

## Native SMB1 power-up semantics

SMB1 stores the active power-up object inside the enemy-object machinery, but semantically it is not a hostile enemy.

For the validated SMB1 disassembly family:

- `PowerUpObject = $2e`
- the active power-up uses enemy-object slot 5,
- `PowerUpType = $39`,
- `PowerUpType` values are:
  - `0` — Mushroom,
  - `1` — Fire Flower,
  - `2` — Star,
  - `3` — 1-Up,
- `PlayerStatus = $0756`,
- `StarInvincibleTimer = $079f`.

The existing radar must therefore classify object-buffer entries by **semantic role**, not merely by storage location.

An active slot with `Enemy_ID == $2e` belongs to the reward channel. It must not contribute to `nearest_enemy_dx` or trigger hostile-enemy avoidance.

## Perception contract

### Hazard radar

The hazard channel contains scene elements that can directly threaten traversal:

- hostile active enemies,
- gaps,
- raised collision obstacles,
- future hazard types added explicitly later.

Representative payload:

```json
{
  "nearest_enemy_dx": 74,
  "nearest_gap_dx": null,
  "nearest_obstacle_dx": 96
}
```

### Reward radar

The reward channel contains active positive targets decoded from native SMB1 state:

```json
{
  "nearest_reward_dx": 58,
  "nearest_reward_type": "star",
  "rewards": [
    {
      "slot": 5,
      "type": "star",
      "x": 1616,
      "y": 144,
      "dx": 58,
      "state": 128
    }
  ]
}
```

The first implementation intentionally focuses on the active power-up object. Hidden blocks and unopened question blocks are not yet treated as reward targets because their future contents are not represented as an active collectible.

## Player capability state

Reward value depends on Mario's current capability state.

The perception surface should expose at least:

- `player_status`,
- `star_invincible_timer`,
- derived `invincible` state.

This allows the planner to distinguish cases such as:

```text
small Mario + mushroom       -> high marginal value
super Mario + mushroom       -> lower marginal value
non-fiery Mario + fireflower -> high value
fiery Mario + fireflower     -> low marginal value
star active + enemy ahead    -> reduced enemy hazard cost
```

The capability state is authoritative SMB1 RAM state, not a learned estimate.

## State-dependent reward utility

Reward utility must remain explicit and auditable.

A first bounded heuristic is sufficient:

```text
utility = progress_value - hazard_cost + reward_value
```

The reward term is a function of both reward type and player capability.

Illustrative ordering:

```text
Star         very high
Mushroom     high when small, lower when already upgraded
Fire Flower  high when not fiery, low when already fiery
1-Up         medium/high
```

Distance should discount reward value. A nearby reward should matter more than one at the edge of the lookahead window.

The utility is a **proposal preference**, not authority. A candidate that Mesen predicts will immediately die is never rescued by reward value.

## Safe pursuit policy

Reward seeking must remain receding-horizon and scene-driven.

The planner should follow this order:

```text
current authoritative scene
        |
        +--> immediate Mesen safety gate
        |
        +--> current hazard response
        |
        +--> reward opportunity
        |       |
        |       +--> safely reachable?
        |       +--> capability-dependent value?
        |       +--> short bounded detour worth it?
        |
        +--> learned transition/risk prior
        |
        +--> choose candidate
```

Priority rules:

1. Immediate Mesen death evidence always dominates reward pursuit.
2. Current near-field gap/enemy/obstacle handling remains active.
3. Reward pursuit is allowed only inside the remaining immediate-safe candidate set.
4. Reward preference should be bounded; it must not create an irreversible long detour or replace the no-progress watchdog.
5. If no reward is visible, behavior reduces to the existing hazard-aware planner.

## Star-specific semantics

Star collection changes the control problem rather than merely adding score.

While `StarInvincibleTimer > 0`:

- ordinary hostile-enemy collision cost should be strongly reduced,
- gaps and terrain hazards remain fully dangerous,
- the controller should still avoid trajectories that Mesen shows as terminal,
- reward pursuit remains bounded by the same safety gate.

This is the first example of **capability-conditioned hazard semantics** in Fami Pixel.

## Learned surrogate role

The tiny learned surrogate remains a cheap transition/risk prior.

It may contribute:

- predicted progress,
- delayed-risk score,
- no-progress score,
- later, reward-collection likelihood if trained explicitly.

It does not define:

- whether a power-up exists,
- whether it was collected,
- whether Mario is invincible,
- whether Mario died,
- whether the level completed.

Those remain native Mesen/SMB1 facts.

## Mesen authority boundary

Reward awareness does not weaken the existing authority rule.

Mesen remains the source of truth for:

- active object state,
- power-up type,
- Mario capability state,
- collisions,
- reward collection,
- state transitions,
- death and level completion,
- framebuffer evidence.

The planner may prefer a reward-seeking candidate, but the resulting state is accepted only because it is executed or validated by Mesen.

## Web UI

The live UI should expose hazard and reward channels separately.

```text
HAZARD
Enemy       74 px ->
Gap          --
Obstacle    96 px ->

REWARD
Star        58 px ->
Mushroom     --

[M] -------*------E---------O

Pursuit      star
Reward util  0.82
Invincible   no
```

The visual separation matters: the operator should be able to tell whether Mario is jumping because something is dangerous or because something desirable is being pursued.

## Evidence and replay

The normal live-run evidence should capture reward-aware decisions in `timeline.jsonl` and terminal summaries.

At minimum record:

- nearest reward type and distance,
- visible reward objects,
- player status,
- star invincibility timer,
- reward utility,
- pursuit target,
- pursuit mode,
- capability changes observed after collection.

This makes reward-seeking behavior auditable from the same flight-recorder path already used for hazard and watchdog debugging.

## V19 implementation boundary

V19 is intentionally bounded.

It should:

1. distinguish `PowerUpObject` from hostile enemy objects,
2. decode `PowerUpType`, `PlayerStatus`, and `StarInvincibleTimer`,
3. expose reward objects in the native radar payload,
4. add a small explicit state-dependent reward utility,
5. allow a safely reachable power-up to alter candidate preference,
6. expose reward telemetry in the Web UI and run evidence,
7. preserve V18 watchdog behavior and all existing Mesen safety gates.

V19 should **not**:

- hard-code full World 1-1 scripts,
- use screen scraping as the primary reward sensor,
- bypass immediate Mesen safety evidence,
- train an end-to-end reward policy,
- infer unopened block contents as if they were already visible rewards.

## Later extensions

Once V19 is validated, the same semantic split can support richer goals:

- coin collection,
- block-hit opportunities,
- flagpole-quality scoring,
- temporary power-state-aware routing,
- explicit trade-offs between time, safety, score, and capability.

The durable architectural principle remains simple:

> Hazards explain what the agent should avoid. Rewards explain what the agent should pursue. Mesen decides what actually happened.

## References

- SMB1 public disassembly family: `https://6502disassembly.com/nes-smb/SuperMarioBros.html`
- Issue #28: reward-aware SMB1 perception and safe power-up seeking
