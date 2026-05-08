from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass

import numpy as np

from ffmpeg_path import get_ffmpeg


WINDOW_MS = 50
PADDING_MS = 50
WAVEFORM_POINTS = 1200
SAMPLE_RATE = 22050

# Match exporter.py — keep the brief console window from flashing when we
# spawn ffmpeg.exe for audio decode.
_NO_WINDOW_FLAG = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


@dataclass
class AnalysisResult:
    duration: float
    silent_regions: list[dict]
    waveform: list[float]
    waveform_mic: list[float] | None = None


def _decode_audio(path: str, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    cmd = [
        get_ffmpeg(),
        "-nostdin",
        "-loglevel", "error",
        "-i", path,
        "-vn",
        "-ac", "1",
        "-ar", str(sample_rate),
        "-f", "s16le",
        "-",
    ]
    # Generous timeout: 30 minutes for an audio decode is more than enough for
    # multi-hour videos at 22 kHz mono. If ffmpeg is stuck longer than this it
    # means something is seriously wrong (e.g. corrupt input).
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            check=False,
            timeout=1800,
            creationflags=_NO_WINDOW_FLAG,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError("FFmpeg decode timed out after 30 minutes") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg decode failed: {proc.stderr.decode(errors='ignore')[-500:]}")
    audio = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32) / 32768.0
    return audio


def _rms_windows(y: np.ndarray, sr: int, window_ms: int = WINDOW_MS) -> tuple[np.ndarray, float]:
    hop = max(1, int(sr * window_ms / 1000))
    n_frames = int(np.ceil(len(y) / hop))
    pad = n_frames * hop - len(y)
    if pad > 0:
        y = np.pad(y, (0, pad))
    frames = y.reshape(n_frames, hop).astype(np.float64)
    rms = np.sqrt(np.mean(frames * frames, axis=1)).astype(np.float32)
    return rms, hop / sr


def _merge_silent_regions(
    silent_mask: np.ndarray,
    window_seconds: float,
    min_silence: float,
    padding: float,
    duration: float,
) -> list[dict]:
    regions: list[tuple[float, float]] = []
    in_silence = False
    start_idx = 0

    for i, is_silent in enumerate(silent_mask):
        if is_silent and not in_silence:
            start_idx = i
            in_silence = True
        elif not is_silent and in_silence:
            start = start_idx * window_seconds
            end = i * window_seconds
            if (end - start) >= min_silence:
                regions.append((start, end))
            in_silence = False

    if in_silence:
        start = start_idx * window_seconds
        end = len(silent_mask) * window_seconds
        if (end - start) >= min_silence:
            regions.append((start, end))

    padded: list[dict] = []
    for idx, (start, end) in enumerate(regions):
        ps = max(0.0, start + padding)
        pe = min(duration, end - padding)
        if pe - ps >= min_silence * 0.5 and pe > ps:
            padded.append({"id": f"r{idx}", "start": round(ps, 3), "end": round(pe, 3)})

    return padded


def _waveform(y: np.ndarray, points: int = WAVEFORM_POINTS) -> list[float]:
    if len(y) == 0:
        return [0.0] * points
    bucket = max(1, len(y) // points)
    trimmed = y[: bucket * points]
    if len(trimmed) < bucket * points:
        trimmed = np.pad(trimmed, (0, bucket * points - len(trimmed)))
    frames = trimmed.reshape(points, bucket).astype(np.float64)
    rms = np.sqrt(np.mean(frames * frames, axis=1))
    peak = float(rms.max()) if rms.size else 0.0
    if peak > 0:
        rms = rms / peak
    return [round(float(v), 4) for v in rms]


def _align_to_video(
    mic_rms: np.ndarray,
    n_video_windows: int,
    offset_seconds: float,
    window_seconds: float,
) -> np.ndarray:
    """Shift mic RMS so its index 0 corresponds to video time `offset_seconds`.

    A positive offset means the mic recording starts AFTER the video, so we
    pad zeros at the beginning of the (returned) video-aligned mic_rms.
    Negative offset means mic starts BEFORE the video — drop early samples.
    """
    out = np.zeros(n_video_windows, dtype=np.float32)
    offset_windows = int(round(offset_seconds / window_seconds))
    if offset_windows >= 0:
        # mic[0] corresponds to video time offset_seconds.
        dst_start = offset_windows
        src_start = 0
    else:
        # mic[ -offset_windows ] corresponds to video time 0.
        dst_start = 0
        src_start = -offset_windows
    n = min(n_video_windows - dst_start, len(mic_rms) - src_start)
    if n > 0:
        out[dst_start : dst_start + n] = mic_rms[src_start : src_start + n]
    return out


def analyze_audio(
    video_path: str,
    threshold: float,
    min_silence: float,
    mic_path: str | None = None,
    mic_threshold: float | None = None,
    mic_offset: float = 0.0,
) -> AnalysisResult:
    y = _decode_audio(video_path)
    sr = SAMPLE_RATE
    duration = float(len(y) / sr) if sr else 0.0

    rms, window_seconds = _rms_windows(y, sr)
    silent_mask = rms < threshold

    waveform_mic: list[float] | None = None
    if mic_path:
        try:
            y_mic = _decode_audio(mic_path)
            mic_rms_full, _ = _rms_windows(y_mic, sr)
            mic_aligned = _align_to_video(
                mic_rms_full, len(rms), mic_offset, window_seconds
            )
            mic_thr = mic_threshold if mic_threshold is not None else threshold
            mic_silent = mic_aligned < mic_thr
            silent_mask = silent_mask & mic_silent

            # Compute mic waveform aligned to video duration for the timeline.
            mic_y_aligned = _align_audio_samples(
                y_mic, sr, duration, mic_offset
            )
            waveform_mic = _waveform(mic_y_aligned)
        except Exception:
            # Mic decoding failed — fall back to video-only silence detection.
            waveform_mic = None

    padding = PADDING_MS / 1000.0
    regions = _merge_silent_regions(silent_mask, window_seconds, min_silence, padding, duration)

    waveform = _waveform(y)

    return AnalysisResult(
        duration=duration,
        silent_regions=regions,
        waveform=waveform,
        waveform_mic=waveform_mic,
    )


def _align_audio_samples(
    y_mic: np.ndarray, sr: int, video_duration: float, offset_seconds: float
) -> np.ndarray:
    """Align raw mic samples to a buffer matching video_duration."""
    target_len = int(round(video_duration * sr))
    out = np.zeros(target_len, dtype=np.float32)
    offset_samples = int(round(offset_seconds * sr))
    if offset_samples >= 0:
        dst_start = offset_samples
        src_start = 0
    else:
        dst_start = 0
        src_start = -offset_samples
    n = min(target_len - dst_start, len(y_mic) - src_start)
    if n > 0:
        out[dst_start : dst_start + n] = y_mic[src_start : src_start + n]
    return out


@dataclass
class ThresholdSuggestion:
    threshold: float        # suggested RMS threshold (slider value 0.003–0.05)
    noise_floor: float      # estimated background noise level
    signal_level: float     # estimated active-content level
    confidence: str         # "high" | "medium" | "low"
    duration: float


MIN_THRESHOLD = 0.003
MAX_THRESHOLD = 0.05


def _suggest_from_rms(rms: np.ndarray) -> ThresholdSuggestion | None:
    """Pick a silence threshold from an RMS distribution.

    Idea: most recordings have a bimodal RMS distribution (silence vs sound).
    Estimate the noise floor as the median of the quietest 20% of windows
    and the signal level as the median of the loudest 20%, then place the
    threshold above the noise floor — high enough to ignore breaths/HVAC
    rumble but low enough to catch real silence.
    """
    if rms.size == 0:
        return None
    sorted_rms = np.sort(rms)
    n = len(sorted_rms)
    bottom = sorted_rms[: max(1, n // 5)]
    top = sorted_rms[-max(1, n // 5):]
    noise_floor = float(np.median(bottom))
    signal_level = float(np.median(top))

    # Threshold = 4x noise floor as a robust default, but never below
    # noise_floor + 0.001 (so it's actually above the noise) and never
    # higher than signal_level / 3 (so we don't kill quiet speech).
    candidate = max(noise_floor * 4.0, noise_floor + 0.001)
    candidate = min(candidate, max(signal_level / 3.0, candidate * 0.5))
    candidate = float(np.clip(candidate, MIN_THRESHOLD, MAX_THRESHOLD))

    # Snap to slider step (0.001).
    candidate = round(candidate, 3)

    # Confidence: how clearly bimodal is the distribution?
    # If signal_level is much higher than noise_floor → confident.
    if signal_level <= 0:
        ratio = 0.0
    else:
        ratio = signal_level / max(noise_floor, 1e-6)
    if ratio > 20:
        confidence = "high"
    elif ratio > 5:
        confidence = "medium"
    else:
        confidence = "low"

    return ThresholdSuggestion(
        threshold=candidate,
        noise_floor=round(noise_floor, 5),
        signal_level=round(signal_level, 5),
        confidence=confidence,
        duration=0.0,  # caller fills in
    )


def suggest_thresholds(
    video_path: str, mic_path: str | None = None
) -> dict:
    """Decode each track, derive a recommended silence threshold per source."""
    out: dict = {"video": None, "mic": None}

    y_video = _decode_audio(video_path)
    duration = float(len(y_video) / SAMPLE_RATE) if SAMPLE_RATE else 0.0
    rms_v, _ = _rms_windows(y_video, SAMPLE_RATE)
    s = _suggest_from_rms(rms_v)
    if s is not None:
        s.duration = round(duration, 3)
        out["video"] = {
            "threshold": s.threshold,
            "noise_floor": s.noise_floor,
            "signal_level": s.signal_level,
            "confidence": s.confidence,
            "duration": s.duration,
        }

    if mic_path:
        try:
            y_mic = _decode_audio(mic_path)
            duration_mic = float(len(y_mic) / SAMPLE_RATE) if SAMPLE_RATE else 0.0
            rms_m, _ = _rms_windows(y_mic, SAMPLE_RATE)
            sm = _suggest_from_rms(rms_m)
            if sm is not None:
                sm.duration = round(duration_mic, 3)
                out["mic"] = {
                    "threshold": sm.threshold,
                    "noise_floor": sm.noise_floor,
                    "signal_level": sm.signal_level,
                    "confidence": sm.confidence,
                    "duration": sm.duration,
                }
        except Exception:
            out["mic"] = None

    return out
