# Rider tracker — pipeline runbook

Everything except the frontend. `frontend/` is frozen and untouched by this
build; nothing here writes into it unless you explicitly pass `--install`.

```
data/cameras.json          camera roster + Amber-Alert seed (hand-maintained)
data/wave-log.json         ground truth from the ride (scoring only)
data/raw-manifest.json     raw file per camera + its first-frame wall clock  [you supply]
clips/                     trimmed, normalised clips + clips.json sidecar    [generated]
pipeline/prep_clips.py     raw -> clips/, writes the time-mapping sidecar
pipeline/detect.py         YOLO -> data/sightings.json
pipeline/reachability.py   seed + elapsed -> reachable camera set
pipeline/build_payload.py  detector output -> the frozen-contract payload
vss/client.py              VSS REST wrapper (+ --verify against the live spec)
vss/ingest.py              clips -> VSS, writes vss/ingest-index.json
vss/query.py               natural-language query, captions, optional HTTP server
tests/                     stdlib-only, no GPU or clips needed
```

## The contract discrepancy — read this first

The build doc's §2 sketch and the frozen contract disagree. **The frozen
contract wins**, because `frontend/feed.js` is what actually parses the file and
the frontend cannot be changed.

| build doc §2 | actual frozen contract |
|---|---|
| `camera` | `camera_id` |
| `t` = integer minutes since seed | `t` = ISO-8601 with offset |
| `confidence` | `conf` |
| states `detected`, `confirmed` | `detected`, `unverified`, `confirmed`, `linked`, `lost` |
| separate `cameras.json` fetched by the frontend | one bundle: `cameras`, `tracks`, `sightings`, `events`, `predictions`, `ground_truth` |
| `lng` | `lon` |

Seed-relative minutes still exist, as an internal convenience. They are
converted at exactly one boundary — `pipeline/common.to_iso()` — and never leak
into an emitted record.

**The one integration fact that will silently cost you the demo:**
`evalPanel()` in `app.js` scores *only* sightings whose state is exactly
`"linked"`. A pipeline that emits nothing but `confirmed` shows `recall 0/N`
no matter how good the detection is. `build_payload.py --validate` fails loudly
on this.

## Order of operations

```bash
pip install -r requirements.txt
python -m unittest discover -s tests          # 34 tests, no GPU needed

# 1. reachability — works standalone, no clips, no GPU
python -m pipeline.reachability --elapsed 3
python -m pipeline.reachability --elapsed 25 --uncertainty 15    # too_wide

# 2. clips  (needs data/raw-manifest.json — see prep_clips.py docstring)
python -m pipeline.prep_clips --dry-run
python -m pipeline.prep_clips

# 3. detection
python -m pipeline.detect --every 5 --device 0

# 4. VSS  (verify the API surface BEFORE trusting the wrappers)
python -m vss.client --wait --verify
python -m vss.ingest --wait --smoke-test
python -m vss.query "person in orange on a scooter" --repeat 3
python -m vss.query --captions

# 5. assemble, check, then install
python -m pipeline.build_payload --validate
python -m pipeline.build_payload --install     # only this step touches frontend/
```

`--install` backs up `frontend/data/sightings.json` to `.json.bak` first. To
roll back mid-demo, move the `.bak` back — the frontend needs no rebuild.

## VSS

Endpoint paths (`POST /files`, `POST /summarize`, `POST /chat/completions`,
`DELETE /files/{id}`) are confirmed against NVIDIA's current blueprint docs.
The request **body schemas** are not hardcoded from memory: NVIDIA publishes
them only on the deployed instance at `http://<endpoint>/docs`, and they have
moved between blueprint releases.

So the first VSS command to run is:

```bash
python -m vss.client --verify
```

It pulls `/openapi.json` off the running instance and prints the real parameter
names for the three endpoints used here. If it disagrees with the defaults in
`vss/client.py`, change the defaults. Do not argue with the server.

Set `VSS_API_ENDPOINT` if it is not `http://localhost:8000`.

### Hour-three checkpoint

Agreed in advance so it does not read as an accusation: **if VSS is not
ingesting by hour three, cut it.** Detection plus reachability becomes the
demo. There is still time to harden that at hour three; there is not at hour
five. Nothing outside `vss/` imports VSS, so cutting it is deleting two
commands from the runbook — `build_payload.py` skips captions when
`data/captions.json` is absent.

## Deliberately not built

- **`web/index.html`** — frozen at your instruction. The query box, the fetch
  wiring, and the arcade chrome from §7 are all untouched.
- **Predictions / routing branches.** The contract carries them and the frontend
  renders them, but the build doc puts no routing engine in scope. Emitted as
  `[]`, which the frontend handles.
- Everything on the doc's "never start these" list: cross-camera re-id, OSM
  import, camera quality scoring, live scraping, flow analytics, clustering.

`state: "linked"` is assigned by colour-weighted score threshold on a single
subject, not by appearance matching across cameras. That is a threshold, not
re-identification.

## Choices that deviate from the doc, and why

- **Seed is `CMR-0185` (2nd & Battery), not `CMR-0184`.** The doc's seed block
  is placeholder data and is internally inconsistent: `CMR-0184` is Westlake &
  Denny, well off the corridor, while the coordinates printed beside it
  (47.60930, −122.33920) are actually 2nd & Pike. `CMR-0185` is the real
  corridor start and the first logged wave. All 104 cameras carry real SDOT IDs
  and coordinates, already present in the repo — no scraping needed.
- **All 104 cameras, not eight.** Real data was already available, and the
  `too_wide` bloom is far more convincing across a full roster.
- **Colour match has a floor (0.35), not just a soft weight.** The doc says
  colour must score rather than filter; a floor is what guarantees it, since a
  hue miss can then never drive the score to zero on its own.
- **`prep_clips.py` reads the wave log; `detect.py` never does.** Trim windows
  come from logged waves plus ≥90 s padding. Detection searches the whole clip
  independently. `tests/test_pipeline.py` asserts `detect.py`'s source contains
  no reference to the wave log, so the circularity cannot creep back in.
