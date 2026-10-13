# Trivela: football tactical analysis

Upload a match video and get a tactical report: player and ball tracking, possession, passes,
shots, tackles, heat maps, distances and speeds. The engine runs on your own computer.

Two parts are served by one Python app:

- **Trivela web app** at `/trivela`: landing page, dashboard and match analysis.
- **Tactical engine** at `/`: the video processing and tracking tool, with its own HUD and API.

## What the report measures

Positions are estimated from the camera image (broadcast footage has no pitch calibration), so
stats marked **est.** in the report are approximations. Stats marked *not measured yet* are not calculated.
Heat maps and the passing map are not in the report yet.

## Requirements

- Python 3.10 to 3.12
- FFmpeg on your `PATH`
- A GPU is optional. Without one the engine runs on the CPU, which is much slower.

| System  | FFmpeg install |
|---------|----------------|
| Windows | `winget install Gyan.FFmpeg` |
| macOS   | `brew install ffmpeg` |
| Ubuntu  | `sudo apt install ffmpeg` |

## Install

```bash
git clone https://github.com/<your-user>/trivela.git
cd trivela

python -m venv .venv
# Windows:      .venv\Scripts\activate
# macOS/Linux:  source .venv/bin/activate

# 1. PyTorch. Pick ONE from https://pytorch.org/get-started/locally/
pip install torch torchvision                                                       # CPU or macOS
# pip install torch torchvision --index-url https://download.pytorch.org/whl/cu124   # NVIDIA GPU

# 2. The rest
pip install -r requirements.txt
```

## Run

```bash
python run.py
```

- **http://localhost:8000/trivela**: the Trivela app (start here)
- **http://localhost:8000/**: the engine's HUD

The first analysis downloads the football model from Hugging Face into `models/` (once only).

## Analyse a match

1. Open http://localhost:8000/trivela and choose *Analyze a match*.
2. Upload a video, or put it in `videos/` and use the **Local videos** tab with just the file name.
3. Wait for the analysis, then read the report.

## Chat assistant

A round button in the bottom-right corner opens a chat with the Trivela assistant. Once you have analysed a
video, tick **Use my analysed video** to ask about its stats.

The assistant uses an OpenAI-compatible endpoint (AMD Token Factory). Set it up once:

1. Copy `.env.example` to `.env`.
2. Fill in `AMD_API_BASE` (the endpoint address, ending in `/v1`) and `AMD_API_KEY`.
3. Optional: set `AMD_CHAT_MODEL` to the model you want. If you leave it empty, the first model the endpoint
   lists is used, and the list is printed in the server log.
4. Restart `python run.py`.

`.env` is git-ignored, so your key is not committed. Never paste your key into code or a public issue.

## Configuration (optional environment variables)

| Variable | Default | Purpose |
|----------|---------|---------|
| `HOST` | `127.0.0.1` | `0.0.0.0` opens the app to your network |
| `PORT` | `8000` | Port |
| `TRIVELA_VIDEO_DIR` | `./videos` | Folder for local videos |
| `TRIVELA_MODEL_DIR` | `./models` | Where the model is stored |
| `TRIVELA_WORKSPACE` | `./tactical_workspace` | Uploads, processed video and analytics |

Windows PowerShell: `$env:PORT=9000; python run.py`

## Performance

Use 720p and the fastest scan mode on a CPU. An NVIDIA GPU with the CUDA build of PyTorch is much faster.

## Project layout

```
run.py               starts the server
requirements.txt     Python dependencies
app/app.py           the engine: tracking, analytics, HUD and API (FastAPI)
app/trivela.html     the Trivela web app
videos/              your match files (not committed)
models/              the downloaded model (not committed)
tactical_workspace/  generated files (not committed)
```

## Troubleshooting

- **Video won't open**: check it plays in a video player and `ffmpeg -version` works in a terminal.
- **App says it can't reach the engine**: keep `python run.py` running and open the app from the same address.
- **Model download fails**: check your connection and retry.
- **Linux `libGL` error**: `sudo apt install libgl1`.

## Credits and licence

- Football detection model: [martinjolif/yolo-football-player-detection](https://huggingface.co/martinjolif/yolo-football-player-detection) on Hugging Face. Check its licence before commercial use.
- Built on [Ultralytics YOLO](https://github.com/ultralytics/ultralytics).

Add a `LICENSE` file (for example MIT) before publishing.
