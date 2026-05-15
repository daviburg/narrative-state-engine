# Arclight (B70) Server Access

## SSH Access
- **`ssh arclight`** → `nse-agent@192.168.10.169` — use for server operations (restart ov_serve.py, check logs)
- **`ssh arclight-admin`** → `david@192.168.10.169` — use for admin tasks
- Keys: `~/.ssh/nse-agent_ed25519` and `~/.ssh/arclight-david_ed25519`
- **NEVER** use raw `ssh david@192.168.10.169` — keys are identity-specific (`IdentitiesOnly yes`)

## llama-server (Vulkan, b9127) — Current Active Backend
- Binary: `/home/nse-agent/llama-b9127-vulkan/llama-b9127/llama-server`
- Version: 9127 (a9883db8e), Vulkan backend
- Ports 8000 and 8001, each pinned to one B70 GPU via `--device Vulkan0`/`Vulkan1`
- Launch script: `/tmp/start-llama.sh` (created ad-hoc; see below)
- Flags: `-np 1 --reasoning-format none --reasoning-budget 0 -c 32768 -ngl 999 --split-mode none`
- Logs: `/tmp/llama-8000.log`, `/tmp/llama-8001.log`
- SYCL builds don't work on arclight: Level Zero not installed system-wide, ABI mismatch with ipex-llm's bundled libs

### Launch commands (run on arclight)
```bash
LLAMA_DIR=/home/nse-agent/llama-b9127-vulkan/llama-b9127
MODEL=/home/nse-agent/models/Qwen3.5-9B-Q4_K_M.gguf
export LD_LIBRARY_PATH=$LLAMA_DIR:$LD_LIBRARY_PATH

# GPU 0
nohup $LLAMA_DIR/llama-server -m $MODEL --port 8000 -np 1 --reasoning-format none --reasoning-budget 0 -c 32768 --host 0.0.0.0 -ngl 999 --split-mode none --device Vulkan0 > /tmp/llama-8000.log 2>&1 &

# GPU 1
nohup $LLAMA_DIR/llama-server -m $MODEL --port 8001 -np 1 --reasoning-format none --reasoning-budget 0 -c 32768 --host 0.0.0.0 -ngl 999 --split-mode none --device Vulkan1 > /tmp/llama-8001.log 2>&1 &
```

## ov_serve.py Servers (OpenVINO — Legacy)
- Port 8000 and 8001, each backed by one Arc Pro B70 GPU
- `request_timeout_s=600` — orphan requests block GPU for 10 minutes
- After killing extraction mid-flight, restart servers to flush orphan queue:
  ```
  ssh arclight "pkill -f ov_serve.py; sleep 2"
  ```
  Then restart using the appropriate launch script.

## Config for extraction
- `base_url`: `http://192.168.10.169:8000/v1`
- `base_urls`: `["http://192.168.10.169:8000/v1", "http://192.168.10.169:8001/v1"]`
- Model (current): `Qwen3.5-9B-Q4_K_M` via llama-server Vulkan
- Model (legacy OpenVINO): `qwen3-8b-int4-ov`
