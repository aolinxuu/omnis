# Handover — rider tracker pipeline + VSS

For the next agent. Written 2026-08-15, updated 2026-08-16. Read the
2026-08-16 update, "Where this stands", and "The one thing that will bite you"
before touching anything. Sections are newest-first; anything below the updates
is older and may have been overtaken — the tables and "Next steps" are current.

No credentials are in this file. See [Credentials](#credentials) for where they
live on the box; ask the user for the SSH password. **SSH now works on a key** —
see [The machine](#the-machine); the `expect` wrapper is no longer needed.

---

## Update 2026-08-16 — real footage ingested, subject detected, demo wired

The demo now runs on real footage instead of nothing. A man in a pink shirt was
recorded on eight live SDOT camera feeds between 18:49 and 19:52 on 2026-08-15,
running south down 2nd Ave from Pike to Spring, crossing to 3rd, and coming back
north to University. **VSS finds him in all eight.**

- **All eight clips ingested.** `data/clips/` on the box, uploaded to VST. The
  camera behind each is read off the banner burned into the recording; seven map
  to real roster ids, the eighth (Broadway) has no roster entry and so can be
  queried but never placed on the map.
- **`vss/pink_subject.py`** is the scenario driver, hard-coded on purpose:
  `--register` indexes the clips, `--sweep` asks VSS and **caches to
  `data/subject-hits.json`**, `--sightings` emits detector-shaped output.
  The cache is the point — rebuilding costs seconds instead of twenty minutes of
  VLM calls. Do not throw it away on demo day.
- **`python -m vss.query "man in a pink shirt running"` returns 7 placed
  segments** with camera names and wall clock. That is old step 4, now done.
- **Eval reads recall 7/7, precision 7/7** against
  `data/wave-log-pink.json`, via the new `build_payload --ground-truth` flag.

### What the VLM actually does — this shaped the parser

- **It ignores the requested `VERDICT:/TIME:/...` format** and answers in prose
  ("At timestamp 00:00:04, a man wearing..."). `parse_answer()` reads prose as
  well as fields. Do not "simplify" it back to field-only parsing.
- **It sometimes emits markdown on one line**, and a naive `MOTION:` grab
  swallows the rest of the answer whole. Motion is normalised against a fixed
  vocabulary instead.
- **Clip-local timestamps drift by roughly ±10s between identical runs.** Inside
  evalPanel's 20s window, so the eval is stable, but never present these as
  precise.
- **Long clips sample sparsely**: `max_fps=2, max_frames=30`. The 68s clip gets
  ~0.44 fps.

### The handover's cropping fear did not materialise

The Westlake section below warns the subject is 2–3% of frame height and that
this is the likeliest failure mode. **That does not apply to this footage.** He
is far closer to these cameras; VSS found him at native 3016×1952 with the
player chrome still burned in, uncropped, on the first try. No `prep_clips.py`
run was needed.

### Verified against the frames, not taken on trust

Each detection was checked by pulling the frame at the second VSS named and
looking at it. Six of seven confirmed, cross-checked against the player clock
burned into the recording (VSS said 34s, clock read 00:00:33).

**The exception matters: at 3rd & University (`CMR-0035`) VSS reported 8s and he
is not there.** He appears at 14–20s. `wave-log-pink.json` records 14s. A later
independent sweep also returned 14s. This is the one case where believing the
model would have put a marker on the map at the wrong time.

### Where the footage lives

**Not in git** — six of the eight clips are over GitHub's 100 MB per-file limit,
so `git push` rejects them outright and LFS would need a paid pack (1.3 GB
against a 1 GB free tier). They are **release assets** on the `clips-2026-08-15`
tag:

```bash
gh release download clips-2026-08-15 --dir data/clips --repo aolinxuu/omnis
```

That path is what `pink_subject.py` expects. `data/wave-log-pink.json` **is** in
git (it is hand-authored and unrecoverable); `subject-hits.json` and
`sightings.json` are not, because a sweep regenerates them.

### Still open

- **The frontend still shows the hand-written fake data.** 55 sightings,
  `"HAND-WRITTEN FAKE DATA"` in its meta. None of the above is visible until
  someone runs `build_payload --install`, which is Emily's side of the CLAUDE.md
  boundary and was deliberately left alone.
- **`predictions` is empty.** No `data/predictions.json` exists, so the routing
  branches render nothing against real footage. `pipeline/predict.py --write`
  replays the *fake* rides; pointing it at the pink-shirt track is unverified.
- **Both Pike St clips collapse to one index entry.** The index is keyed by
  `camera_id`, exactly the collision warned about below. It keeps `184958`, the
  better clip. Both still produce sightings.

---

## Update 2026-08-15 evening — VSS wrappers fixed, end-to-end proven

Everything in the "Next steps" list below is done except re-ingesting the demo
clips (no clips exist yet — `prep_clips.py` has not been run on real footage).

- `vss/client.py` rewritten against the real 3.2.1 surface (verified live):
  `PUT /api/v1/videos-for-search/{filename}` → `sensor_id`; `POST /generate`
  with `input_message`; VST list at `:30888/vst/api/v1/sensor/list`; identity
  = `/api/v1/videos` (legacy `/files`+`/summarize` still accepted); no `/health`.
  Default endpoint `http://localhost:8010`. `python -m vss.client --list --ask <video> "<q>"`.
- **Scoping a query to one video: name it in the message.** The agent is a NAT
  planner with tools `video_understanding(sensor_id, start_ts, end_ts, prompt)`,
  `vst_video_list`, `vst_video_clip`, `vst_snapshot`, `report_agent`; it picks
  the sensor from the filename you mention. `VSSClient.ask()` prefixes
  "In the video <name>, ...".
- `vss/ingest.py` writes `sensor_id` + `video` (upload name = camera id);
  `vss/query.py` uses `ask()` and prefers the VLM tool's own text.
- Tests: 41 pass (`TestVSSIdentity` rewritten, `TestVSSAnswerParsing` added).
- **Proven:** `/tmp/westlake-22s.mp4` uploaded (sensor
  `040cd9e7-0f4d-41c3-bfbb-2a37473d1887`, name `westlake-22s`). Asked *"is
  anyone waving at the camera?"* → *"Yes … person on the sidewalk in the lower
  right corner, grey shirt and dark pants, waving from about 00:08."*
- **Model backend changed to fully local.** The NGC key is 403 for hosted
  inference (both the key on the box and a second one tried), and the remote
  config had a `/v1/v1` URL bug. The agent now uses the multimodal
  `nvidia/Qwen3.6-35B-A3B-NVFP4` on `nemoclaw-vllm:8000` for LLM and VLM. See
  `deploy/vss/README.md` for the override, the config patch, and how to switch
  back to Nemotron + Cosmos-Reason2 when a working build.nvidia.com key lands.
- **DNS was fixed with sudo** (`resolvectl dns wlP9s9 1.1.1.1 1.0.0.1`);
  non-persistent, redo after reconnect. Details in `deploy/vss/README.md`.
- Both API keys that were pasted into chats today should be rotated after the demo.
- **Routing engine built** (the "Routing — team-built entirely" box). `pipeline/roadgraph.py`
  = OSM road graph for downtown + SLU (12k nodes / 21k directed edges, cached in
  `data/road_graph.json`, stdlib Dijkstra, respects one-ways). `pipeline/predict.py`
  = trajectory prediction: candidate next cameras reachable by road within the
  horizon at the observed speed, scored by total path turning vs current heading
  and road distance, softmax → branches with street-following paths in the
  contract's `prediction` shape; `resolve()` fills `actual` from later sightings.
  `python -m pipeline.predict --from-frontend --every` replays the fake rides:
  the actual next camera is the top branch in nearly every case.
  `--write` → `data/predictions.json`, which `build_payload.py` now merges.
  Speed defaults to scooter; pass `--speed 10` for a car. 46 tests pass.

## Where this stands

| Piece | State |
|---|---|
| Detection / reachability / payload pipeline | **Built, tested, committed.** 46 stdlib-only tests pass. |
| VSS 3.2.1 on the DGX Spark | **Deployed and healthy.** 9 containers, API answering. |
| `vss/client.py`, `vss/ingest.py`, `vss/query.py` | **Working.** Rewritten against the real 3.2.1 surface and exercised live. |
| Real footage → VSS → sightings | **Working.** 8 clips ingested, subject found in all 8, eval 7/7. See the 2026-08-16 update. |
| `predictions` on real footage | **Empty.** No `data/predictions.json`; branches render nothing. |
| `frontend/` | **Frozen. Do not touch.** Explicit user instruction. Still on hand-written fake data. |

Branch: `main` (the pipeline work was merged; `pipeline-detection-reachability-vss`
is history). The frontend must stay byte-identical to whatever Emily last
pushed — check `git status frontend/` is empty before you commit anything.

---

## The one thing that will bite you

**`evalPanel()` in `app.js` scores only sightings whose `state` is exactly
`"linked"`, and only within 20 seconds of a ground-truth entry on the same
camera.**

Miss either half and the demo looks broken for reasons that have nothing to do
with detection:

- emit only `confirmed` and the table reads `recall 0/N` however good detection is
- score a scenario against the wrong wave log and *every* row reads "miss".
  The original `wave-log.json` is the 18:02–18:08 fake ride; the pink-shirt run
  is 18:49–19:15, so the two never match. That is what
  `build_payload --ground-truth` exists for.

`build_payload.py --validate` fails loudly on the first case. It cannot catch
the second — you have to pass the right file.

### Resolved, kept so nobody re-litigates it

The wrappers in `vss/` were once written against `POST /files` and
`POST /summarize`, which do not exist on this deployment; those paths come from
NVIDIA docs describing an older blueprint. `looks_like_vss()` required them and
so rejected the real VSS. **Both are fixed** — the client now accepts
`/api/v1/videos` as the discriminator while still rejecting a bare OpenAI
server, which is a check that earned its place (the client once went green
against vLLM on the same port). See [The real API](#the-real-api-verified-live).

---

## The machine

`gn100-223b`, reachable over Tailscale (`100.111.152.111`). User `acer01`.

- NVIDIA GB10 (DGX Spark), aarch64, 121 GB unified memory, driver 580.173.02
- Ubuntu, kernel 6.17.0-1029-nvidia, Docker 29.2.1, Compose v5.0.2
- Disk: 3.3 TB free

**SSH works on a key now** — the `expect` wrapper and the password are no longer
needed:

```bash
ssh acer01@gn100-223b     # key: ~/Library/Application Support/NVIDIA/Sync/config/nvsync.key
```

The bare hostname resolves over Tailscale only (`gn100-223b.tail0c58b0.ts.net`);
the `.local` mDNS name and the old LAN IP `172.16.94.127` do not resolve from
off-LAN. A `Host gn100-223b` block on the Mac points the short name at the key.

Three traps that cost real time:

1. **Never write a `pkill -f <pattern>` whose pattern also matches your own
   remote command string.** `pkill -f "docker compose.*pull"` matched the very
   shell running it and killed the SSH session silently, twice.
2. **Nested quoting is still treacherous**, key or not. An apostrophe inside a
   `ssh box '...'` command closes the quote — a commit message containing
   "evalPanel's" silently truncated at that word. Write the script or message to
   a file, `scp` it, run it. This one bit again on 2026-08-16.
3. **Python buffers stdout through a pipe.** `ssh box 'python3 x.py' | tail`
   shows nothing until the process exits. Use `python3 -u` and do not pipe
   through `tail` if you want progress.

### Networking

- **The box still cannot reach GitHub.** No credentials, and `origin` is an SSH
  remote; the user chose on 2026-08-15 to leave it that way rather than add a
  deploy key. Move code as **git bundles over Tailscale**, in both directions:

  ```bash
  # box -> GitHub (the Mac has the gh auth)
  ssh gn100-223b 'cd ~/omnis && git bundle create /tmp/push.bundle <base>..main'
  scp gn100-223b:/tmp/push.bundle . && git fetch ./push.bundle main:refs/remotes/box/main
  git push origin refs/remotes/box/main:main     # leaves the Mac's own tree alone
  ```

  Check `gh api repos/aolinxuu/omnis/commits/main` first — it is the cheapest way
  to see whether the box is behind without any credentials on the box at all.
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

### Deployment config — now in the repo

All of it is committed under **`deploy/vss/`**, with setup instructions in
`deploy/vss/README.md`. It was previously box-only, one piece in `/tmp` where a
reboot would have destroyed it.

| Repo | Live location on the box |
|---|---|
| `deploy/vss/compose.override.yml` | `~/vss-blueprint/deploy/docker/compose.override.yml` |
| `deploy/vss/run-up.sh` | `/tmp/run-up.sh` |
| `deploy/vss/ngc-env.example` | `~/.ngc-env` (filled in, mode 600, **not** committed) |
| documented in the README | `VSS_AGENT_PORT=8010` in `dev-profile-base/.env` (backup `.env.bak`) |

If you change deployment config on the box, mirror it back into `deploy/vss/`
or the next person loses it.

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

**Resolved — scoping is by *naming the video in the message*.** There is no
`sensor_id` field on the chat body. The agent is a NAT planner holding
`video_understanding(sensor_id, start_ts, end_ts, prompt)`, `vst_video_list`,
`vst_video_clip`, `vst_snapshot` and `report_agent`, and it picks the sensor
from the filename you mention. `VSSClient.ask()` therefore prefixes every
question with `"In the video <name>, ..."`, which is why upload names are
camera ids. Prefer the VLM tool's own text (`tool_result`) over the planner's
paraphrase — the constrained answer format survives better there.

### There is no `/health`

Not in the 33 paths. Use `GET /openapi.json` for liveness. For identity, the
discriminator is **`/api/v1/videos`** — `/v1/chat/completions` is useless for
this, since every OpenAI-compatible server has one (that is exactly how the
client went green against vLLM).

---

## Next steps, in order

Steps 1–6 of the previous list (fix `looks_like_vss()`, rewrite `VSSClient`,
work out query scoping, prove it end to end, update `ingest.py`/`query.py`,
re-run the tests) are **all done**. What is left:

1. **Decide on `build_payload --install`.** This is the whole remaining gap
   between "the pipeline works" and "the demo shows it". It overwrites
   `frontend/data/sightings.json`, which is Emily's side of the CLAUDE.md
   boundary, and replaces the hand-written fake data. It keeps a `.bak`, so it
   is reversible mid-demo by moving the `.bak` back. **Ask before running it.**
2. **Decide whether predictions matter for the demo.** Currently empty against
   real footage. `pipeline/predict.py --write` replays the fake rides; making it
   emit branches for the pink-shirt track is real, unverified work. If the
   routing panel is part of the pitch, this is the second-biggest gap.
3. **Rotate the credentials.** Both API keys and the SSH password were pasted
   into chat transcripts. The password is unnecessary now that key auth works.
4. **Re-run the sweep only if you must.** `data/subject-hits.json` is cached and
   `--sightings` rebuilds from it in seconds. A fresh `--sweep` costs ~20 minutes
   and will return slightly different timestamps (±10s).

To bring a fresh box or a teammate's checkout up:

```bash
gh release download clips-2026-08-15 --dir data/clips --repo aolinxuu/omnis
python -m vss.pink_subject --all
python -m pipeline.build_payload --validate --ground-truth data/wave-log-pink.json
```

---

## About those Westlake clips

> **Superseded as the demo footage** by the eight pink-shirt clips (see the
> 2026-08-16 update). Kept because the *reasoning* below is still correct and
> still applies to any new capture — but note that its central warning, subject
> size, did **not** apply to the pink-shirt footage. Check the frame before
> assuming a subject is too small; it cost nothing to test and saved a
> `prep_clips.py` run.

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
**`--install` has still never been run** — the hand-written fake demo data is
intact (55 sightings, `"HAND-WRITTEN FAKE DATA"` in its meta). The payload
itself *has* been built and validated many times; only the install step is
untouched.

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

The SSH password is not recorded here, and you should not need it: login is on
the `nvsync.key` described in [The machine](#the-machine). The password **was**
pasted into chat transcripts on 2026-08-15 and again on 2026-08-16, so it should
be rotated with `passwd` on the box along with the API keys.

Git commits on the box use a **repo-local** identity (`git config --local` in
`~/omnis`), set 2026-08-16 because the box had none and commits would otherwise
fail. Nothing was written to the box's global git config.
