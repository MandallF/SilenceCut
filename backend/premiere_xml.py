"""Generate a Final Cut Pro 7 XML (xmeml v5) describing a SilenceCut export
as a Premiere Pro-importable timeline.

Premiere's "Import" reads this XML and creates a sequence with all the kept
segments concatenated, plus a separate audio track for the mic file (if
provided). Nothing is re-encoded — Premiere references the original media
files in place. The user can then trim further, add transitions, music,
color, etc., and export through Adobe Media Encoder.

We deliberately stay close to the minimum viable XML: too many optional
fields produce import errors in older Premiere builds, while the core
clipitem/file/track structure has been stable for 15 years.
"""

from __future__ import annotations

import os
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ffmpeg_path import get_ffmpeg


_NO_WINDOW_FLAG = 0x08000000 if os.name == "nt" else 0


def _probe_media(path: str) -> dict:
    """Extract basic metadata by parsing FFmpeg's stderr (no ffprobe needed).

    Returns: {
        "duration":   float seconds,
        "width":      int px (0 if no video),
        "height":     int px (0 if no video),
        "fps":        float (0 if no video),
        "sample_rate": int Hz (0 if no audio),
        "channels":   int (0 if no audio),
    }
    """
    try:
        result = subprocess.run(
            [get_ffmpeg(), "-i", path, "-hide_banner"],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=_NO_WINDOW_FLAG,
        )
        out = result.stderr or ""
    except Exception:
        out = ""

    info = {"duration": 0.0, "width": 0, "height": 0, "fps": 0.0, "sample_rate": 0, "channels": 0}

    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", out)
    if m:
        h, mn, s = int(m.group(1)), int(m.group(2)), float(m.group(3))
        info["duration"] = h * 3600 + mn * 60 + s

    # Video stream — match the first one only. Resolution and rate can show
    # up in either order in FFmpeg's stderr depending on the codec, so we
    # query them with two separate searches scoped to the Video: line.
    vline_match = re.search(r"Video:[^\n]*", out)
    if vline_match:
        vline = vline_match.group(0)
        rm = re.search(r"(\d{2,5})x(\d{2,5})", vline)
        if rm:
            info["width"] = int(rm.group(1))
            info["height"] = int(rm.group(2))
        # Try fps first, then tbr as a fallback (many sources only carry tbr).
        fps_m = re.search(r"(\d+(?:\.\d+)?)\s*fps", vline)
        if not fps_m:
            fps_m = re.search(r"(\d+(?:\.\d+)?)\s*tbr", vline)
        if fps_m:
            info["fps"] = float(fps_m.group(1))

    # Audio stream — sample rate + channel layout.
    am = re.search(r"Audio:.*?(\d+)\s*Hz,\s*([a-z0-9.()\s]+?),", out)
    if am:
        info["sample_rate"] = int(am.group(1))
        layout = am.group(2).strip().lower()
        if "mono" in layout:
            info["channels"] = 1
        elif "5.1" in layout or "7.1" in layout:
            info["channels"] = 6 if "5.1" in layout else 8
        else:
            info["channels"] = 2

    return info


def _invert_silent_to_keep(silent: list[dict], duration: float) -> list[tuple[float, float]]:
    """Same logic as exporter._invert_regions, kept here to avoid the import
    cycle that would happen if we pulled it from exporter."""
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


def _path_to_url(path: str) -> str:
    """Windows path → file:// URL Premiere can resolve.

    Path.as_uri() returns 'file:///D:/foo/bar.mp4'. Premiere Pro on Windows
    mis-parses the empty-host form: it treats the leading triple slash as a
    UNC path (\\D:\\foo\\bar.mp4) and reports the file as offline. Adobe's
    own apps write the explicit 'file://localhost/...' form, which Premiere
    parses correctly as a local path. So we rewrite to that form.
    """
    uri = Path(path).resolve().as_uri()
    if uri.startswith("file:///"):
        uri = "file://localhost/" + uri[len("file:///"):]
    return uri


def _rate_xml(parent: ET.Element, fps: float) -> None:
    """Append <rate><timebase/><ntsc/></rate> with appropriate NTSC flag."""
    rate = ET.SubElement(parent, "rate")
    # Treat 23.976 / 29.97 / 59.94 as their NDF integer counterparts with
    # the ntsc flag set; everything else as exact integer rates.
    timebase = int(round(fps))
    ntsc = "TRUE" if abs(fps - timebase) > 0.01 and timebase in (24, 30, 60) else "FALSE"
    ET.SubElement(rate, "timebase").text = str(timebase if timebase > 0 else 30)
    ET.SubElement(rate, "ntsc").text = ntsc


def _add_video_format(track_or_format_parent: ET.Element, width: int, height: int, fps: float) -> None:
    fmt = ET.SubElement(track_or_format_parent, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    _rate_xml(sc, fps)
    ET.SubElement(sc, "width").text = str(width or 1920)
    ET.SubElement(sc, "height").text = str(height or 1080)
    ET.SubElement(sc, "anamorphic").text = "FALSE"
    ET.SubElement(sc, "pixelaspectratio").text = "square"
    ET.SubElement(sc, "fielddominance").text = "none"


def _add_audio_format(parent: ET.Element, sample_rate: int) -> None:
    fmt = ET.SubElement(parent, "format")
    sc = ET.SubElement(fmt, "samplecharacteristics")
    ET.SubElement(sc, "depth").text = "16"
    ET.SubElement(sc, "samplerate").text = str(sample_rate or 48000)


def _build_video_file_node(
    parent: ET.Element,
    file_id: str,
    path: str,
    info: dict,
    fps: float,
) -> None:
    """Build the <file> sub-tree for a video file. First reference only;
    subsequent uses just write <file id="X"/>."""
    f = ET.SubElement(parent, "file", id=file_id)
    ET.SubElement(f, "name").text = Path(path).name
    ET.SubElement(f, "pathurl").text = _path_to_url(path)
    _rate_xml(f, fps)
    if info["duration"] > 0:
        ET.SubElement(f, "duration").text = str(int(round(info["duration"] * fps)))
    media = ET.SubElement(f, "media")
    v = ET.SubElement(media, "video")
    vsc = ET.SubElement(v, "samplecharacteristics")
    ET.SubElement(vsc, "width").text = str(info["width"] or 1920)
    ET.SubElement(vsc, "height").text = str(info["height"] or 1080)
    if info["sample_rate"]:
        a = ET.SubElement(media, "audio")
        asc = ET.SubElement(a, "samplecharacteristics")
        ET.SubElement(asc, "depth").text = "16"
        ET.SubElement(asc, "samplerate").text = str(info["sample_rate"])
        ET.SubElement(a, "channelcount").text = str(info["channels"] or 2)


def _build_audio_only_file_node(
    parent: ET.Element, file_id: str, path: str, info: dict
) -> None:
    f = ET.SubElement(parent, "file", id=file_id)
    ET.SubElement(f, "name").text = Path(path).name
    ET.SubElement(f, "pathurl").text = _path_to_url(path)
    rate = ET.SubElement(f, "rate")
    ET.SubElement(rate, "timebase").text = "30"
    ET.SubElement(rate, "ntsc").text = "FALSE"
    if info["duration"] > 0:
        ET.SubElement(f, "duration").text = str(int(round(info["duration"] * 30)))
    media = ET.SubElement(f, "media")
    a = ET.SubElement(media, "audio")
    asc = ET.SubElement(a, "samplecharacteristics")
    ET.SubElement(asc, "depth").text = "16"
    ET.SubElement(asc, "samplerate").text = str(info["sample_rate"] or 48000)
    ET.SubElement(a, "channelcount").text = str(info["channels"] or 2)


def generate_premiere_xml(
    video_path: str,
    silent_regions: list[dict],
    duration: float,
    mic_path: str | None = None,
    mic_offset: float = 0.0,
    sequence_name: str = "SilenceCut Sequence",
) -> str:
    """Build the full xmeml document and return it as a UTF-8 string."""
    keep = _invert_silent_to_keep(silent_regions, duration)
    if not keep:
        raise ValueError("No segments to keep — entire timeline would be empty")

    video_info = _probe_media(video_path)
    if not video_info["fps"] or video_info["fps"] <= 0:
        # Falling back to 30 here would silently desync the timeline by 20%
        # on a 24 fps source. Better to fail loudly so the user knows the
        # source is unusual and can re-encode if needed.
        raise ValueError(
            "Video'nun frame rate'i tespit edilemedi. "
            "Kaynak dosya bozuk olabilir veya nadir bir codec kullanıyor olabilir."
        )
    fps = video_info["fps"]
    width = video_info["width"] or 1920
    height = video_info["height"] or 1080
    sample_rate = video_info["sample_rate"] or 48000

    mic_info = _probe_media(mic_path) if mic_path else None

    def s2f(seconds: float) -> int:
        """Seconds → frames at sequence rate. We use ceil/round carefully:
        Premiere is forgiving by ±1 frame, but we round to nearest to keep
        cuts as close to user intent as possible."""
        return int(round(seconds * fps))

    total_keep = sum(e - s for s, e in keep)
    seq_duration_frames = s2f(total_keep)

    # ---------- root ----------
    xmeml = ET.Element("xmeml", version="5")
    sequence = ET.SubElement(xmeml, "sequence", id="sequence-1")
    ET.SubElement(sequence, "name").text = sequence_name
    ET.SubElement(sequence, "duration").text = str(seq_duration_frames)
    _rate_xml(sequence, fps)
    tc = ET.SubElement(sequence, "timecode")
    _rate_xml(tc, fps)
    ET.SubElement(tc, "string").text = "00:00:00:00"
    ET.SubElement(tc, "frame").text = "0"
    ET.SubElement(tc, "displayformat").text = "NDF"

    media = ET.SubElement(sequence, "media")

    # ---------- video ----------
    video_el = ET.SubElement(media, "video")
    _add_video_format(video_el, width, height, fps)
    vtrack = ET.SubElement(video_el, "track")

    timeline_pos = 0  # cursor on the OUTPUT timeline (frames)
    video_file_written = False  # only embed full <file> once
    VIDEO_FILE_ID = "file-video"

    for i, (src_in, src_out) in enumerate(keep, start=1):
        seg_frames = s2f(src_out - src_in)
        if seg_frames <= 0:
            continue
        clip = ET.SubElement(vtrack, "clipitem", id=f"clipitem-v-{i}")
        ET.SubElement(clip, "name").text = f"{Path(video_path).stem} [{i}]"
        ET.SubElement(clip, "enabled").text = "TRUE"
        ET.SubElement(clip, "duration").text = str(s2f(video_info["duration"] or duration))
        _rate_xml(clip, fps)
        ET.SubElement(clip, "start").text = str(timeline_pos)
        ET.SubElement(clip, "end").text = str(timeline_pos + seg_frames)
        ET.SubElement(clip, "in").text = str(s2f(src_in))
        ET.SubElement(clip, "out").text = str(s2f(src_out))
        if not video_file_written:
            _build_video_file_node(clip, VIDEO_FILE_ID, video_path, video_info, fps)
            video_file_written = True
        else:
            ET.SubElement(clip, "file", id=VIDEO_FILE_ID)
        # Link to the matching audio clip from this same source so they move
        # together in Premiere.
        link = ET.SubElement(clip, "link")
        ET.SubElement(link, "linkclipref").text = f"clipitem-v-{i}"
        ET.SubElement(link, "mediatype").text = "video"
        ET.SubElement(link, "trackindex").text = "1"
        ET.SubElement(link, "clipindex").text = str(i)
        link2 = ET.SubElement(clip, "link")
        ET.SubElement(link2, "linkclipref").text = f"clipitem-a-{i}"
        ET.SubElement(link2, "mediatype").text = "audio"
        ET.SubElement(link2, "trackindex").text = "1"
        ET.SubElement(link2, "clipindex").text = str(i)
        timeline_pos += seg_frames

    # ---------- audio ----------
    audio_el = ET.SubElement(media, "audio")
    _add_audio_format(audio_el, sample_rate)

    # Audio track 1: from the video file
    atrack_video = ET.SubElement(audio_el, "track")
    timeline_pos = 0
    for i, (src_in, src_out) in enumerate(keep, start=1):
        seg_frames = s2f(src_out - src_in)
        if seg_frames <= 0:
            continue
        clip = ET.SubElement(atrack_video, "clipitem", id=f"clipitem-a-{i}")
        ET.SubElement(clip, "name").text = f"{Path(video_path).stem} [audio {i}]"
        ET.SubElement(clip, "enabled").text = "TRUE"
        ET.SubElement(clip, "duration").text = str(s2f(video_info["duration"] or duration))
        _rate_xml(clip, fps)
        ET.SubElement(clip, "start").text = str(timeline_pos)
        ET.SubElement(clip, "end").text = str(timeline_pos + seg_frames)
        ET.SubElement(clip, "in").text = str(s2f(src_in))
        ET.SubElement(clip, "out").text = str(s2f(src_out))
        ET.SubElement(clip, "file", id=VIDEO_FILE_ID)
        st = ET.SubElement(clip, "sourcetrack")
        ET.SubElement(st, "mediatype").text = "audio"
        ET.SubElement(st, "trackindex").text = "1"
        # Link back to the video clip so they stay in sync.
        link = ET.SubElement(clip, "link")
        ET.SubElement(link, "linkclipref").text = f"clipitem-v-{i}"
        ET.SubElement(link, "mediatype").text = "video"
        ET.SubElement(link, "trackindex").text = "1"
        ET.SubElement(link, "clipindex").text = str(i)
        link2 = ET.SubElement(clip, "link")
        ET.SubElement(link2, "linkclipref").text = f"clipitem-a-{i}"
        ET.SubElement(link2, "mediatype").text = "audio"
        ET.SubElement(link2, "trackindex").text = "1"
        ET.SubElement(link2, "clipindex").text = str(i)
        timeline_pos += seg_frames

    # Audio track 2: separate mic, only if provided
    if mic_path and mic_info:
        MIC_FILE_ID = "file-mic"
        atrack_mic = ET.SubElement(audio_el, "track")
        mic_dur = mic_info["duration"]
        timeline_pos = 0
        mic_file_written = False
        mic_clip_idx = 0
        for src_in, src_out in keep:
            seg_frames = s2f(src_out - src_in)
            if seg_frames <= 0:
                continue
            # Convert video-time segment → mic-time segment.
            mic_in = src_in - mic_offset
            mic_out = src_out - mic_offset
            # Clip to mic's available range.
            head_skip = max(0.0, -mic_in)  # mic doesn't cover the start of this segment
            tail_cut = max(0.0, mic_out - mic_dur) if mic_dur else 0.0
            effective_in = max(0.0, mic_in)
            effective_out = min(mic_dur or mic_out, mic_out)
            if effective_out - effective_in <= 0.01:
                # Mic doesn't cover this segment at all — skip.
                timeline_pos += seg_frames
                continue
            mic_clip_idx += 1
            clip = ET.SubElement(atrack_mic, "clipitem", id=f"clipitem-mic-{mic_clip_idx}")
            ET.SubElement(clip, "name").text = f"{Path(mic_path).stem} [{mic_clip_idx}]"
            ET.SubElement(clip, "enabled").text = "TRUE"
            ET.SubElement(clip, "duration").text = str(s2f(mic_dur or duration))
            _rate_xml(clip, fps)
            # Place mic clip on the OUTPUT timeline shifted by head_skip if
            # the mic doesn't cover the very start of this kept segment.
            tl_start = timeline_pos + s2f(head_skip)
            tl_end = timeline_pos + seg_frames - s2f(tail_cut)
            ET.SubElement(clip, "start").text = str(tl_start)
            ET.SubElement(clip, "end").text = str(tl_end)
            ET.SubElement(clip, "in").text = str(s2f(effective_in))
            ET.SubElement(clip, "out").text = str(s2f(effective_out))
            if not mic_file_written:
                _build_audio_only_file_node(clip, MIC_FILE_ID, mic_path, mic_info)
                mic_file_written = True
            else:
                ET.SubElement(clip, "file", id=MIC_FILE_ID)
            st = ET.SubElement(clip, "sourcetrack")
            ET.SubElement(st, "mediatype").text = "audio"
            ET.SubElement(st, "trackindex").text = "1"
            timeline_pos += seg_frames

    # ---------- serialize ----------
    ET.indent(xmeml, space="  ")
    body = ET.tostring(xmeml, encoding="unicode")
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE xmeml>\n'
        + body
    )
