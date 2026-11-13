from __future__ import annotations

import json
import logging
import math
import os
import argparse
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger("apply_zoom")

DEFAULT_VIEWPORT = (1280, 720)
DEFAULT_FPS = 25.0
DEFAULT_ZOOM_FACTOR = 2.0
DEFAULT_RAMP_SEC = 0.5
MAX_ZOOM = 10.0
OPEN_END = 10**9
SUPERSAMPLE = 2
VP9_CRF = 32


class SidecarError(ValueError):
    """Failed to read sidecar or sidecar is incorrect..."""


@dataclass(frozen=True)
class Event:
    t: float
    x: float
    y: float
    type: str


@dataclass(frozen=True)
class Sidecar:
    viewport: Tuple[int, int]
    fps: float
    zoom_factor: float
    ramp_sec: float
    events: Tuple[Event, ...]


@dataclass(frozen=True)
class Segment:
    start: int
    end: Optional[int]
    cx: float
    cy: float


def to_frame(t, fps):
    return int(t * fps + 0.5)


def ramp_frames(ramp_sec, fps):
    return max(1, int(ramp_sec * fps + 0.5))


def ease(u):
    u = min(1.0, max(0.0, u))
    return u * u * (3.0 - 2.0 * u)


def map_to_video(x, y, viewport, video_size):
    vw, vh = viewport
    w, h = video_size
    px = min(max(x * w / vw, 0.0), float(w))
    py = min(max(y * h / vh, 0.0), float(h))
    return px, py


def _fmt(v):
    return ("%.6f" % v).rstrip("0").rstrip(".") or "0"


def _num(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Number expected, got %r" % (value,))
    v = float(value)
    if not math.isfinite(v):
        raise ValueError("Finite number expected")
    return v


def normalize_events(raw):
    if not isinstance(raw, list):
        raise SidecarError("'events' must be a list")

    parsed = []
    for i, item in enumerate(raw):
        try:
            if not isinstance(item, dict):
                raise ValueError("not an object")
            t, x, y = _num(item["t"]), _num(item["x"]), _num(item["y"])
            if t < 0:
                raise ValueError("negative t")
            typ = item.get("type")
            if typ is not None and typ not in ("in", "out"):
                raise ValueError("bad type %r" % (typ,))
        except (KeyError, ValueError) as e:
            log.warning("events[%d] skipped: %s", i, e)
            continue
        parsed.append((t, i, x, y, typ))

    parsed.sort(key=lambda p: (p[0], p[1]))

    events, state = [], "out"
    for t, i, x, y, typ in parsed:
        want = "in" if state == "out" else "out"
        if typ is None:
            typ = want
        elif typ != want:
            log.warning(
                "events[%d] skipped: redundant '%s' while zoom is '%s'", i, typ, state
            )
            continue
        events.append(Event(t, x, y, typ))
        state = typ
    return events


def _setting(data, key, default, lo, hi):
    try:
        v = _num(data.get(key, default))
    except ValueError as e:
        raise SidecarError("%s: %s" % (key, e)) from e
    if not lo <= v <= hi:
        raise SidecarError("%s must be in [%s, %s], got %s" % (key, lo, hi, v))
    return v


def parse_sidecar(data):
    if not isinstance(data, dict):
        raise SidecarError("sidecar must be a JSON object")

    vp = data.get("viewport")
    if vp is None:
        width, height = DEFAULT_VIEWPORT
    elif isinstance(vp, dict):
        try:
            width = int(_num(vp.get("width", DEFAULT_VIEWPORT[0])))
            height = int(_num(vp.get("height", DEFAULT_VIEWPORT[1])))
        except ValueError as e:
            raise SidecarError("viewport: %s" % e) from e
        if width <= 0 or height <= 0:
            raise SidecarError("viewport sizes must be positive")
    else:
        raise SidecarError("'viewport' must be an object")

    return Sidecar(
        viewport=(width, height),
        fps=_setting(data, "fps", DEFAULT_FPS, 1, 240),
        zoom_factor=_setting(data, "zoom_factor", DEFAULT_ZOOM_FACTOR, 1, MAX_ZOOM),
        ramp_sec=_setting(data, "ramp_sec", DEFAULT_RAMP_SEC, 0, 10),
        events=tuple(normalize_events(data.get("events", []))),
    )


def load_sidecar(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        raise SidecarError("invalid JSON in %s: %s" % (path, e)) from e
    return parse_sidecar(data)


def find_sidecar(video_path):
    """Ищет <video>.zoom.json, затем <video без расширения>.zoom.json."""
    stem, _ = os.path.splitext(video_path)
    for p in (video_path + ".zoom.json", stem + ".zoom.json"):
        if os.path.isfile(p):
            return p
    return None


def build_segments(events, fps, ramp_sec, viewport, video_size=None):
    video_size = video_size or viewport
    r = ramp_frames(ramp_sec, fps)
    segments, free_from, i = [], 0, 0
    while i < len(events):
        ev_in = events[i]
        ev_out = events[i + 1] if i + 1 < len(events) else None
        i += 2

        px, py = map_to_video(ev_in.x, ev_in.y, viewport, video_size)
        cx, cy = px / video_size[0], py / video_size[1]

        start = max(to_frame(ev_in.t, fps), free_from)

        if ev_out is None:
            segments.append(Segment(start, None, cx, cy))
            break

        end = max(to_frame(ev_out.t, fps), start + r)
        segments.append(Segment(start, end, cx, cy))
        free_from = end + r
    return segments


def _ease_expr(frame_start, r):
    u = "min(1,max(0,(on-%d)/%d))" % (frame_start, r)
    return "(%s*%s*(3-2*%s))" % (u, u, u)


def zoom_expr(segments, zoom_factor, r):
    if not segments:
        return "1"
    parts = []
    for s in segments:
        rise = _ease_expr(s.start, r)
        parts.append(
            rise if s.end is None else "(%s-%s)" % (rise, _ease_expr(s.end, r))
        )
    return "1+%s*(%s)" % (_fmt(zoom_factor - 1.0), "+".join(parts))


def _center_expr(segments, r, attr):
    expr = "0.5"
    for s in reversed(segments):
        end = OPEN_END if s.end is None else s.end + r
        expr = "if(between(on,%d,%d),%s,%s)" % (
            s.start,
            end,
            _fmt(getattr(s, attr)),
            expr,
        )
    return expr


def position_expr(segments, r, axis):
    size = "iw" if axis == "x" else "ih"
    center = _center_expr(segments, r, "cx" if axis == "x" else "cy")
    return "max(0,min((%s)*%s-%s/zoom/2,%s-%s/zoom))" % (center, size, size, size, size)


def build_zoompan_filter(segments, zoom_factor, ramp_sec, fps, out_size):
    r = ramp_frames(ramp_sec, fps)
    w, h = out_size
    return "zoompan=z='%s':x='%s':y='%s':d=1:s=%dx%d:fps=%s" % (
        zoom_expr(segments, zoom_factor, r),
        position_expr(segments, r, "x"),
        position_expr(segments, r, "y"),
        w,
        h,
        _fmt(fps),
    )


def build_filter(sc, video_size=None):
    if not sc.events:
        return None
    size = video_size or sc.viewport
    segments = build_segments(sc.events, sc.fps, sc.ramp_sec, sc.viewport, size)
    return build_zoompan_filter(segments, sc.zoom_factor, sc.ramp_sec, sc.fps, size)


def zoom_at_frame(segments, n, zoom_factor, r):
    k = 0.0
    for s in segments:
        k += ease((n - s.start) / r)
        if s.end is not None:
            k -= ease((n - s.end) / r)
    return 1.0 + (zoom_factor - 1.0) * k


def crop_at_frame(segments, n, zoom_factor, r, iw, ih):
    z = zoom_at_frame(segments, n, zoom_factor, r)
    cx = cy = 0.5
    for s in segments:
        end = OPEN_END if s.end is None else s.end + r
        if s.start <= n <= end:
            cx, cy = s.cx, s.cy
            break
    x = max(0.0, min(cx * iw - iw / z / 2, iw - iw / z))
    y = max(0.0, min(cy * ih - ih / z / 2, ih - ih / z))
    return z, x, y


class FfmpegError(RuntimeError):
    """ffmpeg is missing or executed with error."""


@dataclass(frozen=True)
class Result:
    output: str
    command: Optional[list]
    copied: bool


def default_output_path(video_path):
    stem, _ = os.path.splitext(video_path)
    return stem + ".zoomed.webm"


def resolve_ffmpeg(ffmpeg):
    path = shutil.which(ffmpeg)
    if path is None:
        raise FfmpegError(
            "ffmpeg not found (%r). Install ffmpeg or pass --ffmpeg <path>" % ffmpeg
        )
    return path


def probe_size(ffmpeg, video_path):
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", video_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    except OSError:
        return None
    m = re.search(r"Video:.*?,\s*(\d{2,5})x(\d{2,5})", proc.stderr or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def build_command(ffmpeg, video_path, output_path, zoompan, fps, size):
    w, h = size
    chain = ["fps=%s" % _fmt(fps)]  # постоянная частота кадров: on == номер кадра
    if SUPERSAMPLE > 1:
        chain.append("scale=%d:%d" % (w * SUPERSAMPLE, h * SUPERSAMPLE))
    chain += [zoompan, "format=yuv420p"]
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-y",
        "-i",
        video_path,
        "-vf",
        ",".join(chain),
        "-c:v",
        "libvpx-vp9",
        "-b:v",
        "0",
        "-crf",
        str(VP9_CRF),
        "-row-mt",
        "1",
        "-an",
        "-f",
        "webm",
        output_path,
    ]
