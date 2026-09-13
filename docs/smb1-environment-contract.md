# SMB1 Environment Contract

This document defines the durable Super Mario Bros. environment boundary used by Fami Pixel above the generic Mesen adapter.

The contract deliberately keeps **machine authority in Mesen**, **game interpretation in the SMB1 decoder**, and future **policy / learning logic outside both**.

## Design sources

Three external references informed this contract:

- Alberto-00/Super-Mario-Bros-AI — reinforcement-learning environment design, discrete controller actions, reward shaping, episode metrics, and comparison of Q-learning / SARSA / DQN / DDQN.
- Thenjiwe Kubheka, “Build an AI Model to Play Super Mario” — simplified discrete action space, grayscale/frame-stack preprocessing, and PPO as a practical baseline.
- d12/Super-Mario-Neural-Net-AI — emulator/agent separation and hybrid observation using both a compressed game image and Mario X read directly from emulator RAM.

These are **design references, not backend dependencies**. Fami Pixel does not adopt `gym-super-mario-bros`, `nes-py`, Nintaco, HTTP hooks, or their emulator abstractions. The authoritative backend remains the pinned Mesen CE interop path.

References:

- https://github.com/Alberto-00/Super-Mario-Bros-AI
- https://python.plainenglish.io/build-an-ai-model-to-play-super-mario-7607b1ec1e17
- https://github.com/d12/Super-Mario-Neural-Net-AI

## Authority boundary

```text
Mesen CE
  │
  ├─ native frame id
  ├─ controller state
  ├─ NES RAM / CPU / PPU state
  └─ canonical 256x240 framebuffer
        │
        ▼
SMB1 decoder
  │
  ├─ world / level
  ├─ Mario absolute X
  ├─ Mario Y
  ├─ player state
  └─ SMB controller witness
        │
        ▼
SMB1 environment projection
        │
        ├─ Observation
        ├─ Action
        ├─ Episode state
        ├─ Reward inputs
        └─ Metrics
```

The environment is a projection. It does not become the authority for emulator state, game state, or learned state.

## Observation

The first stable observation contract is hybrid rather than pixel-only:

```text
Smb1Observation
  frame_id
  framebuffer        optional for state-only policies, canonical when present
  world
  level
  mario_x_abs
  mario_y
  player_state
  raw_joypad
  terminal_reason    optional
```

This preserves both observation families:

1. **structured game state** for deterministic testing, reward calculation, scripted policies, and compact ML inputs;
2. **native framebuffer** for CNN / visual-policy experiments.

A model may consume either projection or both. The environment must not force image inference for state already available authoritatively from RAM.

## Action

Start with a deliberately small discrete action vocabulary:

```text
NOOP
RIGHT
RIGHT_A
LEFT
A
```

Additional combinations such as `RIGHT_B`, `RIGHT_A_B`, or `LEFT_A` may be introduced by an experiment when justified. The environment translates each logical action into an exact NES controller byte and applies it through the existing native Mesen input provider.

The action contract is frame-duration explicit:

```text
ActionCommand
  action
  frame_count
```

No wall-clock key hold is part of the environment contract.

## Episode lifecycle

```text
reset machine
→ reach title menu
→ START
→ wait for World 1-1 player-control state
→ emit initial observation
→ action / observation loop
→ terminal condition
→ trace + metrics
```

Initial terminal conditions should include at least:

```text
death
level_complete
timeout
explicit_reset
```

Reset is an environment/supervisory operation, not an extra controller bit invented by the policy interface.

## Reward inputs

Reward calculation belongs above the decoder and must be reconstructable from trace data. The initial reward inputs are:

```text
progress        = delta(mario_x_abs)
death           = terminal death event
level_complete  = terminal completion event
time_cost       = elapsed environment steps / frames
```

Coins, score changes, power-ups, enemy interactions, and damage can be added later after their SMB1 state sources are explicitly verified.

The baseline reward should remain simple enough to audit:

```text
reward = progress_weight * delta_x
       + completion_bonus
       - death_penalty
       - time_penalty
```

Reward shaping is an experiment policy, not machine truth.

## Trace requirement

Every environment transition must remain reducible to the lower-level evidence already established in M0:

```text
frame_id
logical action
native controller byte
SMB decoded state before/after
optional framebuffer reference
reward components
terminal state
```

This keeps training and evaluation replayable without making the ML framework the source of truth.

## Baseline policy path

The first learned baseline should be intentionally conventional:

```text
SMB1 Environment
→ simplified discrete action space
→ optional grayscale / frame stack for visual policy
→ PPO or DQN baseline
→ trace / metric evaluation
```

Stable-Baselines3 is a reasonable baseline implementation for early experiments, but it is an adapter-level choice rather than part of the environment contract.

## M0 machine witness

The contract is grounded by the verified SMB1 machine path:

```text
START → World 1-1
RIGHT x60
RIGHT+A x10
RELEASE x2
```

Observed result:

```text
Title menu:  NativeFrame=33, OperMode=0, Task=3
Game entry:  NativeFrame=196, OperMode=1, Engine=0x08, World 1-1
Mario X:     40 → 117  (delta +77)
Jump:        observed (11 non-ground frames)
Y range:     0x8A..0xB0
Release:     SMB joypad returned to 0x00
```

This establishes that Fami Pixel can enter SMB gameplay, drive actual Mario movement/jump behavior, and observe resulting game state directly rather than merely inject controller bytes.
