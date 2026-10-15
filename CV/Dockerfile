# syntax=docker/dockerfile:1
# ==============================================================================
# Football Tactical Engine + Trivela site - Docker image
#   CPU (default):  docker compose up --build
#   NVIDIA GPU:     see the GPU notes in docker-compose.yml
# ==============================================================================
FROM python:3.12-slim

ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    YOLO_CONFIG_DIR=/tmp/Ultralytics

# ffmpeg/ffprobe: video repair, probing and encoding. libgl1/libglib2.0-0: OpenCV runtime libraries.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# torch first, from the CPU (or CUDA) index, so ultralytics doesn't pull the multi-GB default CUDA build
RUN pip install torch torchvision --index-url "${TORCH_INDEX}" \
 && pip install ultralytics fastapi uvicorn python-multipart pyyaml huggingface_hub scipy "lap>=0.5.12"

# football detection weights, baked in so the container starts offline
RUN python -c "from huggingface_hub import hf_hub_download; hf_hub_download(repo_id='martinjolif/yolo-football-player-detection', filename='yolo-football-player-detection.pt', local_dir='.')" \
 && python -c "from ultralytics import YOLO; print('Model classes:', YOLO('yolo-football-player-detection.pt').names)"

COPY app.py hud.html ./
RUN python -m py_compile app.py && mkdir -p /content tactical_workspace

# The Trivela site (trivela-frontend/landing, passed in as the "web" build context by docker-compose.yml).
# app.py serves it at /web with a config.js pointing at this engine, so the app is connected without setup.
COPY --from=web . web/
RUN test -f web/app.html && test -f web/assets/js/engine.js

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/info', timeout=4)"

CMD ["python", "-m", "uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
