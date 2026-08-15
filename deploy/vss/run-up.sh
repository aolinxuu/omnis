#!/bin/bash
# Bring up VSS 3.2.1 on the DGX Spark, hybrid mode.
#
#   services  -> local containers
#   VLM + LLM -> NVIDIA's hosted API (integrate.api.nvidia.com)
#
# Hybrid rather than fully local because nvcr.io/nim/* returns HTTP 402 with
# this NGC key (no NIM entitlement). It also removes all GPU contention with the
# user's nemoclaw-vllm, which is deliberately left running.
#
# Prerequisites on the box:
#   ~/.ngc-env                                   (see ngc-env.example)
#   ~/vss-blueprint                              (blueprint v3.2.1 checkout)
#   deploy/docker/compose.override.yml           (from this directory)
#   VSS_AGENT_PORT=8010 in developer-profiles/dev-profile-base/.env
#
# Run detached; it takes a few minutes:
#   setsid nohup bash run-up.sh >/dev/null 2>&1 </dev/null & disown
set -o pipefail
exec >/tmp/vss-up.log 2>&1
rm -f /tmp/vss-up.done

source ~/.ngc-env
cd ~/vss-blueprint || exit 1

bash deploy/docker/scripts/dev-profile.sh up -p base -H DGX-SPARK \
  --use-remote-llm --use-remote-vlm \
  --llm nvidia/nvidia-nemotron-nano-9b-v2 \
  --vlm nvidia/cosmos-reason2-8b
echo "EXIT=$?"
echo done > /tmp/vss-up.done
