# M0 native NES input provider

The stock Mesen debugger `SetInputOverrides()` path is not a sufficient deterministic agent-input primitive for fami-pixel. In the pinned Mesen CE source, the NES override handler only applies an override when `DebugControllerState::HasPressedButton()` is true, so an all-released controller state cannot be represented reliably through that API.

The fami-pixel MesenCE fork therefore adds a narrow native input surface implemented through Mesen's existing `IInputProvider` mechanism.

## Native byte contract

`FamiPixelSetNesControllerState(port, buttons)` uses the same byte layout as `NesController::ToByte()`:

```text
bit 0  A
bit 1  B
bit 2  Select
bit 3  Start
bit 4  Up
bit 5  Down
bit 6  Left
bit 7  Right
```

Examples:

```text
RELEASE   = 0x00
A         = 0x01
START     = 0x08
RIGHT     = 0x80
RIGHT + A = 0x81
```

This representation is the emulator-side controller representation. Game-specific RAM encodings may use a different bit order and must remain outside the generic Mesen adapter.

## Injection path

```text
Python
  -> FamiPixelSetNesControllerState()
  -> FamiPixelInputProvider
  -> BaseControlManager::UpdateInputState()
  -> emulated NesController
```

`FamiPixelGetNesControllerState(port)` returns the actual current byte stored by the emulated NES controller and is used as the first independent witness that the provider state reached the controller device.

The controller witness is deliberately separate from SMB RAM observation. First prove deterministic emulator-level input delivery; only then use game-specific RAM to prove that a particular game consumed and interpreted that input.
