import json
import re
from pathlib import Path

import pytest

SPEC_EVENTS = [
    {"t": 3.0, "x": 640, "y": 360, "type": "in"},
    {"t": 8.0, "x": 640, "y": 360, "type": "out"},
]


@pytest.fixture
def spec_events():
    return [dict(e) for e in SPEC_EVENTS]


@pytest.fixture
def ffeval():
    """Считает выражение ffmpeg средствами Python (if, between, min, max)."""

    def _eval(expr, **variables):
        expr = re.sub(r"\bif\(", "_if(", expr)
        env = {
            "_if": lambda c, a, b: a if c else b,
            "between": lambda x, lo, hi: 1 if lo <= x <= hi else 0,
            "min": min,
            "max": max,
        }
        env.update(variables)
        return eval(expr, {"__builtins__": {}}, env)

    return _eval


@pytest.fixture
def video(tmp_path):
    """Поддельный .webm: для путей без ffmpeg содержимое не важно."""
    path = tmp_path / "clip.webm"
    path.write_bytes(b"fake-webm-bytes")
    return path


@pytest.fixture
def make_sidecar():
    def _make(video_path, events, **extra):
        data = {
            "viewport": {"width": 1280, "height": 720},
            "fps": 25,
            "zoom_factor": 2.0,
            "ramp_sec": 0.5,
            "events": events,
        }
        data.update(extra)
        path = Path(str(video_path) + ".zoom.json")
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    return _make
