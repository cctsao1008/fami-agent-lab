# M0 repeated frame-step semantics

This note records the current, source-audited understanding of repeated `Step(CpuType::Nes, 1, StepType::PpuFrame)` calls against the pinned Mesen CE revision.

## Important corrections

The first repeated-step probe used only `IsExecutionStopped()` as a completion signal. That was insufficient: after the first step, the debugger is already stopped, so an immediately observed `true` can be the previous boundary rather than evidence that a new frame ran.

A later workaround called `ResumeExecution()` after installing the next step request. Source audit shows that this is also incorrect. `ResumeExecution()` reaches `Debugger::Run()`, and `Debugger::Run()` calls each CPU debugger's `Run()` method. For NES, `NesDebugger::Run()` replaces the current `StepRequest` with a new empty request. Therefore:

```text
Step(Nes, 1, PpuFrame)
ResumeExecution()
```

can erase the PPU-frame request that was just installed.

## Pinned-source behavior

`Step()` itself is wrapped by `DebugBreakHelper`. For a host-thread call, the helper temporarily requests a debugger break, waits until execution is stopped, installs the requested step, and releases that temporary break in its destructor.

The adapter therefore keeps the native operation minimal:

```text
Step(Nes, 1, PpuFrame)
```

and does not append `ResumeExecution()`.

## Completion evidence

`IsExecutionStopped()` alone is not accepted as proof of a new frame. M0 now uses an independent machine-state witness when validating repeated stepping. For the SMB workload, `examples/mesen_smb_frame_witness_smoke.py` requires SMB's `FrameCounter` at `$0009` to change for every requested PPU-frame step.

The same probe observes `RawJoypad1Bits` at `$074A` rather than `SavedJoypadBits` at `$06FC`. `$06FC` belongs to SMB's gameplay control path and is not a valid boot/title-screen input witness; the M0 action sequence is currently issued immediately after ROM load, before gameplay has been entered.

Until an emulator-native frame counter is bound, the SMB frame counter is the workload-specific witness used to distinguish a real frame advance from a stale debugger stopped state.
