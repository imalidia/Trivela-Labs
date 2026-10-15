# Trivela + Football Tactical Engine (Docker)

## Run it
1. Install and open **Docker Desktop**.
2. In this folder, run:
   ```
   docker compose up --build
   ```
   The first build downloads PyTorch and the football model (a few GB, 10–20 min). Later starts take seconds.
3. Open **http://localhost:8000/trivela** → *Analyze a match* → upload a video. The model server is already connected.
   The engine's own HUD is at **http://localhost:8000/**.

The image copies in the Trivela site from `../trivela-frontend/landing` (the `web` build context in `docker-compose.yml`), so `/trivela` always opens the current app, served at `/web/` and pointed at this engine. After changing the frontend, run `docker compose up --build` again.

To use the frontend from its own server instead (e.g. `python -m http.server 5500 --directory trivela-frontend/landing`), open `http://localhost:5500/app.html?engine=http://localhost:8000`.

After the first build you can start and stop the `trivela` stack from the Containers tab in Docker Desktop.

## Files
- `app.py`: the engine (FastAPI): upload, analysis, video stream, saved analyses (`/api/analyses`), and `/api/coach`, which it forwards to the AI coach container.
- `hud.html`: the engine's own control page at http://localhost:8000/.
- `tests/`: `python -m pytest tests` (the API tests run when `fastapi` and `httpx` are installed).
- The AI coach container is built from `../trivela-frontend/coach`; its model key goes in `../trivela-frontend/coach/.env` (see `docs/ai-coach.md` there). Without it, everything else still runs.

## Options
- **Videos already on disk:** put them in `videos/`, then in the HUD's "Server videos folder" tab type just the file name (e.g. `match.mp4`).
- **Share a public link:** `docker compose --profile tunnel up --build`, then copy the `https://….trycloudflare.com` address from the *tunnel* container's logs and add `/trivela`.
- **NVIDIA GPU:** follow the notes at the top of `docker-compose.yml`. Without one it runs on CPU, which is much slower; use 720p + the fastest scan mode.

## Updating
After editing `app.py`, `hud.html` or the site, run `docker compose up --build` again. Saved analyses and processed video stay in the `workspace` volume.
