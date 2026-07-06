"""Turkish speech-to-text for SilenceCut using faster-whisper.

Produces SRT subtitles whose timestamps are remapped onto the CUT timeline
(the one the user gets after silent regions are removed), so the .srt lines
line up with the exported video / Premiere sequence, not the raw recording.

The Whisper model is downloaded on first use into a `models/` directory next
to the executable (or the repo root in dev mode) so the user can see and
manage it; falls back to %LOCALAPPDATA% when that location isn't writable.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from analyzer import _decode_audio
from premiere_xml import _invert_silent_to_keep


# Whisper models are trained on 16 kHz mono audio.
WHISPER_SAMPLE_RATE = 16000

# Ignore remapped subtitle fragments shorter than this — they're slivers
# created when a segment barely grazes a keep region boundary.
MIN_FRAGMENT_SECONDS = 0.05

ProgressCallback = Callable[[dict], None]


@dataclass
class Segment:
    start: float
    end: float
    text: str


def get_model_dir() -> Path:
    """Model cache directory: next to the exe (frozen) / repo root (dev).

    The user asked for the model to live alongside the app rather than in a
    hidden AppData folder. If that location isn't writable (e.g. app run
    from a read-only share), fall back to %LOCALAPPDATA%\\SilenceCut\\models.
    """
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parent.parent
    candidate = base / "models"
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".write_test"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return candidate
    except OSError:
        pass
    local = os.environ.get("LOCALAPPDATA")
    fallback = (Path(local) if local else Path.home()) / "SilenceCut" / "models"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def is_model_downloaded(model_size: str = "small") -> bool:
    """True if the faster-whisper snapshot for *model_size* is already cached."""
    snapshot = get_model_dir() / f"models--Systran--faster-whisper-{model_size}"
    return snapshot.is_dir()


def transcribe(
    audio_path: str,
    model_size: str = "small",
    language: str = "tr",
    progress_cb: ProgressCallback | None = None,
) -> list[Segment]:
    """Run Whisper over *audio_path* and return raw segments in SOURCE time."""

    def _report(percent: float, phase: str) -> None:
        if progress_cb is not None:
            try:
                progress_cb({"percent": round(percent, 1), "phase": phase})
            except Exception:
                pass  # progress must never kill the transcription

    # Heavy import — keep it inside the function so app startup stays fast.
    from faster_whisper import WhisperModel

    _report(0.0, "downloading" if not is_model_downloaded(model_size) else "loading")
    model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
        download_root=str(get_model_dir()),
    )

    # Decode with our bundled FFmpeg (reuses analyzer's decoder) instead of
    # letting faster-whisper open the file via PyAV — sidesteps container
    # quirks like the AAC-in-MP4-named-.mp3 files we've seen from OBS.
    _report(0.0, "decoding")
    audio = _decode_audio(str(audio_path), sample_rate=WHISPER_SAMPLE_RATE)
    duration = len(audio) / WHISPER_SAMPLE_RATE if len(audio) else 0.0

    _report(0.0, "transcribing")
    segments_iter, _info = model.transcribe(
        audio,
        language=language,
        beam_size=5,
        # VAD suppresses hallucinated text in long quiet/music-only stretches,
        # which gameplay recordings are full of.
        vad_filter=True,
    )

    out: list[Segment] = []
    for seg in segments_iter:
        text = (seg.text or "").strip()
        if text:
            out.append(Segment(start=float(seg.start), end=float(seg.end), text=text))
        if duration > 0:
            _report(min(99.5, float(seg.end) / duration * 100.0), "transcribing")
    return out


def remap_segments(
    segments: list[Segment],
    silent_regions: list[dict],
    duration: float,
    time_shift: float = 0.0,
) -> list[Segment]:
    """Convert SOURCE-time segments to CUT-timeline time.

    *time_shift* is added to every segment first — pass mic_offset when the
    transcript came from the separate mic track so its clock matches the
    video's before we apply the cut mapping.

    A segment overlapping a removed silence gets clipped; one spanning
    multiple keep regions is split into one entry per region (the text
    repeats across the cut, which reads naturally).
    """
    keep = _invert_silent_to_keep(silent_regions, duration)

    # Precompute each keep region's start position on the OUTPUT timeline.
    regions: list[tuple[float, float, float]] = []  # (src_start, src_end, out_start)
    out_cursor = 0.0
    for ks, ke in keep:
        regions.append((ks, ke, out_cursor))
        out_cursor += ke - ks

    mapped: list[Segment] = []
    for seg in segments:
        s = seg.start + time_shift
        e = seg.end + time_shift
        for ks, ke, out_start in regions:
            ov_s = max(s, ks)
            ov_e = min(e, ke)
            if ov_e - ov_s < MIN_FRAGMENT_SECONDS:
                continue
            mapped.append(
                Segment(
                    start=out_start + (ov_s - ks),
                    end=out_start + (ov_e - ks),
                    text=seg.text,
                )
            )
    mapped.sort(key=lambda x: x.start)
    return mapped


def _fmt_timestamp(t: float) -> str:
    ms = max(0, int(round(t * 1000)))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def to_srt(segments: list[Segment]) -> str:
    """Serialize segments as an SRT document (UTF-8, 1-based indices)."""
    lines: list[str] = []
    for i, seg in enumerate(segments, start=1):
        lines.append(str(i))
        lines.append(f"{_fmt_timestamp(seg.start)} --> {_fmt_timestamp(seg.end)}")
        lines.append(seg.text)
        lines.append("")
    return "\n".join(lines)
