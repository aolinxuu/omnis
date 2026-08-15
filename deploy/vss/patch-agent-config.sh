#!/usr/bin/env bash
# Patch the VSS agent NAT config on the box for local-vLLM mode:
# the shipped `vllm_vlm` profile does not disable Qwen's thinking mode, so the
# VLM can spend its whole 4096-token budget reasoning and return no answer.
# Idempotent. Run on the Spark; defaults to ~/vss-blueprint/deploy/docker.
set -euo pipefail
cd "${1:-$HOME/vss-blueprint/deploy/docker}"
f=developer-profiles/dev-profile-base/vss-agent/configs/config.yml
[ -f "$f.bak" ] || cp "$f" "$f.bak"
python3 - "$f" <<'PY'
import sys
p=sys.argv[1]; s=open(p).read()
old="""  vllm_vlm:
    _type: openai
    model_name: ${VLM_NAME}
    base_url: ${VLM_BASE_URL}/v1
    temperature: 0.0
    max_tokens: 4096
"""
new=old+"""    model_kwargs:
      extra_body:
        chat_template_kwargs:
          enable_thinking: ${LLM_ENABLE_THINKING:-false}
"""
seg=s.split("vllm_vlm:")[1].split("rtvi_vlm:")[0] if "vllm_vlm:" in s else ""
if "enable_thinking" in seg:
    print("already patched")
else:
    assert old in s, "vllm_vlm block not as expected - inspect config.yml"
    open(p,"w").write(s.replace(old,new)); print("patched vllm_vlm: enable_thinking=false")
PY
echo "now recreate the agent:"
echo "  docker compose -p mdx --env-file developer-profiles/dev-profile-base/generated.env -f compose.yml -f compose.override.yml up -d --no-deps --force-recreate vss-agent"
