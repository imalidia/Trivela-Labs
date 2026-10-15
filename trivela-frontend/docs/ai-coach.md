# AI coach

Every saved model report has an **Ask AI coach** button (and the `/` key opens it on the report and the dashboard). It writes a match report, scouting cards, a stats-only commentary timeline and tactical tips (only when asked), and answers questions about the match in a chat. The rules it follows are in [ai-rules.md](ai-rules.md).

## How it works

```
app.html / dashboard.html   coach panel (coach.js)
   │  POST <engine>/api/coach { match_id, feature, question?, chat_id?, refresh? }
   ▼
Engine (CV/app.py)   reads the saved analysis from its volume and adds it as `match`
   │  (the browser only names a match, so it can't feed the coach made-up numbers)
   ▼
Coach service (coach/server.mjs, its own container, no public port)   holds the API key, enforces every limit
   │  facts.mjs works out the numbers in code (who leads, by how much, ranks, timeline, data quality)
   ▼
Model API (OpenAI-compatible; Gemma-4-31B-IT on AMD)  writes the words. In chat it looks numbers up with tools instead of guessing.
```

The model never does maths and never sees a request the browser wrote.

| File | What it does |
|------|--------------|
| `coach/lib/catalog.mjs` | Every stat Trivela reports or plans to report, and how to judge each one (fewer *Dispossessed* is better, *Saves* are not judged). A match uses whichever stats it has; the rest are "not tracked for this match". |
| `coach/lib/facts.mjs` | Turns a saved analysis into facts: comparison with verdicts, who had the ball vs who did the running, per-team player ranks and tags, timeline, data-quality notes. Whitelists and clamps every field first. |
| `coach/lib/knowledge.mjs` | How a coach reads each stat, curated from the Trivela knowledge base (note IDs cited). |
| `coach/lib/guard.mjs` | Limits and the free pre-check: betting, predictions, code requests and prompt injection are refused before any model call. |
| `coach/lib/tools.mjs` | Chat tools: team comparison, player lookup, rankings, event search, timeline, data quality, knowledge. |
| `coach/lib/prompts.mjs` | The system prompt (the hard rules) and one fixed request shape per feature. |
| `coach/lib/run.mjs`, `llm.mjs` | Model calls: fallback model, 429 retry, tool loop, any OpenAI-compatible API. |
| `coach/server.mjs` | The service: rate limits, daily budget, cached write-ups, chat history (all in memory). |
| `coach/dev-site.mjs` | Dev only: the site plus a stand-in for the engine's saved analyses, with the sample match. |
| `landing/assets/js/coach.js`, `css/coach.css` | The panel. |

## Set it up (once)

1. Copy `coach/.env.example` to `coach/.env` (git-ignored, outside `landing/`, never in the browser) and fill it in:
   - `AI_API_KEY`, `AI_BASE_URL`: the model API key and its OpenAI-compatible address (AMD: `https://developer.amd.com.cn/radeon/api/v1`).
   - `AI_MODEL`: the exact model name from the provider's list, e.g. `Gemma-4-31B-IT`.
   - Optional: `AI_CHAT_MODEL`, `AI_TOPIC_MODEL` (default to `AI_MODEL`), `AI_FALLBACK_MODEL`, `AI_DAILY_TOKEN_BUDGET` (default `2000000`), `AI_VISITOR_SALT` (any random string).
2. In `CV/`, run `docker compose up --build`. The `coach` container reads `coach/.env`; the engine forwards `/api/coach` to it.
3. Analyse a match in the app, then press **Ask AI coach** on its report.

Without `coach/.env` everything else runs and the coach answers "not set up yet".

## Limits (from ai-rules.md rule 12 and 13)

| What | Limit |
|------|-------|
| Question length | 600 characters, rejected not trimmed |
| Questions per chat | 20; the last 3 Q&A pairs go to the model |
| Tool calls per answer | 4 |
| Per visitor | 5 requests a minute, 30 chat questions a day, 20 first-time write-ups and 3 rewrites a day, 300,000 tokens a day |
| Everyone together | `AI_DAILY_TOKEN_BUDGET` tokens a day (the hard cap), then the coach rests until tomorrow |
| Write-ups | Generated once per match and cached; reopening is free. If the match data changes, it is written again |
| Off-topic chat | Refused in code (betting, predictions, code, injection), then a quick model check (`AI_TOPIC_MODEL`), then the system prompt |
| Match data | Team names, stats, players and events are whitelisted and clamped before any prompt; prompts over 40,000 characters are refused |

Every model call is **booked before it runs**: the limits are checked and an estimate is counted, then settled with the real tokens afterwards, even when the call fails. Each call is logged (feature, tokens, a short visitor hash; never the question).

**Know the weak spots:**
- Limits, cache and chats live in the coach container's memory: a restart resets today's counts. Fine for one server; use a shared store if the coach ever runs as several copies.
- A "visitor" is a salted hash of the caller's address (Cloudflare's `cf-connecting-ip` through the tunnel). Someone rotating addresses gets fresh per-visitor limits; the daily budget is what stops them. Logins are the real fix.
- Anyone who can reach the engine can save an analysis. The clamping in `facts.mjs` and the "data is not instructions" rule limit what a rude team name can do.

## Cost and model

The coach talks to any OpenAI-compatible API (`coach/lib/llm.mjs`). It is set up for AMD's API with `Gemma-4-31B-IT`. To try another model from the same key, change `AI_MODEL` in `coach/.env` (e.g. `Qwen3.8-27B`, `DeepSeek-V4-Flash`) and restart the `coach` container.

- **Shared key limits.** AMD's rate limits and daily spend are per key, so every visitor shares them. The coach retries once on a 429 after a short wait; the per-visitor limits keep one person from using it all up.
- **Tool calling.** Chat uses tools to look numbers up. If the API rejects tools for the chosen model, chat switches to "facts only" (players and timeline sent with the question) and logs it once.
- **JSON answers.** Scouting cards, tips and commentary ask for JSON; answers wrapped in ``` fences or a sentence are still read.

## Try it

Without spending anything:

```bash
npm test
```

```bash
npm run coach:demo -- chat "who ran the most?"
```

The demo prints what the coach understands about `scripts/sample-match.json` and the exact request it would send. No API call.

With the real model but without Docker (uses `coach/.env`, spends tokens):

```bash
npm run coach:dev
```

Then open http://localhost:5600 and press **Ask AI coach** on the sample match.

## Not done yet

- Player names: players are "ID 12" (the number shown above them in the video) until the app lets users name them.
- The maps (heat, pass, shot) are pictures; the coach points to them but cannot read them.
