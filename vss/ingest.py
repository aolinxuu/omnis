"""Push prepared clips into VSS and record the id mapping.

Order matters, and it is the order the build doc gives:

1. containers up and health confirmed - before touching anything else
2. every clip in clips/ ingested
3. index confirmed populated with one trivial query

Writes vss/ingest-index.json mapping camera_id -> VSS file id. query.py joins on
it, so a camera missing here is a camera the query box cannot reach.

    python -m vss.ingest --wait
    python -m vss.ingest --smoke-test
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.common import CLIP_INDEX, ROOT, load_json, write_json  # noqa: E402
from vss.client import DEFAULT_ENDPOINT, VSSClient, VSSError  # noqa: E402

INGEST_INDEX = ROOT / "vss" / "ingest-index.json"
DEFAULT_MODEL = "vila-1.5"

SMOKE_QUESTION = "Describe what happens in this video in one sentence."


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--wait", action="store_true",
                    help="block until VSS reports healthy before uploading")
    ap.add_argument("--only", nargs="*", help="restrict to these camera ids")
    ap.add_argument("--reingest", action="store_true",
                    help="re-upload cameras already in the index")
    ap.add_argument("--smoke-test", action="store_true",
                    help="after ingest, ask one trivial question to prove the index works")
    args = ap.parse_args()

    if not CLIP_INDEX.exists():
        print(f"missing {CLIP_INDEX.relative_to(ROOT)} - run prep_clips.py first",
              file=sys.stderr)
        sys.exit(2)

    client = VSSClient(args.endpoint)
    print(f"endpoint {client.endpoint}")
    try:
        path = client.wait_ready() if args.wait else client.health()
    except VSSError as exc:
        print(f"VSS not reachable: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"healthy via {path}\n")

    existing = (load_json(INGEST_INDEX).get("files", {})
                if INGEST_INDEX.exists() else {})
    clips = load_json(CLIP_INDEX)["clips"]

    mapping = dict(existing)
    failures = []

    for entry in clips:
        cam = entry["camera_id"]
        if args.only and cam not in args.only:
            continue
        if cam in mapping and not args.reingest:
            print(f"{cam}  already ingested ({mapping[cam]['file_id']})")
            continue

        clip = ROOT / entry["clip"]
        if not clip.exists():
            print(f"{cam}  {entry['clip']} missing, skipping", file=sys.stderr)
            failures.append(cam)
            continue

        try:
            file_id = client.upload(clip)
        except VSSError as exc:
            print(f"{cam}  UPLOAD FAILED: {exc}", file=sys.stderr)
            failures.append(cam)
            continue

        mapping[cam] = {
            "file_id": file_id,
            "clip": entry["clip"],
            # Carried through so query.py can turn a clip-local hit back into
            # wall clock without re-reading clips.json.
            "clip_offset_s": entry["clip_offset_s"],
            "duration_s": entry["duration_s"],
        }
        print(f"{cam}  -> {file_id}")

    write_json(INGEST_INDEX, {
        "meta": {"endpoint": client.endpoint, "model": args.model,
                 "note": "camera_id -> VSS file id. query.py joins on this."},
        "files": mapping,
    })
    print(f"\nwrote {INGEST_INDEX.relative_to(ROOT)}  ({len(mapping)} clips indexed)")

    if failures:
        print(f"FAILED: {', '.join(failures)}", file=sys.stderr)

    if args.smoke_test and mapping:
        cam, info = next(iter(mapping.items()))
        print(f"\nsmoke test on {cam}...")
        try:
            reply = client.ask(info["file_id"], SMOKE_QUESTION, args.model)
        except VSSError as exc:
            print(f"  index NOT queryable: {exc}", file=sys.stderr)
            sys.exit(1)
        print(f"  {str(reply)[:400]}")
        print("  index is populated and queryable")

    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
