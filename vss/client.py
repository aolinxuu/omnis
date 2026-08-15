"""Thin client for the NVIDIA VSS blueprint REST API.

Endpoint *paths* below are taken from NVIDIA's current blueprint documentation
(POST /files, POST /summarize, POST /chat/completions, DELETE /files/{id}).
The request *body schemas* are deliberately not hardcoded from memory: NVIDIA
only publishes them on the deployed instance, at http://<endpoint>/docs, and
they have changed between blueprint releases.

So before trusting these wrappers, run:

    python -m vss.client --verify

which pulls /openapi.json off the running instance and prints the real
parameter names for the endpoints we use. If it disagrees with the defaults
here, fix the defaults - do not fight the server.

Configure with VSS_API_ENDPOINT (default http://localhost:8000).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover - dependency check
    # Deferred, so the pure helpers below stay importable without the dep.
    # VSSClient() raises with a clear message instead.
    requests = None  # type: ignore[assignment]

DEFAULT_ENDPOINT = os.environ.get("VSS_API_ENDPOINT", "http://localhost:8000")

# Tried in order; the first that answers 2xx wins. Blueprint releases have moved
# health between these.
HEALTH_PATHS = ("/health/ready", "/v1/health/ready", "/health", "/v1/health")

FILES = "/files"
SUMMARIZE = "/summarize"
CHAT = "/chat/completions"


class VSSError(RuntimeError):
    pass


def looks_like_vss(paths: set[str]) -> bool:
    """Does this OpenAPI surface actually belong to VSS?

    /v1/chat/completions is NOT a discriminator - every OpenAI-compatible
    inference server has one. File ingestion is what makes it VSS.
    """
    return FILES in paths and SUMMARIZE in paths


def identify(paths: set[str]) -> str:
    """Best-effort name for whatever is squatting on the port, for the error."""
    if {"/v1/models", "/tokenize", "/detokenize"} <= paths:
        return "a vLLM / OpenAI-compatible inference server"
    if "/v1/models" in paths:
        return "an OpenAI-compatible API"
    return "an unknown service"


class VSSClient:
    def __init__(self, endpoint: str = DEFAULT_ENDPOINT, timeout: float = 600.0):
        if requests is None:
            raise VSSError("requests not installed:  pip install -r requirements.txt")
        self.endpoint = endpoint.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()

    # -- plumbing ----------------------------------------------------------

    def _url(self, path: str) -> str:
        return f"{self.endpoint}{path}"

    def _request(self, method: str, path: str, **kw) -> Any:
        kw.setdefault("timeout", self.timeout)
        try:
            r = self.session.request(method, self._url(path), **kw)
        except requests.RequestException as exc:
            raise VSSError(f"{method} {path}: {exc}") from exc
        if not r.ok:
            raise VSSError(f"{method} {path} -> {r.status_code}: {r.text[:400]}")
        if not r.content:
            return None
        try:
            return r.json()
        except ValueError:
            return r.text

    # -- introspection -----------------------------------------------------

    def health(self, verify_identity: bool = True) -> str:
        """Confirm VSS is up *and* that it is actually VSS.

        Liveness alone is not enough. A plain vLLM server answers GET /health
        with 200 and shares /v1/chat/completions with us, so a naive check goes
        green against the wrong service and the failure only surfaces later, as
        a confusing 404 from /files. Ports get reused on a shared box; this is
        not hypothetical.
        """
        found = None
        for path in HEALTH_PATHS:
            try:
                self._request("GET", path, timeout=10)
                found = path
                break
            except VSSError:
                continue
        if found is None:
            raise VSSError(
                f"no health endpoint answered at {self.endpoint} "
                f"(tried {', '.join(HEALTH_PATHS)}). Is the container up?")

        if verify_identity:
            try:
                paths = set(self.openapi().get("paths", {}))
            except VSSError:
                return found  # no spec exposed; caller gets liveness only
            if not looks_like_vss(paths):
                raise VSSError(
                    f"{self.endpoint} answered {found} but does not expose the "
                    f"VSS API ({FILES} and {SUMMARIZE} are absent). Something "
                    f"else is on this port - it looks like "
                    f"{identify(paths)}. Set VSS_API_ENDPOINT to the real "
                    f"VSS endpoint.")
        return found

    def openapi(self) -> dict[str, Any]:
        return self._request("GET", "/openapi.json", timeout=30)

    def describe(self, *paths: str) -> dict[str, Any]:
        """Real request schema for the given paths, straight off the instance."""
        spec = self.openapi()
        components = spec.get("components", {}).get("schemas", {})

        def resolve(node: Any, depth: int = 0) -> Any:
            if depth > 4 or not isinstance(node, dict):
                return node
            if "$ref" in node:
                name = node["$ref"].rsplit("/", 1)[-1]
                return resolve(components.get(name, {}), depth + 1)
            if "properties" in node:
                return {k: resolve(v, depth + 1).get("type", resolve(v, depth + 1))
                        if isinstance(resolve(v, depth + 1), dict) else v
                        for k, v in node["properties"].items()}
            return node.get("type", node)

        out = {}
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

    # -- operations --------------------------------------------------------

    def upload(self, path: Path, purpose: str = "vision",
               media_type: str = "video") -> str:
        """Upload one file. Returns the id VSS assigns it."""
        with open(path, "rb") as fh:
            data = self._request(
                "POST", FILES,
                files={"file": (path.name, fh, "video/mp4")},
                data={"purpose": purpose, "media_type": media_type},
            )
        file_id = (data or {}).get("id") or (data or {}).get("file_id")
        if not file_id:
            raise VSSError(f"upload of {path.name} returned no id: {data}")
        return file_id

    def delete_file(self, file_id: str) -> None:
        self._request("DELETE", f"{FILES}/{file_id}")

    def list_files(self) -> Any:
        return self._request("GET", FILES, timeout=30)

    def summarize(self, file_id: str, prompt: str, model: str,
                  chunk_duration: int = 20, **extra) -> Any:
        body = {"id": file_id, "model": model, "prompt": prompt,
                "chunk_duration": chunk_duration, **extra}
        return self._request("POST", SUMMARIZE, json=body)

    def ask(self, file_id: str, question: str, model: str,
            max_tokens: int = 256, **extra) -> Any:
        body = {
            "id": file_id,
            "model": model,
            "messages": [{"role": "user", "content": question}],
            "max_tokens": max_tokens,
            **extra,
        }
        return self._request("POST", CHAT, json=body)

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
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    ap.add_argument("--verify", action="store_true",
                    help="print the instance's real schema for the endpoints we use")
    ap.add_argument("--wait", action="store_true", help="block until healthy")
    args = ap.parse_args()

    c = VSSClient(args.endpoint)
    print(f"endpoint {c.endpoint}")

    path = c.wait_ready() if args.wait else c.health()
    print(f"healthy via {path}")

    if args.verify:
        print("\n--- real schema from /openapi.json ---")
        print(json.dumps(c.describe(FILES, SUMMARIZE, CHAT), indent=2))


if __name__ == "__main__":
    main()
