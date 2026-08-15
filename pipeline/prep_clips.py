"""Trim and normalise raw capture into one clip per camera.

Reads data/raw-manifest.json, which maps each camera to a raw file and the wall
clock of that file's first frame:

    {"raws": [
      {"camera_id": "CMR-0185", "file": "raw/battery.mp4",
       "started": "2026-08-15T18:00:12-07:00"}
    ]}

Trims a window around each logged wave with generous padding, normalises to a
uniform resolution and frame rate, and writes clips/<CAMERA_ID>.mp4.

Two things here are load-bearing:

* **Padding.** At least ninety seconds either side of the wave. The detector
  must have room to find the rider at a time nobody logged - that is what makes
  the eval mean anything.
* **The sidecar.** clips/clips.json records, per clip, the seed-relative second
  that clip-local t=0 corresponds to. Get this wrong and every detection shifts
  silently; the eval then looks broken for reasons unrelated to detection.

This module reads the wave log, to pick trim windows. detect.py must not.

    python -m pipeline.prep_clips --dry-run
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import (CLIP_INDEX, CLIPS, DATA, ROOT, from_iso, load_cameras,
                     load_json, load_t0, write_json)

RAW_MANIFEST = DATA / "raw-manifest.json"


def build_windows(waves: list[dict[str, Any]], raws: dict[str, dict[str, Any]],
                  t0: datetime, pad_s: float) -> list[dict[str, Any]]:
    """One trim window per camera, spanning all of that camera's waves + padding."""
    by_cam: dict[str, list[float]] = {}
    for w in waves:
        cam = w["camera_id"]
        if cam not in raws:
            continue
        by_cam.setdefault(cam, []).append(from_iso(t0, w["t"]))

    windows = []
    for cam, wave_times in sorted(by_cam.items()):
        raw = raws[cam]
        raw_start = from_iso(t0, raw["started"])  # seed-relative sec of raw frame 0
        first, last = min(wave_times), max(wave_times)

        start_rel = first - pad_s          # seed-relative
        end_rel = last + pad_s
        ss = start_rel - raw_start         # clip-local seek into the raw file

        if ss < 0:                         # raw does not reach back far enough
            start_rel -= ss                # clamp, and keep the mapping honest
            ss = 0.0

        windows.append({
            "camera_id": cam,
            "source": raw["file"],
            "ss": round(ss, 3),
            "duration": round(end_rel - start_rel, 3),
            # THE mapping: clip-local 0 s == this many seconds after the seed.
            "clip_offset_s": round(start_rel, 3),
            "waves_covered": len(wave_times),
        })
    return windows


def run_ffmpeg(win: dict[str, Any], fps: int, width: int, dry: bool) -> Path | None:
    out = CLIPS / f"{win['camera_id']}.mp4"
    cmd = [
        "ffmpeg", "-nostdin", "-y",
        "-ss", str(win["ss"]),
        "-t", str(win["duration"]),
        "-i", str(ROOT / win["source"]),
        "-r", str(fps),
        "-vf", f"scale={width}:-2",
        "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
        "-an", str(out),
    ]
    if dry:
        print("  " + " ".join(cmd))
        return out
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"  ffmpeg FAILED for {win['camera_id']}:\n{proc.stderr[-600:]}",
              file=sys.stderr)
        return None
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pad", type=float, default=90.0,
                    help="seconds of padding either side of a wave (min 90)")
    ap.add_argument("--fps", type=int, default=15)
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--dry-run", action="store_true",
                    help="print ffmpeg commands and write the sidecar only")
    args = ap.parse_args()

    if args.pad < 90:
        print(f"refusing pad={args.pad}s: the build doc requires >= 90 s either "
              f"side, so the detector can find the rider at unlogged times",
              file=sys.stderr)
        sys.exit(2)

    if not RAW_MANIFEST.exists():
        print(f"missing {RAW_MANIFEST.relative_to(ROOT)} - see this module's "
              f"docstring for the shape", file=sys.stderr)
        sys.exit(2)
    if not args.dry_run and not shutil.which("ffmpeg"):
        print("ffmpeg not on PATH", file=sys.stderr)
        sys.exit(2)

    cams = load_cameras()
    t0 = load_t0(cams)
    raws = {r["camera_id"]: r for r in load_json(RAW_MANIFEST)["raws"]}
    waves = load_json(DATA / "wave-log.json")["waves"]

    windows = build_windows(waves, raws, t0, args.pad)
    if not windows:
        print("no windows: raw-manifest and wave-log share no camera ids",
              file=sys.stderr)
        sys.exit(1)

    CLIPS.mkdir(parents=True, exist_ok=True)
    index = []
    for win in windows:
        print(f"{win['camera_id']}  ss={win['ss']:.1f}s  dur={win['duration']:.1f}s  "
              f"clip0 -> t{win['clip_offset_s']:+.1f}s")
        out = run_ffmpeg(win, args.fps, args.width, args.dry_run)
        if out is None:
            continue
        index.append({
            "camera_id": win["camera_id"],
            "clip": f"clips/{win['camera_id']}.mp4",
            "clip_offset_s": win["clip_offset_s"],
            "duration_s": win["duration"],
            "fps": args.fps,
            "width": args.width,
            "source": win["source"],
        })

    write_json(CLIP_INDEX, {
        "meta": {
            "note": "clip_offset_s is the seed-relative second that clip-local "
                    "t=0 maps to. detect.py adds it to every detection.",
            "t0": cams["meta"]["t0"],
            "pad_s": args.pad,
        },
        "clips": index,
    })
    print(f"\nwrote {CLIP_INDEX.relative_to(ROOT)}  ({len(index)} clips)")


if __name__ == "__main__":
    main()
