"""Natural-language query over the ingested clips (VSS 3.2.1 agent).

Text in, ranked clip segments out. Every hit carries the camera it came from and
a wall-clock time, because a segment the operator cannot place on the map and
the timeline is not an answer.

Three modes:

    python -m vss.query "person in orange on a scooter"
    python -m vss.query "..." --reachable-at 6 --uncertainty 1   # search only
                                                                 # reachable cameras
    python -m vss.query --captions        # write data/captions.json for the ticker
    python -m vss.query "..." --repeat 3  # the doc's "three times in a row" gate

`--serve` exposes GET /query?q=... on localhost for a frontend query box to call.
The frontend is frozen in this build, so nothing calls it yet; it exists so
wiring it later is a two-line change on the frontend side.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import (DATA, PIPELINE_OUT, ROOT, camera_index,  # noqa: E402
                             load_cameras, load_json, load_t0, to_iso, write_json)
from pipeline.reachability import reachable  # noqa: E402
from pipeline.roadgraph import ROAD_GRAPH, RoadGraph  # noqa: E402
from pipeline.predict import predict as predict_next  # noqa: E402
from vss.client import DEFAULT_ENDPOINT, VSSClient, VSSError  # noqa: E402
from vss.ingest import DEFAULT_MODEL, INGEST_INDEX  # noqa: E402

CAPTIONS_OUT = DATA / "captions.json"

# Tuned against the blueprint's Q&A behaviour: force a parseable first token so
# ranking does not depend on prose, and demand a timestamp so hits are placeable.
QUERY_PROMPT = (
    "You are reviewing traffic camera footage. Question: {q}\n"
    "Answer in exactly this format and nothing else:\n"
    "VERDICT: YES or NO\n"
    "TIME: the timestamp in seconds from the start of this clip, or NONE\n"
    "DETAIL: one short sentence describing what you see, or NONE"
)

CAPTION_PROMPT = (
    "Describe, in one short sentence under twelve words, the person on a bicycle "
    "or scooter visible around {t:.0f} seconds into this clip. Mention their "
    "clothing colour and direction of travel. If nobody is visible, answer NONE."
)

NEGATIVE = re.compile(r"\b(no|none|not|nobody|cannot|isn't|there is no)\b", re.I)


def reply_text(reply: Any) -> str:
    """VSS answers in an OpenAI-ish envelope; tolerate the plain-string case."""
    if isinstance(reply, str):
        return reply
    if isinstance(reply, dict):
        choices = reply.get("choices")
        if choices:
            msg = choices[0].get("message", {})
            return msg.get("content") or choices[0].get("text", "")
        for key in ("response", "content", "text", "answer"):
            if key in reply:
                return str(reply[key])
    return json.dumps(reply)


def parse(text: str) -> dict[str, Any]:
    """Pull verdict / time / detail out of the constrained answer format."""
    verdict = re.search(r"VERDICT:\s*(YES|NO)", text, re.I)
    tm = re.search(r"TIME:\s*([0-9]+(?:\.[0-9]+)?)", text, re.I)
    mmss = re.search(r"\b(\d{1,2}):(\d{2})\b", text)   # VLMs like to say 00:08 for "8 seconds in"
    detail = re.search(r"DETAIL:\s*(.+)", text, re.I)

    if verdict:
        hit = verdict.group(1).upper() == "YES"
    else:  # model ignored the format - fall back to reading the prose
        hit = not NEGATIVE.search(text[:80])

    d = detail.group(1).strip() if detail else text.strip()[:160]
    return {
        "hit": hit and d.upper() != "NONE",
        "t_clip": float(tm.group(1)) if tm else (int(mmss.group(1)) * 60 + int(mmss.group(2)) if mmss else None),
        "detail": d,
    }


def search(client: VSSClient, files: dict[str, Any], question: str, model: str,
           t0: datetime, cams: dict[str, dict[str, Any]],
           only: set[str] | None = None, on_result=None) -> list[dict[str, Any]]:
    """Ask every ingested clip, return the hits ranked by clip order.

    `on_result(cam_id, hit_or_None)` is called after each camera so a server can
    stream progress instead of making the operator wait for the whole sweep."""
    results = []
    for cam_id, info in files.items():
        if only is not None and cam_id not in only:
            continue
        try:
            reply = client.ask(info["video"], QUERY_PROMPT.format(q=question))
        except VSSError as exc:
            print(f"  {cam_id}: query failed - {exc}", file=sys.stderr)
            continue

        # Prefer the VLM tool's own words over the routing agent's paraphrase;
        # the constrained VERDICT/TIME/DETAIL format survives better there.
        parsed = parse(reply["tool_result"] or reply["answer"])
        if not parsed["hit"]:
            if on_result:
                on_result(cam_id, None)
            continue

        t_clip = parsed["t_clip"]
        seed_rel = info["clip_offset_s"] + (t_clip if t_clip is not None else 0.0)
        results.append({
            "camera_id": cam_id,
            "camera_name": cams.get(cam_id, {}).get("name", cam_id),
            "lat": cams.get(cam_id, {}).get("lat"),
            "lon": cams.get(cam_id, {}).get("lon"),
            "t_clip_s": t_clip,
            "t": to_iso(t0, seed_rel),
            "seed_rel_s": round(seed_rel, 1),
            "detail": parsed["detail"],
            "approximate_time": t_clip is None,
        })
        if on_result:
            on_result(cam_id, results[-1])

    results.sort(key=lambda r: r["seed_rel_s"])
    return results


def do_captions(client: VSSClient, files: dict[str, Any], model: str,
                t0: datetime) -> dict[str, str]:
    """Caption the subject sightings, for the ticker and the sighting note field."""
    if not PIPELINE_OUT.exists():
        print(f"missing {PIPELINE_OUT.relative_to(ROOT)} - run detect.py first",
              file=sys.stderr)
        sys.exit(2)

    captions: dict[str, str] = {}
    for s in load_json(PIPELINE_OUT)["sightings"]:
        if s["state"] not in ("linked", "confirmed"):
            continue
        info = files.get(s["camera_id"])
        if not info:
            continue
        t_clip = (datetime.fromisoformat(s["t"]) - t0).total_seconds() - info["clip_offset_s"]
        try:
            reply = client.ask(info["video"], CAPTION_PROMPT.format(t=t_clip),
                               start_s=max(0.0, t_clip - 5), end_s=t_clip + 5)
        except VSSError as exc:
            print(f"  {s['id']} {s['camera_id']}: {exc}", file=sys.stderr)
            continue
        text = (reply["tool_result"] or reply["answer"]).strip().strip('"')
        if text and text.upper() != "NONE":
            captions[s["id"]] = text
            print(f"  {s['id']}  {s['camera_id']}  {text}")
    return captions


def serve(client: VSSClient, files: dict[str, Any], model: str, t0: datetime,
          cams: dict[str, dict[str, Any]], port: int, host: str = "0.0.0.0") -> None:
    """GET /query?q=...            -> {"query", "results":[...]}
       GET /query?q=...&stream=1   -> NDJSON, one line per camera as it is answered:
                                       {"camera_id", "done": n, "total": N, "hit": {...}|null}
                                       then a final {"query", "results":[...], "complete": true}
       GET /cameras                -> the ingested camera ids (what a search will cover)"""
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse

    graph = RoadGraph.load() if ROAD_GRAPH.exists() else None
    if graph is None:
        print(f"note: {ROAD_GRAPH.name} missing - /predict disabled", file=sys.stderr)

    class Handler(BaseHTTPRequestHandler):
        def do_OPTIONS(self):  # noqa: N802  (CORS preflight for POST /predict)
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.end_headers()

        def do_POST(self):  # noqa: N802
            """POST /predict  {"sightings":[...linked sightings of one track, oldest first...],
                                "cameras":[...optional roster with id/name/lat/lon/alive...],
                                "speed_mps": optional, "horizon_s": optional}
               -> the contract's prediction object (branches with paths + p), or {"prediction": null}."""
            u = urlparse(self.path)
            if u.path != "/predict":
                self.send_error(404); return
            n = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(n) or b"{}")
                if graph is None:
                    raise RuntimeError("road graph not built on this box")
                roster = body.get("cameras") or list(cams.values())
                seq = sorted(body.get("sightings") or [], key=lambda x: x["t"])
                pred = predict_next(roster, seq, graph, horizon_s=float(body.get("horizon_s") or 180),
                                    speed_mps=body.get("speed_mps"))
                out = json.dumps({"prediction": pred}).encode(); code = 200
            except Exception as exc:
                out = json.dumps({"prediction": None, "error": str(exc)}).encode(); code = 400
            self._head(code, "application/json"); self.send_header("Content-Length", str(len(out))); self.end_headers(); self.wfile.write(out)

        def _head(self, code, ctype):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-store")

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path); qs = parse_qs(u.query)
            if u.path == "/cameras":
                body = json.dumps({"cameras": [{"camera_id": c, "video": i.get("video"), **{k: cams.get(c, {}).get(k) for k in ("name", "lat", "lon")}} for c, i in files.items()]}).encode()
                self._head(200, "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body); return
            q = qs.get("q", [""])[0]
            if not q:
                self.send_error(400, "missing q"); return
            if qs.get("stream", ["0"])[0] in ("1", "true"):
                self._head(200, "application/x-ndjson"); self.end_headers()
                total = len(files); n = [0]
                def emit(obj):
                    try:
                        self.wfile.write((json.dumps(obj) + "\n").encode()); self.wfile.flush()
                    except (BrokenPipeError, ConnectionResetError):
                        raise
                def cb(cam_id, hit):
                    n[0] += 1; emit({"camera_id": cam_id, "done": n[0], "total": total, "hit": hit})
                try:
                    hits = search(client, files, q, model, t0, cams, on_result=cb)
                    emit({"query": q, "results": hits, "complete": True})
                except (BrokenPipeError, ConnectionResetError):
                    return
                except Exception as exc:
                    emit({"query": q, "error": str(exc), "complete": True})
                return
            try:
                hits = search(client, files, q, model, t0, cams)
                body = json.dumps({"query": q, "results": hits}).encode(); code = 200
            except Exception as exc:  # keep the demo alive on a bad query
                body = json.dumps({"query": q, "error": str(exc)}).encode(); code = 500
            self._head(code, "application/json"); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

        def log_message(self, fmt, *a):  # one line per request, quiet otherwise
            sys.stderr.write("  %s %s\n" % (self.command, self.path[:120]))

    class Server(ThreadingMixIn, HTTPServer):
        daemon_threads = True

    print(f"query server on http://{host}:{port}/query?q=...   ({len(files)} camera clip(s) indexed)")
    Server((host, port), Handler).serve_forever()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("question", nargs="?", help="natural-language query")
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--reachable-at", type=float, default=None,
                    help="minutes since seed; restricts the search to reachable cameras")
    ap.add_argument("--uncertainty", type=float, default=0.0)
    ap.add_argument("--captions", action="store_true",
                    help="caption detected sightings into data/captions.json")
    ap.add_argument("--repeat", type=int, default=1,
                    help="run the query N times and report whether it is stable")
    ap.add_argument("--serve", type=int, metavar="PORT", default=None)
    ap.add_argument("--host", default="0.0.0.0", help="bind address for --serve (0.0.0.0 so the frontend on another box can reach it)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not INGEST_INDEX.exists():
        print(f"missing {INGEST_INDEX.relative_to(ROOT)} - run vss/ingest.py first",
              file=sys.stderr)
        sys.exit(2)

    files = load_json(INGEST_INDEX)["files"]
    cams_raw = load_cameras()
    t0 = load_t0(cams_raw)
    cams = camera_index(cams_raw)
    client = VSSClient(args.endpoint)

    if args.captions:
        captions = do_captions(client, files, args.model, t0)
        write_json(CAPTIONS_OUT, {
            "meta": {"note": "VSS captions keyed by sighting id; merged by "
                             "pipeline/build_payload.py into the note field.",
                     "model": args.model},
            "captions": captions,
        })
        print(f"\nwrote {CAPTIONS_OUT.relative_to(ROOT)}  ({len(captions)} captions)")
        return

    if args.serve is not None:
        serve(client, files, args.model, t0, cams, args.serve, args.host)
        return

    if not args.question:
        ap.error("a question is required unless --captions or --serve is given")

    only = None
    if args.reachable_at is not None:
        r = reachable(cams_raw["cameras"], cams_raw["seed"],
                      args.reachable_at, args.uncertainty)
        if r["state"] == "too_wide":
            print(f"TOO WIDE - {r['message']} "
                  f"({r['count']}/{r['total']} cameras). Narrow the uncertainty "
                  f"before querying.")
            sys.exit(3)
        only = {c["id"] for c in r["cameras"]}
        print(f"restricted to {len(only)} reachable cameras "
              f"(radius {r['radius_m']:.0f} m)\n")

    runs = []
    for i in range(max(1, args.repeat)):
        hits = search(client, files, args.question, args.model, t0, cams, only)
        runs.append(hits)
        if args.repeat > 1:
            print(f"run {i + 1}: {len(hits)} hit(s) "
                  f"[{', '.join(h['camera_id'] for h in hits) or '-'}]")

    hits = runs[-1]
    if args.repeat > 1:
        shapes = {tuple(h["camera_id"] for h in r) for r in runs}
        print("STABLE across all runs" if len(shapes) == 1
              else f"UNSTABLE - {len(shapes)} different result sets. Tune the prompt.")

    if args.json:
        print(json.dumps({"query": args.question, "results": hits}, indent=2))
        return

    if not hits:
        print("no matching segments")
        return
    print(f"{len(hits)} segment(s):\n")
    for h in hits:
        approx = " (time approximate)" if h["approximate_time"] else ""
        print(f"  {h['t'][11:19]}  {h['camera_id']}  {h['camera_name']}{approx}")
        print(f"            {h['detail']}")


if __name__ == "__main__":
    main()
