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
