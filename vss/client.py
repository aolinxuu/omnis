"""Thin client for the NVIDIA VSS 3.2.1 *agent* API as deployed on gn100-223b.

Every path below was read off the live instance's /openapi.json (33 paths) and
exercised on 2026-08-15 - not taken from docs. Re-verify with:

    python -m vss.client --verify

VSS 3.2.1 is not the /files + /summarize surface the public docs describe. It is
a NeMo-Agent-Toolkit routing agent in front of VST (video storage). The surface
that matters:

  ingest   PUT  /api/v1/videos-for-search/{filename}   raw bytes -> {sensor_id}
           POST /api/v1/videos  +  POST /api/v1/videos/{sensor_id}/complete
                                                       (chunked variant, unused)
           DELETE /api/v1/videos/{video_id}
  query    POST /generate  {"input_message": "..."}   -> {"value": "<agent-think>...</agent-think>\\n\\nanswer"}
           POST /chat, /v1/chat/completions            (OpenAI-shaped, same agent)
  list     GET  <VST>/vst/api/v1/sensor/list          (VST itself, port 30888)
  identity GET  /openapi.json                          (there is NO /health)

How a query is scoped to one video: the agent's tools are
video_understanding(sensor_id, start_timestamp, end_timestamp, user_prompt),
vst_video_list, vst_video_clip, vst_snapshot, report_agent. It picks the video
from the *filename or sensor id you mention in the message*. ask() therefore
prefixes every question with "In the video <name>, ...".

Configure with VSS_API_ENDPOINT (default http://localhost:8010) and
VST_ENDPOINT (default http://localhost:30888).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - dependency check
    requests = None  # type: ignore[assignment]

DEFAULT_ENDPOINT = os.environ.get("VSS_API_ENDPOINT", "http://localhost:8010")
DEFAULT_VST = os.environ.get("VST_ENDPOINT", "http://localhost:30888")

# Real 3.2.1 surface (verified live).
VIDEOS = "/api/v1/videos"
VIDEOS_FOR_SEARCH = "/api/v1/videos-for-search/{filename}"
GENERATE = "/generate"
CHAT = "/chat"
OPENAPI = "/openapi.json"
VST_SENSOR_LIST = "/vst/api/v1/sensor/list"

# Legacy blueprint surface, kept so the identity check still recognises an
# older deployment if someone points us at one.
LEGACY_FILES = "/files"
LEGACY_SUMMARIZE = "/summarize"

_THINK = re.compile(r"<agent-think>.*?</agent-think>", re.S)


class VSSError(RuntimeError):
    pass


def looks_like_vss(paths: set[str]) -> bool:
    """Does this OpenAPI surface actually belong to VSS?

    /v1/chat/completions is NOT a discriminator - every OpenAI-compatible
    inference server has one (that is exactly how an earlier version of this
    client went green against vLLM on the same box). Video ingestion is what
    makes it VSS: /api/v1/videos on 3.2.1, /files + /summarize on the older
    blueprint.
    """
    return VIDEOS in paths or (LEGACY_FILES in paths and LEGACY_SUMMARIZE in paths)


def identify(paths: set[str]) -> str:
    """Best-effort name for whatever is squatting on the port, for the error."""
    if {"/v1/models", "/tokenize", "/detokenize"} <= paths:
        return "a vLLM / OpenAI-compatible inference server"
    if "/v1/models" in paths:
        return "an OpenAI-compatible API"
    return "an unknown service"


def strip_think(value: str) -> str:
    """Drop the <agent-think> trace and return the user-facing answer."""
    return _THINK.sub("", value or "").strip()


def think_steps(value: str) -> list[str]:
    """The agent's plan / tool-call trace, for logging and for parsing tool results."""
    return re.findall(r'<agent-think-step title="[^"]*">(.*?)</agent-think-step>', value or "", re.S)


class VSSClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, vst: str = DEFAULT_VST,
                 timeout: float = 600.0):
        if requests is None:
            raise VSSError("requests not installed:  pip install -r requirements.txt")
        self.endpoint = endpoint.rstrip("/")
        self.vst = vst.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # -- plumbing ----------------------------------------------------------

    def _request(self, method: str, url: str, **kw) -> Any:
        kw.setdefault("timeout", self.timeout)
        try:
            r = self.session.request(method, url, **kw)
        except requests.RequestException as exc:
            raise VSSError(f"{method} {url}: {exc}") from exc
        if not r.ok:
            raise VSSError(f"{method} {url} -> {r.status_code}: {r.text[:400]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    def _api(self, method: str, path: str, **kw) -> Any:
        return self._request(method, f"{self.endpoint}{path}", **kw)

    # -- introspection -----------------------------------------------------

    def openapi(self) -> dict[str, Any]:
        return self._api("GET", OPENAPI, timeout=30)

    def health(self, verify_identity: bool = True) -> str:
        """Confirm VSS is up *and* that it is actually VSS.

        3.2.1 has no /health; a 200 from /openapi.json is liveness. Identity is
        the presence of the video-ingest paths, never /v1/chat/completions.
        """
        try:
            spec = self.openapi()
        except VSSError as exc:
            raise VSSError(f"VSS not answering at {self.endpoint}: {exc}") from exc
        if verify_identity:
            paths = set(spec.get("paths", {}))
            if not looks_like_vss(paths):
                raise VSSError(
                    f"{self.endpoint} answers /openapi.json but does not expose "
                    f"the VSS ingest API ({VIDEOS}). It looks like {identify(paths)}. "
                    f"Set VSS_API_ENDPOINT to the real VSS agent (port 8010 on the Spark).")
        return OPENAPI

    def describe(self, *paths: str) -> dict[str, Any]:
        """Real request schema for the given paths, straight off the instance."""
        spec = self.openapi()
        components = spec.get("components", {}).get("schemas", {})

        def resolve(node: Any, depth: int = 0) -> Any:
            if depth > 5 or not isinstance(node, dict):
                return node
            if "$ref" in node:
                return resolve(components.get(node["$ref"].rsplit("/", 1)[-1], {}), depth + 1)
            if "properties" in node:
                return {k: resolve(v, depth + 1) for k, v in node["properties"].items()}
            if "anyOf" in node:
                return [resolve(x, depth + 1) for x in node["anyOf"]]
            return node.get("type", node)

        out: dict[str, Any] = {}
        for path in paths:
            item = spec.get("paths", {}).get(path)
            if item is None:
                out[path] = "NOT PRESENT ON THIS INSTANCE"
                continue
            for method, op in item.items():
                body = op.get("requestBody", {}).get("content", {})
                schema = next(iter(body.values()), {}).get("schema", {})
                out[f"{method.upper()} {path}"] = {
                    "body": resolve(schema),
                    "params": [p.get("name") for p in op.get("parameters", [])],
                }
        return out

    # -- ingestion ---------------------------------------------------------

    def upload(self, path: Path, name: str | None = None) -> dict[str, Any]:
        """Upload one video. Returns {"sensor_id", "filename", ...}.

        `filename` (minus extension) is what you refer to the video by in
        questions; VST also assigns a sensor_id UUID. Uploading the same name
        twice creates a second sensor - delete first if you mean to replace.
        """
        name = name or path.name
        with open(path, "rb") as fh:
            data = self._api("PUT", VIDEOS_FOR_SEARCH.format(filename=name),
                             data=fh, headers={"Content-Type": "video/mp4"})
        if not isinstance(data, dict) or not data.get("sensor_id"):
            raise VSSError(f"upload of {name} returned no sensor_id: {data}")
        return data

    def delete_video(self, video_id: str) -> None:
        self._api("DELETE", f"{VIDEOS}/{video_id}")

    def list_videos(self) -> list[dict[str, Any]]:
        """Straight from VST: [{sensorId, name, state, type, ...}]."""
        data = self._request("GET", f"{self.vst}{VST_SENSOR_LIST}", timeout=30)
        return data if isinstance(data, list) else []

    def find_video(self, name: str) -> dict[str, Any] | None:
        stem = Path(name).stem
        for v in self.list_videos():
            if v.get("name") == stem or v.get("sensorId") == name:
                return v
        return None

    # -- query -------------------------------------------------------------

    def generate(self, message: str) -> str:
        """Raw agent call. Returns the full value including the think trace."""
        data = self._api("POST", GENERATE, json={"input_message": message})
        if isinstance(data, dict) and "value" in data:
            return data["value"]
        return json.dumps(data)

    def ask(self, video: str, question: str, start_s: float | None = None,
            end_s: float | None = None) -> dict[str, Any]:
        """Ask one question about one video. Returns {answer, tool_result, raw}.

        `video` is the upload filename (with or without extension) or the
        sensor_id. Scoping works by naming it in the message - that is how the
        agent's planner picks the sensor for video_understanding.
        """
        stem = Path(video).stem
        window = ""
        if start_s is not None or end_s is not None:
            window = (f" Only look between {start_s or 0:.0f} and "
                      f"{end_s if end_s is not None else 'the end'} seconds.")
        raw = self.generate(f"In the video {stem}, {question.strip()}{window}")
        answer = strip_think(raw)
        tool_result = None
        for step in think_steps(raw):
            # The "Tool Call" step reads: "Tool: video_understanding Args: {...} Result: <VLM text>"
            m = re.match(r"\s*Tool:\s*video_understanding\b.*?Result:\s*(.*)$", step, re.S)
            if m:
                tool_result = m.group(1).strip()
        if answer.startswith("Sorry, I wasn't able to complete your request"):
            err = next((s for s in think_steps(raw) if "Error" in s), raw[:300])
            raise VSSError(f"agent failed: {err.strip()[:400]}")
        return {"answer": answer, "tool_result": tool_result, "raw": raw}

    def wait_ready(self, attempts: int = 30, delay: float = 10.0) -> str:
        last: Exception | None = None
        for i in range(attempts):
            try:
                return self.health()
            except VSSError as exc:
                last = exc
                print(f"  waiting for VSS ({i + 1}/{attempts})...", file=sys.stderr)
                time.sleep(delay)
        raise VSSError(f"VSS never became ready: {last}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--vst", default=DEFAULT_VST)
    ap.add_argument("--verify", action="store_true",
                    help="print the instance's real schema for the endpoints we use")
    ap.add_argument("--wait", action="store_true", help="block until healthy")
    ap.add_argument("--list", action="store_true", help="list videos VST knows about")
    ap.add_argument("--ask", nargs=2, metavar=("VIDEO", "QUESTION"),
                    help="ask one question about one uploaded video")
    args = ap.parse_args()

    c = VSSClient(args.endpoint, args.vst)
    print(f"endpoint {c.endpoint}   vst {c.vst}")
    path = c.wait_ready() if args.wait else c.health()
    print(f"healthy via {path}")

    if args.verify:
        print("\n--- real schema from /openapi.json ---")
        print(json.dumps(c.describe(VIDEOS, VIDEOS_FOR_SEARCH, GENERATE, CHAT), indent=2))
    if args.list:
        for v in c.list_videos():
            print(f"  {v.get('sensorId')}  {v.get('name')}  {v.get('state')}  {v.get('type')}")
    if args.ask:
        r = c.ask(args.ask[0], args.ask[1])
        print(f"\nVLM tool result: {r['tool_result']}\n\nanswer: {r['answer']}")


if __name__ == "__main__":
    main()
