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

## Hosting: site on Vercel, engine on Modal

**Site (Vercel).** Import this repo in Vercel and set **Root Directory** to `trivela-frontend/landing`
(Framework preset: *Other*, no build command). Every push to `main` redeploys it.

**Engine (Modal, `CV/modal_app.py`).** Every push to `main` that touches `CV/`, the site or the coach redeploys it
(`.github/workflows/deploy-modal.yml`). It has a fixed address, set once in `trivela-frontend/landing/assets/js/config.js`.

- A small CPU server (the API, uploads, saved analyses, video playback, AI coach) sleeps when nobody uses it.
- Each analysis starts an NVIDIA A10 and stops it when done: no GPU runs, and nothing is billed, between analyses.
- Uploads, processed video and saved analyses live on the Modal volume `trivela-data`.

One-time setup:
1. Modal: create the secret `trivela-coach` with the variables from `trivela-frontend/coach/.env.example`.
2. Modal: Settings → API Tokens → New Token. GitHub: add it as the repository secrets `MODAL_TOKEN_ID` and `MODAL_TOKEN_SECRET`.
3. If the Vercel address changes, update `SITE_ORIGINS` in `CV/modal_app.py` (only that site may start analyses).

The Docker setup above still runs everything on one machine, with or without an NVIDIA GPU.
