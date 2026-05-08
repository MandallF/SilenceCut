from __future__ import annotations

import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Callable

from ffmpeg_path import get_ffmpeg


# Progress callback receives a dict of FFmpeg's -progress key=value pairs.
# Common keys: frame, fps, out_time_us, total_size, speed, progress (continue|end).
ProgressCallback = Callable[[dict], None]


# Windows' CreateProcess limits the full command line to ~32 KiB. A long video
# with many silent regions easily blows past this when the filter_complex string
# is passed directly. Once we cross this threshold we write the filter to a
# temp file and use FFmpeg's -/filter_complex_script flag instead.
FILTER_INLINE_LIMIT = 8 * 1024


def _run_ffmpeg_with_filter(
    base_cmd: list[str],
    filter_complex: str,
    tail_cmd: list[str],
    timeout: float,
    progress_cb: ProgressCallback | None = None,
) -> None:
    """Run FFmpeg, passing *filter_complex* via a temp file when it's too long.

    *base_cmd* is everything up to (but not including) the filter argument;
    *tail_cmd* is everything after it (output mapping, codecs, output path).
    """
    if len(filter_complex) <= FILTER_INLINE_LIMIT:
        cmd = base_cmd + ["-filter_complex", filter_complex] + tail_cmd
        _run_ffmpeg(cmd, timeout, progress_cb=progress_cb)
        return

    # Write filter to a temp file. NOTE: must use utf-8 with no BOM.
    tmp = tempfile.NamedTemporaryFile(
        mode="w", suffix=".ffscript", delete=False, encoding="utf-8"
    )
    try:
        tmp.write(filter_complex)
        tmp.close()
        cmd = base_cmd + ["-/filter_complex", tmp.name] + tail_cmd
        _run_ffmpeg(cmd, timeout, progress_cb=progress_cb)
    finally:
        try:
            Path(tmp.name).unlink(missing_ok=True)
        except OSError:
            pass


QUALITY_PRESETS = {
    # name -> (x264 preset, crf). lower crf = higher quality, larger file.
    # ultrafast preset compresses badly (large file) but encodes ~5x faster.
    "fast":     ("ultrafast", 23),
    "balanced": ("fast",      20),
    "high":     ("slow",      18),
    # "gpu" is special-cased — codec args are picked at runtime based on the
    # detected hardware encoder (AMF / NVENC / QSV). The tuple values are
    # placeholders so QUALITY_PRESETS.get(...) still returns something.
    "gpu":      ("hw",        20),
}


# Cache the result of hardware-encoder probing — first call may take a couple
# of seconds because we actually try to encode a test pattern. Subsequent
# calls return instantly. Sentinel -1 means "not yet probed".
_HW_ENCODER_CACHE: str | None = -1  # type: ignore[assignment]


def _probe_hw_encoder(encoder: str) -> bool:
    """Try a 1-frame test encode to see if *encoder* actually works on this box.

    Just being listed in `-encoders` is not enough — NVENC requires a working
    NVIDIA driver, AMF requires an AMD GPU + driver, QSV requires Intel iGPU.
    A real probe is the only reliable check.
    """
    try:
        result = subprocess.run(
            [
                get_ffmpeg(), "-y", "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=128x128:r=30",
                "-t", "0.1",
                "-c:v", encoder,
                "-f", "null", "-",
            ],
            capture_output=True,
            timeout=8,
        )
        return result.returncode == 0
    except Exception:
        return False


def detect_hw_encoder() -> str | None:
    """Return the best available hardware H.264 encoder name, or None."""
    global _HW_ENCODER_CACHE
    if _HW_ENCODER_CACHE != -1:
        return _HW_ENCODER_CACHE  # type: ignore[return-value]
    # Order: NVENC (best quality/perf trade-off on consumer cards) → QSV
    # (Intel iGPUs, very common) → AMF (AMD discrete + APUs).
    for enc in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if _probe_hw_encoder(enc):
            _HW_ENCODER_CACHE = enc
            return enc
    _HW_ENCODER_CACHE = None
    return None


def _codec_args_for(quality: str) -> list[str]:
    """Build the FFmpeg codec arguments for the given quality preset.

    For software presets returns libx264 with the matching x264 preset/crf.
    For "gpu" picks whichever hardware encoder probes successfully on this
    machine; falls back to libx264 fast/crf 20 if no hardware works.
    """
    if quality == "gpu":
        enc = detect_hw_encoder()
        if enc == "h264_nvenc":
            # p4 = balanced preset (1 = slowest/best, 7 = fastest/worst).
            return [
                "-c:v", "h264_nvenc",
                "-preset", "p4",
                "-tune", "hq",
                "-rc", "vbr",
                "-cq", "20",
                "-b:v", "0",
            ]
        if enc == "h264_qsv":
            return [
                "-c:v", "h264_qsv",
                "-preset", "medium",
                "-global_quality", "20",
            ]
        if enc == "h264_amf":
            # CQP rate-control with QP ~20 → roughly comparable to libx264 crf 20.
            return [
                "-c:v", "h264_amf",
                "-quality", "balanced",
                "-rc", "cqp",
                "-qp_i", "20",
                "-qp_p", "22",
                "-qp_b", "24",
            ]
        # No hardware available — silently fall back to fast software.
        return ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]

    preset, crf = QUALITY_PRESETS.get(quality, QUALITY_PRESETS["balanced"])
    return ["-c:v", "libx264", "-preset", preset, "-crf", str(crf)]


def _encode_timeout(duration_sec: float, preset: str) -> float:
    """Reasonable subprocess timeout for an x264 encode.

    Conservatively allow 30x realtime for `slow` preset, 12x for `fast`,
    8x for `ultrafast`, plus a 60 second floor.
    """
    factor = {
        "ultrafast": 8,
        "fast":      12,
        "medium":    20,
        "slow":      30,
    }.get(preset, 20)
    return max(60.0, duration_sec * factor)


def ffmpeg_available() -> bool:
    try:
        binary = get_ffmpeg()
        subprocess.run(
            [binary, "-version"],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


def _invert_regions(silent: list[dict], duration: float) -> list[tuple[float, float]]:
    regions = sorted(
        [(float(r["start"]), float(r["end"])) for r in silent], key=lambda x: x[0]
    )
    keep: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in regions:
        if start > cursor:
            keep.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        keep.append((cursor, duration))
    return [(s, e) for s, e in keep if e - s > 0.01]


def export_video(
    input_path: str,
    output_path: str,
    silent_regions: list[dict],
    duration: float,
    mic_path: str | None = None,
    mic_offset: float = 0.0,
    video_audio_gain_db: float = 0.0,
    mic_gain_db: float = 0.0,
    quality: str = "balanced",
    progress_cb: ProgressCallback | None = None,
) -> str:
    keep = _invert_regions(silent_regions, duration)
    if not keep:
        raise ValueError("No segments to keep — entire video would be removed")

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)

    codec_args = _codec_args_for(quality)
    # Pick a timeout factor: hardware encodes are typically much faster than
    # the slowest software preset, so we use the same 8x floor.
    preset_for_timeout = quality if quality != "gpu" else "ultrafast"
    if preset_for_timeout in QUALITY_PRESETS:
        x264_preset = QUALITY_PRESETS[preset_for_timeout][0]
    else:
        x264_preset = "fast"
    kept_duration = sum(e - s for s, e in keep)
    timeout = _encode_timeout(max(kept_duration, duration), x264_preset)

    if mic_path:
        return _export_with_mic(
            input_path,
            mic_path,
            output_path,
            keep,
            mic_offset,
            video_audio_gain_db,
            mic_gain_db,
            codec_args=codec_args,
            timeout=timeout,
            progress_cb=progress_cb,
        )
    return _export_video_only(
        input_path, output_path, keep,
        codec_args=codec_args, timeout=timeout, progress_cb=progress_cb,
    )


def _run_ffmpeg(cmd: list[str], timeout: float, progress_cb: ProgressCallback | None = None) -> None:
    """Execute FFmpeg, optionally streaming progress events to *progress_cb*.

    With a callback we use Popen so we can read FFmpeg's `-progress pipe:1`
    output in real time. Without one we fall back to the simple blocking
    subprocess.run for the version-check / probe paths.
    """
    if progress_cb is None:
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"FFmpeg encode timed out after {int(timeout)} s") from exc
        if proc.returncode != 0:
            raise RuntimeError(f"FFmpeg failed: {proc.stderr[-1000:]}")
        return

    # Inject -progress / -nostats right after the binary so they're applied
    # to the global FFmpeg invocation.
    cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + cmd[1:]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stderr_tail: list[str] = []

    def _drain_stderr() -> None:
        # FFmpeg writes encode logs to stderr. Drain in a thread so the pipe
        # doesn't fill up and deadlock the encoder.
        assert proc.stderr is not None
        for line in iter(proc.stderr.readline, ""):
            stderr_tail.append(line)
            if len(stderr_tail) > 200:
                del stderr_tail[:100]
        try:
            proc.stderr.close()
        except Exception:
            pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stderr_thread.start()

    deadline = time.time() + timeout
    pending: dict[str, str] = {}

    try:
        assert proc.stdout is not None
        while True:
            line = proc.stdout.readline()
            if not line:
                # EOF — process exited or pipe closed.
                if proc.poll() is not None:
                    break
                if time.time() > deadline:
                    proc.kill()
                    raise RuntimeError(f"FFmpeg encode timed out after {int(timeout)} s")
                continue
            line = line.strip()
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            pending[key] = val
            if key == "progress":
                try:
                    progress_cb(dict(pending))
                except Exception:
                    pass  # never let a bad callback kill the encode
                if val == "end":
                    break
            if time.time() > deadline:
                proc.kill()
                raise RuntimeError(f"FFmpeg encode timed out after {int(timeout)} s")
    finally:
        try:
            if proc.stdout:
                proc.stdout.close()
        except Exception:
            pass
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        stderr_thread.join(timeout=2)

    if proc.returncode != 0:
        tail = "".join(stderr_tail[-30:])[-1200:]
        raise RuntimeError(f"FFmpeg failed: {tail}")


def _export_video_only(
    input_path: str,
    output_path: str,
    keep: list[tuple[float, float]],
    codec_args: list[str] | None = None,
    timeout: float = 1800.0,
    progress_cb: ProgressCallback | None = None,
) -> str:
    if codec_args is None:
        codec_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    filter_parts: list[str] = []
    concat_inputs_v: list[str] = []
    concat_inputs_a: list[str] = []

    for i, (start, end) in enumerate(keep):
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        filter_parts.append(
            f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs_v.append(f"[v{i}]")
        concat_inputs_a.append(f"[a{i}]")

    n = len(keep)
    concat = (
        "".join(f"{v}{a}" for v, a in zip(concat_inputs_v, concat_inputs_a))
        + f"concat=n={n}:v=1:a=1[outv][outa]"
    )
    filter_complex = ";".join(filter_parts + [concat])

    base_cmd = [get_ffmpeg(), "-y", "-i", input_path]
    tail_cmd = (
        ["-map", "[outv]", "-map", "[outa]"]
        + codec_args
        + ["-c:a", "aac", "-b:a", "192k", output_path]
    )
    _run_ffmpeg_with_filter(base_cmd, filter_complex, tail_cmd, timeout, progress_cb=progress_cb)
    return output_path


def _export_with_mic(
    video_path: str,
    mic_path: str,
    output_path: str,
    keep: list[tuple[float, float]],
    mic_offset: float,
    video_gain_db: float,
    mic_gain_db: float,
    codec_args: list[str] | None = None,
    timeout: float = 1800.0,
    progress_cb: ProgressCallback | None = None,
) -> str:
    if codec_args is None:
        codec_args = ["-c:v", "libx264", "-preset", "fast", "-crf", "20"]
    """Mix the original video audio with a separate mic track, then cut.

    The mic stream is delayed (or trimmed) to align with the video timeline
    so its time 0 corresponds to video time `mic_offset` seconds. The two
    streams are mixed with `amix` (weights derived from the gain dBs), then
    each kept segment is trimmed from the mixed stream and concatenated.
    """
    filter_parts: list[str] = []

    # Build the mic-aligned stream so it lives in the video's time base.
    if mic_offset > 0:
        # Mic starts later than video → pad with silence at beginning.
        ms = int(round(mic_offset * 1000))
        filter_parts.append(f"[1:a]adelay={ms}:all=1[mic_aligned]")
    elif mic_offset < 0:
        # Mic starts earlier than video → trim the mic head.
        filter_parts.append(
            f"[1:a]atrim=start={-mic_offset:.3f},asetpts=PTS-STARTPTS[mic_aligned]"
        )
    else:
        filter_parts.append("[1:a]anull[mic_aligned]")

    # Apply per-stream gain.
    v_gain = 10 ** (video_gain_db / 20.0)
    m_gain = 10 ** (mic_gain_db / 20.0)
    filter_parts.append(f"[0:a]volume={v_gain:.4f}[va]")
    filter_parts.append(f"[mic_aligned]volume={m_gain:.4f}[ma]")

    # Mix. duration=longest so we don't lose audio if mic is shorter/longer.
    # normalize=0 means we don't auto-attenuate; we already chose gains.
    filter_parts.append(
        "[va][ma]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0[mixed]"
    )

    concat_inputs_v: list[str] = []
    concat_inputs_a: list[str] = []
    for i, (start, end) in enumerate(keep):
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{i}]"
        )
        filter_parts.append(
            f"[mixed]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{i}]"
        )
        concat_inputs_v.append(f"[v{i}]")
        concat_inputs_a.append(f"[a{i}]")

    n = len(keep)
    concat = (
        "".join(f"{v}{a}" for v, a in zip(concat_inputs_v, concat_inputs_a))
        + f"concat=n={n}:v=1:a=1[outv][outa]"
    )
    filter_complex = ";".join(filter_parts + [concat])

    base_cmd = [get_ffmpeg(), "-y", "-i", video_path, "-i", mic_path]
    tail_cmd = (
        ["-map", "[outv]", "-map", "[outa]"]
        + codec_args
        + ["-c:a", "aac", "-b:a", "192k", output_path]
    )
    _run_ffmpeg_with_filter(base_cmd, filter_complex, tail_cmd, timeout, progress_cb=progress_cb)
    return output_path
