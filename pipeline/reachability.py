"""Seed + elapsed time -> reachable camera set.

A hard filter, not a ranking function. Ordering within the reachable set is by
distance from the seed and nothing else.

The degradation behaviour is the point: when the search space covers every
camera, the system says so instead of returning a full list as though it were
an answer.

    python -m pipeline.reachability --elapsed 6 --uncertainty 2
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from .common import SPEED_M_PER_MIN, distance_m, load_cameras


def reachable(
    cameras: list[dict[str, Any]],
    seed: dict[str, float],
    elapsed_min: float,
    uncertainty_min: float = 0.0,
) -> dict[str, Any]:
    """Cameras the rider could have reached, given elapsed time and seed slop.

    Returns a dict rather than a bare list so callers must look at `state` and
    cannot accidentally treat a blown-out search as a narrowed one.
    """
    radius = (elapsed_min + uncertainty_min) * SPEED_M_PER_MIN
    scored = [(distance_m(seed, c), c) for c in cameras]
    hits = [(d, c) for d, c in scored if d <= radius]
    hits.sort(key=lambda pair: pair[0])

    if hits and len(hits) == len(cameras) and uncertainty_min > 0:
        return {
            "state": "too_wide",
            "message": "Search space too large to narrow",
            "radius_m": round(radius, 1),
            "elapsed_min": elapsed_min,
            "uncertainty_min": uncertainty_min,
            "count": len(hits),
            "total": len(cameras),
            "cameras": [],
        }

    return {
        "state": "ok",
        "radius_m": round(radius, 1),
        "elapsed_min": elapsed_min,
        "uncertainty_min": uncertainty_min,
        "count": len(hits),
        "total": len(cameras),
        "cameras": [
            {"id": c["id"], "name": c["name"], "lat": c["lat"], "lon": c["lon"],
             "alive": c["alive"], "distance_m": round(d, 1)}
            for d, c in hits
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elapsed", type=float, required=True,
                    help="minutes since the seed timestamp")
    ap.add_argument("--uncertainty", type=float, default=0.0,
                    help="minutes of slop on the seed")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    cams = load_cameras()
    result = reachable(cams["cameras"], cams["seed"], args.elapsed, args.uncertainty)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"seed {cams['seed']['camera']} ({cams['seed']['name']})  "
          f"elapsed {args.elapsed}m  uncertainty ±{args.uncertainty}m")
    print(f"radius {result['radius_m']:.0f} m")
    if result["state"] == "too_wide":
        print(f"\n  TOO WIDE - {result['message']} "
              f"({result['count']}/{result['total']} cameras)")
        return
    print(f"{result['count']}/{result['total']} cameras reachable\n")
    for c in result["cameras"]:
        flag = "" if c["alive"] else "  [dead]"
        print(f"  {c['distance_m']:8.0f} m  {c['id']}  {c['name']}{flag}")


if __name__ == "__main__":
    main()
