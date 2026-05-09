from __future__ import annotations

import asyncio
import contextlib
import errno
import os
import re
import shutil
import sys
import threading
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from analyzer import analyze_audio, suggest_thresholds
from exporter import detect_hw_encoder, export_video, ffmpeg_available
from premiere_xml import generate_premiere_xml


BASE_DIR = Path(__file__).resolve().parent

# When packaged with PyInstaller, frontend assets live in sys._MEIPASS/frontend_dist.
# Temp uploads must live in a writable user-data directory with plenty of space —
# never inside _MEIPASS (read-only) and never on a system drive that's nearly full.
def _pick_temp_dir() -> Path:
    """Choose a temp directory on whichever drive has the most free space.

    Multi-GB video uploads can quickly fill a near-full system drive (we've
    seen failures at ~14% upload because C: only had 8 GB free). Prefer the
    largest drive among the obvious candidates so users don't have to babysit
    disk usage.
    """
    candidates: list[Path] = []
    # 1. Override via environment variable (highest priority).
    override = os.environ.get("SILENCECUT_TEMP")
    if override:
        candidates.append(Path(override))
    # 2. Drives D:..Z: (skip A:, B:, C:) if they exist.
    for letter in "DEFGHIJKLMNOPQRSTUVWXYZ":
        root = Path(f"{letter}:/")
        if root.exists():
            candidates.append(root / "SilenceCut" / "temp")
    # 3. Fall back to %LOCALAPPDATA% (typically C:).
    local = os.environ.get("LOCALAPPDATA")
    if local:
        candidates.append(Path(local) / "SilenceCut" / "temp")
    # 4. Last resort.
    candidates.append(Path.home() / ".silencecut" / "temp")

    best: Path | None = None
    best_free = -1
    import shutil as _shutil
    for c in candidates:
        try:
            c.mkdir(parents=True, exist_ok=True)
            free = _shutil.disk_usage(c).free
        except Exception:
            continue
        if free > best_free:
            best = c
            best_free = free
    # Should always find something, but fall back to candidates[0] just in case.
    return best or candidates[0]


if getattr(sys, "frozen", False):
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
    FRONTEND_DIST = BUNDLE_DIR / "frontend_dist"
    TEMP_DIR = _pick_temp_dir()
else:
    FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"
    TEMP_DIR = BASE_DIR / "temp"

TEMP_DIR.mkdir(parents=True, exist_ok=True)


# Naming scheme inside TEMP_DIR. The double-underscore separator after the
# file_id prevents accidental prefix collisions when running cleanup.
#   {file_id}__video__{name}    -- the source video
#   {file_id}__mic__{name}      -- optional secondary mic track
#   {file_id}__output.mp4       -- final export
FILE_ID_SEP = "__"
VIDEO_TAG = "__video__"
MIC_TAG = "__mic__"
OUTPUT_SUFFIX = "__output.mp4"

# Keep full TEMP_DIR + tags + filename + .mp4 under Windows' 260 char MAX_PATH
# limit. file_id is 32 chars + separators (~15), so 80 leaves comfortable room.
MAX_FILENAME_LEN = 80

# file_id is always uuid4().hex — exactly 32 lowercase hex chars. Anything else
# is either a programming error or a probe; reject up front.
FILE_ID_RE = re.compile(r"^[0-9a-f]{32}$")


@contextlib.asynccontextmanager
async def _lifespan(app: FastAPI):
    if not ffmpeg_available():
        sys.stderr.write(
            "ERROR: FFmpeg not available (imageio-ffmpeg missing and no system ffmpeg).\n"
        )
    yield


app = FastAPI(title="SilenceCut", lifespan=_lifespan)

# CORS only matters in dev mode (vite at :5173 → backend at :8000). In the
# packaged app the frontend is served from the same origin so CORS is moot.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)


api = APIRouter(prefix="/api")


class Region(BaseModel):
    id: str
    start: float = Field(..., ge=0)
    end: float = Field(..., ge=0)

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, v: float, info) -> float:  # noqa: ANN001
        start = info.data.get("start", 0.0)
        if v <= start:
            raise ValueError("end must be greater than start")
        return v


class AnalyzeRequest(BaseModel):
    file_id: str
    threshold: float = Field(0.015, gt=0, le=1.0)
    min_silence: float = Field(0.4, gt=0, le=60.0)
    mic_threshold: float | None = Field(None, ge=0, le=1.0)
    mic_offset: float = Field(0.0, ge=-3600, le=3600)


class SuggestRequest(BaseModel):
    file_id: str


class ExportRequest(BaseModel):
    file_id: str
    regions: list[Region]
    duration: float = Field(..., gt=0)
    mic_offset: float = Field(0.0, ge=-3600, le=3600)
    video_gain_db: float = Field(0.0, ge=-60, le=20)
    mic_gain_db: float = Field(0.0, ge=-60, le=20)
    quality: str = "balanced"  # validated against QUALITY_PRESETS in exporter
    # Frontend explicitly tells us whether to mix the mic track. Defaults to
    # True for backwards compatibility, but a False here means "ignore the
    # mic file even if it's still on disk" — needed when the user removed
    # the mic in the UI but the file lingers until the next cleanup.
    use_mic: bool = True


class PremiereXmlRequest(BaseModel):
    file_id: str
    regions: list[Region]
    duration: float = Field(..., gt=0)
    mic_offset: float = Field(0.0, ge=-3600, le=3600)
    use_mic: bool = True


def _validate_file_id(file_id: str) -> None:
    """Strict hex-only check. Centralises the rule that file_id is uuid4().hex."""
    if not file_id or not FILE_ID_RE.match(file_id):
        raise HTTPException(status_code=400, detail="invalid file_id")


def _find_video(file_id: str) -> Path:
    _validate_file_id(file_id)
    prefix = f"{file_id}{VIDEO_TAG}"
    for entry in TEMP_DIR.iterdir():
        if entry.is_file() and entry.name.startswith(prefix):
            return entry
    raise HTTPException(status_code=404, detail="video not found for file_id")


def _find_mic(file_id: str) -> Path | None:
    if not file_id or not FILE_ID_RE.match(file_id):
        return None
    prefix = f"{file_id}{MIC_TAG}"
    for entry in TEMP_DIR.iterdir():
        if not entry.is_file() or not entry.name.startswith(prefix):
            continue
        # Skip in-progress upload staging files — they're not a real mic yet.
        if entry.name.endswith(".staging"):
            continue
        return entry
    return None


def _check_disk_space(directory: Path, required_bytes: int) -> None:
    """Raise HTTP 507 if there isn't enough free space in *directory*'s filesystem."""
    try:
        free = shutil.disk_usage(directory).free
    except Exception:
        return  # best-effort; skip check on error
    if free < required_bytes:
        free_mb = free // (1024 * 1024)
        need_mb = required_bytes // (1024 * 1024)
        raise HTTPException(
            status_code=507,
            detail=f"Yetersiz disk alanı — {need_mb} MB gerekiyor, {free_mb} MB mevcut. "
                   f"Geçici dosyaları temizleyin veya farklı bir diske kurun.",
        )


async def _stream_to_disk(request: Request, dest: Path) -> int:
    """Stream request body to *dest*, writing in a thread so the event loop stays free.

    Deletes the partial file and re-raises on any I/O error.
    """
    size = 0
    try:
        with dest.open("wb") as out:
            async for chunk in request.stream():
                if not chunk:
                    continue
                # Run the synchronous write in the default thread-pool executor so
                # we never block the asyncio event loop (critical for uvicorn's
                # h11 flow-control to work correctly with large uploads).
                await asyncio.to_thread(out.write, chunk)
                size += len(chunk)
    except Exception:
        # Remove the partial file so it doesn't silently eat disk space.
        try:
            dest.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return size


def _safe_filename(name: str) -> str:
    name = Path(name).name  # strip any path components
    # Drop control chars and characters that NTFS rejects.
    cleaned = "".join(c for c in name if c.isprintable() and c not in '<>:"/\\|?*')
    cleaned = cleaned.strip().rstrip(".")
    if not cleaned:
        return "upload.bin"
    if len(cleaned) > MAX_FILENAME_LEN:
        # Preserve extension when truncating.
        stem, dot, ext = cleaned.rpartition(".")
        if dot and len(ext) <= 8:
            keep = MAX_FILENAME_LEN - len(ext) - 1
            cleaned = (stem[:keep] if keep > 0 else "f") + "." + ext
        else:
            cleaned = cleaned[:MAX_FILENAME_LEN]
    return cleaned


@api.get("/health")
def health():
    """Lightweight liveness probe used by the frontend reconnect button."""
    return {"status": "ok"}


@api.get("/encoders")
def encoders():
    """Tell the frontend which encoders are available so it can show or hide
    the GPU option in the quality dropdown. Probes hardware on first call
    (cached afterwards) — may take a couple of seconds the first time.
    """
    hw = detect_hw_encoder()
    label_map = {
        "h264_nvenc": "GPU (NVIDIA NVENC)",
        "h264_qsv":   "GPU (Intel Quick Sync)",
        "h264_amf":   "GPU (AMD AMF)",
    }
    return {
        "hw_encoder": hw,
        "hw_label": label_map.get(hw or "", None),
    }


@api.post("/upload-raw")
async def upload_raw(
    request: Request,
    x_filename: str = Header(..., alias="X-Filename"),
):
    """Raw streaming upload — body is the file bytes, filename in header.

    Avoids multipart parsing's spool-then-copy double I/O cost, which matters
    for multi-GB videos.
    """
    # Proactive disk-space guard: if the browser sent Content-Length we can
    # check before touching the disk at all.
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            _check_disk_space(TEMP_DIR, int(content_length))
        except ValueError:
            pass  # malformed header — skip check

    file_id = uuid.uuid4().hex
    safe_name = _safe_filename(x_filename)
    dest = TEMP_DIR / f"{file_id}{VIDEO_TAG}{safe_name}"
    try:
        size = await _stream_to_disk(request, dest)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise HTTPException(
                status_code=507,
                detail="Disk dolu — geçici klasör için yeterli alan yok.",
            ) from exc
        raise HTTPException(status_code=500, detail=f"Dosya yazma hatası: {exc}") from exc
    return {"file_id": file_id, "filename": safe_name, "size": size}


@api.post("/upload-mic-raw")
async def upload_mic_raw(
    request: Request,
    x_filename: str = Header(..., alias="X-Filename"),
    x_file_id: str = Header(..., alias="X-File-Id"),
):
    _find_video(x_file_id)  # also validates file_id format
    safe_name = _safe_filename(x_filename)
    final = TEMP_DIR / f"{x_file_id}{MIC_TAG}{safe_name}"
    # Capture which mic (if any) exists BEFORE we start writing — _find_mic
    # skips .staging files so it won't see our in-progress upload, but doing
    # the lookup first is also a clearer guarantee.
    existing = _find_mic(x_file_id)

    # Write to a staging path so a failed/interrupted upload doesn't destroy
    # the previous mic file. Replace atomically only on success.
    staging = TEMP_DIR / f"{x_file_id}{MIC_TAG}.staging"
    try:
        size = await _stream_to_disk(request, staging)
    except OSError as exc:
        if exc.errno == errno.ENOSPC:
            raise HTTPException(status_code=507, detail="Disk dolu.") from exc
        raise HTTPException(status_code=500, detail=f"Dosya yazma hatası: {exc}") from exc

    # New mic is fully on disk → safe to remove the old one and move staging
    # into place. os.replace is atomic on Windows when source and destination
    # share a volume (always true here).
    if existing is not None and existing != final:
        try:
            existing.unlink()
        except OSError:
            pass
    try:
        os.replace(staging, final)
    except OSError as exc:
        # Best-effort cleanup if the rename fails for any reason.
        try:
            staging.unlink(missing_ok=True)
        except OSError:
            pass
        raise HTTPException(status_code=500, detail=f"Dosya taşıma hatası: {exc}") from exc
    return {"file_id": x_file_id, "filename": safe_name, "size": size, "kind": "mic"}


@api.delete("/upload-mic/{file_id}")
def delete_mic(file_id: str):
    _validate_file_id(file_id)
    existing = _find_mic(file_id)
    if existing is None:
        return {"removed": 0}
    try:
        existing.unlink()
    except OSError:
        return {"removed": 0}
    return {"removed": 1}


@api.post("/suggest-threshold")
def suggest_threshold(req: SuggestRequest):
    video = _find_video(req.file_id)
    mic = _find_mic(req.file_id)
    try:
        result = suggest_thresholds(str(video), str(mic) if mic else None)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"suggestion failed: {exc}") from exc
    return result


@api.post("/analyze")
def analyze(req: AnalyzeRequest):
    video = _find_video(req.file_id)
    mic = _find_mic(req.file_id)
    try:
        result = analyze_audio(
            str(video),
            req.threshold,
            req.min_silence,
            mic_path=str(mic) if mic else None,
            mic_threshold=req.mic_threshold,
            mic_offset=req.mic_offset,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"analysis failed: {exc}") from exc
    return {
        "duration": round(result.duration, 3),
        "silent_regions": result.silent_regions,
        "waveform": result.waveform,
        "waveform_mic": result.waveform_mic,
        "has_mic": mic is not None,
    }


# Live FFmpeg progress per file_id, updated from the encode subprocess and
# read by the /api/export-progress endpoint. Protected by a lock because the
# writer (FFmpeg subprocess thread) and reader (HTTP request) run on
# different threads.
_PROGRESS_LOCK = threading.Lock()
_PROGRESS: dict[str, dict] = {}

# Per-file_id reservation set used to block concurrent /api/export calls for
# the same source. Two encodes writing to the same out_path would interleave
# bytes and corrupt both. Held only for the duration of the export request.
_EXPORT_INFLIGHT_LOCK = threading.Lock()
_EXPORT_INFLIGHT: set[str] = set()


def _set_progress(file_id: str, data: dict | None) -> None:
    with _PROGRESS_LOCK:
        if data is None:
            _PROGRESS.pop(file_id, None)
        else:
            _PROGRESS[file_id] = data


def _get_progress(file_id: str) -> dict | None:
    with _PROGRESS_LOCK:
        return _PROGRESS.get(file_id)


@contextlib.contextmanager
def _claim_export(file_id: str):
    """Reserve *file_id* for an export, raising 409 if one's already running."""
    with _EXPORT_INFLIGHT_LOCK:
        if file_id in _EXPORT_INFLIGHT:
            raise HTTPException(
                status_code=409,
                detail="Bu video için zaten bir export çalışıyor.",
            )
        _EXPORT_INFLIGHT.add(file_id)
    try:
        yield
    finally:
        with _EXPORT_INFLIGHT_LOCK:
            _EXPORT_INFLIGHT.discard(file_id)


@api.get("/export-progress/{file_id}")
def export_progress(file_id: str):
    """Polled by the frontend to show live encode percent + ETA."""
    _validate_file_id(file_id)
    state = _get_progress(file_id)
    if state is None:
        return {"active": False}
    return {"active": True, **state}


@api.post("/export")
def export(req: ExportRequest):
    video = _find_video(req.file_id)
    # Honour the frontend's explicit choice: ignore the mic file if the user
    # toggled it off in the UI even though the file still exists on disk.
    mic = _find_mic(req.file_id) if req.use_mic else None
    out_path = TEMP_DIR / f"{req.file_id}{OUTPUT_SUFFIX}"

    # Compute the kept duration so we can convert FFmpeg's out_time into a
    # percentage. Mirrors what the exporter will compute internally.
    silent = [r.model_dump() for r in req.regions]
    silent_sorted = sorted(silent, key=lambda r: r["start"])
    kept_seconds = req.duration
    for r in silent_sorted:
        kept_seconds -= max(0.0, min(r["end"], req.duration) - max(r["start"], 0.0))
    kept_seconds = max(kept_seconds, 0.001)

    start_time = time.monotonic()

    def _on_progress(data: dict) -> None:
        try:
            out_us = int(data.get("out_time_us") or data.get("out_time_ms") or 0)
            # out_time_ms is poorly named — it's actually microseconds in many
            # FFmpeg versions. We accept either.
            encoded_s = out_us / 1_000_000.0
            pct = max(0.0, min(99.5, encoded_s / kept_seconds * 100.0))
            speed_str = (data.get("speed") or "0").rstrip("x").strip()
            try:
                speed = float(speed_str)
            except ValueError:
                speed = 0.0
            elapsed = time.monotonic() - start_time
            remaining = (kept_seconds - encoded_s) / speed if speed > 0.01 else None
            phase = "finalizing" if data.get("progress") == "end" else "encoding"
            _set_progress(req.file_id, {
                "percent": round(pct, 1),
                "encoded_seconds": round(encoded_s, 1),
                "total_seconds": round(kept_seconds, 1),
                "speed": speed,
                "fps": float(data.get("fps") or 0) or 0.0,
                "eta_seconds": round(remaining, 1) if remaining is not None else None,
                "elapsed_seconds": round(elapsed, 1),
                "phase": phase,
            })
        except Exception:
            pass

    _set_progress(req.file_id, {
        "percent": 0.0,
        "encoded_seconds": 0.0,
        "total_seconds": kept_seconds,
        "speed": 0.0,
        "fps": 0.0,
        "eta_seconds": None,
        "elapsed_seconds": 0.0,
        "phase": "starting",
    })

    with _claim_export(req.file_id):
        try:
            try:
                export_video(
                    str(video),
                    str(out_path),
                    silent,
                    req.duration,
                    mic_path=str(mic) if mic else None,
                    mic_offset=req.mic_offset,
                    video_audio_gain_db=req.video_gain_db,
                    mic_gain_db=req.mic_gain_db,
                    quality=req.quality,
                    progress_cb=_on_progress,
                )
            except Exception as exc:
                # Clean up partial output so the next attempt isn't confused by it.
                try:
                    out_path.unlink(missing_ok=True)
                except OSError:
                    pass
                raise HTTPException(status_code=500, detail=f"export failed: {exc}") from exc
        finally:
            # The response is about to start streaming the file back; the
            # frontend will switch to its own "downloading" UI from here.
            _set_progress(req.file_id, None)

    stem = video.name.split(VIDEO_TAG, 1)[-1]
    download_name = f"{Path(stem).stem}_silencecut.mp4"
    # Let Starlette's FileResponse build the Content-Disposition header itself
    # — it handles RFC 5987 percent-encoded UTF-8 for non-ASCII filenames,
    # which a hand-written `filename="..."` does not.
    return FileResponse(
        path=str(out_path),
        media_type="video/mp4",
        filename=download_name,
    )


@api.post("/export-premiere-xml")
def export_premiere_xml(req: PremiereXmlRequest):
    """Generate a Final Cut Pro 7 XML referencing the source files in TEMP_DIR.

    Returns the XML as a download. No re-encoding happens — the file just
    describes a Premiere timeline with the silent regions cut out. The user
    opens it in Premiere via File → Import.
    """
    video = _find_video(req.file_id)
    mic = _find_mic(req.file_id) if req.use_mic else None

    silent = [r.model_dump() for r in req.regions]
    try:
        xml_text = generate_premiere_xml(
            video_path=str(video),
            silent_regions=silent,
            duration=req.duration,
            mic_path=str(mic) if mic else None,
            mic_offset=req.mic_offset,
            sequence_name=f"SilenceCut — {Path(video.name.split(VIDEO_TAG, 1)[-1]).stem}",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"XML üretimi başarısız: {exc}") from exc

    stem = video.name.split(VIDEO_TAG, 1)[-1]
    download_name = f"{Path(stem).stem}_silencecut.xml"
    return Response(
        content=xml_text,
        media_type="application/xml",
        headers={
            "Content-Disposition": f'attachment; filename="{download_name}"',
            # The XML references absolute paths in TEMP_DIR. If the user
            # cleans up before opening it in Premiere they'll have to relink
            # the media — we leave that to the human to manage.
        },
    )


@api.delete("/cleanup/{file_id}")
def cleanup(file_id: str):
    if not file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="invalid file_id")
    # The double-underscore separator prevents matching unrelated files whose
    # name happens to start with the same hex prefix as our file_id.
    prefix = f"{file_id}{FILE_ID_SEP}"
    removed = 0
    for entry in TEMP_DIR.iterdir():
        if entry.is_file() and entry.name.startswith(prefix):
            try:
                entry.unlink()
                removed += 1
            except OSError:
                pass
    return {"removed": removed}


app.include_router(api)


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIST), html=True), name="frontend")
