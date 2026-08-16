"""Per-frame person boxes for the recorded demo clips, so the HUD can highlight
the subject *inside* the video as it plays.

For each clip: YOLO person detections every N-th frame; each box gets a colour
score for the subject's shirt hue (same idea as pipeline/detect.py: colour is a
weight, never a filter); the best-scoring person per frame is the subject.
Output is one JSON per clip in frontend/data/clips/<name>.boxes.json:

    {"clip": "20260815-184958.mp4", "w": 960, "h": 592, "every": 3, "fps": 30.0,
     "boxes": [{"t": 4.10, "x": 12, "y": 470, "w": 22, "h": 58, "conf": 0.71, "hue": 0.86}, ...]}

Boxes are in the proxy clip's pixel space; the frontend scales them to the tile.
Stdlib + ultralytics + opencv. Runs on CPU fine for a handful of short clips.

    python -m pipeline.box_track --clips data/clips-proxy2 --out frontend/data/clips --hue 165 --every 3
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2  # type: ignore
import numpy as np  # type: ignore

PERSON = 0  # COCO class id in the YOLO models


def hue_score(bgr: np.ndarray, target_h: int, tol: float = 22.0) -> float:
    """How pink is this crop? Fraction-weighted circular hue match on saturated pixels."""
    if bgr.size == 0:
        return 0.0
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    h, s, v = hsv[..., 0].astype(float), hsv[..., 1], hsv[..., 2]
    mask = (s > 60) & (v > 60)                       # ignore grey/black/white
    if mask.sum() < 20:
        return 0.0
    d = np.abs(h[mask] - target_h); d = np.minimum(d, 180 - d)
    match = np.exp(-(d / tol) ** 2)
    return float(match.mean() * 0.6 + (match > 0.5).mean() * 0.4)


def track_clip(model, path: Path, target_h: int, every: int, conf: float) -> dict:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    W, H = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    boxes, i = [], 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if i % every == 0:
            res = model.predict(frame, classes=[PERSON], conf=conf, verbose=False, imgsz=960)[0]
            best = None
            for b in res.boxes:
                x1, y1, x2, y2 = [int(v) for v in b.xyxy[0].tolist()]
                if x2 - x1 < 6 or y2 - y1 < 12:
                    continue
                crop = frame[y1:y2, x1:x2]
                # shirt is the upper-middle of the box; weight that region
                torso = crop[int(0.15 * (y2 - y1)):int(0.6 * (y2 - y1)), :]
                hs = hue_score(torso if torso.size else crop, target_h)
                score = float(b.conf) * (0.35 + 0.65 * hs)
                if best is None or score > best[0]:
                    best = (score, x1, y1, x2, y2, float(b.conf), hs)
            if best:
                s, x1, y1, x2, y2, c, hs = best
                boxes.append({"t": round(i / fps, 2), "x": x1, "y": y1, "w": x2 - x1, "h": y2 - y1,
                              "conf": round(c, 3), "hue": round(hs, 3), "score": round(s, 3)})
        i += 1
    cap.release()
    return {"clip": path.name, "w": W, "h": H, "fps": fps, "every": every, "target_hue": target_h, "boxes": boxes}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips", default="data/clips-proxy2")
    ap.add_argument("--out", default="frontend/data/clips")
    ap.add_argument("--model", default="yolo11n.pt")
    ap.add_argument("--hue", type=int, default=165, help="OpenCV hue 0-179 of the subject's shirt (pink/magenta ~160-175)")
    ap.add_argument("--every", type=int, default=3)
    ap.add_argument("--conf", type=float, default=0.2)
    ap.add_argument("--only", nargs="*")
    a = ap.parse_args()
    from ultralytics import YOLO  # deferred: heavy import
    model = YOLO(a.model)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    for p in sorted(Path(a.clips).glob("*.mp4")):
        if a.only and p.stem not in a.only:
            continue
        r = track_clip(model, p, a.hue, a.every, a.conf)
        (out / f"{p.stem}.boxes.json").write_text(json.dumps(r))
        pinks = sum(1 for b in r["boxes"] if b["hue"] > 0.35)
        print(f"{p.name}: {len(r['boxes'])} person frames, {pinks} pink-ish, {r['w']}x{r['h']} @ {r['fps']:.1f} fps", flush=True)


if __name__ == "__main__":
    main()
