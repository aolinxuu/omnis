"""The "man in the pink shirt" demo scenario, pinned end to end.

Eight screen recordings of live SDOT cameras were captured on the evening of
2026-08-15 while the subject - a man in a bright pink shirt - ran south down 2nd
Ave from Pike St to Spring St, crossed to 3rd Ave, and came back north to
University St. This module hard-codes that scenario, which is the point: the
camera behind each clip comes from the banner burned into the video and its wall
clock from the capture filename. Nothing in the pipeline can infer either, so
guessing them at runtime would only add a way for the demo to break.

What it does, in the order the handover's "next steps" ask for:

    --register   put all eight clips in vss/ingest-index.json so vss/query.py
                 can join camera_id -> sensor_id like it does for any clip
    --sweep      ask VSS where the runner is in each clip, write
                 data/subject-hits.json
    --sightings  turn those hits into detector-shaped data/sightings.json, which
                 pipeline/build_payload.py turns into the frozen-contract payload
    --all        all three, in that order

Deliberately does NOT touch frontend/. Installing the payload is
`python -m pipeline.build_payload --install`, a separate and reversible step
that is Emily's side of the boundary in CLAUDE.md.

    python -m vss.pink_subject --all
    python -m vss.pink_subject --sweep --only CMR-0302
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import (DATA, PIPELINE_OUT, ROOT, camera_index,  # noqa: E402
                             load_cameras, load_t0, to_iso, write_json)
from vss.client import VSSClient, VSSError  # noqa: E402
from vss.ingest import INGEST_INDEX  # noqa: E402

HITS_OUT = DATA / "subject-hits.json"
CLIP_DIR = DATA / "clips"
SUBJECT_TRACK = "T-SUBJ"          # matches build_payload.SUBJECT_TRACK
SUBJECT_CLASS = "runner"          # display-only; app.js renders the string as-is

# video name in VST | camera id | capture wall clock | clip length
# camera ids read off the banner burned into each recording, then matched to
# data/cameras.json. The Broadway clip has camera_id None - that camera is not
# in the 112-camera roster, so it can be queried but cannot be placed on the map.
CLIPS: list[dict[str, Any]] = [
    {"video": "CMR-0302-184917", "camera_id": "CMR-0302", "file": "20260815-184917.mp4", "started": "2026-08-15T18:49:17-07:00", "duration_s": 12.4},
    {"video": "CMR-0302-184958", "camera_id": "CMR-0302", "file": "20260815-184958.mp4", "started": "2026-08-15T18:49:58-07:00", "duration_s": 20.3},
    {"video": "CMR-0069-185645", "camera_id": "CMR-0069", "file": "20260815-185645.mp4", "started": "2026-08-15T18:56:45-07:00", "duration_s": 45.4},
    {"video": "CMR-0415-190340", "camera_id": "CMR-0415", "file": "20260815-190340.mp4", "started": "2026-08-15T19:03:40-07:00", "duration_s": 46.3},
    {"video": "CMR-0305-190806", "camera_id": "CMR-0305", "file": "20260815-190806.mp4", "started": "2026-08-15T19:08:06-07:00", "duration_s": 67.8},
    {"video": "CMR-0191-191211", "camera_id": "CMR-0191", "file": "20260815-191211.mp4", "started": "2026-08-15T19:12:11-07:00", "duration_s": 56.6},
    {"video": "CMR-0035-191534", "camera_id": "CMR-0035", "file": "20260815-191534.mp4", "started": "2026-08-15T19:15:34-07:00", "duration_s": 36.5},
    {"video": "broadway-195253", "camera_id": None,       "file": "20260815-195253.mp4", "started": "2026-08-15T19:52:53-07:00", "duration_s": 48.1},
]

# Constrained format so ranking never depends on prose. Same discipline as
# vss/query.py's QUERY_PROMPT, with the two extra fields the demo narrates.
SUBJECT_PROMPT = (
    "A man in a bright pink or salmon shirt is the subject of a search. "
    "Answer in exactly this format and nothing else:\n"
    "VERDICT: YES or NO\n"
    "TIME: the second of this clip where he is clearest, or NONE\n"
    "MOTION: running, walking, standing, or NONE\n"
    "DIRECTION: the direction he moves across frame, or NONE\n"
    "DETAIL: one short sentence, or NONE"
)

_NEG = re.compile(r"\b(no|not|nobody|cannot|isn't|there is no)\b", re.I)
_PINK = re.compile(r"\bpink|salmon\b", re.I)
_MOTION = re.compile(r"\b(running|jogging|walking|standing|stationary|still)\b", re.I)
# Prose timestamps, most specific first: 00:01:23 | 01:23 | "0.0 seconds"
_HMS = re.compile(r"\b(\d{1,2}):(\d{2}):(\d{2})\b")
_MS = re.compile(r"\b(\d{1,2}):(\d{2})\b")
_SECS = re.compile(r"(\d+(?:\.\d+)?)\s*seconds?\b", re.I)


def _clean(value: str | None) -> str | None:
    """Trim markdown and any following field the model crammed onto one line."""
    if not value:
        return None
    v = re.split(r"\s*-?\s*\*\*", value.strip().lstrip("*").strip())[0]
    v = v.strip(" *-").strip()
    return v[:120] or None


def _time_from(text: str) -> float | None:
    """Seconds into the clip, from either the constrained field or prose.

    The VLM routinely ignores the requested format and writes "At timestamp
    00:00:04, ..." instead, so prose has to work as well as the field does.
    """
    if not text:
        return None
    m = _HMS.search(text)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    m = _MS.search(text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = _SECS.search(text)
    if m:
        return float(m.group(1))
    return None


def parse_answer(text: str) -> dict[str, Any]:
    """Pull the five fields out, tolerating a model that ignores the format."""
    text = text or ""

    def field(name: str) -> str | None:
        m = re.search(rf"{name}:\s*(.+)", text, re.I)
        if not m:
            return None
        v = m.group(1).strip().split("\n")[0].strip()
        return None if v.upper() in ("NONE", "N/A", "") else v

    verdict = field("VERDICT")
    if verdict:
        hit = verdict.upper().startswith("YES")
    else:
        # Prose: a mention of the pink shirt that is not a denial counts.
        hit = bool(_PINK.search(text)) and not _NEG.search(text[:60])

    t_clip = _time_from(field("TIME") or "")
    if t_clip is None:
        t_clip = _time_from(text)

    # Always normalise motion to one word from the known vocabulary. The model
    # sometimes answers in markdown on a single line ("** Walking - **Direction:**
    # ..."), and a raw field grab swallows the rest of the answer into it.
    m = _MOTION.search(field("MOTION") or "") or _MOTION.search(text)
    motion = m.group(1).lower() if m else None

    direction = _clean(field("DIRECTION"))
    detail = _clean(field("DETAIL")) or (text.strip().split("\n")[0][:200] or None)

    return {"hit": bool(hit), "t_clip": t_clip, "motion": motion,
            "direction": direction, "detail": detail,
            "verdict_raw": verdict}


def confidence(p: dict[str, Any]) -> float:
    """Hard-coded but not arbitrary: a hit that also pins a time and reads as
    running is worth more than a bare YES."""
    if not p["hit"]:
        return 0.0
    conf = 0.72
    if p["t_clip"] is not None:
        conf += 0.12
    if (p["motion"] or "").lower().startswith(("run", "jog")):
        conf += 0.11
    return round(min(conf, 0.97), 3)


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------

def register(client: VSSClient) -> dict[str, Any]:
    """Index every clip that VST actually has, camera_id -> sensor_id."""
    t0 = load_t0(load_cameras())
    index = {"meta": {}, "files": {}}
    if INGEST_INDEX.exists():
        index = json.loads(INGEST_INDEX.read_text())
    files = index.setdefault("files", {})

    for entry in CLIPS:
        v = client.find_video(entry["video"])
        if not v:
            print(f"  {entry['video']}: NOT in VST - upload it first", file=sys.stderr)
            continue
        cam = entry["camera_id"] or entry["video"]
        started = datetime.fromisoformat(entry["started"])
        files[cam] = {
            "sensor_id": v["sensorId"],
            "video": v["name"],
            "clip": f"data/clips/{entry['file']}",
            "clip_offset_s": (started - t0).total_seconds(),
            "duration_s": entry["duration_s"],
        }
        print(f"  {cam:16s} -> {v['sensorId']}  ({v['name']})")

    index["meta"] = {"endpoint": client.endpoint, "model": "agent",
                     "note": "camera_id -> VST sensor_id + upload name. query.py joins on this."}
    write_json(INGEST_INDEX, index)
    print(f"wrote {INGEST_INDEX.relative_to(ROOT)}  ({len(files)} entries)")
    return index


def sweep(client: VSSClient, only: set[str] | None = None) -> list[dict[str, Any]]:
    """Ask every clip where the runner is. One VLM call per clip."""
    out = []
    for entry in CLIPS:
        cam = entry["camera_id"] or entry["video"]
        if only and cam not in only and entry["video"] not in only:
            continue
        started = datetime.fromisoformat(entry["started"])
        rec = dict(entry, camera_key=cam)
        began = time.time()
        try:
            r = client.ask(entry["video"], SUBJECT_PROMPT)
            text = r["tool_result"] or r["answer"]
            rec["parsed"] = parse_answer(text)
            rec["raw_answer"] = text
        except VSSError as exc:
            rec["error"] = str(exc)[:400]
            rec["parsed"] = {"hit": False, "t_clip": None, "motion": None,
                             "direction": None, "detail": None, "verdict_raw": None}
        rec["query_s"] = round(time.time() - began, 1)

        p = rec["parsed"]
        if p["hit"]:
            off = p["t_clip"] if p["t_clip"] is not None else entry["duration_s"] / 2
            rec["seen_at"] = (started + timedelta(seconds=off)).isoformat(timespec="seconds")
        mark = "HIT " if p["hit"] else "miss"
        print(f"  {mark} {cam:16s} {entry['video']:18s} "
              f"t={p['t_clip']} motion={p['motion']} dir={p['direction']} [{rec['query_s']}s]")
        out.append(rec)

    write_json(HITS_OUT, {"meta": {"question": SUBJECT_PROMPT,
                                   "generated": datetime.now().astimezone().isoformat(timespec="seconds")},
                          "hits": out})
    print(f"wrote {HITS_OUT.relative_to(ROOT)}  ({sum(1 for r in out if r['parsed']['hit'])}/{len(out)} hits)")
    return out


def to_sightings(hits: list[dict[str, Any]]) -> dict[str, Any]:
    """Detector-shaped output for pipeline/build_payload.py.

    Every confirmed hit is `linked` to the subject track on purpose: evalPanel()
    in app.js scores only sightings whose state is exactly "linked", so anything
    else silently reports 0 recall however good the detection was. The handover
    calls this out as the one integration fact that costs you the demo.
    """
    cams = load_cameras()
    idx = camera_index(cams)
    sightings = []
    skipped = []

    # Re-parse the model's own words when they were kept, so a parser fix can be
    # applied to a cached sweep without spending another round of VLM calls.
    for rec in hits:
        if rec.get("raw_answer"):
            rec["parsed"] = parse_answer(rec["raw_answer"])
            if rec["parsed"]["hit"]:
                started = datetime.fromisoformat(rec["started"])
                off = rec["parsed"]["t_clip"]
                off = off if off is not None else rec["duration_s"] / 2
                rec["seen_at"] = (started + timedelta(seconds=off)).isoformat(timespec="seconds")

    for i, rec in enumerate(sorted((h for h in hits if h["parsed"]["hit"]),
                                   key=lambda h: h["started"])):
        cam_id = rec["camera_id"]
        if cam_id is None or cam_id not in idx:
            skipped.append(rec["video"])
            continue
        cam = idx[cam_id]
        p = rec["parsed"]
        note = p["detail"] or "man in a pink shirt"
        if p["direction"]:
            note = f"{note} (moving {p['direction']})"
        sightings.append({
            "id": f"S-{i + 1:03d}",
            "t": rec["seen_at"],
            "camera_id": cam_id,
            "lat": cam["lat"],
            "lon": cam["lon"],
            "class": SUBJECT_CLASS,
            "state": "linked",
            "conf": confidence(p),
            "track_id": SUBJECT_TRACK,
            "note": note,
            "source": f"vss:{rec['video']}",
        })

    payload = {
        "meta": {
            "detector": "vss.pink_subject",
            "note": "VSS 3.2.1 video_understanding over hard-coded SDOT screen "
                    "recordings; subject = man in a pink shirt.",
            "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
            "skipped_no_camera": skipped,
        },
        "sightings": sightings,
    }
    write_json(PIPELINE_OUT, payload)
    print(f"wrote {PIPELINE_OUT.relative_to(ROOT)}  ({len(sightings)} sightings"
          + (f", skipped {', '.join(skipped)} - camera not in roster)" if skipped else ")"))
    return payload


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default=None)
    ap.add_argument("--register", action="store_true")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--sightings", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--only", nargs="*", help="restrict the sweep to these camera ids or video names")
    args = ap.parse_args()

    if not any([args.register, args.sweep, args.sightings, args.all]):
        ap.error("pick at least one of --register / --sweep / --sightings / --all")

    client = VSSClient(args.endpoint) if args.endpoint else VSSClient()
    print(f"endpoint {client.endpoint}")
    client.health()
    print("healthy\n")

    if args.register or args.all:
        print("registering clips:")
        register(client)
        print()

    hits: list[dict[str, Any]] = []
    if args.sweep or args.all:
        print("sweeping for the subject:")
        hits = sweep(client, set(args.only) if args.only else None)
        print()

    if args.sightings or args.all:
        if not hits:
            if not HITS_OUT.exists():
                print(f"no {HITS_OUT.relative_to(ROOT)} - run --sweep first", file=sys.stderr)
                sys.exit(2)
            hits = json.loads(HITS_OUT.read_text())["hits"]
        print("building sightings:")
        to_sightings(hits)
        print("\nnext:  python -m pipeline.build_payload --validate")
        print("       python -m pipeline.build_payload --install   # touches frontend/, ask first")


if __name__ == "__main__":
    main()
