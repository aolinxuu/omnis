"""Assemble the frozen-contract payload the frontend fetches.

frontend/feed.js fetches a single data/sightings.json carrying `cameras`,
`tracks`, `sightings`, `events`, `predictions` and `ground_truth`, keyed on
ISO-8601 wall clock. This module builds exactly that from pipeline output, so
the frontend never has to change.

By default it writes data/frontend-payload.json and touches nothing the
frontend reads. `--install` is the deliberate, reversible step that swaps it
into frontend/data/sightings.json, keeping a .bak.

    python -m pipeline.build_payload --validate
    python -m pipeline.build_payload --install
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .common import (DATA, FRONTEND_PAYLOAD, PIPELINE_OUT, ROOT, STATES,
                     camera_index, from_iso, load_cameras, load_json, load_t0,
                     to_iso, write_json)

BUILT = DATA / "frontend-payload.json"
SUBJECT_TRACK = "T-SUBJ"
LOST_GAP_S = 150.0     # linked-to-linked gap beyond which the subject is lost


def derive_lost(sightings: list[dict[str, Any]], t0: datetime,
                gap_s: float) -> list[dict[str, Any]]:
    """Emit a `lost` marker when the subject track goes quiet for too long.

    Derived from detections alone - never from the wave log. Confidence decays
    from the last known value, as the contract specifies.
    """
    linked = [s for s in sightings if s.get("track_id") == SUBJECT_TRACK]
    out = []
    for prev, nxt in zip(linked, linked[1:]):
        gap = from_iso(t0, nxt["t"]) - from_iso(t0, prev["t"])
        if gap <= gap_s:
            continue
        out.append({
            "id": f"L-{prev['id'][2:]}",
            "t": to_iso(t0, from_iso(t0, prev["t"]) + gap / 2),
            "camera_id": prev["camera_id"],
            "lat": prev["lat"],
            "lon": prev["lon"],
            "class": prev["class"],
            "state": "lost",
            "conf": round(prev["conf"] * 0.5, 3),
            "track_id": SUBJECT_TRACK,
            "note": f"no subject detection for {gap:.0f}s after {prev['camera_id']}",
        })
    return out


def build_events(cams: dict[str, Any], sightings: list[dict[str, Any]],
                 t0: datetime, captions: dict[str, str]) -> list[dict[str, Any]]:
    """Ticker content, derived from facts the pipeline actually established."""
    roster = cams["cameras"]
    dead = [c["id"] for c in roster if not c["alive"]]
    events = [{
        "t": to_iso(t0, -30),
        "kind": "system",
        "text": (f"capture window open · {len(roster)} cameras · "
                 f"{len(roster) - len(dead)} alive"
                 + (f" · dead: {', '.join(dead)}" if dead else "")),
    }, {
        "t": to_iso(t0, 0),
        "kind": "system",
        "text": (f"seed {cams['seed']['camera']} ({cams['seed']['name']}) · "
                 f"reachability clock started"),
    }]

    for s in sightings:
        if s["state"] == "linked":
            cap = captions.get(s["id"])
            events.append({
                "t": s["t"], "kind": "vss" if cap else "system",
                "camera_id": s["camera_id"],
                "text": cap or f"subject linked at {s['camera_id']} · conf {s['conf']:.2f}",
            })
        elif s["state"] == "lost":
            events.append({
                "t": s["t"], "kind": "system", "camera_id": s["camera_id"],
                "text": f"subject lost after {s['camera_id']} · confidence decaying",
            })

    events.sort(key=lambda e: e["t"])
    return events


def validate(payload: dict[str, Any]) -> list[str]:
    """Check the payload against what feed.js and evalPanel() actually require."""
    problems = []
    for key in ("meta", "cameras", "tracks", "sightings", "predictions",
                "events", "ground_truth"):
        if key not in payload:
            problems.append(f"missing top-level key: {key}")
    if problems:
        return problems

    cam_ids = {c["id"] for c in payload["cameras"]}
    for c in payload["cameras"]:
        for f in ("id", "name", "lat", "lon", "kind", "alive"):
            if f not in c:
                problems.append(f"camera {c.get('id')} missing {f}")

    for s in payload["sightings"]:
        for f in ("id", "t", "camera_id", "lat", "lon", "class", "state", "conf"):
            if f not in s:
                problems.append(f"sighting {s.get('id')} missing {f}")
        if s.get("state") not in STATES:
            problems.append(f"sighting {s.get('id')} bad state {s.get('state')!r}")
        if s.get("camera_id") not in cam_ids:
            problems.append(f"sighting {s.get('id')} unknown camera {s.get('camera_id')}")
        try:
            datetime.fromisoformat(s["t"])
        except (ValueError, KeyError):
            problems.append(f"sighting {s.get('id')} unparseable t {s.get('t')!r}")

    for g in payload["ground_truth"]:
        if g.get("camera_id") not in cam_ids:
            problems.append(f"ground_truth references unknown camera {g.get('camera_id')}")

    # evalPanel() only ever scores sightings whose state is exactly "linked".
    if payload["ground_truth"] and not any(
            s["state"] == "linked" for s in payload["sightings"]):
        problems.append("no sighting has state 'linked' - the eval table will "
                        "score 0 recall regardless of detection quality")
    return problems


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--install", action="store_true",
                    help="write into frontend/data/sightings.json (keeps a .bak)")
    ap.add_argument("--validate", action="store_true",
                    help="check against the frozen contract and exit non-zero on problems")
    ap.add_argument("--lost-gap", type=float, default=LOST_GAP_S)
    ap.add_argument("--detections", type=Path, default=PIPELINE_OUT,
                    help="detector output to build from (default data/sightings.json)")
    ap.add_argument("--out", type=Path, default=BUILT)
    args = ap.parse_args()

    if not args.detections.exists():
        print(f"missing {args.detections} - run detect.py first", file=sys.stderr)
        sys.exit(2)

    cams = load_cameras()
    t0 = load_t0(cams)
    det = load_json(args.detections)
    sightings = list(det["sightings"])

    captions_path = DATA / "captions.json"
    captions = load_json(captions_path).get("captions", {}) if captions_path.exists() else {}
    for s in sightings:
        if s["id"] in captions:
            s["note"] = captions[s["id"]]

    sightings += derive_lost(sightings, t0, args.lost_gap)
    sightings.sort(key=lambda s: s["t"])

    wave = load_json(DATA / "wave-log.json")

    payload = {
        "meta": {
            "note": "Built by pipeline/build_payload.py from detector output. "
                    "Shape is the frozen contract in frontend/CONTRACT.md.",
            "session": cams["meta"]["session"],
            "corridor": cams["meta"]["corridor"],
            "subject_track": SUBJECT_TRACK,
            "started": cams["meta"]["t0"],
            "generated": datetime.now().astimezone().isoformat(timespec="seconds"),
            "detector": det.get("meta", {}),
        },
        "cameras": cams["cameras"],
        "tracks": [{"id": SUBJECT_TRACK, "label": "SUBJECT (presenter, consented)",
                    "class": "scooter", "color": "amber"}],
        "sightings": sightings,
        # Routing/branch prediction is out of scope for this build. The frontend
        # renders an empty list without complaint.
        "predictions": [],
        "events": build_events(cams, sightings, t0, captions),
        "ground_truth": wave["waves"],
    }

    problems = validate(payload)
    if problems:
        print("CONTRACT PROBLEMS:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        if args.validate:
            sys.exit(1)

    write_json(args.out, payload)
    linked = sum(1 for s in payload["sightings"] if s["state"] == "linked")
    print(f"wrote {args.out}  "
          f"({len(payload['sightings'])} sightings, {linked} linked, "
          f"{len(payload['events'])} events)")

    if args.install:
        if FRONTEND_PAYLOAD.exists():
            bak = FRONTEND_PAYLOAD.with_suffix(".json.bak")
            shutil.copy2(FRONTEND_PAYLOAD, bak)
            print(f"backed up -> {bak.relative_to(ROOT)}")
        shutil.copy2(args.out, FRONTEND_PAYLOAD)
        print(f"installed -> {FRONTEND_PAYLOAD.relative_to(ROOT)}")
    elif not problems:
        print("not installed. re-run with --install to swap it into the frontend.")


if __name__ == "__main__":
    main()
