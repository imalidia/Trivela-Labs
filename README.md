# Trivela Labs

Football match analysis: upload match video, get player stats, a dashboard and an AI coach.

| Folder | What it is |
|---|---|
| `CV/` | Video engine (Python, FastAPI) and `docker-compose.yml` that runs everything |
| `trivela-frontend/` | Website (`landing/`, static, no build) and the AI coach service (`coach/`) |

## Run

```bash
cd CV
docker compose up --build
```

Then open http://localhost:8000/trivela

The AI coach needs a model key: copy `trivela-frontend/coach/.env.example` to `trivela-frontend/coach/.env` and fill it in. Never commit `.env`.

## Tests

```bash
cd trivela-frontend && npm test
```

Pushing to `main` runs both test suites on GitHub Actions (`.github/workflows/tests.yml`).

## Hosting: site on Vercel, engine on a rented GPU

**Site (Vercel).** Import this repo in Vercel and set **Root Directory** to `trivela-frontend/landing`
(Framework preset: *Other*, no build command). Every push to `main` redeploys it.

**Engine (rented NVIDIA GPU, e.g. Vast.ai).** Copy the `CV/` folder to the machine, then run:

```bash
TRIVELA_ALLOWED_ORIGINS=https://your-app.vercel.app bash CV/run_gpu.sh
```

It installs PyTorch (CUDA 12.8, works on RTX 5090/4090/3090), downloads the model, checks the GPU, starts the
engine and prints an `https://....trycloudflare.com` address. Open the Vercel site → *Analyze a match* and paste
that address into the model-server box. The address changes each time the script starts.

The site is https, so the engine must be reached over https too: that's what the Cloudflare tunnel is for.
`TRIVELA_ALLOWED_ORIGINS` lets only your Vercel site start or cancel analyses on the engine.

