# Arclight Server — Infrastructure Reference

## Hardware
- 2× Intel Arc Pro B70 GPUs (BMG-G31, 31GB VRAM each, 256 CUs each)
- Host: arclight (192.168.10.169)
- SSH accounts: `nse-agent` (no sudo), `david` (sudo, requires `-t` for TTY)

## LLM Server (llama-server SYCL)

### Systemd Services (user-level, nse-agent)
- **Template**: `~/.config/systemd/user/llama-vk@.service`
- **Instances**: `llama-vk@0` (Vulkan0, port 8000), `llama-vk@1` (Vulkan1, port 8001)
- **Enabled**: yes (auto-start on boot)
- **Linger**: yes (services persist after logout)
- **Logs**: `/data/nse-agent/logs/llama-vk{0,1}.log`

### Management Commands
```bash
# Status
ssh nse-agent@arclight "systemctl --user status llama-vk@0 llama-vk@1 --no-pager"

# Restart one GPU
ssh nse-agent@arclight "systemctl --user restart llama-vk@0"

# Restart both
ssh nse-agent@arclight "systemctl --user restart llama-vk@0 llama-vk@1"

# Stop both
ssh nse-agent@arclight "systemctl --user stop llama-vk@0 llama-vk@1"

# View logs (live)
ssh nse-agent@arclight "journalctl --user -u llama-vk@0 -f --no-pager"

# View log file
ssh nse-agent@arclight "tail -100 /data/nse-agent/logs/llama-vk0.log"
```

### Health Check
- **Timer**: `llama-health.timer` — runs every 5 minutes
- **Script**: `/data/nse-agent/scripts/health-check.sh`
- **Action**: auto-restarts unhealthy instances
- **Log**: `/data/nse-agent/logs/health-check.log`

### Log Rotation
- **Timer**: `log-rotate.timer` — runs weekly
- **Script**: `/data/nse-agent/scripts/rotate-logs.sh`
- **Policy**: rotate files >10MB, gzip, keep 7 days

### Health Check from Windows
```powershell
Invoke-RestMethod -Uri "http://192.168.10.169:8000/health"
Invoke-RestMethod -Uri "http://192.168.10.169:8001/health"
```

## Model
- Binary: `/home/nse-agent/llama-b9127-vulkan/llama-b9127/llama-server`
- Model: `/home/nse-agent/models/Qwen3.5-9B-Q4_K_M.gguf`
- Flags: `-np 1 --reasoning off --reasoning-format none -c 32768 -ngl 999 --split-mode none`

## Key Constraints
- MUST use `-np 1` with llama-server on Arc (multi-slot causes timeout deadlocks)
- After killing extraction, restart services to flush orphan request queue
- Shutdown: `ssh -t david@arclight "sudo shutdown now"` (requires interactive password)

## Directory Layout
```
/home/nse-agent/
  .config/systemd/user/
    llama-vk@.service          # Service template
    llama-health.service       # Health check oneshot
    llama-health.timer         # 5-minute health check
    log-rotate.service         # Log rotation oneshot
    log-rotate.timer           # Weekly rotation
  llama-b9127-vulkan/          # Server binary
  models/                      # GGUF model files

/data/nse-agent/
  logs/                        # Service logs
  scripts/
    health-check.sh            # Auto-restart unhealthy instances
    rotate-logs.sh             # Rotate & compress old logs
```
