/* Trivela ⇄ Tactical Engine (the YOLO model server; its address is set in config.js).
   Client for its HTTP API, plus toReport(): engine summary -> the stats, player rows and info the app saves on the engine.
   Engine team codes: 0 = team A (darker kit), 1 = team B (lighter kit), 2 = goalkeeper, 3 = official. */
(function (root) {
  'use strict';

  const STORE_KEY = 'trivela.engineUrl';
  const CHUNK_SIZE = 4 * 1024 * 1024;   // matches the engine's own uploader; small enough for any proxy's request cap (Cloudflare's is 100 MB)
  const CHUNK_RETRIES = 4;
  const UPLOAD_PARALLEL = 4;            // chunks in flight at once
  const MIN_PLAYER_SECS = 3;            // shorter tracks are fragments, not players
  const MAX_PLAYERS = 40;
  const MAX_EVENTS = 3000;              // keeps the saved log small; a full match logs well under this

  /* Every stat the engine measures, in report order. u = unit suffix, d = decimals.
     src: the engine's match-analytics key (stats.<src>_a / _b). Those stats come from its pitch-mapping pass and are
     only in the report when the engine sent them (an older engine, or a failed pass, leaves them out instead of showing 0) */
  const STAT_DEFS = [
    { g: 'Possession', k: 'Possession', u: '%' },
    { g: 'Possession', k: 'Touches' },
    { g: 'Possession', k: 'Time in final third', u: ' min', d: 1, src: 'final_third_s', scale: 1 / 60 },
    { g: 'Possession', k: 'Touches in opposition box', src: 'box_touches' },
    { g: 'Possession', k: 'Dispossessed', src: 'dispossessed' },
    { g: 'Passing', k: 'Passes' },
    { g: 'Passing', k: 'Pass accuracy', u: '%' },
    { g: 'Passing', k: 'Long balls', src: 'long_balls_att' },
    { g: 'Passing', k: 'Crosses', src: 'crosses' },
    { g: 'Attacking', k: 'Shots', src: 'shots' },
    { g: 'Attacking', k: 'Shots on target', src: 'shots_on' },
    { g: 'Attacking', k: 'Goals', src: 'goals' },
    { g: 'Attacking', k: 'xG', d: 2, src: 'xg' },
    { g: 'Attacking', k: 'Dribbles', src: 'dribbles' },
    { g: 'Attacking', k: 'Strikes' },
    { g: 'Attacking', k: 'Fastest strike', u: ' km/h', d: 1, src: 'fastest_strike' },
    { g: 'Set pieces', k: 'Corners', src: 'corners' },
    { g: 'Set pieces', k: 'Free kicks', src: 'free_kicks' },
    { g: 'Defending', k: 'Balls won' },
    { g: 'Defending', k: 'Tackles', src: 'tackles' },
    { g: 'Defending', k: 'Interceptions', src: 'interceptions' },
    { g: 'Defending', k: 'Clearances', src: 'clearances' },
    { g: 'Defending', k: 'Saves', src: 'saves' },
    { g: 'Defending', k: 'Defensive contribution', src: 'def_actions' },
    { g: 'Physical', k: 'Distance covered', u: ' km', d: 2 },
    { g: 'Physical', k: 'Top speed', u: ' km/h', d: 1 },
    { g: 'Physical', k: 'Avg sprint speed', u: ' km/h', d: 1, src: 'sprint_kmh' },
    { g: 'Physical', k: 'Sprints >25 km/h' },
  ];
  /* Per-player numbers from the analytics pass kept with the report (engine.players), besides the player rows */
  const PLAYER_EXTRA = ['shots', 'shots_on', 'goals', 'xg', 'tackles', 'interceptions', 'clearances', 'dispossessed', 'dribbles',
    'crosses', 'long_balls', 'long_balls_att', 'box_touches', 'free_kicks', 'corners', 'saves', 'final_third_s', 'on_ball_s', 'sprint_kmh'];
  const GK_CODE = 2;
  const PITCH_L = 105, PITCH_W = 68;

  /* A server address -> "https://host" (no trailing slash), or null if it isn't an http(s) URL */
  function normalizeBase(input) {
    let s = String(input || '').trim();
    if (!s) return null;
    // No scheme typed: a local server is plain http, anything else https. "javascript:x" becomes an invalid host
    if (!/^[a-z][a-z0-9+.-]*:\/\//i.test(s)) s = (/^(localhost|127\.0\.0\.1)(:|\/|$)/i.test(s) ? 'http://' : 'https://') + s;
    try {
      const u = new URL(s);
      return /^https?:$/.test(u.protocol) ? u.origin : null;
    } catch (_) {
      return null;
    }
  }

  /* Engine status -> overall progress 0..1 (scan pass ~15%, tracking pass ~80%, encode the rest) */
  function phaseStep(st) {
    const p = Math.max(0, Math.min(100, +st.progress || 0)) / 100;
    switch (st.status) {
      case 'scanning': return 0.05;
      case 'processing': return 0.15 + 0.8 * p;
      case 'finalizing': return 0.97;
      case 'completed': return 1;
      default: return 0;
    }
  }

  const num = v => (Number.isFinite(+v) ? +v : 0);
  const fix = (v, d) => +num(v).toFixed(d);
  const isNum = v => typeof v === 'number' && Number.isFinite(v);

  /* The engine's pitch maps -> home/away. The engine rotates its maps so team A attacks towards x = 105;
     the report has home attacking left to right, so when home is team B every point and grid is turned 180°
     (for a row-major grid that is the cells in reverse order). side: engine team code -> 'home' | 'away' | null */
  function toMaps(maps, pitch, homeCode, side) {
    if (!maps || typeof maps !== 'object' || !maps.heat) return null;
    const flip = homeCode === 1;
    const size = Array.isArray(maps.heat_size) && maps.heat_size.length === 2 ? maps.heat_size.map(num) : [21, 14];
    const grid = (g, n) => (Array.isArray(g) && g.length === n ? (flip ? [...g].reverse() : [...g]).map(num) : null);
    const cells = size[0] * size[1];
    const heatOf = s => ({ all: grid(maps.heat[s + '_all'], cells), on: grid(maps.heat[s + '_on'], cells), off: grid(maps.heat[s + '_off'], cells) });
    const [home, away] = homeCode === 0 ? ['a', 'b'] : ['b', 'a'];
    const pt = (x, y) => (flip ? [fix(PITCH_L - num(x), 1), fix(PITCH_W - num(y), 1)] : [fix(x, 1), fix(y, 1)]);
    const passes = (Array.isArray(maps.passes) ? maps.passes : [])
      .filter(p => Array.isArray(p) && side(p[4]))
      .map(([x0, y0, x1, y1, t]) => [...pt(x0, y0), ...pt(x1, y1), side(t)]);
    const shots = (Array.isArray(maps.shots) ? maps.shots : [])
      .filter(s => Array.isArray(s) && side(s[2]))
      .map(([x, y, t, on, xg, kmh, goal]) => { const [px, py] = pt(x, y); return { x: px, y: py, team: side(t), on: !!on || !!goal, goal: !!goal, xg: fix(xg, 2), kmh: num(kmh) }; });
    return {
      size, heat: { home: heatOf(home), away: heatOf(away) }, passes, shots,
      player_size: Array.isArray(maps.player_heat_size) && maps.player_heat_size.length === 2 ? maps.player_heat_size.map(num) : [12, 8],
      anchored_pct: pitch && isNum(pitch.anchored_pct) ? pitch.anchored_pct : null,
    };
  }

  /* summary: the engine's /api/summary JSON. homeKit: 'a' if the home side wears the darker kit, 'b' if the lighter one */
  function toReport(summary, homeKit, base = null) {
    const s = summary.stats || {}, info = summary.team_info || {}, players = s.players || {}, events = summary.events || [];
    const numbers = summary.player_no || {};   // the numbers drawn over the players in the engine's video
    const homeCode = homeKit === 'b' ? 1 : 0, awayCode = 1 - homeCode;
    const side = code => (code === homeCode ? 'home' : code === awayCode ? 'away' : null);
    const pair = (a, b) => (homeCode === 0 ? [a, b] : [b, a]);   // engine A/B -> [home, away]

    // Outfield players only: goalkeepers and officials have no team side
    const outfield = Object.entries(players)
      .map(([id, p]) => ({ id: +id, p, team: id in info ? side(+info[id]) : null }))
      .filter(x => x.team);
    const teamSum = (team, f) => outfield.filter(x => x.team === team).reduce((t, x) => t + num(f(x.p)), 0);
    const teamMax = (team, f) => outfield.filter(x => x.team === team).reduce((t, x) => Math.max(t, num(f(x.p))), 0);
    const byTeam = f => ['home', 'away'].map(f);

    // Per-player passing from the event log: [frame, 'pass', receiver, team, passer] and [frame, 'won', winner, team, loser]
    const passed = {}, lost = {}, won = {}, strikes = [0, 0];
    for (const [, type, id, team, extra] of events) {
      if (type === 'pass') passed[extra] = (passed[extra] || 0) + 1;
      else if (type === 'won') { lost[extra] = (lost[extra] || 0) + 1; won[id] = (won[id] || 0) + 1; }
      else if (type === 'strike' && (team === 0 || team === 1)) strikes[team] += 1;
    }

    const stats = {
      Possession: pair(Math.round(num(s.possession_a)), Math.round(num(s.possession_b))),
      Touches: byTeam(t => teamSum(t, p => p.touches)),
      Passes: pair(num(s.passes_a), num(s.passes_b)),
      'Pass accuracy': pair(Math.round(num(s.pass_acc_a)), Math.round(num(s.pass_acc_b))),
      'Balls won': pair(num(s.gains_a), num(s.gains_b)),
      Strikes: pair(strikes[0], strikes[1]),
      'Distance covered': byTeam(t => fix(teamSum(t, p => p.dist_m) / 1000, 2)),
      'Top speed': byTeam(t => fix(teamMax(t, p => p.top_kmh), 1)),
      'Sprints >25 km/h': byTeam(t => teamSum(t, p => p.sprints)),
    };
    for (const d of STAT_DEFS) {
      if (!d.src || !isNum(s[d.src + '_a']) || !isNum(s[d.src + '_b'])) continue;
      const v = x => fix(x * (d.scale || 1), d.d || 0);
      stats[d.k] = pair(v(s[d.src + '_a']), v(s[d.src + '_b']));
    }
    // Same order as STAT_DEFS, so a saved report lists its stats the way the app shows them
    const ordered = Object.fromEntries(STAT_DEFS.filter(d => d.k in stats).map(d => [d.k, stats[d.k]]));

    const row = ({ id, p, team }) => {
      const done = passed[id] || 0, tries = done + (lost[id] || 0);
      return {
        player_key: 't' + id, team, jersey: numbers[id] ?? null, pos: null,
        distance: fix(num(p.dist_m) / 1000, 3), top_speed: fix(p.top_kmh, 1), sprints: num(p.sprints),
        passes: done, accuracy: tries ? Math.round(done / tries * 100) : null, touches: num(p.touches), won: won[id] || 0, intensity: null,
      };
    };
    const players_ = outfield
      .filter(x => num(x.p.secs) >= MIN_PLAYER_SECS)
      .sort((a, b) => num(b.p.secs) - num(a.p.secs) || a.id - b.id)
      .slice(0, MAX_PLAYERS)
      .map(row);
    const keepers = keeperRows(players, info, side, row).map(k => ({ ...k, ...heatGrids(players[k.player_key.slice(1)], homeCode) }));

    const kit = summary.team_colors || {};
    const engine = {
      base, home_kit: homeCode === 1 ? 'b' : 'a', tracker: summary.tracker || null, mode: summary.mode || null,
      fps: num(summary.fps), duration_s: summary.fps ? Math.round(num(summary.total_frames) / num(summary.fps)) : 0,
      kit: { home: (homeCode === 0 ? kit.team_a : kit.team_b) || null, away: (homeCode === 0 ? kit.team_b : kit.team_a) || null },
      kit_learned: !!summary.kit_learned,
      ball: { visible_pct: num(s.ball_visible_pct), max_kmh: num(s.ball_max_kmh), avg_kmh: num(s.ball_avg_kmh) },
      wide_pct: num(s.wide_pct), swaps_fixed: num(s.swaps_fixed), tracks: outfield.length,
      maps: toMaps(summary.maps, summary.pitch, homeCode, side),
      players: playerExtras(players_, players, homeCode),
      keepers,
    };

    // For the AI coach: the event log in seconds with home/away sides, and each player row's time on screen
    const fps = num(summary.fps) || 25;
    const track = id => (Number.isInteger(id) && id >= 0 ? 't' + id : null);
    const context = {
      events: events.slice(0, MAX_EVENTS).map(([frame, type, id, team, extra]) => {
        const pair = type === 'pass' ? [track(extra), track(id)] : type === 'won' ? [track(id), track(extra)] : [track(id), null];
        const value = type === 'strike' || type === 'sprint' ? fix(extra, 1) : null;
        return { t: fix(num(frame) / fps, 1), type, team: side(team), player: pair[0], other: pair[1], value };
      }),
      secs: Object.fromEntries(players_.map(p => [p.player_key, Math.round(num(players[p.player_key.slice(1)].secs))])),
    };
    return { stats: ordered, players: players_, engine, context };
  }

  /* The two goalkeepers: tracks the engine labelled keeper and placed in front of one goal (gk_side, from its analytics pass).
     One per side, the longest seen; their numbers ride along without the heat grid. Empty for older engines */
  function keeperRows(players, info, side, row) {
    const best = {};
    for (const [id, p] of Object.entries(players)) {
      if (+info[id] !== GK_CODE || !isNum(p.gk_side) || num(p.secs) < MIN_PLAYER_SECS) continue;
      const team = side(p.gk_side);
      if (team && (!best[team] || num(p.secs) > num(best[team].p.secs))) best[team] = { id: +id, p, team };
    }
    return ['home', 'away'].filter(t => best[t]).map(t => {
      const x = best[t], extra = {};
      for (const k of PLAYER_EXTRA) if (isNum(x.p[k])) extra[k] = x.p[k];
      return { ...row(x), pos: 'GK', ...extra };
    });
  }

  /* A player's heat grids (whole match, on and off the ball), turned like the team maps so home attacks left to right */
  const HEAT_KEYS = ['heat', 'heat_on', 'heat_off'];
  function heatGrids(p, homeCode) {
    const out = {};
    for (const k of HEAT_KEYS) if (Array.isArray(p[k]) && p[k].length) out[k] = (homeCode === 1 ? [...p[k]].reverse() : [...p[k]]).map(num);
    return out;
  }

  /* Analytics-pass numbers and heat grids for each listed player, keyed by player_key. Empty when the engine sent none */
  function playerExtras(rows, players, homeCode) {
    const out = {};
    for (const { player_key: key } of rows) {
      const p = players[key.slice(1)] || {};
      if (!PLAYER_EXTRA.some(k => isNum(p[k])) && !HEAT_KEYS.some(k => Array.isArray(p[k]))) continue;
      const row = {};
      for (const k of PLAYER_EXTRA) if (isNum(p[k])) row[k] = p[k];
      Object.assign(row, heatGrids(p, homeCode));
      out[key] = row;
    }
    return out;
  }

  /* ---------- HTTP client (browser only) ---------- */
  function getBase() {
    try { return normalizeBase(localStorage.getItem(STORE_KEY)); } catch (_) { return null; }
  }
  function setBase(url) {
    const b = normalizeBase(url);
    try { if (b) localStorage.setItem(STORE_KEY, b); else localStorage.removeItem(STORE_KEY); } catch (_) { /* storage blocked: lasts this page only */ }
    return b;
  }

  async function call(base, path, init) {
    let res;
    try { res = await fetch(base + path, init); } catch (err) {
      if (err && err.name === 'AbortError') throw err;
      throw new Error('Can\'t reach the model server. Check it\'s running and try again.');
    }
    const text = await res.text();
    let body = null;
    try { body = text ? JSON.parse(text) : null; } catch (_) { body = null; }
    if (!res.ok) throw Object.assign(new Error((body && (body.error || body.detail)) || `Model server error ${res.status}`), { status: res.status });
    return body;
  }

  const form = obj => { const fd = new FormData(); for (const [k, v] of Object.entries(obj)) fd.append(k, v); return fd; };

  /* info() checks the server answers and returns { device, gpu } */
  const info = base => call(base, '/api/info');
  const status = base => call(base, '/api/status');
  const summary = base => call(base, '/api/summary');
  const cancel = base => call(base, '/api/cancel', { method: 'POST' });
  const videoUrl = base => `${base}/api/video-stream/tactical_output.mp4`;

  /* Chunked upload with per-chunk retries. onProgress(0..1). signal: AbortSignal to stop.
     Chunk 0 goes first (the engine starts a fresh file on it), then UPLOAD_PARALLEL chunks travel at once: each one
     writes to its own byte offset, so order doesn't matter, and the link stays busy instead of waiting on round trips */
  async function upload(base, file, onProgress, signal) {
    const total = Math.max(1, Math.ceil(file.size / CHUNK_SIZE));
    let done = 0;
    const send = async i => {
      const blob = file.slice(i * CHUNK_SIZE, Math.min(file.size, (i + 1) * CHUNK_SIZE));
      for (let attempt = 1; ; attempt++) {
        if (signal && signal.aborted) throw Object.assign(new Error('Upload cancelled'), { name: 'AbortError' });
        try {
          await call(base, '/api/upload-chunk', { method: 'POST', signal,
            body: form({ chunk: blob, fileName: file.name, chunkIndex: i, totalChunks: total, offset: i * CHUNK_SIZE }) });
          break;
        } catch (err) {
          if (err.name === 'AbortError' || err.status === 409 || err.status === 400 || attempt >= CHUNK_RETRIES) throw err;
          await new Promise(r => setTimeout(r, 800 * attempt));
        }
      }
      done++;
      if (onProgress) onProgress(done / total);
    };
    await send(0);
    let next = 1;
    const worker = async () => { while (next < total) await send(next++); };
    await Promise.all(Array.from({ length: Math.min(UPLOAD_PARALLEL, total - 1) }, worker));
  }

  /* mode: fast | balanced | ultra. Graphics are burned into the video, which is what the report's Video tab plays */
  const start = (base, { mode = 'balanced', res = '720p' } = {}) =>
    call(base, '/api/start-analysis', { method: 'POST', body: form({ fileName: 'source_match.mp4', targetRes: res, mode, source: 'upload', burn: '1' }) });

  /* Saved analyses live on the engine (its Docker volume), so every browser and the AI coach see the same ones */
  const analyses = base => call(base, '/api/analyses');
  const analysis = (base, id) => call(base, `/api/analyses/${encodeURIComponent(id)}`);
  const saveAnalysis = (base, row) => call(base, `/api/analyses/${encodeURIComponent(row.id)}`,
    { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(row) });
  /* The engine set at deploy time (config.js; the engine's own copy of the site points at itself), else the one saved in this browser */
  const configuredBase = () => normalizeBase((root.TRIVELA_CONFIG || {}).engineUrl) || getBase();

  const api = { STAT_DEFS, normalizeBase, phaseStep, toReport, getBase, setBase, configuredBase, info, status, summary, cancel, videoUrl, upload, start,
    analyses, analysis, saveAnalysis };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TrivelaEngine = api;
})(typeof self !== 'undefined' ? self : this);
