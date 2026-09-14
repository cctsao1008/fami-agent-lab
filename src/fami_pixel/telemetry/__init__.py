"""Observer-only telemetry surfaces for fami-pixel."""

from .web_viewer import NesWebViewer, raw_frame_to_bmp

__all__ = ["NesWebViewer", "raw_frame_to_bmp"]
