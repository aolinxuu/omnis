# VSS deployment config

Everything needed to reproduce the running VSS 3.2.1 deployment on
`gn100-223b`. Previously these lived only on the box (one of them in `/tmp`,
which would not survive a reboot).

Full context, including the five blockers already solved, is in
[`../../handover.md`](../../handover.md).

## Shape of the deployment

Hybrid: **VSS services run locally, the VLM and LLM run on NVIDIA's hosted API.**

That is not the default, and it is deliberate. `nvcr.io/nim/*` returns
`HTTP 402 Payment Required` with this NGC key — the key grants
`nvcr.io/nvidia/vss-core/*` but carries no NIM entitlement. Going hybrid dodges
that, and as a side effect removes all GPU contention with the user's
`nemoclaw-vllm` (Qwen3.6-35B), which is left running untouched.

| Model | Where |
|---|---|
| `nvidia/cosmos-reason2-8b` (VLM) | `integrate.api.nvidia.com` |
| `nvidia/nvidia-nemotron-nano-9b-v2` (LLM) | `integrate.api.nvidia.com` |

For a *fully local* deploy the LLM id must instead be
`nvidia/NVIDIA-Nemotron-Nano-9B-v2-FP8` — capitalised exactly like that, and
note the `-FP8`. Spark `hw-DGX-SPARK*.env` files ship only for
`cosmos3-reasoner`, `cosmos-reason2-8b`, and `nvidia-nemotron-nano-9b-v2-fp8`.
The script's own defaults do not work on Spark: it picks a non-FP8 LLM and
`nvidia/cosmos3-nano-reasoner`, which has no service directory at all.

## Setup on a fresh box

```bash
git clone --depth 1 --branch v3.2.1 \
  https://github.com/NVIDIA-AI-Blueprints/video-search-and-summarization.git ~/vss-blueprint

cp ngc-env.example ~/.ngc-env && chmod 600 ~/.ngc-env   # then fill in the key
source ~/.ngc-env
echo "$NGC_CLI_API_KEY" | docker login nvcr.io -u '$oauthtoken' --password-stdin

cp compose.override.yml ~/vss-blueprint/deploy/docker/

# vss-agent uses HOST networking and defaults to 8000, which nemoclaw-vllm
# already owns. Without this it dies with:
#   OSError: [Errno 98] error while attempting to bind on address ('0.0.0.0', 8000)
sed -i 's/^VSS_AGENT_PORT=8000/VSS_AGENT_PORT=8010/' \
  ~/vss-blueprint/deploy/docker/developer-profiles/dev-profile-base/.env

cp run-up.sh ~/ && setsid nohup bash ~/run-up.sh >/dev/null 2>&1 </dev/null & disown
```

Watch `/tmp/vss-up.log`; `/tmp/vss-up.done` appears when it finishes.

**Pull images sequentially with retries.** The systemd-resolved stub on this
network drops roughly one DNS query in three, and compose's parallel pull fails
on it. A loop of `docker pull` per image, retried, gets through.

## Endpoints once up

| Service | URL |
|---|---|
| **Agent API** | `http://gn100-223b:8010` |
| Web UI | `http://gn100-223b:3000` |
| HAProxy public | `http://gn100-223b:7777` |
| Phoenix tracing | `http://gn100-223b:6006` |

`http://gn100-223b:8010/openapi.json` is the authoritative API reference — trust
it over any documentation, this file included. There is no `/health` endpoint;
use `/openapi.json` for liveness.

## Verify

```bash
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'vss|phoenix|redis'
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8010/openapi.json   # 200
docker logs vss-agent 2>&1 | grep -i 'uvicorn running'   # should say 0.0.0.0:8010
```

Expect nine containers: `vss-agent`, `vss-agent-ui`, `vss-vios-sensor`,
`vss-vios-ingress`, `vss-vios-streamprocessing`, `vss-vios-postgres`,
`vss-haproxy-ingress`, `redis`, `phoenix`.
