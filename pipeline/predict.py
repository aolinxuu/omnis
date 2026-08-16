"""Trajectory prediction: where does the subject go next?

Given the ordered sightings of one track, predict the next camera(s) it will
reach, with probabilities and street-following paths, in the frozen contract's
`prediction` shape (see frontend/CONTRACT.md):

    {id, t, track_id, at: [lat, lon], at_camera,
     branches: [{label, p, path: [[lat, lon], ...]}, ...],   # p sums to 1
     actual?, resolved_at?}                                  # filled once known

The LLM does not do this. It is a classical scorer over the OSM road graph
(pipeline/roadgraph.py); the VLM only ever answers "what did the camera see".

Score for each candidate camera reachable by road within `horizon_s` at the
observed speed:

    score = exp(-(turn/σθ)^2) * exp(-dist/λ) * (0.35 if camera dead else 1)

`turn` is the angle between the heading the track came in on (last two
sightings, or the incoming road bearing) and the initial bearing of the road
path to the candidate - people mostly keep going. `dist` is road metres. Softmax
over scores gives p; the top-k become branches. Cameras already visited are
excluded so we predict progress, not U-turns.

    python -m pipeline.predict                       # from data/sightings.json (pipeline output)
    python -m pipeline.predict --track T-SUBJ --at CMR-0302
    python -m pipeline.predict --from-frontend       # dry run on frontend/data/sightings.json
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import datetime
from typing import Any

from .common import (DATA, FRONTEND_PAYLOAD, PIPELINE_OUT, distance_m, load_cameras, load_json, write_json)
from .roadgraph import ROAD_GRAPH, RoadGraph, bearing_deg, turn_deg

PREDICTIONS_OUT = DATA / "predictions.json"

SIGMA_TURN_DEG = 55.0      # how sharply we penalise turning away from the current heading
LAMBDA_M = 450.0           # distance decay (metres); shorter = "next camera" bias
DEAD_CAMERA_FACTOR = 0.35  # a dead camera can still be passed, but we would not see it
DEFAULT_SPEED_MPS = 233 / 60  # scooter ~14 km/h; cars pass a higher --speed
MIN_P = 0.05               # branches below this are folded into the others


def _iso(t: str) -> datetime:
    return datetime.fromisoformat(t)


def heading_from(sightings: list[dict[str, Any]], graph: RoadGraph) -> float | None:
    """Heading the track is travelling on, from the last two distinct camera positions."""
    pts = []
    for s in sightings:
        p = {"lat": s["lat"], "lon": s["lon"]}
        if not pts or (abs(pts[-1]["lat"] - p["lat"]) + abs(pts[-1]["lon"] - p["lon"])) > 1e-5:
            pts.append(p)
    if len(pts) < 2:
        return None
    a, b = pts[-2], pts[-1]
    # Follow the road between the two, not the straight line, when we can.
    na, nb = graph.nearest(a), graph.nearest(b)
    if na is not None and nb is not None and na != nb:
        r = graph.shortest_path(na, nb)
        if r and len(r[1]) >= 2:
            # bearing of the *last* stretch of that path = current heading
            tail = r[1][-min(len(r[1]), 6):]
            return bearing_deg(graph.pos[tail[0]], graph.pos[tail[-1]])
    return bearing_deg(a, b)


def path_turning(graph: RoadGraph, path: list[int], heading: float | None,
                 min_seg_m: float = 15.0) -> float:
    """Total heading change to follow `path`, degrees: the initial turn away from
    the current heading plus every real turn along the way (short jitters ignored).
    Straight on ~0, one right-angle turn ~90, a doubling-back ~180+."""
    segs = []
    acc, start = 0.0, path[0]
    for a, b in zip(path, path[1:]):
        acc += distance_m(graph.pos[a], graph.pos[b])
        if acc >= min_seg_m:
            segs.append(bearing_deg(graph.pos[start], graph.pos[b])); acc, start = 0.0, b
    if not segs:
        segs = [bearing_deg(graph.pos[path[0]], graph.pos[path[-1]])]
    total = abs(turn_deg(heading, segs[0])) if heading is not None else 0.0
    for x, y in zip(segs, segs[1:]):
        t = abs(turn_deg(x, y))
        if t >= 20.0:
            total += t
    return min(total, 200.0)


def speed_from(sightings: list[dict[str, Any]], graph: RoadGraph,
               default: float = DEFAULT_SPEED_MPS) -> float:
    """Metres per second along the road between the last two sightings."""
    if len(sightings) < 2:
        return default
    a, b = sightings[-2], sightings[-1]
    dt = (_iso(b["t"]) - _iso(a["t"])).total_seconds()
    if dt <= 0:
        return default
    na, nb = graph.nearest({"lat": a["lat"], "lon": a["lon"]}), graph.nearest({"lat": b["lat"], "lon": b["lon"]})
    r = graph.shortest_path(na, nb) if na is not None and nb is not None else None
    if not r or r[0] <= 0:
        return default
    v = r[0] / dt
    return min(max(v, 1.0), 25.0)  # clamp: 3.6 - 90 km/h


def predict(cameras: list[dict[str, Any]], track_sightings: list[dict[str, Any]],
            graph: RoadGraph, horizon_s: float = 180.0, top_k: int = 3,
            speed_mps: float | None = None, ignore_dead: bool = False) -> dict[str, Any] | None:
    """Return one prediction dict for the track's latest sighting, or None."""
    if not track_sightings:
        return None
    last = track_sightings[-1]
    here = {"lat": last["lat"], "lon": last["lon"]}
    src = graph.nearest(here)
    if src is None:
        return None
    heading = heading_from(track_sightings, graph)
    v = speed_mps or speed_from(track_sightings, graph)
    reach_m = v * horizon_s
    visited = {s["camera_id"] for s in track_sightings}

    cands = []
    for c in cameras:
        if c["id"] in visited or c["id"] == last["camera_id"]:
            continue
        cp = {"lat": c["lat"], "lon": c["lon"]}
        # cheap prefilter: straight-line distance can't exceed road distance
        if math.hypot((cp["lat"] - here["lat"]) * 110540,
                      (cp["lon"] - here["lon"]) * 111320 * math.cos(math.radians(here["lat"]))) > reach_m:
            continue
        dst = graph.nearest(cp)
        if dst is None or dst == src:
            continue
        r = graph.shortest_path(src, dst, max_m=reach_m)
        if not r:
            continue
        dist_m, path = r
        b0 = graph.initial_bearing(path)
        turn = path_turning(graph, path, heading)
        score = math.exp(-(turn / SIGMA_TURN_DEG) ** 2) * math.exp(-dist_m / LAMBDA_M)
        if not c.get("alive", True) and not ignore_dead:
            score *= DEAD_CAMERA_FACTOR
        cands.append((score, c, dist_m, path, b0, turn))
    if not cands:
        return None

    cands.sort(key=lambda x: -x[0])
    # co-located cameras (same intersection, e.g. Pike NS / Pike EW) are one destination
    merged, seen = [], []
    for cand in cands:
        c = cand[1]
        twin = next((m for m in merged if distance_m(m[1], c) < 30.0), None)
        if twin:
            twin[1].setdefault("also", []).append(c["id"]); continue
        merged.append([cand[0], dict(c), *cand[2:]])
    top = merged[:top_k]
    z = sum(s for s, *_ in top)
    branches = []
    for score, c, dist_m, path, b0, turn in top:
        p = score / z
        names = graph.street_names(path)
        via = " → ".join(names[:2]) if names else "local streets"
        branches.append({
            "label": f"{c['name']} via {via}",
            "p": p, "camera_id": c["id"], "also_cameras": c.get("also", []), "distance_m": round(dist_m),
            "eta_s": round(dist_m / v), "turn_deg": round(turn),
            "path": [[round(la, 6), round(lo, 6)] for la, lo in graph.coords(path)],
        })
    # fold tiny branches and renormalise so p sums to exactly 1
    branches = [b for b in branches if b["p"] >= MIN_P] or branches[:1]
    z = sum(b["p"] for b in branches)
    for b in branches:
        b["p"] = round(b["p"] / z, 3)
    branches[0]["p"] = round(1.0 - sum(b["p"] for b in branches[1:]), 3)

    return {
        "id": f"P-{last['id']}",
        "t": last["t"],
        "track_id": last.get("track_id"),
        "at": [last["lat"], last["lon"]],
        "at_camera": last["camera_id"],
        "heading_deg": round(heading) if heading is not None else None,
        "speed_mps": round(v, 2),
        "horizon_s": horizon_s,
        "branches": branches,
    }


def resolve(pred: dict[str, Any], later: list[dict[str, Any]]) -> dict[str, Any]:
    """Fill `actual`/`resolved_at` from the first later sighting at a branch camera."""
    if not pred or "actual" in pred:
        return pred
    by_cam = {b["camera_id"]: b["label"] for b in pred["branches"]}
    for b in pred["branches"]:
        for cid in b.get("also_cameras", []):
            by_cam[cid] = b["label"]
    for s in later:
        if _iso(s["t"]) > _iso(pred["t"]) and s["camera_id"] in by_cam:
            return {**pred, "actual": by_cam[s["camera_id"]], "resolved_at": s["t"]}
    return pred


def predictions_for_track(cameras: list[dict[str, Any]], sightings: list[dict[str, Any]],
                          track_id: str, graph: RoadGraph, every: bool = False,
                          **kw) -> list[dict[str, Any]]:
    """One prediction per linked sighting (every=True) or just the latest."""
    seq = sorted((s for s in sightings if s.get("track_id") == track_id and s["state"] == "linked"),
                 key=lambda s: s["t"])
    if not seq:
        return []
    idxs = range(1, len(seq) + 1) if every else [len(seq)]
    out = []
    for i in idxs:
        p = predict(cameras, seq[:i], graph, **kw)
        if p:
            out.append(resolve(p, seq[i:]))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", default=None, help="track id (default: every T-SUBJ* track)")
    ap.add_argument("--at", default=None, help="predict as of this camera id (truncate the track there)")
    ap.add_argument("--every", action="store_true", help="a prediction at every linked sighting, resolved against later ones")
    ap.add_argument("--horizon", type=float, default=180.0, help="seconds ahead")
    ap.add_argument("--speed", type=float, default=None, help="m/s override (car ~ 8-12)")
    ap.add_argument("--from-frontend", action="store_true", help="dry run on frontend/data/sightings.json")
    ap.add_argument("--write", action="store_true", help=f"write {PREDICTIONS_OUT.name}")
    a = ap.parse_args()

    if not ROAD_GRAPH.exists():
        print(f"missing {ROAD_GRAPH} - run python -m pipeline.roadgraph --build", file=sys.stderr)
        sys.exit(2)
    graph = RoadGraph.load()
    src = FRONTEND_PAYLOAD if a.from_frontend else PIPELINE_OUT
    if not src.exists():
        print(f"missing {src}", file=sys.stderr); sys.exit(2)
    payload = load_json(src)
    sightings = payload["sightings"]
    # detector output keeps the roster in data/cameras.json; the frontend payload carries its own
    cameras = payload.get("cameras") or load_cameras()["cameras"]
    tracks = [a.track] if a.track else sorted({s.get("track_id") for s in sightings
                                               if (s.get("track_id") or "").startswith("T-SUBJ")})
    out = []
    for tid in tracks:
        seq = sightings
        if a.at:
            seq = [s for s in sightings if not (s.get("track_id") == tid and s["state"] == "linked")]  # others untouched
            linked = sorted((s for s in sightings if s.get("track_id") == tid and s["state"] == "linked"), key=lambda s: s["t"])
            cut = next((i for i, s in enumerate(linked) if s["camera_id"] == a.at), None)
            if cut is None:
                print(f"{tid}: camera {a.at} not on track", file=sys.stderr); continue
            seq = seq + linked[:cut + 1]
        preds = predictions_for_track(cameras, seq, tid, graph, every=a.every,
                                      horizon_s=a.horizon, speed_mps=a.speed)
        out.extend(preds)
        for p in preds:
            print(f"\n{p['id']}  {tid} at {p['at_camera']}  heading {p['heading_deg']}°  "
                  f"speed {p['speed_mps']} m/s  ({len(p['branches'])} branches)")
            for b in p["branches"]:
                mark = " <== actual" if p.get("actual") == b["label"] else ""
                print(f"   {b['p']:.2f}  {b['label']}  ({b['distance_m']} m, turn {b['turn_deg']}°, eta {b['eta_s']} s){mark}")
    if a.write:
        write_json(PREDICTIONS_OUT, {"meta": {"note": "trajectory predictions; merged into the payload by build_payload.py",
                                              "params": {"sigma_turn_deg": SIGMA_TURN_DEG, "lambda_m": LAMBDA_M}},
                                     "predictions": out})
        print(f"\nwrote {PREDICTIONS_OUT}  ({len(out)} predictions)")


if __name__ == "__main__":
    main()
