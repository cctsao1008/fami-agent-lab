"""Verified NES configuration ABI for the pinned Mesen CE revision.

Only the pieces required to configure a standard NES controller are exposed as
Python helpers, but the ctypes structures mirror the complete native NesConfig
layout because GetNesConfig/SetNesConfig pass that structure by value.
"""

from __future__ import annotations

import ctypes

from .loader import MesenCore, MesenLoadError


CONTROLLER_TYPE_NONE = 0
CONTROLLER_TYPE_NES_CONTROLLER = 10


class KeyMapping(ctypes.Structure):
    _fields_ = [
        ("A", ctypes.c_uint16),
        ("B", ctypes.c_uint16),
        ("X", ctypes.c_uint16),
        ("Y", ctypes.c_uint16),
        ("L", ctypes.c_uint16),
        ("R", ctypes.c_uint16),
        ("Up", ctypes.c_uint16),
        ("Down", ctypes.c_uint16),
        ("Left", ctypes.c_uint16),
        ("Right", ctypes.c_uint16),
        ("Start", ctypes.c_uint16),
        ("Select", ctypes.c_uint16),
        ("U", ctypes.c_uint16),
        ("D", ctypes.c_uint16),
        ("TurboA", ctypes.c_uint16),
        ("TurboB", ctypes.c_uint16),
        ("TurboX", ctypes.c_uint16),
        ("TurboY", ctypes.c_uint16),
        ("TurboL", ctypes.c_uint16),
        ("TurboR", ctypes.c_uint16),
        ("TurboSelect", ctypes.c_uint16),
        ("TurboStart", ctypes.c_uint16),
        ("GenericKey1", ctypes.c_uint16),
        ("CustomKeys", ctypes.c_uint16 * 100),
    ]


class KeyMappingSet(ctypes.Structure):
    _fields_ = [
        ("Mapping1", KeyMapping),
        ("Mapping2", KeyMapping),
        ("Mapping3", KeyMapping),
        ("Mapping4", KeyMapping),
        ("TurboSpeed", ctypes.c_uint32),
    ]


class ControllerConfig(ctypes.Structure):
    _fields_ = [
        ("Keys", KeyMappingSet),
        ("Type", ctypes.c_int),
    ]


class OverscanDimensions(ctypes.Structure):
    _fields_ = [
        ("Left", ctypes.c_uint32),
        ("Right", ctypes.c_uint32),
        ("Top", ctypes.c_uint32),
        ("Bottom", ctypes.c_uint32),
    ]


class NesConfig(ctypes.Structure):
    _fields_ = [
        ("Port1", ControllerConfig),
        ("Port2", ControllerConfig),
        ("ExpPort", ControllerConfig),
        ("Port1SubPorts", ControllerConfig * 4),
        ("ExpPortSubPorts", ControllerConfig * 4),
        ("MapperInput", ControllerConfig),
        ("LightDetectionRadius", ctypes.c_uint32),
        ("AutoConfigureInput", ctypes.c_bool),
        ("Region", ctypes.c_int),
        ("EnableHdPacks", ctypes.c_bool),
        ("DisableGameDatabase", ctypes.c_bool),
        ("FdsAutoLoadDisk", ctypes.c_bool),
        ("FdsFastForwardOnLoad", ctypes.c_bool),
        ("FdsAutoInsertDisk", ctypes.c_bool),
        ("VsDualVideoOutput", ctypes.c_int),
        ("VsDualAudioOutput", ctypes.c_int),
        ("SpritesEnabled", ctypes.c_bool),
        ("BackgroundEnabled", ctypes.c_bool),
        ("ForceBackgroundFirstColumn", ctypes.c_bool),
        ("ForceSpritesFirstColumn", ctypes.c_bool),
        ("RemoveSpriteLimit", ctypes.c_bool),
        ("AdaptiveSpriteLimit", ctypes.c_bool),
        ("EnablePalBorders", ctypes.c_bool),
        ("UseCustomVsPalette", ctypes.c_bool),
        ("NtscOverscan", OverscanDimensions),
        ("PalOverscan", OverscanDimensions),
        ("ConsoleType", ctypes.c_int),
        ("DisablePpuReset", ctypes.c_bool),
        ("AllowInvalidInput", ctypes.c_bool),
        ("DisableGameGenieBusConflicts", ctypes.c_bool),
        ("DisableFlashSaves", ctypes.c_bool),
        ("OverwriteOriginalRom", ctypes.c_bool),
        ("EnableOamDecay", ctypes.c_bool),
        ("EnablePpuOamRowCorruption", ctypes.c_bool),
        ("EnablePpuSpriteEvalBug", ctypes.c_bool),
        ("DisableOamAddrBug", ctypes.c_bool),
        ("DisablePaletteRead", ctypes.c_bool),
        ("DisablePpu2004Reads", ctypes.c_bool),
        ("EnablePpu2000ScrollGlitch", ctypes.c_bool),
        ("EnablePpu2006ScrollGlitch", ctypes.c_bool),
        ("RestrictPpuAccessOnFirstFrame", ctypes.c_bool),
        ("EnableDmcSampleDuplicationGlitch", ctypes.c_bool),
        ("EnableCpuTestMode", ctypes.c_bool),
        ("RandomizeMapperPowerOnState", ctypes.c_bool),
        ("RandomizeCpuPpuAlignment", ctypes.c_bool),
        ("RamPowerOnState", ctypes.c_int),
        ("PpuExtraScanlinesBeforeNmi", ctypes.c_uint32),
        ("PpuExtraScanlinesAfterNmi", ctypes.c_uint32),
        ("DisableNoiseModeFlag", ctypes.c_bool),
        ("ReduceDmcPopping", ctypes.c_bool),
        ("SilenceTriangleHighFreq", ctypes.c_bool),
        ("SwapDutyCycles", ctypes.c_bool),
        ("ReverseDpcmBitOrder", ctypes.c_bool),
        ("BreakOnCrash", ctypes.c_bool),
        ("InputScanline", ctypes.c_int32),
        ("IsFullColorPalette", ctypes.c_bool),
        ("UserPalette", ctypes.c_uint32 * 512),
        ("ChannelVolumes", ctypes.c_uint32 * 11),
        ("EpsmVolume", ctypes.c_uint32),
        ("ChannelPanning", ctypes.c_uint32 * 11),
        ("StereoFilter", ctypes.c_int),
        ("StereoDelay", ctypes.c_int32),
        ("StereoPanningAngle", ctypes.c_int32),
        ("StereoCombFilterDelay", ctypes.c_int32),
        ("StereoCombFilterStrength", ctypes.c_int32),
    ]


def bind_nes_config_api(core: MesenCore) -> None:
    """Bind GetNesConfig/SetNesConfig for the pinned native layout."""
    try:
        core._dll.GetNesConfig.argtypes = []
        core._dll.GetNesConfig.restype = NesConfig
        core._dll.SetNesConfig.argtypes = [NesConfig]
        core._dll.SetNesConfig.restype = None
    except AttributeError as exc:
        raise MesenLoadError("MesenCore.dll is missing NES configuration exports.") from exc


def get_nes_config(core: MesenCore) -> NesConfig:
    bind_nes_config_api(core)
    return core._dll.GetNesConfig()


def configure_standard_nes_controller(core: MesenCore, *, port: int = 1) -> NesConfig:
    """Configure a standard controller before LoadRom without host input devices.

    Mesen's debugger override operates on emulated control devices. A fresh
    headless configuration can have ControllerType::None on every port, so the
    device must exist before the ROM constructs NesControlManager.
    """
    if port not in (1, 2):
        raise ValueError("standard NES controller port must be 1 or 2")
    if core.is_running():
        raise MesenLoadError("configure controller before load_rom().")

    bind_nes_config_api(core)
    config = core._dll.GetNesConfig()
    target = config.Port1 if port == 1 else config.Port2
    target.Type = CONTROLLER_TYPE_NES_CONTROLLER
    # Preserve every other current setting. Disable ROM-database input
    # auto-configuration so LoadRom cannot replace the explicit port choice.
    config.AutoConfigureInput = False
    core._dll.SetNesConfig(config)
    return config
