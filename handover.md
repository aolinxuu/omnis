# Handover — rider tracker pipeline + VSS

For the next agent. Written 2026-08-15. Read the "Where this stands" and "The
one thing that will bite you" sections before touching anything.

No credentials are in this file. See [Credentials](#credentials) for where they
live on the box; ask the user for the SSH password.

---

## Where this stands

| Piece | State |
|---|---|
| Detection / reachability / payload pipeline | **Built, tested, committed.** 37 stdlib-only tests pass. |
| VSS 3.2.1 on the DGX Spark | **Deployed and healthy.** 9 containers, API answering. |
| `vss/client.py`, `vss/ingest.py`, `vss/query.py` | **Broken.** Written against an API that does not exist on this deployment. This is the next job. |
| `frontend/` | **Frozen. Do not touch.** Explicit user instruction. |

Branch: `pipeline-detection-reachability-vss`. The frontend is byte-identical to
`d8a307d` and must stay that way.

---

## The one thing that will bite you

**The VSS wrappers in `vss/` are written against `POST /files` and
`POST /summarize`. Neither exists on this deployment.**

Those paths come from NVIDIA's public docs, which describe an older blueprint
release. The build doc warned about exactly this ("Endpoint paths and payload
shapes have changed between blueprint releases; do not code against remembered
signatures") and it was right. The live 3.2.1 agent exposes 33 completely
different paths — see [The real API](#the-real-api-verified-live).

Worse, `vss/client.py` has a `looks_like_vss()` identity check that *requires*
`/files` and `/summarize`. It was added to stop the client going green against
a vLLM server on the same port, and it does that correctly — but it will now
**reject the real VSS**. Fix that function first or you will chase a phantom.

---

## The machine

`gn100-223b`, reachable over Tailscale (`100.111.152.111`). User `acer01`.

- NVIDIA GB10 (DGX Spark), aarch64, 121 GB unified memory, driver 580.173.02
- Ubuntu, kernel 6.17.0-1029-nvidia, Docker 29.2.1, Compose v5.0.2
- Disk: 3.3 TB free

**Password SSH only** — no key is installed for GitHub or for login. Commands
were driven through an `expect` wrapper. Two traps that cost real time:

1. **Never write a `pkill -f <pattern>` whose pattern also matches your own
   remote command string.** `pkill -f "docker compose.*pull"` matched the very
   shell running it and killed the SSH session silently, twice.
2. **Nested quoting through expect is treacherous.** Inline `python3 -c "..."`
   with inner quotes breaks. Write the script to a file, `scp` it, run it.

### Networking

- **The box cannot reach GitHub.** No credentials, and `origin` is an SSH
  remote. Code was moved Mac → box as **git bundles over Tailscale**. Do the
  same, or install a deploy key.
- **DNS is flaky.** The systemd-resolved stub (`127.0.0.53`) drops roughly one
  query in three; both upstreams answer fine when queried directly. This breaks
  `docker pull` intermittently. **Workaround that worked: pull images
  sequentially with retries** rather than compose's parallel pull.
  A real fix needs root (`sudo resolvectl dns wlP9s9 1.1.1.1 1.0.0.1`, or
  Docker daemon DNS) — sudo was not available to the agent.

---

## VSS deployment

Blueprint v3.2.1 cloned at `~/vss-blueprint`. Bring-up is wrapped in
`/tmp/run-up.sh`; the underlying command is:

```bash
source ~/.ngc-env
cd ~/vss-blueprint
bash deploy/docker/scripts/dev-profile.sh up -p base -H DGX-SPARK \
  --use-remote-llm --use-remote-vlm \
  --llm nvidia/nvidia-nemotron-nano-9b-v2 \
  --vlm nvidia/cosmos-reason2-8b
```

### Endpoints

| Service | URL |
|---|---|
| **Agent API** (what you want) | `http://gn100-223b:8010` |
| Web UI | `http://gn100-223b:3000` |
| HAProxy public | `http://gn100-223b:7777` |
| Phoenix tracing | `http://gn100-223b:6006` |

`http://gn100-223b:8010/openapi.json` is the **authoritative** API reference.
Trust it over any documentation, including this file.

### Five blockers already solved — do not re-litigate

1. **NIM images return HTTP 402 Payment Required.** The NGC key has no NIM
   entitlement. Fixed by running **hybrid**: VSS services local, VLM and LLM on
   NVIDIA's hosted API. This also removed all GPU contention. Do not try to pull
   `nvcr.io/nim/*` again without sorting entitlement first.
2. **Wrong model IDs for Spark.** `dev-profile.sh` defaults to
   `nvidia/nvidia-nemotron-nano-9b-v2` and `nvidia/cosmos3-nano-reasoner`. The
   latter has no service directory at all, and Spark `hw-DGX-SPARK*.env` files
   exist **only** for `cosmos3-reasoner`, `cosmos-reason2-8b`, and
   `nvidia-nemotron-nano-9b-v2-fp8`. For a *local* deploy the LLM id must be
   `nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` (capitalised — see `get_llm_slug()`).
3. **Flaky DNS** — see above. Sequential pulls with retries.
4. **`unknown or invalid runtime name: nvidia`.** The nvidia runtime is not
   registered with dockerd and there is no `/etc/docker/daemon.json`. But **CDI
   is configured** (`/var/run/cdi/nvidia.yaml`, `nvidia.com/gpu=all`) and works.
   Fixed without root via `deploy/docker/compose.override.yml`, which gives
   `sensor-ms` and `streamprocessing-ms` `runtime: runc` + `gpus: all`.
   The proper fix is `sudo nvidia-ctk runtime configure --runtime=docker`.
5. **Port 8000 collision.** `vss-agent` runs with **host networking** and
   defaulted to 8000, which `nemoclaw-vllm` (the user's separate Qwen3.6-35B
   server) already owns. The agent crashed with
   `OSError: [Errno 98] address already in use`. Fixed by setting
   `VSS_AGENT_PORT=8010` in
   `deploy/docker/developer-profiles/dev-profile-base/.env` (backup at
   `.env.bak`). **`nemoclaw-vllm` was deliberately left running** — the user was
   asked and never answered, so the non-destructive route was taken.

### Files changed on the box (outside git)

- `~/.ngc-env` — credentials, mode 600
- `~/vss-blueprint/deploy/docker/compose.override.yml` — the CDI GPU override
- `~/vss-blueprint/deploy/docker/developer-profiles/dev-profile-base/.env` —
  `VSS_AGENT_PORT=8010` (backup `.env.bak`)
- `/tmp/run-up.sh` — bring-up wrapper (in `/tmp`; will not survive a reboot)

None of this is version-controlled. Consider moving it into the repo.

---

## The real API (verified live)

From `http://gn100-223b:8010/openapi.json`, 33 paths. The ones that matter:

### Ingestion — three steps

```
POST /api/v1/videos                      body {"filename": "x.mp4"}
                                         -> {"url": "<VST upload URL>"}
     (upload the bytes to that URL)
POST /api/v1/videos/{sensor_id}/complete body {"filename":..., "custom_params":...}
                                         -> {message, sensor_id, filename, chunks_processed}
```

There is a **simpler deprecated single-shot** that is likely all this demo
needs:

```
PUT /api/v1/videos-for-search/{filename}
    -> {message, sensor_id, filename, chunks_processed}
```

`DELETE /api/v1/videos/{video_id}` removes one.

### Query — OpenAI-shaped

```
POST /chat                 POST /chat/stream
POST /v1/chat/completions  POST /v1/chat        POST /v1/chat/stream
POST /generate             /generate/async      /generate/full   /generate/stream
POST /v1/workflow          /v1/workflow/async   /v1/workflow/full
```

`/chat` takes the usual `{messages, model, max_tokens, temperature, ...}`.

**Unresolved:** how a query is scoped to one ingested video. Whether it is a
`sensor_id` field on the chat body, a `custom_params` entry, or implicit across
the whole index was not determined. **Establish this first** — the whole
per-camera query design depends on it.

### There is no `/health`

Not in the 33 paths. Use `GET /openapi.json` for liveness. For identity, the
discriminator is **`/api/v1/videos`** — `/v1/chat/completions` is useless for
this, since every OpenAI-compatible server has one (that is exactly how the
client went green against vLLM).

---

## Next steps, in order

1. **Fix `looks_like_vss()`** in `vss/client.py` so it accepts the real surface
   (`/api/v1/videos`) as well as the legacy one (`/files` + `/summarize`).
   Keep rejecting bare OpenAI servers — that check earned its place.
2. **Rewrite `VSSClient`** against the real paths. Default endpoint should
   become `http://localhost:8010`. `health()` must stop depending on `/health`.
3. **Determine how to scope a query to one video** (see above).
4. **Prove it end-to-end.** A real clip is already staged on the box at
   `/tmp/westlake-22s.mp4` (71 MB, 22.3 s). Upload it, then ask
   *"is anyone waving at the camera?"* This was the user's original question and
   it is still unanswered.
5. **Update `vss/ingest.py` and `vss/query.py`** to match. `ingest.py` currently
   writes `{"file_id": ...}`; the real identifier is a **`sensor_id`**.
6. Re-run `python -m unittest discover -s tests` and update
   `tests/test_pipeline.py::TestVSSIdentity`, which currently encodes the wrong
   contract.

---

## About those Westlake clips

Two screen recordings live in the user's `~/Downloads`:
`20260815-124353.mp4` (22.3 s) and `20260815-124540.mp4` (60.5 s). Both are
3016×1956 captures of the **same** SDOT camera, *Westlake Ave N & Harrison St*.

Three problems worth knowing before drawing conclusions from them:

- **Player chrome is burned into the frame** — Stop button, `00:00:08 / 01:00:00`
  timeline, cursor. A VLM asked "who is waving" can latch onto the UI. Crop first.
- **The subject is roughly 2–3% of frame height.** `prep_clips.py` scales to
  1280 wide, which leaves them about 20 px tall; a wave is a few pixels of arm
  motion. **This is the likeliest failure mode, and no pipeline work fixes it.**
  Consider cropping and scaling *up* instead.
- **That camera is not in the roster.** `data/cameras.json` has 104 real SDOT
  cameras; the only Westlake entries are Denny Way (`CMR-0184`, `CMR-0267`).
  Both clips are the same camera, and `clips.json` is keyed by `camera_id`, so
  one would overwrite the other.

Also note: `detect.py` can **never** answer "who is waving". It is YOLO
person/bicycle plus orange-hue scoring — waving is an action, not a class or a
colour. Only the VLM path can. That split is deliberate.

---

## The pipeline (already built — context only)

See `RUNBOOK.md` for the full operational guide. Two things not to undo:

**The frozen contract wins over the build doc.** The build doc's §2 sketch
(`camera`, integer-minute `t`, `confidence`, two states, separate
`cameras.json`) contradicts what `frontend/feed.js` actually parses
(`camera_id`, ISO-8601 `t`, `conf`, five states, one bundle). The frontend is
frozen, so it wins. Seed-relative minutes convert at exactly one boundary,
`pipeline/common.to_iso()`.

**`evalPanel()` scores only sightings whose state is exactly `"linked"`.** A
pipeline emitting only `confirmed` reports `recall 0/N` however good detection
is, and it looks like a detection bug. `build_payload.py --validate` fails
loudly on it.

Also: `prep_clips.py` reads the wave log (for trim windows); `detect.py` never
does, and a test asserts its source contains no reference to it. Do not
"helpfully" wire the wave log into detection — it makes the eval circular.

`build_payload.py` writes to `data/frontend-payload.json` by default. Only
`--install` touches `frontend/data/sightings.json`, and it takes a `.bak` first.
**It has never been run** — the hand-written fake demo data is intact.

---

## Credentials

Nothing secret is committed. On the box:

- `~/.ngc-env` (mode 600) — `NGC_CLI_API_KEY`, `NGC_API_KEY`, `NVIDIA_API_KEY`,
  `LLM_ENDPOINT_URL`, `VLM_ENDPOINT_URL`. `source` it before any VSS command.
- `docker login nvcr.io` is already done and persisted.
- The NGC key was pasted into a chat transcript, so **it should be rotated** at
  ngc.nvidia.com once the demo is over.
- The key works for the hosted API (`integrate.api.nvidia.com`, HTTP 200) and
  for `nvcr.io/nvidia/vss-core/*`, but **not** for `nvcr.io/nim/*` (402).

The SSH password is not recorded here — ask the user.
