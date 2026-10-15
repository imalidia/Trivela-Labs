# Trivela Labs

Football match analysis from video. Upload a match, the Tactical Engine (YOLO tracking) detects players and the ball, and the app turns it into possession, passing, physical and per-player stats. An AI coach (Gemma on AMD) explains them.

This folder holds the site (static, no build step) and the AI coach service. The video engine is in `../CV`. Everything runs in Docker.

## Pages

| Page | File | What it is |
|------|------|------------|
| Landing | `landing/index.html` | Marketing page with the live tracking canvas |
| Dashboard | `landing/dashboard.html` | What is analyzing now, past analyses, recent activity |
| App | `landing/app.html` | Upload a match, follow processing, read the report, ask the AI coach |

## Structure

```
landing/                the site
  index.html  dashboard.html  app.html
  assets/
    css/   landing.css  dashboard.css  app.css  coach.css
    js/    landing.js   dashboard.js   app.js   engine.js  coach.js  config.js
    img/   logo.png  favicon.jpg  vision-crop.jpg  vision-marked.jpg
coach/                  AI coach service (Node, no dependencies), run by ../CV/docker-compose.yml
  server.mjs  dev-site.mjs  Dockerfile  .env.example
  lib/        facts, prompts, tools, guard, model client
docs/                   ai-coach.md (how the coach works), ai-rules.md (its hard rules)
scripts/                coach-demo.mjs, sample-match.json
tests/
```

`assets/js/engine.js` is the client for the engine's HTTP API. It maps the engine's summary into the report: team stats (possession, passing, shots, xG, tackles, set pieces, physical), player rows, and the engine's pitch maps (heatmaps on and off the ball, pass map, shot map).

## Where data lives

Finished analyses are saved **on the engine**, in its Docker volume (`/api/analyses`), so the dashboard, every browser and the AI coach see the same matches. The app also keeps a copy in the browser so a report opens offline. Without a connected engine, the dashboard shows sample matches and uploads run the built-in sample analysis.

## Run it (Docker)

```bash
cd ../CV
docker compose up --build
```

Then open http://localhost:8000/trivela. The engine serves this site at `/web/` and points it at itself. After changing the site, run the same command again.

For the AI coach, copy `coach/.env.example` to `coach/.env` and fill in the model API key first. Details: [docs/ai-coach.md](docs/ai-coach.md).

Other ways to reach the engine:

- **Local frontend + local engine:** `python -m http.server 5500 --directory landing`, then open http://localhost:5500/app.html?engine=http://localhost:8000.
- **Deployed site:** set `engineUrl` in `assets/js/config.js` to the engine's https address (a Cloudflare tunnel works), and add the site's address to `TRIVELA_ALLOWED_ORIGINS` in `../CV/docker-compose.yml`.

## Without Docker

```bash
npm run coach:dev
```

http://localhost:5600 serves the site with a stand-in engine that has the sample match saved, so the dashboard and the AI coach can be tried (the coach needs `coach/.env`). Video analysis needs the real engine.

## Tests

```bash
npm test
```

Uses Node's built-in test runner (Node 18+), no dependencies. The engine's tests: `python -m pytest ../CV/tests`.

## Deploy the site alone

Plain static files. `.github/workflows/pages.yml` runs the tests and publishes `landing/` to GitHub Pages on every push to `main` (Settings → Pages → Source: **GitHub Actions**). Netlify, Vercel or Cloudflare Pages also work: publish directory `landing`, no build command. A hosted site still needs a reachable engine (see "Deployed site" above).
