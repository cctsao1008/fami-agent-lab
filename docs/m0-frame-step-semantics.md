# M0 repeated frame-step semantics

This note records the corrected host-side contract for repeated `Step(CpuType::Nes, 1, StepType::PpuFrame)` calls against the pinned Mesen CE revision.

## Important correction

A single PPU-frame step from a running emulator is sufficient to reach a stopped debugger state. However, once the debugger is already stopped at that boundary, installing another step request does not itself advance execution.

Therefore repeated frame stepping must use this host-side sequence:

```text
if debugger is running:
    Step(Nes, 1, PpuFrame)
    wait until IsExecutionStopped() == true

if debugger is already stopped:
    Step(Nes, 1, PpuFrame)
    ResumeExecution()
    wait until IsExecutionStopped() == true
```

The previous repeated-step probe incorrectly treated an already-true `IsExecutionStopped()` value as proof that each later frame had executed. The near-zero latencies observed after the first frame were the signal that no additional emulation work was occurring.

`MesenCore.step_ppu_frame()` now records the pre-call stopped state and issues `ResumeExecution()` after installing the next PPU-frame step request when necessary.

This correction must be used for controller and RAM-observation probes; otherwise action changes can be applied in Python while the emulated machine remains frozen on the same frame.
