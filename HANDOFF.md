# Engine accuracy work: handoff plan

Written for the next session to follow step by step. Read it all once, then work the **Steps** in order.
Branch: `claude/modal-engine` (not merged to `main`). Last engine commit: `0556d45`.

---

## 0. Ground rules (save credits)

- **Do not** use multi-agent workflows or subagents. Work inline.
- **GPU runs cost real money**: a full eval of the 6-min test clip on the A10 costs about $0.15–0.30. The user's Modal spend limit is $0 beyond $30/month free credits. Run at most **one GPU eval per step**, and only when a step says so.
- Prefer the **local CPU harness** (section 3) on 20–30 s segments: free, and reruns take about 30 s once detections are cached.
- Keep the user's style: short, plain replies. They dislike long explanations ("no yapping").
- Never print or commit secrets. Modal token is in GitHub secrets `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET`. The coach key is in the Modal secret `trivela-coach`.

---

## 1. What exists (infrastructure, all working)

| Piece | Where | Notes |
|---|---|---|
| Engine | `CV/app.py` | FastAPI + YOLO11m football detector (`yolo-football-player-detection.pt`, classes: 0 ball, 1 goalkeeper, 2 player, 3 referee) + BoT-SORT + custom team/ball/pitch/analytics. One job at a time. |
| Modal app | `CV/modal_app.py` | `Web` class (CPU, sleeps, serves API + site + runs the Node coach on 127.0.0.1:5600) and `analyze` (A10, 2 h timeout) started per job. Volume `trivela-data` (workspace at `/data/workspace`), Dict `trivela-jobs` (progress). `app.WORKER` hook in app.py hands jobs to the GPU. |
| Engine URL | `https://imalidia16--trivela.us-east.modal.run` | `/trivela` serves the site itself (best test link). `trivela-frontend/landing/assets/js/config.js` points here. |
| Allowed site | `SITE_ORIGINS` in `CV/modal_app.py` = `https://trivela-lab.vercel.app` | Change there if Vercel address changes. |
| Deploy | `.github/workflows/deploy-modal.yml` | On push to `main` **and** `claude/modal-engine` (paths CV/**, landing/**, coach/**). Runs `CV/tests` then `modal deploy`. Prints errors/URL as run annotations. |
| Eval | `CV/modal_eval.py` + `.github/workflows/eval-modal.yml` | Triggered by a commit on `claude/modal-engine` whose message contains `[eval ...]`. Results are committed to the orphan branch **`eval-data`** under `runs/<short sha>/`. |
| Test clip | Modal volume `/data/eval/clip.mp4` | 6:11 Serie A highlights, Roma 2–2 Inter, 720p25, AV1 audio opus. Also exported to `eval-data` branch `runs/dfd517f/clip.mp4` with the weights `runs/dfd517f/yolo-football-player-detection.pt`. |
| Local harness | `CV/tools/run_local.py` | Runs `process_football_match` on CPU on a segment, caches detections per frame, prints per-second metrics. |

### Eval tag syntax (put it in the commit message)

- `[eval]` → snapshot of the site's last analysis (no GPU).
- `[eval rerun=balanced times=9.5,10.4,90]` → re-analyse the test clip on the A10 with this commit's code, then snapshot + full-size frames at those clip seconds. **Costs GPU.**
- `[eval export]` → also return the raw clip + weights (already done once; don't repeat).
- `res=720p` optional.

### Reading eval results (from this container)

GitHub log downloads are blocked by the network proxy; use annotations and the `eval-data` branch:

```bash
cd /home/user/trivela-labs
gh run list --repo imalidia/Trivela-Labs --branch claude/modal-engine --limit 4
JOB=$(gh run view <RUN_ID> --repo imalidia/Trivela-Labs --json jobs --jq '.jobs[0].databaseId')
gh api repos/imalidia/Trivela-Labs/check-runs/$JOB/annotations --jq '.[] | "\(.annotation_level): \(.title): \(.message)"'
git config --add remote.origin.fetch '+refs/heads/eval-data:refs/remotes/origin/eval-data'   # once
git fetch -q origin eval-data && git ls-tree --name-only origin/eval-data runs/
mkdir -p /tmp/eval && git archive origin/eval-data runs/<sha> | tar -x -C /tmp/eval
```

Files per run: `summary.json` (stats, events, maps, perf), `tactical_metadata.json.gz` (per-frame `p` = `[id,x1,y1,x2,y2,code]`, code 0 = team A (darker kit), 1 = team B, 2 = goalkeeper, 3 = official; `b` = `[x,y,predicted]`; `c` = carrier), `sheet_*.jpg` (frame every 10 s), `frame_*.jpg`, `job_state.json`. View images with the Read tool.

---

## 2. Ground truth for the test clip (Roma 2–2 Inter highlights)

Times are **clip seconds**. Team A in the engine = Roma (darker kit), team B = Inter (white).

| Event | Clip time | How we know |
|---|---|---|
| Goal 1, Roma (#17 volley from edge of box, cross from the right first at ~9.4 s) | ball struck ~10.2–10.5 s, in net ~10.8 s | screenshot; local runs: strike at frame 262 |
| Score bug 1-0 | read by 24 s | OCR |
| Goal 2, Roma | between ~100 s and 172 s (VAR check visible ~140–150 s) | OCR: 2-0 confirmed at 172 s |
| Goal 3, Inter | ~212–218 s ("GOOOOOAL" banner at ~220 s) | OCR: 2-1 at 228 s |
| Goal 4, Inter | ~290–300 s | OCR: 2-2 at 308 s |

Expected engine output on the clip: `goals_a = 2`, `goals_b = 2`, each goal attached to a shot (shots map entries with 7th element = 1), `shots_on ≥ goals`. Replays may show goals twice: count only once (see Step 6).

Baseline before this work (run `707983d`, old code): ball visible 14.9 % of wide frames, 0 goals, shots 2/1 with 0 on target, keeper never tracked, many players without rings, radar squashed to the goal line.

---

## 3. Set up the local harness (do this first in a new session)

```bash
# 1) tools
apt-get update -q && apt-get install -y -q tesseract-ocr          # works in this container (Ubuntu apt)
pip download torch --no-deps -d /tmp/whl -q                        # PyPI torch (download.pytorch.org is blocked)
pip install -q /tmp/whl/torch-*.whl torchvision ultralytics lap pytesseract \
  pytest numpy opencv-python-headless scipy fastapi httpx python-multipart pyyaml && rm -rf /tmp/whl
# 2) clip + weights
export TRIVELA_WORK=/tmp/trivela-work && mkdir -p $TRIVELA_WORK
cd /home/user/trivela-labs
git config --add remote.origin.fetch '+refs/heads/eval-data:refs/remotes/origin/eval-data'; git fetch -q origin eval-data
git show origin/eval-data:runs/dfd517f/clip.mp4 > $TRIVELA_WORK/clip.mp4
git show origin/eval-data:runs/dfd517f/yolo-football-player-detection.pt > $TRIVELA_WORK/yolo-football-player-detection.pt
ffmpeg -y -loglevel error -i $TRIVELA_WORK/clip.mp4 -c:v libx264 -preset veryfast -crf 18 -c:a aac $TRIVELA_WORK/clip_h264.mp4   # ~2 min (OpenCV can't read AV1)
cp CV/tools/run_local.py $TRIVELA_WORK/
# 3) run a segment (first run detects every frame on CPU: ~1.4 s/frame, so 20 s of video ≈ 12 min; reruns ≈ 30 s)
cd $TRIVELA_WORK && python run_local.py 0 20 --tag t1 --frames 10.4,10.8 > t1.log 2>&1; tail -40 t1.log
```

- Run long first passes in the background (`run_in_background: true`). The harness prints events, strikes, shots map, label counts and a per-second table (`A`/`B`/`GK`/`OFF` = average tracked people per frame, `ball` = % of frames with a detected ball). Frames land in `$TRIVELA_WORK/out/<tag>/frame_*.jpg`.
- Useful segments: `0 20` (goal 1), `95 80` (goal 2 area, long), `200 25` (goal 3), `283 25` (goal 4), `80 15` (wide open play, good for rings/radar/sprints).
- **Tests**: `cd CV && python -m pytest tests/test_media.py tests/test_match_analytics.py tests/test_api.py -q` (this order matters locally: `test_api` stubs `ultralytics`, which breaks `test_media` if it runs after; CI has no ultralytics so CI is fine). Expected: 40 passed. Site tests: `cd trivela-frontend && npm test` → 60 pass.

---

## 4. What commit `0556d45` changed in `CV/app.py` (already done)

1. **Ball every wide frame**: `DetectorThread` now detects every wide frame (`"det": wide`); people still only every `stride` frames (`item["need"]`). Main loop takes ball candidates from any `bn`, people only when `do_det`.
2. **Ball tracker** (`BallTracker`): candidates carry `over_pitch` (`ball_over_pitch()` = turf in the column just below). Gate bounded `min(0.15W, 0.035W·(1+0.7·lost))`; off-pitch candidates only continue a flight near the prediction; re-acquire after `BALL_REACQ=4` lost frames only on-pitch with conf ≥ 0.25; stop drawing after `BALL_SHOW_LOST=8`.
3. **Tracker thresholds** (`TRACKER_YAML`): `track_high 0.25, low 0.12, new_track 0.22`. People boxes ≥ `PERSON_MIN_CONF=0.12` go to the tracker after `drop_part_boxes()` (removes part-of-player duplicates). `fuse_people()` merges the previous frame's detections (max conf, add missed people).
4. **Roles** (`TeamModel.label_for`): officials' shirt colour learned from detector referee boxes (conf ≥ 0.5, needs ≥ 12 samples: `REF_MIN_SAMPLES`); `refish()` sample = nearer the officials' colour than both kits; off-kit and not refish ⇒ **goalkeeper**; early decision after 6 samples at ≥ 80 % off-kit; `recent` window (last 8 shirt samples) splits a track that slid from a player onto a keeper/referee (sets `flipped`, caller splits identity, `hand_over` copies only the recent counts). Shirt resampling now per track call (`calls[cid] % KIT_EVERY`), fixing a bug where half of the ids were never resampled.
5. **Speeds** (`GroundModel`): per frame, fit player height vs feet row (`h = a + b·row`); sideways m/px = 1.78/h; depth foreshortening `k = f·b/h` with `f = h(center)·CAM_DIST_M/1.78`, `CAM_DIST_M=60` (defined in the analytics constants block so tests can load it). Speeds are averaged over `SPEED_WIN_S=1.0`; distance = integrated 1-s speed; sprint = 1-s speed ≥ 7.0 m/s held `SPRINT_MIN_S=0.2`, ends < 6.0; readings > `MAX_RUN_MS=10.4` m/s reset the window (ID jumps). On the clip the fit gives camera height ≈ 20 m (median), k ≈ 2–4.
6. **Strikes**: ball speed uses `GroundModel.metres(..., depth_pow=BALL_DEPTH_POW=0.5)`; strike = two consecutive raw readings (`v > STRIKE_KMH=65` and previous > 0.7×65); re-armed when smoothed speed < `STRIKE_REARM_KMH=45` or direction turns ≥ 60°. Local result on 0–20 s: strikes at 7.0 s (long ball) and 10.5 s (the goal volley).
7. **Shots** (`MatchAnalytics`): pending strikes now keep the ball image trail from 0.4 s before (`img_trail`), keeper boxes from the last second (`gk_trail`); `_toward_keeper()` decides shot/on-target in image space (flight closes on a keeper); falls back to the old pitch-coordinate test. Local result: goal volley = Roma shot (on-target not yet, the goal fixes it).
8. **Goals** (`ScoreReader`): OCR (tesseract via `pytesseract`) of the top 18 % band every 2 s in a worker thread; locates the score in each half of the band, then reads only a crop around it (`psm 6`); `goals()` confirms a new score on 2 consecutive reads and returns `[(frame, side)]`; `MatchAnalytics._goal()` attaches each goal to the last shot (else last strike) within `GOAL_LOOKBACK_S=150` s, marks it on target, learns which score side is which team, bumps `goals`. New stat `goals_a/b`, per-player `goals`, shots map entries get a 7th element `1` for goals. tesseract + pytesseract added to `CV/Dockerfile` and `CV/modal_app.py` image.
9. **Radar/pitch**: `PitchMapper.depth_y()` gives across-pitch metres from metric depth (distance ∝ 1/height), per-shot smoothed height fit; falls back to the old row curve. Test camera in `tests/test_match_analytics.py` is now a pinhole camera (20 m high, 25 m back); mapper test passes.
10. **Report**: `engine.js` STAT_DEFS gained `Goals` (src `goals`) and `Defensive contribution` (src `def_actions`); `stats-view.js` shows Goals (top list, attack tab, per-player column).

---

## 5. Steps (do in order; tick each in your replies)

### Step 1 — Read the GPU rerun of `0556d45` (already ran; no new GPU spend)

1. Find the eval run for commit `0556d45` (`gh run list ... --workflow eval-modal.yml`). If it failed, read its annotation; most likely causes: image build (tesseract), an exception in `process_football_match` (check `job_state.json` → `error_msg`).
2. Fetch `runs/0556d45` from `eval-data`. Report in a table vs baseline (section 2): `ball_visible_pct`, `goals_a/b`, `shots_a/b`, `shots_on_a/b`, label counts in `team_info` (how many code 2), `sprints`, `sprint_kmh_a/b`, top speeds (`stats.top_speeds`), `perf.fps` and `perf.seconds` (speed regression from per-frame ball detection; old run was 86 fps / 108 s), `pitch.anchored_pct`.
3. Look at `sheet_01.jpg` and the `frame_*.jpg` (9.5, 10.4, 90, 150, 212, 293): rings on most players, keeper ring (magenta), referee **without** ring, radar dots spread (not stuck on one line), ball marker.
4. Print `summary["events"]` strikes and `summary["maps"]["shots"]`; compare with the goal times in section 2.

Acceptance to move on: no crash; goals found ≥ 3 of 4. Write down every gap you see; the next steps fix them.

### Step 2 — Goals: make sure all 4 are found and attached to the right shot

Likely issue: goal 1 missed because the **initial score 0-0 is never confirmed** (needs 2 consecutive reads; the first seconds of a clip may read garbage). In `ScoreReader.goals()`:
- Treat the first single read of `(0, 0)` as confirmed immediately (0-0 is the only score that needs no confirmation), **or** if the first confirmed score is `(1, 0)`/`(0, 1)` and the earliest reads (before it) contain no confirmed score, infer `0-0` before and emit a goal at the first read of the new score.
- Test offline without the engine: a small script that feeds `clip_h264.mp4` frames every 50 frames into `ScoreReader.offer()` (wait while `q.full()`), `close(60)`, print `reads` and `goals()`. Target: goals ≈ `[(~24 s,0), (~172 s,0), (~228 s,1), (~308 s,1)]`. Also add a unit test for `goals()` with a synthetic `reads` list (misreads, 0-0 start, a goal ruled out).
- Check attachment: each goal's shot must be the real goal shot (clip times in section 2), not a replay or an unrelated strike. If the attached shot is a **replay** (goal shown again later), see Step 6.

### Step 3 — Shots: precision and recall on the clip

1. Using the local harness on segments `0 20`, `200 25`, `283 25`, and the second-goal segment (find it from the sheet: look for the goal-mouth action before the VAR frames, probably 100–135 s), check each goal shot is a shot.
2. Tune only if needed: `STRIKE_KMH` (65), the "two readings" rule, `_toward_keeper` thresholds (`d0 ≥ 2h`, `de ≤ 0.55·d0`, `de ≤ 4h`, on-target `de ≤ 1.3h` or lost closing within 2h). Print debug with a temporary `print` and remove it before committing.
3. False shots: list every shot in the full-clip summary with its clip time and check the frame (eval frames or local `--frames`). Long balls/crosses toward the box near the keeper can be mis-judged; if so require the strike to start within ~35 m of goal (pitch x from `PitchMapper`) **or** the flight to end within 2h of the keeper.
4. xG: uses pitch position of the strike (`shot_xg`). If the shot position is clearly wrong (e.g. 37 m for a volley at the edge of the box), use image-space distance to the keeper as a fallback: `dist_m ≈ |p0 − keeper| / keeper_height_px × 1.78 × sqrt(k)`; goal line ≈ keeper + 1–2 m.

### Step 4 — Keeper and officials

1. Full-clip summary: there should be 2 keeper identities (`id_info` labels `GK1`, `GK2`), each with `gk_side` set in `stats.players`. Referee: label `REF*`, no ring in frames (frame 90 old run had the referee ringed as team A).
2. If the referee is still a team player: check `team.ref_c` gets learned on the full clip (add a temporary print of `len(team.ref_samples)` after the main loop). If too few confident referee boxes, also learn from tracks the detector calls referee ≥ 25 % (`roles[cid]["ref"]`).
3. If outfield players become keepers (more than 2–3 GK identities with long screen time): raise `OFF_KIT_EARLY_SHARE` to 0.85 or the recent-window share from 0.75 to 0.85.

### Step 5 — Speeds and sprints (user asked explicitly: "make sure sprint speed is calculated properly")

1. From the full-clip summary: `stats.top_speeds` must be ≤ 37 km/h, mostly 28–35 km/h for sprinting players; `sprint_kmh_a/b` (average peak of sprints) ~ 27–32 km/h; sprint count per team for 6 minutes of highlights: plausibly 5–30.
2. Sanity test locally on segment `80 15` (open play): print per player `top_kmh`, `dist_m`, `secs`. Distance/second for an active player should be ~2–7 m/s.
3. If speeds look systematically high/low, the lever is `CAM_DIST_M` (60 m): depth scale ∝ it. Keep it unless clearly wrong; document any change.
4. Do **not** go back to 0.2 s speed windows (too noisy).

### Step 6 — Replays (avoid double counting in full matches and highlights)

Broadcasters hide the score bug during replays. Once `ScoreReader` has a `box`, add a cheap per-frame "bug visible" check (normalized correlation of the grey crop vs a template stored when the score was read; every 5th frame is enough). Mark camera shots (`seg_n` segments) where the bug is absent for > 70 % of frames as **replay**; skip strikes/shots/possession events from replay segments in analytics (the frame log has `s` per frame; pass a set of replay segment ids into `match_analytics`). Verify on the clip: goal replays must not add shots. If this gets complicated, ship without it and tell the user.

### Step 7 — Radar / pitch positions

1. Look at radar in frames 9.5/10.4 (near goal) and 90 (midfield). Players must be spread like on the pitch, not on one line.
2. Known weakness: x (along the touchline) comes from camera pan + keeper anchors (`PitchMapper._solve`); near-goal views can still be off. Cheap improvement if needed: in `_observe` use the ground model to get lateral metres; and when a keeper is visible, anchor x so the keeper is ~`GK_DEPTH_M` (9 m, consider 5–6 m when the ball is far) from his goal line.
3. A real fix would be pitch-line calibration (not available as a public model; Hugging Face search found nothing usable). Do not start it without asking the user.

### Step 8 — Stats coverage (the user's 28-stat list)

Make this table in the reply, from code + the full-clip summary, and fix any gap:

| User stat | Engine key | Shown in report |
|---|---|---|
| Passes / Passing accuracy % / Possession % | `passes_*`, `pass_acc_*`, `possession_*` | yes |
| Heat maps on/off ball (team + player) | `maps.heat.{a,b}_{on,off,all}`, players `heat_on/heat_off` | check dashboard renders both |
| Crosses / Long balls | `crosses_*`, `long_balls_att_*` (`long_balls_*` = accurate) | yes |
| Shots / on target / Goals / xG / Shots map | `shots_*`, `shots_on_*`, `goals_*`, `xg_*`, `maps.shots` | yes (Goals new) |
| Tackles / Saves / Dispossessed / Interceptions / Clearances | `tackles_*`, `saves_*`, `dispossessed_*`, … | yes |
| Defensive contribution | `def_actions_*` (= tackles + interceptions + clearances) | yes (new team row; per player `def` already) |
| Corners / Free kicks | `corners_*`, `free_kicks_*` | yes (dead-ball detection, check counts) |
| Distance / Top speed / Sprint speed / Sprints (number) | players `dist_m`, `top_kmh`, `sprint_kmh`, `sprints`; team `sprint_kmh_*` | yes |
| Passing map | `maps.passes` | check |
| Dribbles | `dribbles_*` | yes |
| Time in final third | `final_third_s_*` | yes |
| Touches / Touches in opposition box | players `touches`, `box_touches_*` | yes |
| Ball speed | `stats.ball_max_kmh`, `ball_avg_kmh`, `fastest_strike_*` | **check: probably not shown** → add a "Ball speed (max)" line in the match view from `engine.ball.max_kmh` (already in the saved report) |

`fastest_strike_*` is capped at 150 km/h in `_flush_kick` (`min(150.0, k["v"])`) and the old run showed 150.0 for both teams = a cap hit, i.e. a glitch. Replace with the strike speeds from `xev` strike events (5th field) for that team, max, and ignore > 130.

### Step 9 — Performance check (speed matters to the user: target a 90-min match in minutes)

- Per-frame ball detection doubled detector work. From `perf` in the full-clip summary: `fps`, `gpu_s`, `wait_s`, `bottleneck`. If total time for the 6-min clip > ~150 s (it was 108 s), options: `batch` 12 → 24 for balanced mode (A10 has 24 GB), and/or detect the ball on non-people frames at a lower `imgsz` (e.g. 960 crop) — only if accuracy stays.
- The planned big win is splitting a match across up to 9 A10s in parallel (user's Modal Starter allows 10 concurrent GPUs). Design was discussed with the user: learn kits once → process overlapping pieces in parallel → join tracking logs, re-ID and analytics on CPU → render video pieces → concat. **Separate task; ask the user before starting.**

### Step 10 — Finish

1. Remove `print`/debug code; run tests (section 3); `git diff` review.
2. One GPU eval: commit with `[eval rerun=balanced times=<goal times>]`, read results (Step 1 method), compare with the previous run in a short table.
3. Before merging to `main`: in `.github/workflows/deploy-modal.yml` remove `claude/modal-engine` from `branches` (keep `main`). Keep `eval-modal.yml` on the branch or delete it and `CV/modal_eval.py` if the user doesn't want it.
4. Ask the user if they want a PR. Remind them: the `eval-data` branch contains the broadcast clip and weights (private repo); delete it when done (`git push origin --delete eval-data`).
5. Tell the user how to test: open `https://imalidia16--trivela.us-east.modal.run/trivela`, analyse a clip; for the 90-min comparison they have the real stats.

---

## 6. Code map (CV/app.py, approximate line numbers at `0556d45`)

| Area | Look for |
|---|---|
| Constants | top: `MODES`, `BALL_MIN_CONF`, `PERSON_MIN_CONF`, `SPRINT_MS`…, `STRIKE_KMH` |
| Grass/turf helpers | `GrassModel`, `valid_geometry`, `on_turf`, `drop_part_boxes`, `fuse_people`, `ball_over_pitch`, `grass_top` |
| Teams/roles | `TeamModel` (`label_for`, `add_ref_sample`, `refish`, `hand_over`) |
| Tracker | `TRACKER_YAML`, `make_tracker`, `IdentityManager` (`split`, `assign_new`, `check_swaps`) |
| Perspective | `CameraMotion`, `GroundModel` |
| Re-ID | `reid_cluster`, `REID_TARGET` |
| Ball | `BallTracker`, `BALL_*` |
| Possession | `PossessionEngine` (passes / won events) |
| Analytics | `# MATCH ANALYTICS` section: constants incl. `CAM_DIST_M`, `GOAL_LOOKBACK_S`; `PitchMapper` (`depth_y`, `to_pitch`, `build`, `_solve`), `MatchAnalytics` (`frame`, `_strike_shot`, `_toward_keeper`, `_goal`, `_flush_kick`, `result`), `match_analytics()` |
| Score OCR | `ScoreReader`, `_score_of`, `SCORE_*` (just before `pick_encoder`) |
| Drawing | `draw_marker`, `draw_radar`, `burn_numbers` (second pass draws numbers + radar from the log) |
| Main loop | `process_football_match`: pass 1 (kit + referee colour sampling), main loop (ball cands → people → labels → speeds → ball speed/strikes → drawing → log), finalize (re-ID, stats, analytics, burn numbers, summary) |
| API/Modal hook | `WORKER`, `job_state()`, routes `/api/*` |
