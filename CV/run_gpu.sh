#!/usr/bin/env bash
# Run the engine on a rented NVIDIA GPU (Vast.ai, RunPod, ...) without Docker and give it an https address
# that the Vercel site can call.
#   TRIVELA_ALLOWED_ORIGINS=https://your-app.vercel.app bash run_gpu.sh
# At the end it prints a link: open it and the site connects to this engine. The link changes every time this script starts.
set -euo pipefail
cd "$(dirname "$0")"

: "${TRIVELA_ALLOWED_ORIGINS:?set it to the Vercel site address, e.g. TRIVELA_ALLOWED_ORIGINS=https://trivela.vercel.app}"
# cu128 runs on RTX 50xx (Blackwell) as well as older cards; the cu124 build does not support the 5090
TORCH_INDEX="${TORCH_INDEX:-https://download.pytorch.org/whl/cu128}"
MODEL=yolo-football-player-detection.pt

if ! command -v ffmpeg >/dev/null; then
  apt-get update && apt-get install -y --no-install-recommends ffmpeg
fi

pip install torch torchvision --index-url "$TORCH_INDEX"
pip install ultralytics fastapi uvicorn python-multipart pyyaml huggingface_hub scipy "lap>=0.5.12"

if [ ! -f "$MODEL" ]; then
  python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='martinjolif/yolo-football-player-detection', filename='$MODEL', local_dir='.')"
fi

# Stop here instead of silently running on the CPU
python -c "import sys, torch; ok = torch.cuda.is_available(); print('GPU:', torch.cuda.get_device_name(0) if ok else 'NOT FOUND'); sys.exit(0 if ok else 1)"

if ! command -v cloudflared >/dev/null; then
  curl -fsSL -o /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
  chmod +x /usr/local/bin/cloudflared
fi

mkdir -p /content tactical_workspace
export TRIVELA_ALLOWED_ORIGINS

# Engine and tunnel run in the background, so closing the terminal or browser tab doesn't stop them.
# Re-running this script restarts only the engine: the tunnel (and so the site's engine address) stays the same.
pkill -f "uvicorn app:app" 2>/dev/null || true
nohup python -m uvicorn app:app --host 0.0.0.0 --port 8000 > engine.log 2>&1 &
if ! pgrep -f "cloudflared tunnel" >/dev/null; then
  rm -f tunnel.log
  nohup cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8000 > tunnel.log 2>&1 &
fi

URL=""
for _ in $(seq 1 60); do
  URL=$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' tunnel.log 2>/dev/null | head -1 || true)
  [ -n "$URL" ] && break
  sleep 1
done
[ -n "$URL" ] || { echo "The tunnel did not start; see tunnel.log"; exit 1; }

echo
echo "=================================================================="
echo " ENGINE ADDRESS (put it in trivela-frontend/landing/assets/js/config.js):"
echo "   $URL"
echo " It stays the same until the machine restarts or the tunnel is killed."
echo " Engine log: tail -f $(pwd)/engine.log"
echo "=================================================================="
