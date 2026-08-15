"""Shared contract helpers.

The frozen contract lives in frontend/CONTRACT.md and is what frontend/feed.js
actually consumes. Every module here joins on `camera_id` and speaks ISO-8601
wall clock, because that is what the frontend reads.

Seed-relative minutes (the `t` in the build doc) are an *internal* convenience.
They are converted to wall clock at the contract boundary, in exactly one place:
`to_iso()`. Do not let integer minutes leak into emitted records.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CLIPS = ROOT / "clips"

CAMERAS_JSON = DATA / "cameras.json"
CLIP_INDEX = CLIPS / "clips.json"
PIPELINE_OUT = DATA / "sightings.json"
FRONTEND_PAYLOAD = ROOT / "frontend" / "data" / "sightings.json"

# Contract enums. Ordered as the marker state machine progresses.
STATES = ("detected", "unverified", "confirmed", "linked", "lost")
CLASSES = ("scooter", "bike")

SPEED_M_PER_MIN = 233  # ~14 km/h, cycling


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

def load_t0(cameras: dict[str, Any]) -> datetime:
    """Wall clock corresponding to seed-relative t=0."""
    return datetime.fromisoformat(cameras["meta"]["t0"])


def to_iso(t0: datetime, seconds: float) -> str:
    """Seed-relative seconds -> ISO-8601 with offset, matching the contract."""
    return (t0 + timedelta(seconds=seconds)).isoformat(timespec="seconds")


def from_iso(t0: datetime, iso: str) -> float:
    """ISO-8601 -> seed-relative seconds."""
    return (datetime.fromisoformat(iso) - t0).total_seconds()


# --------------------------------------------------------------------------
# geo
# --------------------------------------------------------------------------

def distance_m(a: dict[str, float], b: dict[str, float]) -> float:
    """Equirectangular approximation.

    Correct to well under a metre at downtown scale; haversine is unnecessary
    here and the build doc explicitly rules it out.
    """
    dx = (b["lon"] - a["lon"]) * 111320 * math.cos(math.radians(a["lat"]))
    dy = (b["lat"] - a["lat"]) * 110540
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------
# io
# --------------------------------------------------------------------------

def load_json(path: Path) -> Any:
    with open(path) as fh:
        return json.load(fh)


def write_json(path: Path, payload: Any) -> None:
    """Atomic write. A half-written sightings.json mid-demo is unrecoverable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def load_cameras() -> dict[str, Any]:
    return load_json(CAMERAS_JSON)


def camera_index(cameras: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {c["id"]: c for c in cameras["cameras"]}
