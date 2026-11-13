from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Optional, Tuple

log = logging.getLogger("apply_zoom")

DEFAULT_VIEWPORT = (1280, 720)
DEFAULT_FPS = 25.0
DEFAULT_ZOOM_FACTOR = 2.0
DEFAULT_RAMP_SEC = 0.5
MAX_ZOOM = 10.0
OPEN_END = 10**9


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
