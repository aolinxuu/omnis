"""Record SDOT camera video for the demo — the piece the handover's risk #4 says
must be running before the ride, because SDOT keeps nothing.

For each camera it runs two recorders at once, because SDOT's stream server flaps
between 200 / 404 / silence within the hour:

  * HLS video via ffmpeg (1080p), written as 5-minute segments named by wall
    clock: raw/<CAM>/<CAM>-YYYYmmddTHHMMSS.mp4. ffmpeg is supervised and
    restarted with backoff whenever the stream drops.
  * the 1920px still endpoint polled every --still-every seconds into
    raw/<CAM>/stills/YYYYmmddTHHMMSS.jpg (identical frames and SDOT's
    "under maintenance" card are skipped). This is the fallback recorder for
    cameras whose stream is 404 that hour, and it never died today.

It writes data/raw-manifest.json (the shape pipeline/prep_clips.py expects) from
the segment names, so the pipeline can pick the recording up directly.

    python -m pipeline.capture --route pink --minutes 120
    python -m pipeline.capture --cameras CMR-0302,CMR-0069 --minutes 30
    python -m pipeline.capture --route pink,slu --minutes 0      # until Ctrl-C
    python -m pipeline.capture --manifest                          # (re)build the manifest only

Camera URLs come from the frontend roster (frontend/data/sightings.json,
read-only), which has image + stream for every SDOT camera. Stdlib + ffmpeg +
requests only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

from .common import DATA, ROOT, load_json, write_json

RAW = ROOT / "raw"
MANIFEST = DATA / "raw-manifest.json"
FRONTEND_ROSTER = ROOT / "frontend" / "data" / "sightings.json"
PLACEHOLDER_MD5 = {"1c2dbf6b9e1018d3eb13b6f26a33dbfa"}   # SDOT "CAMERA UNDER MAINTENANCE"

ROUTES = {
    # the pink-shirt route recorded 2026-08-15 (2nd Ave south from Pike, cross to 3rd, back north)
    "pink": ["CMR-0302", "CMR-0303", "CMR-0069", "CMR-0415", "CMR-0218", "CMR-0305", "CMR-0191",
             "CMR-0035", "CMR-0304", "CMR-0178", "CMR-0217"],
    # ride 1 in the demo data: 2nd Ave bike lane, Battery -> S Jackson
    "downtown": ["CMR-0185", "CMR-0264", "CMR-0240", "CMR-0239", "CMR-0030", "CMR-0302",
                 "CMR-0069", "CMR-0415", "CMR-0218", "CMR-0265", "CMR-0425"],
    # ride 2 / the venue: South Lake Union loop from 1700 Westlake Ave N
    "slu": ["CMR-0184", "CMR-0267", "CMR-0260", "CMR-0146", "CMR-0154", "CMR-0266",
            "CMR-0202", "CMR-0259", "CMR-0211", "CMR-0203"],
}

STOP = threading.Event()


def roster() -> dict[str, dict[str, Any]]:
    d = load_json(FRONTEND_ROSTER)
    return {c["id"]: c for c in d["cameras"] if c.get("image")}


def stamp(t: float | None = None) -> str:
    return datetime.fromtimestamp(t or time.time()).strftime("%Y%m%dT%H%M%S")


def hls_ok(url: str) -> bool:
    try:
        r = requests.get(url, timeout=8)
        return r.status_code == 200 and b"#EXTM3U" in r.content[:64]
    except Exception:
        return False


def record_hls(cam: dict[str, Any], out_dir: Path, segment_s: int, log) -> None:
    """Supervise one ffmpeg per camera; restart with backoff when the stream drops."""
    url = cam["stream"]
    backoff = 5.0
    while not STOP.is_set():
        if not hls_ok(url):
            log(f"{cam['id']}  stream not available (404/silent) - retry in {backoff:.0f}s; stills continue")
            STOP.wait(backoff); backoff = min(backoff * 1.6, 120.0); continue
        pattern = str(out_dir / f"{cam['id']}-%Y%m%dT%H%M%S.mp4")
        cmd = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error",
               "-rw_timeout", "15000000", "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "10",
               "-i", url, "-c", "copy", "-an",
               "-f", "segment", "-segment_time", str(segment_s), "-reset_timestamps", "1", "-strftime", "1",
               "-segment_format_options", "movflags=+faststart", pattern]
        log(f"{cam['id']}  recording HLS -> {out_dir.name}/ (segments {segment_s}s)")
        started = time.time()
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        while proc.poll() is None and not STOP.is_set():
            time.sleep(1.0)
        if proc.poll() is None:
            proc.send_signal(signal.SIGINT)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        err = (proc.stderr.read() or "").strip().splitlines()[-1:] if proc.stderr else []
        ran = time.time() - started
        if STOP.is_set():
            break
        log(f"{cam['id']}  ffmpeg exited after {ran:.0f}s ({err[0][:120] if err else 'no error text'}) - restarting")
        backoff = 5.0 if ran > 60 else min(backoff * 1.6, 120.0)
        STOP.wait(backoff)


def record_stills(cam: dict[str, Any], out_dir: Path, every_s: float, log) -> None:
    """Poll the still endpoint; skip identical frames and the maintenance card."""
    d = out_dir / "stills"; d.mkdir(parents=True, exist_ok=True)
    last = None; n = 0; bad = 0
    while not STOP.is_set():
        t0 = time.time()
        try:
            r = requests.get(cam["image"], timeout=8, headers={"Cache-Control": "no-cache"})
            if r.status_code == 200 and len(r.content) > 1000:
                h = hashlib.md5(r.content).hexdigest()
                if h in PLACEHOLDER_MD5:
                    bad += 1
                    if bad in (1, 30, 300): log(f"{cam['id']}  still endpoint shows the maintenance card")
                elif h != last:
                    (d / f"{stamp()}.jpg").write_bytes(r.content); last = h; n += 1; bad = 0
                    if n in (1, 100, 1000): log(f"{cam['id']}  {n} stills saved")
        except Exception:
            pass
        STOP.wait(max(0.2, every_s - (time.time() - t0)))


def build_manifest(cams: list[str] | None = None) -> dict[str, Any]:
    """raw/<CAM>/<CAM>-<stamp>.mp4 -> {camera_id, file, started} entries (+ stills dirs noted)."""
    raws = []
    for camdir in sorted(RAW.glob("CMR-*")) + sorted(RAW.glob("*")):
        if not camdir.is_dir() or (cams and camdir.name not in cams):
            continue
        for f in sorted(camdir.glob(f"{camdir.name}-*.mp4")):
            m = re.search(r"-(\d{8}T\d{6})\.mp4$", f.name)
            if not m or f.stat().st_size < 100_000:
                continue
            started = datetime.strptime(m.group(1), "%Y%m%dT%H%M%S").astimezone()
            raws.append({"camera_id": camdir.name, "file": str(f.relative_to(ROOT)),
                         "started": started.isoformat(timespec="seconds"), "bytes": f.stat().st_size})
        stills = camdir / "stills"
        if stills.is_dir() and any(stills.iterdir()):
            raws.append({"camera_id": camdir.name, "file": str(stills.relative_to(ROOT)) + "/",
                         "started": None, "kind": "stills", "count": sum(1 for _ in stills.glob("*.jpg"))})
    seen = {r["camera_id"] for r in raws}
    return {"meta": {"note": "written by pipeline/capture.py from raw/. `started` is the wall clock of each segment's "
                             "first frame (segment filename). Stills dirs are the fallback recorder; prep_clips ignores them.",
                     "written": datetime.now().astimezone().isoformat(timespec="seconds"),
                     "cameras": sorted(seen)},
            "raws": [r for r in raws if r.get("kind") != "stills"],
            "stills": [r for r in raws if r.get("kind") == "stills"]}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--route", default=None, help=f"preset(s), comma-separated: {', '.join(ROUTES)}")
    ap.add_argument("--cameras", default=None, help="explicit camera ids, comma-separated (added to --route)")
    ap.add_argument("--minutes", type=float, default=60.0, help="how long to record; 0 = until Ctrl-C")
    ap.add_argument("--segment", type=int, default=300, help="HLS segment length in seconds")
    ap.add_argument("--still-every", type=float, default=2.0, help="seconds between still polls; 0 disables")
    ap.add_argument("--no-hls", action="store_true", help="stills only")
    ap.add_argument("--manifest", action="store_true", help="only (re)write data/raw-manifest.json from raw/")
    a = ap.parse_args()

    if a.manifest:
        m = build_manifest(); write_json(MANIFEST, m)
        print(f"wrote {MANIFEST.relative_to(ROOT)}: {len(m['raws'])} segments, {len(m['stills'])} stills dirs, cameras {m['meta']['cameras']}")
        return
    if requests is None:
        sys.exit("requests not installed")
    if not shutil.which("ffmpeg") and not a.no_hls:
        sys.exit("ffmpeg not found (apt install ffmpeg) - or use --no-hls for stills only")

    ids: list[str] = []
    for r in (a.route or "").split(","):
        if r.strip():
            ids += ROUTES.get(r.strip(), []) or sys.exit(f"unknown route {r}; choose from {list(ROUTES)}")
    ids += [c.strip() for c in (a.cameras or "").split(",") if c.strip()]
    ids = list(dict.fromkeys(ids))
    if not ids:
        sys.exit("give --route and/or --cameras")
    ros = roster()
    cams = [ros[i] for i in ids if i in ros]
    missing = [i for i in ids if i not in ros]
    if missing:
        print(f"not in roster (skipped): {missing}", file=sys.stderr)

    RAW.mkdir(exist_ok=True)
    logf = open(RAW / "capture.log", "a")
    def log(msg: str) -> None:
        line = f"{datetime.now().strftime('%H:%M:%S')}  {msg}"
        print(line, flush=True); logf.write(line + "\n"); logf.flush()

    log(f"capture start: {len(cams)} cameras, {a.minutes or '∞'} min, hls={'off' if a.no_hls else 'on'}, stills every {a.still_every}s")
    threads = []
    for c in cams:
        d = RAW / c["id"]; d.mkdir(parents=True, exist_ok=True)
        if not a.no_hls and c.get("stream"):
            threads.append(threading.Thread(target=record_hls, args=(c, d, a.segment, log), daemon=True, name=f"hls-{c['id']}"))
        if a.still_every > 0:
            threads.append(threading.Thread(target=record_stills, args=(c, d, a.still_every, log), daemon=True, name=f"still-{c['id']}"))
    for t in threads:
        t.start()

    def stop(*_):
        log("stopping..."); STOP.set()
    signal.signal(signal.SIGINT, stop); signal.signal(signal.SIGTERM, stop)

    deadline = time.time() + a.minutes * 60 if a.minutes else None
    last_manifest = 0.0
    while not STOP.is_set():
        if deadline and time.time() >= deadline:
            log("time is up"); STOP.set(); break
        if time.time() - last_manifest > 60:
            write_json(MANIFEST, build_manifest()); last_manifest = time.time()
        STOP.wait(2.0)
    for t in threads:
        t.join(timeout=15)
    m = build_manifest(); write_json(MANIFEST, m)
    log(f"done: {len(m['raws'])} video segments, stills for {len(m['stills'])} cameras -> {MANIFEST.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
