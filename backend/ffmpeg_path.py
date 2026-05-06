"""Locate a usable ffmpeg binary.

Prefers `imageio_ffmpeg`'s bundled static binary so the packaged app does not
require a system FFmpeg install. Falls back to `ffmpeg` on PATH.
"""

from __future__ import annotations

import shutil


def get_ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        path = shutil.which("ffmpeg")
        if path:
            return path
        raise RuntimeError("FFmpeg not available (install imageio-ffmpeg or ffmpeg).")
