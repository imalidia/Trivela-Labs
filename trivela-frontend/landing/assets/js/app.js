(() => {
/* ---------- Sample data (deterministic, clearly fictional) ---------- */
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const R = rng(7);
const rand = (a, b) => a + R() * (b - a);
const W = 105, H = 68;

const HOME_FORM = [[5,34,1,'GK'],[22,10,2,'RB'],[20,26,5,'CB'],[20,42,4,'CB'],[22,58,3,'LB'],[42,20,8,'CM'],[38,34,6,'DM'],[42,48,10,'CM'],[68,12,7,'RW'],[72,34,9,'ST'],[68,56,11,'LW']];
const AWAY_FORM = [[100,34,1,'GK'],[83,58,2,'RB'],[85,42,5,'CB'],[85,26,4,'CB'],[83,10,3,'LB'],[64,58,7,'RM'],[66,42,8,'CM'],[66,26,6,'CM'],[64,10,11,'LM'],[44,42,9,'ST'],[46,26,10,'ST']];

const players = [];
[['home', HOME_FORM], ['away', AWAY_FORM]].forEach(([team, form]) => form.forEach(([x, y, n, pos], i) => {
  const gk = pos === 'GK';
  players.push({ id: team[0] + n, team, idx: i, jersey: n, pos, bx: x, by: y, ph: rand(0, 6.28), ph2: rand(0, 6.28),
    distance: gk ? rand(4.9, 5.6) : rand(9.4, 11.9), top: gk ? rand(22, 25) : rand(28.5, 34.2),
    passes: Math.round(gk ? rand(22, 34) : rand(24, 72)), acc: Math.round(rand(72, 94)), touches: Math.round(gk ? rand(30, 42) : rand(38, 96)),
    sprints: Math.round(gk ? rand(1, 4) : rand(8, 24)), spark: Array.from({ length: 12 }, () => rand(.2, 1)) });
}));
const byTeam = t => players.filter(p => p.team === t);

const STATS = [
  { g: 'Possession', k: 'Possession', h: 56, a: 44, u: '%' },
  { g: 'Possession', k: 'Touches', h: 642, a: 531 },
  { g: 'Possession', k: 'Time in final third', h: 21, a: 15, u: ' min' },
  { g: 'Passing', k: 'Passes', h: 498, a: 391 },
  { g: 'Passing', k: 'Pass accuracy', h: 87, a: 81, u: '%' },
  { g: 'Passing', k: 'Progressive passes', h: 41, a: 29 },
  { g: 'Passing', k: 'Final-third entries', h: 52, a: 38 },
  { g: 'Passing', k: 'Pass network', view: 'passes' },
  { g: 'Attacking', k: 'Shots', h: 14, a: 9 },
  { g: 'Attacking', k: 'Shots on target', h: 6, a: 3 },
  { g: 'Attacking', k: 'Goals', h: 2, a: 1 },
  { g: 'Attacking', k: 'Shot map', view: 'shots' },
  { g: 'Attacking', k: 'Dribbles', h: 11, a: 8 },
  { g: 'Physical', k: 'Distance covered', h: 112.4, a: 109.8, u: ' km', d: 1 },
  { g: 'Physical', k: 'Top speed', h: 34.1, a: 33.2, u: ' km/h', d: 1 },
  { g: 'Physical', k: 'Sprints >25 km/h', h: 148, a: 131 },
  { g: 'Physical', k: 'High-intensity distance', h: 9.8, a: 8.9, u: ' km', d: 1 },
  { g: 'Spatial', k: 'Heatmaps', view: 'heat' },
  { g: 'Spatial', k: 'Average positions', view: 'avg', txt: ['4-3-3', '4-4-2'] },
  { g: 'Defending', k: 'Tackles', h: 17, a: 21 },
  { g: 'Defending', k: 'Interceptions', h: 12, a: 15 },
  { g: 'Defending', k: 'Clearances', h: 14, a: 23 },
];

const SHOTS = [];
for (let i = 0; i < 14; i++) SHOTS.push({ team: 'home', x: rand(82, 99), y: rand(20, 48), on: i < 6, goal: i < 2 });
for (let i = 0; i < 9; i++) SHOTS.push({ team: 'away', x: rand(6, 23), y: rand(20, 48), on: i < 3, goal: i < 1 });

function heatGrid(list, spread = 11) {
  const g = [];
  for (let j = 0; j < 14; j++) for (let i = 0; i < 21; i++) {
    const cx = (i + .5) * 5, cy = (j + .5) * (H / 14);
    let v = 0;
    list.forEach(p => { if (p.pos === 'GK' && list.length > 1) return; const dx = cx - (p.bx + (p.team === 'home' ? 6 : -6)), dy = cy - p.by; v += Math.exp(-(dx * dx + dy * dy) / (2 * spread * spread)); });
    g.push({ i, j, v: v + R() * .25 });
  }
  const m = Math.max(...g.map(c => c.v));
  return g.map(c => ({ ...c, v: c.v / m }));
}
const HEAT = { home: heatGrid(byTeam('home')), away: heatGrid(byTeam('away')) };

/* ---------- Clip simulation: ball keyframes + events ---------- */
const CLIP = 180;
const SCRIPTED = [{ t: 22, team: 'home', goal: false }, { t: 47, team: 'home', goal: true }, { t: 76, team: 'away', goal: false },
  { t: 98, team: 'away', goal: true }, { t: 118, team: 'home', goal: false }, { t: 133, team: 'home', goal: true }, { t: 161, team: 'away', goal: false }];
const keys = []; const events = [];
(function buildClip() {
  let t = 0, holder = players.find(p => p.id === 'h6'), si = 0;
  keys.push({ t: 0, p: holder });
  while (t < CLIP - 2) {
    const dt = rand(1.6, 3.1); const nt = t + dt;
    const shot = SCRIPTED[si];
    if (shot && nt >= shot.t) {
      const shooter = players.find(p => p.id === (shot.team === 'home' ? 'h9' : 'a9'));
      if (holder !== shooter) { keys.push({ t: shot.t - 2.2, p: shooter }); events.push({ t: shot.t - 2.2 - .9, type: 'pass', team: shooter.team, from: holder, to: shooter }); }
      const gx = shot.team === 'home' ? 105 : 0, gy = 34 + rand(-3, 3) + (shot.goal ? 0 : (R() < .5 ? -6 : 6));
      keys.push({ t: shot.t, pt: { x: gx, y: gy } });
      events.push({ t: shot.t - .5, type: shot.goal ? 'goal' : 'shot', team: shot.team, from: shooter });
      const gk = players.find(p => p.id === (shot.team === 'home' ? 'a1' : 'h1'));
      const restart = shot.goal ? players.find(p => p.id === (shot.team === 'home' ? 'a10' : 'h9')) : gk;
      holder = restart; t = shot.t + 2.5; keys.push({ t, p: holder }); si++; continue;
    }
    const sameTeam = R() < .82;
    const pool = sameTeam ? byTeam(holder.team).filter(p => p !== holder && p.pos !== 'GK') : byTeam(holder.team === 'home' ? 'away' : 'home').filter(p => p.pos !== 'GK');
    pool.sort((a, b) => Math.hypot(a.bx - holder.bx, a.by - holder.by) - Math.hypot(b.bx - holder.bx, b.by - holder.by));
    const next = pool[Math.floor(R() * Math.min(4, pool.length))];
    events.push({ t: t + dt * .55, type: sameTeam ? 'pass' : 'interception', team: sameTeam ? holder.team : next.team, from: holder, to: next });
    t = nt; holder = next; keys.push({ t, p: holder });
  }
})();
events.sort((a, b) => a.t - b.t);

function keyIndex(t) { let lo = 0; for (let i = 0; i < keys.length; i++) if (keys[i].t <= t) lo = i; return lo; }
function shiftAt(t) {
  const i = keyIndex(t), a = keys[i], b = keys[Math.min(i + 1, keys.length - 1)];
  const ax = a.p ? a.p.bx : a.pt.x, bx = b.p ? b.p.bx : b.pt.x;
  const f = b.t > a.t ? Math.min(1, (t - a.t) / (b.t - a.t)) : 0;
  return ((ax + (bx - ax) * f) - 52.5) * .28;
}
function posAt(p, t) {
  const s = p.pos === 'GK' ? shiftAt(t) * .15 : shiftAt(t);
  return { x: Math.max(1, Math.min(104, p.bx + s + Math.sin(t * .7 + p.ph) * 2.6)), y: Math.max(1, Math.min(67, p.by + Math.cos(t * .55 + p.ph2) * 3)) };
}
function ballAt(t) {
  const i = keyIndex(t), a = keys[i], b = keys[i + 1];
  const pa = a.p ? posAt(a.p, t) : a.pt;
  if (!b) return pa;
  const kick = a.t + (b.t - a.t) * .55;
  if (!a.p) return a.pt;
  if (t < kick) return { x: pa.x + 1, y: pa.y + .6 };
  const from = posAt(a.p, kick), to = b.p ? posAt(b.p, b.t) : b.pt, f = (t - kick) / (b.t - kick), e = 1 - Math.pow(1 - f, 2);
  return { x: from.x + 1 + (to.x - from.x - 1) * e, y: from.y + .6 + (to.y - from.y - .6) * e };
}
function possessionAt(t) {
  let h = 0, a = 0;
  for (let i = 0; i < keys.length && keys[i].t < t; i++) {
    const k = keys[i]; if (!k.p) continue;
    const end = Math.min(t, keys[i + 1] ? keys[i + 1].t : t); if (k.p.team === 'home') h += end - k.t; else a += end - k.t;
  }
  return h + a ? Math.round(h / (h + a) * 100) : 50;
}

/* ---------- State ---------- */
const S = {
  route: 'upload', tab: 'overview', matchId: 'm1', teamView: 'heat', teamSide: 'both', heatMode: 'all', sort: { k: 'distance', dir: -1 }, showAll: false,
  matches: [{ id: 'm1', home: 'Harbor City', away: 'Northgate United', hc: null, ac: null, day: 'Sep 28', date: 'Sep 28 · Sample match', status: 'ready', score: [2, 1] }],
  upload: null, job: null,
  video: { t: 40, playing: false, overlays: { boxes: true, ids: true, trail: true, mini: true }, sel: null },
  real: {},   // matchId -> { stats, players, engine }: reports made by the model (engine.js toReport)
};

/* ---------- Model server (engine.js). Its address is set at deploy time in config.js; visitors never see it ---------- */
const E = window.TrivelaEngine;
const FIXED_ENGINE = E.normalizeBase((window.TRIVELA_CONFIG || {}).engineUrl);
const qEngine = new URLSearchParams(location.search).get('engine');
if (FIXED_ENGINE) E.setBase(FIXED_ENGINE);
// Without a deployed server, ?engine= can point at a local one for testing; never anywhere else, so a shared link can't send videos away
else if (qEngine && /^http:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(E.normalizeBase(qEngine) || '')) E.setBase(qEngine);
// A test engine behind a Cloudflare quick tunnel (docker compose --profile tunnel prints the link). Asks first, since a shared link could point at someone else's server
else if (qEngine && /^https:\/\/[a-z0-9-]+\.trycloudflare\.com$/i.test(E.normalizeBase(qEngine) || '')
  && confirm(`Send the videos you analyze to ${E.normalizeBase(qEngine)}?

Only continue if this is your own model server.`)) E.setBase(qEngine);
/* Leaving mid-analysis would strand the match as "busy": nothing else saves the results */
addEventListener('beforeunload', e => { if (S.job && S.job.real && S.job.running) { e.preventDefault(); e.returnValue = ''; } });
S.engine = { base: E.getBase(), ok: false, err: null, device: null, checking: false };
const VIDEO_KEY = 'trivela.engineVideo';   // { matchId, base }: the engine keeps only its latest video, so only that match can play it
const latestVideo = () => { try { return JSON.parse(localStorage.getItem(VIDEO_KEY)); } catch (_) { return null; } };
const rememberVideo = (matchId, base) => { try { localStorage.setItem(VIDEO_KEY, JSON.stringify({ matchId, base })); } catch (_) { /* storage blocked */ } };
async function connectEngine(raw, quiet = false) {
  const base = E.normalizeBase(raw);
  if (!base) { S.engine = { ...S.engine, ok: false, err: 'That isn\'t a web address. Paste the model server\'s address, like https://engine.example.com.' }; if (S.route === 'upload') render(); return; }
  S.engine = { base, ok: false, err: null, device: null, checking: true };
  if (S.route === 'upload') render();
  try {
    const info = await E.info(base);
    E.setBase(base);
    S.engine = { base, ok: true, err: null, device: info && info.device, checking: false };
    if (!quiet) toast(`Connected to the model server${info && info.gpu ? ' (GPU)' : ''}`);
  } catch (err) {
    console.error(err);
    // An https page can't call a plain-http server other than localhost: the browser blocks it before it leaves
    const mixed = location.protocol === 'https:' && base.startsWith('http:') && !/^http:\/\/(localhost|127\.0\.0\.1)(:|$)/i.test(base);
    S.engine = { base, ok: false, err: mixed ? 'This page is served over https, so it can only reach an https model server (or one on localhost).'
      : quiet ? FIXED_ENGINE ? 'The model server isn\'t answering right now, so videos run the sample analysis. Try again in a few minutes.'
      : 'Couldn\'t reach the saved model server. Check it\'s running, then connect again.' : err.message, device: null, checking: false };
  }
  if (S.route === 'upload') render();
}
S.route = location.hash.startsWith('#report') ? 'match' : 'upload'; // deep links from the dashboard and landing page
const LINKED = (location.hash.match(/^#report\/([0-9a-f-]{36})$/i) || [])[1]; // #report/<id> opens a saved analysis

/* ---------- Saved analyses: on the engine (Docker volume) and a copy in this browser. The engine also keeps the annotated video ---------- */
const SAVED_KEY = 'trivela.analyses', MAX_SAVED = 30;
const loadSaved = () => { try { const v = JSON.parse(localStorage.getItem(SAVED_KEY)); return Array.isArray(v) ? v : []; } catch (_) { return []; } };
/* Newest first. When storage is full the oldest analyses are dropped until the new one fits */
function storeAnalysis(entry) {
  let list = [entry, ...loadSaved().filter(x => x.id !== entry.id)].slice(0, MAX_SAVED);
  while (list.length) {
    try { localStorage.setItem(SAVED_KEY, JSON.stringify(list)); return true; }
    catch (err) { if (list.length === 1) { console.error(err); break; } list = list.slice(0, -1); }
  }
  toast('The stats are ready but this browser could not store them. They stay open until you leave the page.');
  return false;
}
const dayOf = d => d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
const fromSaved = r => ({ id: r.id, home: r.home, away: r.away, hc: r.hc || null, ac: r.ac || null, day: dayOf(new Date(r.created_at)), date: `${dayOf(new Date(r.created_at))} · ${r.source_name || 'Upload'}`,
  status: r.status, score: null }); // the model doesn't detect goals yet
const $ = s => document.querySelector(s);
const view = $('#view');
const match = () => S.matches.find(m => m.id === S.matchId);
const css = n => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const teamColor = t => { const m = match(); return (t === 'home' ? m && m.hc : m && m.ac) || css(t === 'home' ? '--home' : '--away'); };
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const fmtT = s => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, '0')}`;
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

const ICON = {
  chev: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M6 9l6 6 6-6"/></svg>',
  back: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M15 18l-6-6 6-6"/></svg>',
  play: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><path d="M7 4.5v15l13-7.5z"/></svg>',
  pause: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor"><rect x="6" y="4.5" width="4" height="15" rx="1"/><rect x="14" y="4.5" width="4" height="15" rx="1"/></svg>',
  share: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15V3M7 8l5-5 5 5M5 13v6a2 2 0 002 2h10a2 2 0 002-2v-6"/></svg>',
  arrow: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"><path d="M9 6l6 6-6 6"/></svg>',
  lift: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M6 11l6-6 6 6"/></svg>',
  coach: '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="14.5" r="5.2" fill="none" stroke="currentColor" stroke-width="3.2"/><rect x="9" y="7.6" width="13" height="4.8" rx="1.3" fill="currentColor"/><circle cx="9" cy="14.5" r="1.7" fill="currentColor"/><circle cx="4.6" cy="5.6" r="1.9" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',   // the coach's whistle mark (coach.js MARK)
};

/* ---------- Pitch SVG ---------- */
function pitchLines() {
  return `<g fill="none" stroke="var(--pitch-line)" stroke-width=".45">
    <rect x=".5" y=".5" width="104" height="67" rx="1"/><line x1="52.5" y1=".5" x2="52.5" y2="67.5"/><circle cx="52.5" cy="34" r="9.15"/>
    <rect x=".5" y="13.85" width="16.5" height="40.3"/><rect x="88" y="13.85" width="16.5" height="40.3"/>
    <rect x=".5" y="24.85" width="5.5" height="18.3"/><rect x="99" y="24.85" width="5.5" height="18.3"/>
    <path d="M17 26.7a9.15 9.15 0 010 14.6M88 26.7a9.15 9.15 0 000 14.6"/></g>
    <circle cx="52.5" cy="34" r=".6" fill="var(--pitch-line)"/><circle cx="11" cy="34" r=".5" fill="var(--pitch-line)"/><circle cx="94" cy="34" r=".5" fill="var(--pitch-line)"/>`;
}
const pitch = (inner, label) => `<svg viewBox="-1 -1 107 70" role="img" aria-label="${esc(label)}"><rect x="-1" y="-1" width="107" height="70" rx="2" fill="var(--pitch)"/>${pitchLines()}${inner}</svg>`;

function heatLayer(side) {
  const sides = side === 'both' ? ['home', 'away'] : [side];
  return sides.map(t => HEAT[t].filter(c => c.v > .12).map(c =>
    `<rect x="${c.i * 5}" y="${(c.j * H / 14).toFixed(2)}" width="5" height="${(H / 14).toFixed(2)}" fill="${teamColor(t)}" fill-opacity="${(c.v * (side === 'both' ? .5 : .82)).toFixed(2)}"/>`).join('')).join('');
}
function shotLayer(side) {
  return SHOTS.filter(s => side === 'both' || s.team === side).map(s => {
    const c = teamColor(s.team);
    return s.goal ? `<g><circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="2.2" fill="${c}"/><circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="3.4" fill="none" stroke="${c}" stroke-width=".5"/></g>`
      : `<circle cx="${s.x.toFixed(1)}" cy="${s.y.toFixed(1)}" r="1.5" fill="${s.on ? c : 'none'}" stroke="${c}" stroke-width=".5"/>`;
  }).join('');
}
function avgLayer(side, withLinks) {
  const sides = side === 'both' ? ['home', 'away'] : [side];
  return sides.map(t => {
    const ps = byTeam(t), c = teamColor(t);
    let links = '';
    if (withLinks) ps.forEach((a, i) => ps.slice(i + 1).forEach(b => {
      const d = Math.hypot(a.bx - b.bx, a.by - b.by); if (d > 26) return;
      const w = Math.max(.3, 2.2 - d / 14);
      links += `<line x1="${a.bx}" y1="${a.by}" x2="${b.bx}" y2="${b.by}" stroke="${c}" stroke-opacity=".55" stroke-width="${w.toFixed(2)}" stroke-linecap="round"/>`;
    }));
    return links + ps.map(p => `<g><circle cx="${p.bx}" cy="${p.by}" r="2.6" fill="${c}"/><text x="${p.bx}" y="${p.by + .95}" text-anchor="middle" font-size="2.6" font-weight="700" fill="#fff" font-family="var(--font)">${p.jersey}</text></g>`).join('');
  }).join('');
}

/* ---------- Router ---------- */
let syncNav = () => {};
function setupNav() {
  const nav = $('#nav'), box = $('#navLinks'), hl = box.querySelector('.nav-hl'), items = [...box.querySelectorAll('a,button')];
  const current = box.querySelector('[aria-current]');
  const rest = () => (current && !current.hidden ? current : null);
  const to = (el, fresh) => {
    hl.classList.toggle('fresh', !!fresh); hl.classList.toggle('pressed', el === rest()); hl.style.opacity = 1; hl.style.width = el.offsetWidth + 'px'; hl.style.height = el.offsetHeight + 'px';
    hl.style.transform = `translate(${el.offsetLeft}px,${el.offsetTop}px)`;
  };
  items.forEach(el => el.addEventListener('pointerenter', () => to(el)));
  box.addEventListener('pointerleave', () => { const r = rest(); if (r) to(r); else hl.style.opacity = 0; });
  syncNav = () => { const r = rest(); if (r) to(r, true); else hl.style.opacity = 0; };
  window.addEventListener('resize', syncNav);
  if (document.fonts) document.fonts.ready.then(syncNav);
  syncNav();
  let lastY = scrollY;
  window.addEventListener('scroll', () => {
    const y = scrollY; if (Math.abs(y - lastY) < 6) return;
    nav.classList.toggle('away', y > lastY && y > 140); lastY = y;
  }, { passive: true });
}
let lastPt = { x: innerWidth / 2, y: innerHeight / 2 };
document.addEventListener('pointerdown', e => { lastPt = { x: e.clientX, y: e.clientY }; }, true);
document.addEventListener('keydown', () => { lastPt = { x: innerWidth / 2, y: innerHeight * .4 }; }, true);
function go(route, opts = {}) {
  Object.assign(S, { route }, opts); // state moves now; only the DOM swap waits for the transition
  const swap = () => { render(); window.scrollTo({ top: 0, behavior: 'auto' }); };
  if (!document.startViewTransition || reduced || document.hidden) { swap(); return; }
  const { x, y } = lastPt, vt = document.startViewTransition(swap);
  vt.ready.then(() => {
    const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
    document.documentElement.animate({ clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
      { duration: 700, easing: 'cubic-bezier(.23,1,.32,1)', pseudoElement: '::view-transition-new(root)' });
  }).catch(() => {});
}
let lastRoute = '';
function render() {
  stopVideo();
  // Re-rendering the same route (theme change, finished job) skips the entrance choreography
  const settled = lastRoute === S.route; lastRoute = S.route;
  document.body.dataset.route = S.route; syncNav();
  const m = match();
  view.innerHTML = `<div class="view${settled ? ' settled' : ''}">${({ upload: vUpload, processing: vProcessing, match: vMatch })[S.route]()}</div>`;
  bind();
  if (S.route === 'processing') updateProcessing();
}

/* ---------- Screens ---------- */
/* Upload a file: the upload screen's drop zone or its file picker */
function acceptFile(f) {
  if (!/^video\//.test(f.type) && !/\.(mp4|mov)$/i.test(f.name)) { toast('That file isn\'t a video. Use an MP4 or MOV.'); return; }
  takeFile(f.name, f.size, f);
}
const upMeta = u => `${u.size} · ${u.err ? 'Upload failed' : u.p < 1 ? `Uploading ${Math.round(u.p * 100)}%${u.real ? ' to the model' : ''}` : u.real ? 'On the model server' : 'Ready · sample analysis (no model server connected)'}`;
function paintUpload(u) {
  if (S.upload !== u || S.route !== 'upload') return;
  const ring = $('#ring'), meta = $('#upmeta'), b = $('#toConfirm');
  if (ring) ring.setAttribute('stroke-dashoffset', 94.2 * (1 - u.p));
  if (meta) meta.textContent = upMeta(u);
  if (b && u.p >= 1 && !u.err && b.disabled) { b.disabled = false; b.focus(); }
}
/* With a file and a connected model server the video really uploads; otherwise (sample clip, no server) the upload is simulated */
let uploadCtl = null;
function takeFile(name, bytes, file) {
  if (uploadCtl) { uploadCtl.abort(); uploadCtl = null; }
  const real = !!(file && S.engine.ok);
  const u = S.upload = { name, size: (bytes / 1e9 >= 1 ? (bytes / 1e9).toFixed(2) + ' GB' : Math.max(1, Math.round(bytes / 1e6)) + ' MB'), p: 0, real };
  if (S.route === 'upload') render(); else go('upload');
  if (real) {
    const ctl = uploadCtl = new AbortController();
    E.upload(S.engine.base, file, p => { u.p = p; paintUpload(u); }, ctl.signal)
      .catch(err => {
        if (err.name === 'AbortError') return;
        console.error(err); u.err = err.message; paintUpload(u);
        toast(err.status === 409 ? 'The model server is busy with another analysis. Try again when it finishes.' : `Upload failed: ${esc(err.message)}`);
      });
    return;
  }
  const t0 = performance.now();
  (function up() {
    if (S.upload !== u || S.route !== 'upload') return;
    u.p = Math.min(1, (performance.now() - t0) / 1800);
    paintUpload(u);
    if (u.p < 1) requestAnimationFrame(up);
  })();
}
function vUpload() {
  const u = S.upload;
  return `<section class="hero compact" aria-labelledby="upH"><div class="lib-wrap">
    <a class="mint-link back rise" href="dashboard" style="--i:0">${ICON.back} Dashboard</a>
    <div class="hero-grid"><div>
      <span class="eyebrow rise" style="--i:1">Step 1 of 3 · Upload</span>
      <h1 id="upH" class="rise" style="--i:2">New analysis</h1>
      <p class="lede rise" style="--i:3">Add a full match or a highlights clip. Processing runs on a GPU.</p>
      <div class="stepper rise" style="--i:4" aria-hidden="true"><b class="on"><i>1</i><span>Upload</span></b><hr><b><i>2</i><span>Confirm teams</span></b><hr><b><i>3</i><span>Results</span></b></div>
    </div></div></div></section>
  <section class="lib"><div class="lib-wrap">
    <div class="updrop rise" id="drop" style="--i:2">
      <div class="updrop-art" aria-hidden="true"><svg viewBox="-2 -2 109 72"><rect class="ants" x="0" y="0" width="105" height="68" rx="5"/>
        <g class="ln"><line x1="52.5" y1="0" x2="52.5" y2="68"/><circle cx="52.5" cy="34" r="9.15"/><rect x="0" y="13.85" width="16.5" height="40.3"/><rect x="88.5" y="13.85" width="16.5" height="40.3"/></g>
        <g class="arrow"><circle cx="52.5" cy="34" r="6" fill="#015C55"/><path d="M52.5 37.5V30.5M49.5 33.5l3-3 3 3" fill="none" stroke="#fff" stroke-width="1.1" stroke-linecap="round" stroke-linejoin="round"/></g></svg></div>
      <h2>Drop a match video here</h2>
      <p>MP4 or MOV · up to 2 GB · 720p or higher</p>
      <div class="updrop-actions">
        <label class="key on-light sm" for="file" tabindex="0" role="button" id="pick">Choose file <span class="key-ico">${ICON.lift}</span></label>
        <input id="file" type="file" accept="video/*" hidden>
        <button class="key soft sm" id="sample">Use sample clip</button>
      </div>
    </div>
    ${u ? `<div class="frow">
      <svg class="ring" viewBox="0 0 36 36"><circle cx="18" cy="18" r="15" fill="none" stroke="rgba(1,92,85,.14)" stroke-width="4"/>
      <circle id="ring" cx="18" cy="18" r="15" fill="none" stroke="#015C55" stroke-width="4" stroke-linecap="round" stroke-dasharray="94.2" stroke-dashoffset="${94.2 * (1 - u.p)}" transform="rotate(-90 18 18)"/></svg>
      <div style="min-width:0;flex:1"><div class="name">${esc(u.name)}</div><div class="muted" id="upmeta">${upMeta(u)}</div></div>
      <button class="key on-light sm" id="toConfirm" ${u.p < 1 || u.err ? 'disabled' : ''}>Continue <span class="key-ico">${ICON.arrow}</span></button></div>` : ''}
    <div class="tip-row">
      <div><b>Wide angle</b>The main broadcast camera works best.</div>
      <div><b>Whole pitch</b>Avoid long replays and close-ups.</div>
      <div><b>Steady footage</b>Phone clips from the stand are fine.</div>
    </div></div></section>`;
}

const STEPS = ['Detecting players', 'Tracking the ball', 'Assigning teams', 'Detecting passes and shots', 'Computing stats'];
function vProcessing() {
  const j = S.job;
  return `<section class="hero compact" aria-labelledby="prH"><div class="lib-wrap">
    <a class="mint-link back rise" href="dashboard" style="--i:0">${ICON.back} Dashboard</a>
    <div class="hero-grid"><div>
      <span class="eyebrow rise" style="--i:1">Step 3 of 3 · Analyzing</span>
      <h1 id="prH" class="rise" style="--i:2">${esc(j.home)} <span style="color:var(--mint)">vs</span> ${esc(j.away)}</h1>
      <p class="lede rise" style="--i:3">${j.real ? 'The model is working on your video. Keep this tab open: the stats are saved here when it finishes.' : 'You can leave this page. We\'ll let you know when the stats are ready.'}</p>
    </div>
    <div class="run-card"><small>Overall progress</small>
      <p class="run-pct num"><b id="pPct" style="font:inherit">0</b><span>%</span></p>
      <span class="jbar"><i id="pBar"></i></span>
      <div class="run-meta"><span class="num" id="pEta"></span></div></div>
    </div></div></section>
  <section class="lib"><div class="lib-wrap">
    <div class="card rise" style="--i:1;margin-bottom:18px"><span class="label">While you wait</span>
      <p id="pTip" style="margin:8px 0 0;transition:opacity .25s ease">${esc(TIPS[tipAt])}</p></div>
    <ol class="psteps rise" style="--i:1">${STEPS.map((t, i) => `<li class="ps wait" data-ps="${i}"><span class="ps-dot" aria-hidden="true"></span>
      <div><span class="ps-label">${t}</span><span class="ps-bar"><i></i></span></div><span class="ps-pct num"></span></li>`).join('')}</ol>
    <div class="proc-actions">${j.real ? '<button class="key soft sm" id="cancelJob">Cancel analysis</button>' : '<a class="key soft sm" href="dashboard">Back to dashboard</a>'}</div>
  </div></section>`;
}
const engineEta = j => j.eta ? `About ${Math.max(1, Math.round(j.eta / 60))} min left` : j.p >= 1 ? 'Finishing up' : 'Warming up';
/* Shown while a match is analysed, instead of the engine's technical status. Half useful, half nonsense */
const TIPS = [
  'Scan before you receive: the best midfielders check their shoulder several times before the ball arrives.',
  'Open your body to the pitch when you receive. Half-turned means one touch fewer.',
  'Pass to the foot away from the defender.',
  'Defending a 1v1? Show the attacker onto his weaker foot and stay on your toes.',
  'Most goals come from inside the box. Shooting from 35 metres is a hobby, not a plan.',
  'Third-man runs break lines: pass, then run past the player who received it.',
  'Width stretches a defence. Hug the touchline when your team has the ball.',
  'After losing the ball, the next five seconds are the best time to win it back.',
  'Keep your shape off the ball: distances between players matter more than speed.',
  'A goalkeeper who speaks early saves more goals than one who dives late.',
  'Fun fact: the model has watched more football today than most pundits.',
  'Counting passes. Some of them were even on purpose.',
  'The referee is being politely ignored. He is used to it.',
  'Teaching a computer what offside is. It asked for VAR.',
  'Measuring sprints. Your centre-back is not as fast as he thinks.',
  'Asking the ball where it has been. It refuses to comment.',
  'Calculating xG. Spoiler: that shot from the corner flag was not a good idea.',
  'Bribing the GPU with extra fans to go faster.',
];
let tipAt = Math.floor(Math.random() * TIPS.length);
setInterval(() => {
  const el = $('#pTip'); if (!el) return;
  tipAt = (tipAt + 1) % TIPS.length;
  el.style.opacity = 0;
  setTimeout(() => { el.textContent = TIPS[tipAt]; el.style.opacity = 1; }, 250);
}, 7000);
function updateProcessing() {
  const j = S.job, p = j ? j.p : 0;
  const set = (sel, fn) => { const el = $(sel); if (el) fn(el); };
  set('#pPct', el => { el.textContent = Math.round(p * 100); });
  set('#pBar', el => el.style.setProperty('--p', p));
  set('#pEta', el => { el.textContent = j && j.real ? engineEta(j) : p >= 1 ? 'Finishing up' : `About ${Math.max(1, Math.ceil((1 - p) * 9))} min left`; });
  document.querySelectorAll('[data-ps]').forEach(li => {
    const i = +li.dataset.ps, sp = Math.max(0, Math.min(1, p * STEPS.length - i)), cls = sp >= 1 ? 'done' : sp > 0 ? 'active' : 'wait';
    li.className = `ps ${cls}`;
    li.querySelector('.ps-dot').textContent = cls === 'done' ? '✓' : '';
    li.querySelector('.ps-bar i').style.setProperty('--p', sp);
    li.querySelector('.ps-pct').textContent = cls === 'done' ? 'Done' : cls === 'active' ? Math.round(sp * 100) + '%' : '';
  });
}

function tabs(list, cur, name) {
  return `<div class="seg" role="tablist" data-seg="${name}"><span class="pill"></span>${list.map(([k, l]) => `<button role="tab" data-${name}="${k}" aria-selected="${k === cur}">${l}</button>`).join('')}</div>`;
}

function vMatch() {
  const m = match();
  const crest = (t, c) => `<span class="crest" style="background:${c}">${esc(t.split(' ').map(w => w[0]).join('').slice(0, 2))}</span>`;
  const rep = S.real[m.id];   // a model-made report: the team-stats pitch views need the engine's pitch maps
  if (rep && S.tab === 'team' && !mapsOf(rep)) S.tab = 'overview';
  const body = rep ? ({ overview: vStats, video: vVideoReal, team: vTeamReal, players: vPlayers })[S.tab](rep, m)
    : ({ overview: vStats, video: vVideo, team: vTeam, players: vPlayers })[S.tab]();
  const tabList = rep && !mapsOf(rep) ? [['overview', 'Overview'], ['video', 'Video'], ['players', 'Players']] : [['overview', 'Overview'], ['video', 'Video'], ['team', 'Team stats'], ['players', 'Players']];
  const length = rep ? `${fmtT((rep.engine && rep.engine.duration_s) || 0)} analyzed by the model` : 'Full match · 90:00';
  return `<section class="hero compact slim"><div class="lib-wrap"><div class="match-head">
      <div class="score">${crest(m.home, teamColor('home'))}<span class="team">${esc(m.home)}</span>
        <span class="big num">${m.score ? `${m.score[0]} – ${m.score[1]}` : 'vs'}</span><span class="team">${esc(m.away)}</span>${crest(m.away, teamColor('away'))}</div>
      <span style="display:flex;gap:8px;flex-wrap:wrap">${S.engine.base && (rep || m.id === 'm1') ? `<button class="coach-cta" data-coach-open aria-haspopup="dialog" aria-keyshortcuts="/"><span class="coach-cta-ic">${ICON.coach}</span>Ask AI coach<kbd>/</kbd></button>` : ''}<button class="key soft sm" id="export">${ICON.share} Export</button></span></div>
    <div class="toolbar">${tabs(tabList, S.tab, 'tab')}
      <span class="muted">${esc(m.date)} · ${length}</span></div></div></section>
  <section class="lib"><div class="lib-wrap">${body}</div></section>`;
}

function splitBar(h, a) {
  const t = h + a;
  return `<div class="split"><i style="width:${t ? h / t * 100 : 50}%;background:${teamColor('home')}"></i><i style="flex:1;background:${teamColor('away')}"></i></div>`;
}
/* ---------- Pitch maps from the model (engine.maps, already turned so home attacks left to right) ---------- */
const mapsOf = rep => (rep && rep.engine && rep.engine.maps) || null;
/* A heat grid of 0..99 values, row-major, nx by ny cells over the 105 x 68 pitch */
function gridCells(grid, [nx, ny], color, strength) {
  if (!Array.isArray(grid) || grid.length !== nx * ny) return '';
  const w = W / nx, h = H / ny;
  return grid.map((v, i) => v > 12 ? `<rect x="${((i % nx) * w).toFixed(2)}" y="${(Math.floor(i / nx) * h).toFixed(2)}" width="${w.toFixed(2)}" height="${h.toFixed(2)}" fill="${color}" fill-opacity="${(v / 99 * strength).toFixed(2)}"/>` : '').join('');
}
const sidesOf = side => (side === 'both' ? ['home', 'away'] : [side]);
function realHeatLayer(maps, side, mode = 'all') {
  return sidesOf(side).map(t => gridCells((maps.heat[t] || {})[mode], maps.size, teamColor(t), side === 'both' ? .5 : .82)).join('');
}
function realShotLayer(maps, side) {
  return maps.shots.filter(s => side === 'both' || s.team === side).map(s => {
    const c = teamColor(s.team), r = (1.1 + Math.sqrt(Math.max(0, s.xg)) * 2.6).toFixed(2);   // bigger circle = better chance
    return `<circle cx="${s.x}" cy="${s.y}" r="${r}" fill="${s.on ? c : 'none'}" fill-opacity=".85" stroke="${c}" stroke-width=".5"><title>${s.on ? 'On target' : 'Off target'} · xG ${s.xg}${s.kmh ? ` · ${s.kmh} km/h` : ''}</title></circle>`;
  }).join('');
}
function realPassLayer(maps, side) {
  return maps.passes.filter(p => side === 'both' || p[4] === side).map(([x0, y0, x1, y1, t]) => {
    const c = teamColor(t);
    return `<line x1="${x0}" y1="${y0}" x2="${x1}" y2="${y1}" stroke="${c}" stroke-opacity=".4" stroke-width=".45" stroke-linecap="round"/><circle cx="${x1}" cy="${y1}" r=".7" fill="${c}" fill-opacity=".75"/>`;
  }).join('');
}
const mapNote = maps => `Positions are estimated from broadcast video${maps.anchored_pct != null ? ` (pitch anchored in ${Math.round(maps.anchored_pct)}% of frames)` : ''}, so treat the maps as approximate.`;

/* Overview: the match-centre stats view (stats-view.js), fed from a model-made report, or from the sample data */
const SV = window.TrivelaStatsView;
const SAMPLE_SAVES = { home: 2, away: 4 };   // the other side's shots on target minus its goals
/* Sample heat per player, made once (heatGrid adds random noise): whole match, on the ball (pushed up the pitch, tighter), off it */
const SAMPLE_HEAT = {};
const flat = g => g.map(c => Math.round(c.v * 99));
const sampleHeat = p => SAMPLE_HEAT[p.id] || (SAMPLE_HEAT[p.id] = {
  all: flat(heatGrid([p], 8)), on: flat(heatGrid([{ ...p, bx: p.bx + (p.team === 'home' ? 9 : -9) }], 5.5)), off: flat(heatGrid([p], 9)) });
const sampleRow = p => ({ id: p.id, team: p.team, badge: p.jersey, name: `${p.pos} #${p.jersey}`, heat: sampleHeat(p),
  v: { distance: +p.distance.toFixed(1), top: +p.top.toFixed(1), sprints: p.sprints, passes: p.passes, acc: p.acc, touches: p.touches } });
const gridsOf = x => ({ all: x.heat, on: x.heat_on, off: x.heat_off });
/* A model-made keeper row (engine.keepers) -> a stats-view row. Older reports have none */
const keeperRow = ({ player_key, team, distance, top_speed, accuracy, jersey, pos, intensity, heat, heat_on, heat_off, ...rest }) =>
  ({ id: player_key, team, badge: 'GK', name: jersey ? 'Goalkeeper' : `Goalkeeper · Track ${String(player_key).slice(1)}`, heat: gridsOf({ heat, heat_on, heat_off }),
    v: { distance, top: top_speed, acc: accuracy, ...rest } });
function svModel(rep) {
  const m = match(), color = { home: teamColor('home'), away: teamColor('away') };
  if (!rep) {
    const defs = STATS.filter(s => s.h != null);
    const toGoal = s => Math.hypot((s.team === 'home' ? W : 0) - s.x, H / 2 - s.y);
    return { home: m.home, away: m.away, color, defs, stats: Object.fromEntries(defs.map(s => [s.k, [s.h, s.a]])),
      shots: SHOTS.map(s => ({ ...s, xg: +Math.min(.75, .9 * Math.exp(-toGoal(s) / 8)).toFixed(2) })),
      heat: { home: HEAT.home.map(c => c.v * 99), away: HEAT.away.map(c => c.v * 99) }, heatSize: [21, 14], playerHeatSize: [21, 14],
      players: players.filter(p => p.pos !== 'GK').map(sampleRow),
      keepers: players.filter(p => p.pos === 'GK').map(p => { const r = sampleRow(p); return { ...r, badge: 'GK', name: `Goalkeeper #${p.jersey}`, v: { ...r.v, saves: SAMPLE_SAVES[p.team] } }; }),
      playerNote: 'Sample data. Players are identified by shirt number and position.', facts: [], note: '' };
  }
  const e = rep.engine || {}, ball = e.ball || {}, maps = mapsOf(rep);
  const heatOf = t => maps && (maps.heat[t].on || maps.heat[t].all);
  return { home: m.home, away: m.away, color, defs: E.STAT_DEFS, stats: rep.stats,
    shots: maps ? maps.shots : null, heat: maps ? { home: heatOf('home'), away: heatOf('away') } : null, heatSize: maps && maps.size,
    players: realPlayers(rep).map(p => { const { heat, heat_on, heat_off, ...x } = p.extra || {}; return { id: p.id, team: p.team, badge: p.jersey, name: `${p.pos} ${p.jersey}`,
      heat: gridsOf({ heat, heat_on, heat_off }), v: { distance: p.distance, top: p.top, sprints: p.sprints, passes: p.passes, acc: p.acc, touches: p.touches, won: p.won, ...x } }; }),
    keepers: (e.keepers || []).map(keeperRow), playerHeatSize: maps && maps.player_size,
    cardNote: 'Numbers match the ones over the players in the video. Measured while the player was on screen; after a camera cut one player can come back under a new number.',
    playerNote: 'Players are listed by tracking id because shirt numbers aren\'t read yet, so one player can appear under two ids after leaving the frame. Goalkeepers are under Goalkeeping; referees aren\'t listed.',
    facts: [['Clip analyzed', fmtT(e.duration_s || 0)], ['Wide-angle footage', `${e.wide_pct ?? 0}%`], ['Ball visible', `${ball.visible_pct ?? 0}%`],
      ['Fastest ball', `${ball.max_kmh ?? 0} km/h`], ['Player tracks', e.tracks ?? 0], ['ID swaps fixed', e.swaps_fixed ?? 0]],
    note: maps
      ? 'Goals and shirt numbers aren\'t read yet. Distances, speeds and pitch positions are estimated from the video, using player height as a ruler, so treat them as approximate.'
      : 'This analysis has no pitch mapping, so shots, tackles and the pitch maps are missing. Distances and speeds are estimated using player height as a ruler.' };
}
function vStats(rep) {
  if (!S.sv || S.sv.id !== S.matchId) S.sv = { id: S.matchId, shot: 0, ptab: 'top', psort: null };
  return SV.render(svModel(rep), S.sv, pitch);
}
/* Team stats of a model-made report: heatmaps (all, on and off the ball), pass map, shot map */
function vTeamReal(rep) {
  const maps = mapsOf(rep), side = S.teamSide;
  const v = ['heat', 'passes', 'shots'].includes(S.teamView) ? S.teamView : 'heat', mode = S.heatMode || 'all';
  const inner = v === 'heat' ? realHeatLayer(maps, side, mode) : v === 'shots' ? realShotLayer(maps, side) : realPassLayer(maps, side);
  const count = v === 'shots' ? maps.shots.filter(s => side === 'both' || s.team === side).length : v === 'passes' ? maps.passes.filter(p => side === 'both' || p[4] === side).length : null;
  const legends = {
    heat: 'Darker cells mean more time spent in that zone. Home attacks left to right.',
    shots: `<span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--fg-2)"/></svg> On target</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="none" stroke="var(--fg-2)"/></svg> Off target</span><span>Bigger circle = higher xG</span>`,
    passes: 'Each line is a completed pass, ending at the dot. Home attacks left to right.',
  };
  const empty = count === 0 ? `<span>No ${v === 'shots' ? 'shots' : 'passes'} were mapped for this selection.</span>` : '';
  return `<div class="toolbar">${tabs([['heat', 'Heatmap'], ['passes', 'Pass map'], ['shots', 'Shot map']], v, 'teamview')}
      ${tabs([['home', esc(match().home)], ['both', 'Both'], ['away', esc(match().away)]], side, 'side')}</div>
    ${v === 'heat' ? `<div class="toolbar" style="margin-top:-10px">${tabs([['all', 'Whole match'], ['on', 'On the ball'], ['off', 'Off the ball']], mode, 'heatmode')}</div>` : ''}
    <div class="pitch-big">${pitch(inner, v)}<div class="pitch-legend">${legends[v]}${empty}<span>${esc(mapNote(maps))}</span></div></div>`;
}
/* The annotated video lives on the model server: each saved analysis keeps its own copy (the newest 30).
   Analyses saved before that only have the server's latest video, for the match that made it */
const NO_VIDEO = '<h3 style="margin-bottom:6px">Video not available</h3><p class="muted" style="margin:0">The model server no longer has the annotated video for this analysis. Analyze the match again to watch it.</p>';
function vVideoReal(rep, m) {
  const e = rep.engine || {}, last = latestVideo();
  if (!e.base) return `<div class="card">${NO_VIDEO}</div>`;
  const fallback = last && last.matchId === m.id && last.base === e.base ? E.videoUrl(e.base) : '';
  return `<div class="card" id="videoCard"><video class="engine-video" id="engineVideo" controls playsinline preload="auto" src="${esc(E.analysisVideoUrl(e.base, m.id))}" data-fallback="${esc(fallback)}"></video>
    <p class="muted" style="margin:12px 0 0">Annotated by the model: rings show each player's team, the gold ring and arrow mark the ball carrier, and the bar at the bottom is live possession.</p></div>`;
}
/* An analysis without its own copy: try the server's latest video, else say it's gone */
function bindVideo() {
  const v = $('#engineVideo'); if (!v) return;
  v.onerror = () => {
    const fb = v.dataset.fallback;
    if (fb && v.getAttribute('src') !== fb) { v.dataset.fallback = ''; v.src = fb; return; }
    const c = $('#videoCard'); if (c) c.innerHTML = NO_VIDEO;
  };
}

function vTeam() {
  const v = S.teamView, side = S.teamSide;
  const inner = v === 'heat' ? heatLayer(side) : v === 'shots' ? shotLayer(side) : v === 'passes' ? avgLayer(side === 'both' ? 'home' : side, true) : avgLayer(side, false);
  const legends = {
    heat: 'Darker cells mean more time spent in that zone.',
    shots: `<span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="var(--fg-2)"/></svg> On target</span><span><svg width="12" height="12"><circle cx="6" cy="6" r="4.5" fill="none" stroke="var(--fg-2)"/></svg> Off target</span><span><svg width="14" height="14"><circle cx="7" cy="7" r="3.5" fill="var(--fg-2)"/><circle cx="7" cy="7" r="6" fill="none" stroke="var(--fg-2)"/></svg> Goal</span>`,
    passes: 'Thicker lines mean more passes between two players. Showing one team at a time.',
    avg: 'Each dot is a player\'s average position. Home attacks left to right.',
  };
  return `<div class="toolbar">${tabs([['heat', 'Heatmap'], ['passes', 'Pass network'], ['shots', 'Shot map'], ['avg', 'Average positions']], v, 'teamview')}
      ${tabs([['home', esc(match().home)], ['both', 'Both'], ['away', esc(match().away)]], side, 'side')}</div>
    <div class="pitch-big">${pitch(inner, v)}<div class="pitch-legend">${legends[v]}</div></div>`;
}

function spark(vals, color) {
  const pts = vals.map((v, i) => `${(i / (vals.length - 1) * 70).toFixed(1)},${(20 - v * 17).toFixed(1)}`).join(' ');
  return `<svg class="spark" width="72" height="22" viewBox="-1 0 73 22"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/></svg>`;
}
/* player rows from the model -> the shape the players table uses. The number is the one drawn in the video (older reports: the track id) */
const realPlayers = rep => rep.players.map(r => {
  const x = ((rep.engine || {}).players || {})[r.player_key] || null;   // the analytics pass's numbers and heat grid, when the engine sent them
  return { id: r.player_key, team: r.team, jersey: r.jersey ?? +String(r.player_key).slice(1), pos: r.jersey ? 'Player' : 'Track', track: true,
    distance: r.distance ?? 0, top: r.top_speed ?? 0, sprints: r.sprints ?? 0, passes: r.passes ?? 0, acc: r.accuracy, touches: r.touches ?? 0, won: r.won,
    extra: x, shots: x ? x.shots ?? 0 : null, defActs: x ? (x.tackles ?? 0) + (x.interceptions ?? 0) : null };
});
const playerList = () => { const rep = S.real[S.matchId]; return rep ? realPlayers(rep) : players; };
function vPlayers(rep) {
  const analytics = !!rep && playerList().some(p => p.extra && 'shots' in p.extra);
  const cols = [['jersey', 'Player'], ['distance', 'Distance'], ['top', 'Top speed'], ['sprints', 'Sprints'], ['passes', 'Passes'], ['acc', 'Accuracy'], ['touches', 'Touches'],
    ...(analytics ? [['shots', 'Shots'], ['defActs', 'Tackles + int.']] : [])];
  const list = [...playerList()].sort((a, b) => ((a[S.sort.k] ?? -1) - (b[S.sort.k] ?? -1)) * S.sort.dir);
  const m = match();
  if (rep && !list.length) return `<div class="card"><p class="muted" style="margin:0">The model didn't keep any player on screen long enough to list. Wide broadcast footage works best.</p></div>`;
  return `<div class="table-wrap"><table><thead><tr>${cols.map(([k, l], i) => `<th class="${i ? 'r' : ''}" ${S.sort.k === k ? `aria-sort="${S.sort.dir > 0 ? 'ascending' : 'descending'}"` : ''}><button data-sort="${k}">${l}${S.sort.k === k ? (S.sort.dir > 0 ? ' ↑' : ' ↓') : ''}</button></th>`).join('')}${rep ? '' : '<th>Intensity</th>'}</tr></thead>
    <tbody>${list.map(p => `<tr data-player="${esc(p.id)}" tabindex="0"><td><div class="pcell"><span class="jersey" style="background:${teamColor(p.team)}">${p.jersey}</span><span><b style="font-weight:600">${p.pos}${p.track ? ' ' + p.jersey : ''}</b> <span class="muted">${esc(p.team === 'home' ? m.home : m.away)}</span></span></div></td>
      <td class="r num">${p.distance.toFixed(p.track ? 2 : 1)} km</td><td class="r num">${p.top.toFixed(1)} km/h</td><td class="r num">${p.sprints}</td><td class="r num">${p.passes}</td><td class="r num">${p.acc == null ? '—' : p.acc + '%'}</td><td class="r num">${p.touches}</td>
      ${analytics ? `<td class="r num">${p.shots ?? '—'}</td><td class="r num">${p.defActs ?? '—'}</td>` : ''}
      ${rep ? '' : `<td>${spark(p.spark, teamColor(p.team))}</td>`}</tr>`).join('')}</tbody></table></div>
    <p class="muted" style="margin-top:12px">${rep ? 'From the model. Players are listed by tracking id because shirt numbers aren\'t read yet, so one player can appear under two ids after leaving the frame. Goalkeepers and referees aren\'t listed.'
      : 'Sample data. Players are identified by shirt number and position.'}</p>`;
}

function vVideo() {
  const v = S.video;
  const evs = events.filter(e => e.type !== 'pass');
  const colorFor = e => e.type === 'goal' ? 'var(--ball)' : e.type === 'shot' ? teamColor(e.team) : 'rgba(255,255,255,.75)';
  return `<div class="video-wrap">
    <div class="stage">
      <canvas id="cv" aria-label="Annotated match video simulation"></canvas>
      <div class="overlay-bar"><div class="glass toggles">${[['boxes', 'Boxes'], ['ids', 'IDs'], ['trail', 'Ball trail'], ['mini', 'Minimap']].map(([k, l]) => `<button data-ov="${k}" aria-pressed="${v.overlays[k]}">${l}</button>`).join('')}</div>
        <span class="glass sim-note">Simulated footage · sample clip</span></div>
      <div class="controls">
        <div class="ctrl-row"><button class="play" id="play" aria-label="Play">${ICON.play}</button>
          <div class="timeline" id="tl" role="slider" tabindex="0" aria-label="Seek" aria-valuemin="0" aria-valuemax="${CLIP}">
            <div class="tl-track"></div>
            ${events.filter(e => e.type === 'pass').map(e => `<i class="tl-tick" style="left:${e.t / CLIP * 100}%"></i>`).join('')}
            <div class="tl-fill" id="tlfill"></div>
            ${evs.map(e => `<i class="tl-ev" style="left:${e.t / CLIP * 100}%;background:${colorFor(e)}" title="${e.type} ${fmtT(e.t)}"></i>`).join('')}
            <div class="tl-head" id="tlhead"></div>
          </div><span class="time num" id="time"></span></div>
        <div class="legend"><span><i class="sw" style="background:var(--ball)"></i>Goal</span><span><i class="sw" style="background:${teamColor('home')}"></i>Shot · home</span><span><i class="sw" style="background:${teamColor('away')}"></i>Shot · away</span><span><i class="sw" style="background:rgba(255,255,255,.75)"></i>Interception</span><span><i class="sw" style="background:rgba(255,255,255,.3)"></i>Pass</span></div>
      </div>
    </div>
    <div class="side">
      <div class="card"><span class="label">At this moment</span><div class="event-now" id="evnow" style="margin-top:8px"></div>
        <div style="margin-top:12px;display:grid;gap:8px"><div class="cmp-top" style="grid-template-columns:44px 1fr 44px"><span class="l num" id="posH"></span><span class="c">Possession so far</span><span class="r num" id="posA"></span></div><div id="posBar"></div>
        <div class="cmp-top" style="grid-template-columns:44px 1fr 44px"><span class="l num" id="pasH"></span><span class="c">Passes so far</span><span class="r num" id="pasA"></span></div></div></div>
      <div class="card" id="selCard"></div>
      <div class="card kbd"><kbd>Space</kbd> play or pause · <kbd>←</kbd> <kbd>→</kbd> 5 s · <kbd>J</kbd> <kbd>K</kbd> previous or next event</div>
    </div></div>`;
}

/* ---------- Video engine ---------- */
let raf = 0, lastTs = 0, cv, ctx, cw = 0;
function stopVideo() { cancelAnimationFrame(raf); raf = 0; S.video.playing = false; ctx = null; cw = 0; }
function setupVideo() {
  cv = $('#cv'); if (!cv) return; ctx = cv.getContext('2d');
  const size = () => { if (!ctx) return; const r = cv.getBoundingClientRect(), d = devicePixelRatio || 1; cw = r.width; cv.width = r.width * d; cv.height = r.width * 68 / 105 * d; ctx.setTransform(d, 0, 0, d, 0, 0); draw(); };
  new ResizeObserver(size).observe(cv); size();
  $('#play').onclick = togglePlay;
  cv.addEventListener('pointerdown', e => {
    const r = cv.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * W, y = (e.clientY - r.top) / r.height * H;
    let best = null, bd = 4;
    players.forEach(p => { const q = posAt(p, S.video.t), d = Math.hypot(q.x - x, q.y - y); if (d < bd) { bd = d; best = p; } });
    S.video.sel = best ? best.id : null; draw();
  });
  const tl = $('#tl');
  const seekTo = e => { const r = tl.getBoundingClientRect(); S.video.t = Math.max(0, Math.min(CLIP, (e.clientX - r.left) / r.width * CLIP)); draw(); };
  tl.addEventListener('pointerdown', e => { tl.setPointerCapture(e.pointerId); seekTo(e); tl.onpointermove = seekTo; });
  tl.addEventListener('pointerup', () => { tl.onpointermove = null; });
  draw();
}
function togglePlay() {
  const v = S.video; v.playing = !v.playing;
  $('#play').innerHTML = v.playing ? ICON.pause : ICON.play; $('#play').setAttribute('aria-label', v.playing ? 'Pause' : 'Play');
  if (v.playing) { if (v.t >= CLIP - .1) v.t = 0; lastTs = 0; raf = requestAnimationFrame(tick); } else cancelAnimationFrame(raf);
}
function tick(ts) {
  if (!lastTs) lastTs = ts;
  S.video.t = Math.min(CLIP, S.video.t + (ts - lastTs) / 1000); lastTs = ts; draw();
  if (S.video.t >= CLIP) { togglePlay(); return; }
  raf = requestAnimationFrame(tick);
}
function draw() {
  if (!ctx || !cw) return;
  const v = S.video, t = v.t, k = cw / W, ch = H * k, o = v.overlays;
  // grass with mowing stripes
  for (let i = 0; i < 14; i++) { ctx.fillStyle = i % 2 ? '#2F6B3B' : '#346F40'; ctx.fillRect(i * cw / 14, 0, cw / 14 + 1, ch); }
  ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 1.4;
  const L = (x, y, w, h) => ctx.strokeRect(x * k, y * k, w * k, h * k);
  L(1, 1, 103, 66); L(1, 13.85, 16, 40.3); L(88, 13.85, 16, 40.3); L(1, 24.85, 5, 18.3); L(99, 24.85, 5, 18.3);
  ctx.beginPath(); ctx.moveTo(52.5 * k, k); ctx.lineTo(52.5 * k, 67 * k); ctx.stroke();
  ctx.beginPath(); ctx.arc(52.5 * k, 34 * k, 9.15 * k, 0, 7); ctx.stroke();
  const hc = teamColor('home'), ac = teamColor('away');
  players.forEach(p => {
    const q = posAt(p, t), x = q.x * k, y = q.y * k, r = Math.max(4, 1.1 * k), col = p.team === 'home' ? hc : ac, sel = v.sel === p.id;
    ctx.fillStyle = 'rgba(0,0,0,.25)'; ctx.beginPath(); ctx.ellipse(x, y + r * .9, r, r * .4, 0, 0, 7); ctx.fill();
    ctx.fillStyle = col; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.9)'; ctx.lineWidth = 1.2; ctx.stroke();
    if (o.boxes || sel) {
      ctx.strokeStyle = sel ? '#FFFFFF' : col; ctx.lineWidth = sel ? 2 : 1.2; ctx.setLineDash(sel ? [] : [3, 2]);
      ctx.strokeRect(x - r * 1.9, y - r * 2.6, r * 3.8, r * 4.2); ctx.setLineDash([]);
    }
    if (o.ids || sel) {
      const label = `#${p.jersey}`; ctx.font = `600 ${Math.max(9, k * 1.5)}px ${css('--font')}`;
      const tw = ctx.measureText(label).width + 8, ly = y - r * 2.6 - 14;
      ctx.fillStyle = sel ? '#FFFFFF' : col; ctx.beginPath(); ctx.roundRect(x - tw / 2, ly, tw, 13, 4); ctx.fill();
      ctx.fillStyle = sel ? '#000' : '#fff'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(label, x, ly + 7);
    }
  });
  if (o.trail) {
    for (let i = 24; i > 0; i--) { const b = ballAt(Math.max(0, t - i * .05)); ctx.fillStyle = `rgba(255,214,10,${(1 - i / 24) * .55})`; ctx.beginPath(); ctx.arc(b.x * k, b.y * k, Math.max(1.5, .35 * k) * (1 - i / 40), 0, 7); ctx.fill(); }
  }
  const b = ballAt(t);
  ctx.fillStyle = '#FFD60A'; ctx.beginPath(); ctx.arc(b.x * k, b.y * k, Math.max(3, .55 * k), 0, 7); ctx.fill(); ctx.strokeStyle = '#000'; ctx.lineWidth = 1; ctx.stroke();
  if (o.mini && cw > 420) {
    const mw = Math.min(170, cw * .22), mh = mw * 68 / 105, mx = cw - mw - 14, my = ch - mh - 14, s = mw / W;
    ctx.fillStyle = 'rgba(20,20,22,.7)'; ctx.beginPath(); ctx.roundRect(mx - 6, my - 6, mw + 12, mh + 12, 10); ctx.fill();
    ctx.strokeStyle = 'rgba(255,255,255,.35)'; ctx.lineWidth = 1; ctx.strokeRect(mx, my, mw, mh); ctx.beginPath(); ctx.moveTo(mx + mw / 2, my); ctx.lineTo(mx + mw / 2, my + mh); ctx.stroke();
    players.forEach(p => { const q = posAt(p, t); ctx.fillStyle = p.team === 'home' ? hc : ac; ctx.beginPath(); ctx.arc(mx + q.x * s, my + q.y * s, 2.4, 0, 7); ctx.fill(); });
    ctx.fillStyle = '#FFD60A'; ctx.beginPath(); ctx.arc(mx + b.x * s, my + b.y * s, 2.2, 0, 7); ctx.fill();
  }
  updatePanel();
}
function updatePanel() {
  const t = S.video.t, m = match();
  const pct = t / CLIP * 100;
  $('#tlfill').style.width = pct + '%'; $('#tlhead').style.left = pct + '%';
  $('#tl').setAttribute('aria-valuenow', Math.round(t)); $('#time').textContent = `${fmtT(t)} / ${fmtT(CLIP)}`;
  const past = events.filter(e => e.t <= t), last = past[past.length - 1];
  const name = e => esc(e.team === 'home' ? m.home : m.away);
  const desc = !last ? '<span class="muted">Kick-off</span>' : last.type === 'goal' ? `<b>Goal</b> by #${last.from.jersey} · ${name(last)}` :
    last.type === 'shot' ? `<b>Shot</b> by #${last.from.jersey} · ${name(last)}` : last.type === 'interception' ? `<b>Interception</b> by #${last.to.jersey} · ${name(last)}` :
    `<b>Pass</b> #${last.from.jersey} → #${last.to.jersey} · ${name(last)}`;
  $('#evnow').innerHTML = `${desc}<div class="muted num" style="font-size:12px">${last ? fmtT(last.t) : '0:00'}</div>`;
  const ph = possessionAt(t);
  $('#posH').textContent = ph + '%'; $('#posA').textContent = (100 - ph) + '%'; $('#posBar').innerHTML = splitBar(ph || 1, 100 - ph || 1);
  $('#pasH').textContent = past.filter(e => e.type === 'pass' && e.team === 'home').length;
  $('#pasA').textContent = past.filter(e => e.type === 'pass' && e.team === 'away').length;
  const p = players.find(x => x.id === S.video.sel);
  $('#selCard').innerHTML = p ? `<span class="label">Selected player</span><div class="pinfo" style="margin-top:10px"><span class="jersey" style="background:${teamColor(p.team)}">${p.jersey}</span>
    <div><b style="font-weight:600">${p.pos} · #${p.jersey}</b><div class="muted">${esc(p.team === 'home' ? m.home : m.away)}</div></div></div>
    <div class="stat-tiles" style="grid-template-columns:1fr 1fr;margin-bottom:8px"><div><b class="num">${p.distance.toFixed(1)}</b><span>km covered</span></div><div><b class="num">${p.top.toFixed(1)}</b><span>top km/h</span></div></div>
    <button class="btn btn-ghost" data-player="${p.id}" style="margin-left:-10px">Player details ${ICON.arrow}</button>`
    : `<span class="label">Selected player</span><p class="muted" style="margin:8px 0 0">Click a player on the video to follow them.</p>`;
}
function jumpEvent(dir) {
  const evs = events.filter(e => e.type !== 'pass'), t = S.video.t;
  const e = dir > 0 ? evs.find(x => x.t > t + .3) : [...evs].reverse().find(x => x.t < t - 1.5);
  if (e) { S.video.t = Math.max(0, e.t - 2); draw(); }
}

/* ---------- Sheet with drag-to-dismiss ---------- */
let onSheetClose = null;
function openSheet(html, label, onClose) {
  const sh = $('#sheet'), sc = $('#scrim');
  $('#sheet-body').innerHTML = html; sh.setAttribute('aria-label', label); onSheetClose = onClose || null;
  sh.hidden = false; sc.hidden = false; sh.style.transform = '';
  requestAnimationFrame(() => requestAnimationFrame(() => { sh.classList.add('show'); sc.classList.add('show'); }));
  setTimeout(() => { const f = sh.querySelector('input,button.btn'); if (f) f.focus({ preventScroll: true }); }, 60);
}
function closeSheet() {
  const sh = $('#sheet'), sc = $('#scrim');
  sh.classList.remove('show'); sc.classList.remove('show'); sh.style.transform = '';
  setTimeout(() => { if (!sh.classList.contains('show')) { sh.hidden = true; sc.hidden = true; } }, reduced ? 0 : 420);
  if (onSheetClose) { const f = onSheetClose; onSheetClose = null; f(); }
}
(function dragSheet() {
  const g = $('#grabber'), sh = $('#sheet'); let y0 = 0, dy = 0, hist = [];
  g.addEventListener('pointerdown', e => { g.setPointerCapture(e.pointerId); y0 = e.clientY; dy = 0; hist = [[e.clientY, e.timeStamp]]; sh.classList.add('dragging'); });
  g.addEventListener('pointermove', e => {
    if (!sh.classList.contains('dragging')) return;
    const raw = e.clientY - y0; dy = raw > 0 ? raw : -(Math.abs(raw) * 60) / (60 + Math.abs(raw)); // rubber-band upward
    sh.style.transform = `translate(-50%, ${dy}px)`; hist.push([e.clientY, e.timeStamp]); if (hist.length > 5) hist.shift();
  });
  g.addEventListener('pointerup', () => {
    sh.classList.remove('dragging');
    const [a, b] = [hist[0], hist[hist.length - 1]], vel = b && a && b[1] > a[1] ? (b[0] - a[0]) / (b[1] - a[1]) * 1000 : 0;
    const projected = dy + (vel / 1000) * .99 / (1 - .99);
    if (projected > sh.offsetHeight * .4) closeSheet(); else sh.style.transform = '';
  });
})();
$('#scrim').onclick = closeSheet;

function confirmSheet() {
  const real = !!(S.upload && S.upload.real);
  const homeSw = ['#035C55', '#1F3A93', '#B3261E'], awaySw = ['#E07A3F', '#6E6E73', '#7A4DC9'];
  const sw = (list, name) => `<div class="swatches" role="radiogroup" aria-label="${name} color">${list.map((c, i) => `<button class="swatch" role="radio" data-sw="${name}" data-c="${c}" aria-checked="${i === 0}" style="background:${c}" aria-label="${c}"></button>`).join('')}</div>`;
  openSheet(`<div class="sheet-head"><h2>Confirm teams</h2><button class="btn btn-ghost" id="cancelSheet">Cancel</button></div>
    <p class="muted" style="margin:-8px 0 20px">${real ? 'Name both teams and pick the colors the report uses for them.' : 'We found two shirt colors in the first frames. Check they match the right team.'}</p>
    <div class="team-row field"><div class="field" style="margin:0"><label for="homeName">Home team</label><input class="input" id="homeName" value="Trivela FC" maxlength="60" autocomplete="off"></div>${sw(homeSw, 'home')}</div>
    <div class="team-row field"><div class="field" style="margin:0"><label for="awayName">Away team</label><input class="input" id="awayName" value="Riverside Athletic" maxlength="60" autocomplete="off"></div>${sw(awaySw, 'away')}</div>
    <p class="hint">${real ? 'Tap a swatch to change a team\'s color.' : 'Colors detected automatically. Tap a swatch to change it.'}</p>
    ${real ? `<div class="field"><label id="kitLab">Show the model who is who</label>
      <div class="kitpick" id="kitpick"><img id="kitimg" alt="A frame of your video: click the players"><div id="kitmarks"></div></div>
      <div class="kitpick-bar"><span id="kitstep" aria-live="polite">Loading a frame…</span>
        <button class="btn btn-ghost" id="kitframe" type="button">Another frame</button><button class="btn btn-ghost" id="kitreset" type="button">Start over</button></div></div>
    <div class="field" id="kitFallback" hidden><label>Which team wears the darker shirts?</label>${tabs([['a', 'Home team'], ['b', 'Away team']], 'a', 'kit')}</div>
    <div class="field"><label>Detail</label>${tabs([['fast', 'Fast'], ['balanced', 'Balanced'], ['ultra', 'Best, slowest']], 'balanced', 'mode')}</div>`
    : `<div class="field"><label>Which half is this?</label>${tabs([['1', 'First half'], ['2', 'Second half'], ['full', 'Full match']], 'full', 'half')}</div>`}
    <button class="btn btn-primary btn-lg" id="start" style="width:100%">Start analysis</button>`, 'Confirm teams');
  const picked = { home: homeSw[0], away: awaySw[0] };
  document.querySelectorAll('[data-sw]').forEach(b => b.onclick = () => {
    document.querySelectorAll(`[data-sw="${b.dataset.sw}"]`).forEach(x => x.setAttribute('aria-checked', x === b)); picked[b.dataset.sw] = b.dataset.c;
  });
  ['half', 'kit', 'mode'].forEach(n => document.querySelectorAll(`[data-${n}]`).forEach(b => b.onclick = () => { document.querySelectorAll(`[data-${n}]`).forEach(x => x.setAttribute('aria-selected', x === b)); placePills(); }));
  const chosen = n => { const b = document.querySelector(`[data-${n}][aria-selected="true"]`); return b && b.dataset[n]; };
  $('#cancelSheet').onclick = closeSheet;
  setTimeout(placePills, 80);
  const kits = real ? kitPicker(picked) : null;
  $('#start').onclick = () => {
    const home = $('#homeName').value.trim(), away = $('#awayName').value.trim();
    if (!home || !away) { ($('#homeName').value.trim() ? $('#awayName') : $('#homeName')).focus(); toast('Add a name for both teams.'); return; }
    if (kits && !kits.failed() && !kits.done()) { toast('Click one home player and one away player on the frame first.'); return; }
    const engineOpts = real ? { homeKit: chosen('kit') || 'a', mode: chosen('mode') || 'balanced', kits: kits && !kits.failed() ? kits.clicks() : null } : null;
    closeSheet(); startJob(home, away, picked.home, picked.away, engineOpts);
  };
}

/* Team picker: a frame of the upload; the user clicks one home player, one away player and (optionally) the referee.
   The engine learns each kit from those shirts, so similar or striped kits and bright referee shirts are told apart */
const KIT_STEPS = [['home', 'Click a home team player'], ['away', 'Click an away team player'], ['ref', 'Click the referee (or skip: Start analysis)']];
function kitPicker(colors) {
  let clicks = [], frame = null, failed = false, n = 0;
  const step = $('#kitstep'), img = $('#kitimg'), marks = $('#kitmarks');
  const paint = () => {
    const s = KIT_STEPS[clicks.length];
    step.textContent = s ? s[1] : 'Done. Start the analysis.';
    marks.innerHTML = clicks.map(c => `<i class="kitmark" style="left:${c.x * 100}%;top:${c.y * 100}%;--c:${c.role === 'home' ? colors.home : c.role === 'away' ? colors.away : '#F2C200'}">${c.role === 'ref' ? 'R' : c.role === 'home' ? 'H' : 'A'}</i>`).join('');
  };
  const load = async t => {
    step.textContent = 'Loading a frame…';
    try {
      const f = await E.uploadFrame(S.engine.base, t);
      if (frame) URL.revokeObjectURL(frame.url);
      frame = f; img.src = f.url; paint();
    } catch (err) {
      console.error(err); failed = true;
      $('#kitpick').hidden = true; $('.kitpick-bar').hidden = true; $('#kitLab').hidden = true; $('#kitFallback').hidden = false; placePills();
    }
  };
  img.onclick = e => {
    if (!frame || clicks.length >= KIT_STEPS.length) return;
    const r = img.getBoundingClientRect();
    clicks.push({ t: frame.t, x: (e.clientX - r.left) / r.width, y: (e.clientY - r.top) / r.height, role: KIT_STEPS[clicks.length][0] });
    paint();
  };
  $('#kitframe').onclick = () => { if (frame && frame.duration) load(((frame.duration / 3) + frame.duration * 0.137 * ++n) % frame.duration); };
  $('#kitreset').onclick = () => { clicks = []; paint(); };
  load(-1);
  return { clicks: () => clicks.map(({ t, x, y, role }) => ({ t, x: +x.toFixed(4), y: +y.toFixed(4), role })),
    done: () => clicks.length >= 2, failed: () => failed };
}

function startJob(home, away, hc, ac, engineOpts) {
  const id = crypto.randomUUID(), source = (S.upload ? S.upload.name : 'Upload').slice(0, 200), real = !!engineOpts;
  const m = { id, home, away, hc: hc === '#035C55' ? null : hc, ac: ac === '#E07A3F' ? null : ac, day: dayOf(new Date()), date: 'Today · ' + source, status: 'busy', score: real ? null : [2, 1] };
  S.matches.unshift(m);
  S.job = { id, home, away, p: 0, real, running: real }; S.upload = null;
  go('processing');
  if (real) { runEngineJob(m, engineOpts, source); return; }
  const t0 = performance.now(), dur = 9000;
  (function step() {
    S.job.p = Math.min(1, (performance.now() - t0) / dur);
    if (S.route === 'processing') updateProcessing();
    if (S.job.p < 1) return setTimeout(step, 120);
    m.status = 'ready';
    toast(`${esc(home)} vs ${esc(away)} is ready`, 'View stats', () => go('match', { matchId: id, tab: 'overview', showAll: false }));
    if (S.route === 'processing') go('match', { matchId: id, tab: 'overview', showAll: false });
  })();
}

/* Real analysis: start the engine, follow its progress, then keep the report in this browser */
const POLL_MS = 2000, MAX_POLL_FAILS = 15;   // ~30 s of lost contact before giving up
const sleep = ms => new Promise(r => setTimeout(r, ms));
async function runEngineJob(m, opts, source) {
  const base = S.engine.base, title = `${m.home} vs ${m.away}`;
  const open = () => go('match', { matchId: m.id, tab: 'overview', showAll: false });
  let report;
  try {
    await E.start(base, { mode: opts.mode, kits: opts.kits });
    for (let fails = 0; ;) {
      await sleep(POLL_MS);
      let st;
      try { st = await E.status(base); fails = 0; } catch (err) { if (++fails >= MAX_POLL_FAILS) throw err; continue; }
      if (st.status === 'error') throw new Error(st.error_msg || 'The model couldn\'t analyze this video.');
      if (st.status === 'cancelled') throw new Error('The analysis was cancelled.');
      if (S.job && S.job.id === m.id) {
        S.job = { ...S.job, p: E.phaseStep(st), phase: st.phase, fps: st.fps, eta: st.eta };
        if (S.route === 'processing') updateProcessing();
      }
      if (st.status === 'completed') break;
    }
    const sum = await E.summary(base);
    report = E.toReport(sum, (sum && sum.home_kit) || opts.homeKit, base);   // with the team picker, the engine knows the home kit
  } catch (err) {
    console.error(err);
    m.status = 'failed';
    if (S.job && S.job.id === m.id) S.job = { ...S.job, running: false };
    toast(`Analysis failed: ${esc(err.message)}`);
    if (S.route === 'processing') go('upload');
    return;
  }
  S.real[m.id] = report; m.status = 'ready';
  if (S.job && S.job.id === m.id) S.job = { ...S.job, running: false };
  rememberVideo(m.id, base);
  if (S.route === 'processing') open();
  const row = { id: m.id, home: m.home, away: m.away, hc: m.hc, ac: m.ac, source_name: source, created_at: new Date().toISOString(), status: 'ready',
    stats: report.stats, players: report.players, engine: report.engine, context: report.context };
  // Saved on the engine (dashboard, other browsers, AI coach) and in this browser (opens offline)
  const [onEngine, here] = await Promise.all([E.saveAnalysis(base, row).then(() => true, err => { console.error(err); return false; }), storeAnalysis(row)]);
  if (onEngine) toast(`${esc(title)} is ready`, 'View stats', open);
  else if (here) toast('The stats are ready, but only in this browser: the model server could not save them for the dashboard and AI coach.', 'View stats', open);
}

/* Player card (stats-view.js playerCard): opens from any player row, keepers included. ‹ › and the heatmap switch redraw it in place */
function playerSheet(id) {
  if (!S.pc || S.pc.id !== id) S.pc = { id, mode: (S.pc && S.pc.mode) || 'all' };
  const card = SV.playerCard(svModel(S.real[S.matchId]), id, S.pc, pitch);
  if (!card) return;
  const sh = $('#sheet');
  if (!sh.hidden && $('#sheet-body .pc')) $('#sheet-body').innerHTML = card.html;
  else openSheet(card.html, card.label);
  sh.setAttribute('aria-label', card.label);
  $('#cancelSheet').onclick = closeSheet;
}
$('#sheet-body').addEventListener('click', e => {
  const t = e.target.closest('[data-pc-go],[data-pc-mode]');
  if (!t || !S.pc) return;
  if (t.dataset.pcMode) S.pc.mode = t.dataset.pcMode;
  playerSheet(t.dataset.pcGo || S.pc.id);
});

function exportSheet() {
  openSheet(`<div class="sheet-head"><h2>Export</h2><button class="btn btn-ghost" id="cancelSheet">Done</button></div>
    <div class="opt-grid">
      <button class="opt" data-exp="PDF report"><b>PDF report</b><span>All 20 stats and pitch maps</span></button>
      <button class="opt" data-exp="CSV"><b>Stats as CSV</b><span>Team and player tables</span></button>
      <button class="opt" data-exp="Annotated video"><b>Annotated video</b><span>MP4 with tracking overlays</span></button>
      <button class="opt" data-exp="link"><b>Copy share link</b><span>Read-only link to this match</span></button>
    </div>`, 'Export');
  $('#cancelSheet').onclick = closeSheet;
  document.querySelectorAll('[data-exp]').forEach(b => b.onclick = () => { closeSheet(); toast(b.dataset.exp === 'link' ? 'Link copied (prototype)' : `${b.dataset.exp} is not connected in this prototype`); });
}

let toastT;
function toast(msg, action, fn) {
  const el = $('#toast');
  el.innerHTML = `<span>${msg}</span>${action ? `<button class="btn btn-primary" id="toastBtn">${action}</button>` : ''}`;
  el.classList.add('show'); clearTimeout(toastT); toastT = setTimeout(() => el.classList.remove('show'), action ? 6000 : 2800);
  if (action) $('#toastBtn').onclick = () => { el.classList.remove('show'); fn(); };
}

/* ---------- Bindings ---------- */
function placePills() {
  document.querySelectorAll('.seg').forEach(seg => {
    const on = seg.querySelector('[aria-selected="true"]'), pill = seg.querySelector('.pill'); if (!on) return;
    pill.style.left = on.offsetLeft + 'px'; pill.style.width = on.offsetWidth + 'px';
  });
}
/* AI coach (coach.js): open on a model-made report saved on the connected engine; it reads the match from there.
   The demo match has its own copy on the engine (scripts/demo-match.json) under DEMO_COACH_ID */
const C = window.TrivelaCoach, DEMO_COACH_ID = '00000000-0000-4000-8000-000000000001';
function syncCoach() {
  const m = match();
  const id = !m ? null : S.real[m.id] ? m.id : m.id === 'm1' ? DEMO_COACH_ID : null;
  if (S.route !== 'match' || !id || !S.engine.base) { C.detach(); return; }
  C.init(S.engine.base);
  C.attach([{ id, label: `${m.home} vs ${m.away}` }], id);
}
function bind() {
  document.querySelectorAll('[data-go]').forEach(b => b.onclick = () => go(b.dataset.go));
  document.querySelectorAll('.toolbar [data-tab]').forEach(b => b.onclick = () => { S.tab = b.dataset.tab; render(); });
  document.querySelectorAll('.toolbar [data-teamview]').forEach(b => b.onclick = () => { S.teamView = b.dataset.teamview; render(); });
  document.querySelectorAll(':not(.toolbar) > [data-teamview], .mini[data-teamview], .link-row [data-teamview]').forEach(b => b.onclick = () => { S.teamView = b.dataset.teamview; S.tab = 'team'; render(); window.scrollTo({ top: 0 }); });
  document.querySelectorAll('[data-side]').forEach(b => b.onclick = () => { S.teamSide = b.dataset.side; render(); });
  document.querySelectorAll('[data-heatmode]').forEach(b => b.onclick = () => { S.heatMode = b.dataset.heatmode; render(); });
  document.querySelectorAll('[data-sort]').forEach(b => b.onclick = () => { const k = b.dataset.sort; S.sort = { k, dir: S.sort.k === k ? -S.sort.dir : -1 }; render(); });
  document.querySelectorAll('tr[data-player]').forEach(r => { r.onclick = () => playerSheet(r.dataset.player); r.onkeydown = e => { if (e.key === 'Enter') playerSheet(r.dataset.player); }; });
  document.querySelectorAll('[data-seek]').forEach(b => b.onclick = () => { S.video.t = Math.max(0, +b.dataset.seek - 2); S.tab = 'video'; render(); });
  SV.bind($('#statsView'), S.sv, render);
  const ex = $('#export'); if (ex) ex.onclick = exportSheet;
  document.querySelectorAll('[data-ov]').forEach(b => b.onclick = () => { const k = b.dataset.ov; S.video.overlays[k] = !S.video.overlays[k]; b.setAttribute('aria-pressed', S.video.overlays[k]); draw(); });
  const sc = $('#selCard'); if (sc) sc.onclick = e => { const b = e.target.closest('[data-player]'); if (b) playerSheet(b.dataset.player); };
  // upload
  const drop = $('#drop');
  if (drop) {
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => { const f = e.dataTransfer.files[0]; if (f) acceptFile(f); });
    $('#file').onchange = e => { const f = e.target.files[0]; if (f) acceptFile(f); };
    $('#sample').onclick = () => takeFile('trivela-sample-broadcast.mp4', 1.28e9);
    const tc = $('#toConfirm'); if (tc) tc.onclick = confirmSheet;
  }
  const cj = $('#cancelJob');
  if (cj) cj.onclick = () => { cj.disabled = true; E.cancel(S.engine.base).catch(err => { console.error(err); cj.disabled = false; toast(`Couldn't cancel: ${esc(err.message)}`); }); };
  bindVideo();
  placePills();
  syncCoach();
  if (S.route === 'match' && S.tab === 'video') setupVideo();
  requestAnimationFrame(() => requestAnimationFrame(() => document.querySelectorAll('.cmp-bars i[data-w]').forEach(i => i.style.width = i.dataset.w + '%')));
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape' && !$('#sheet').hidden) { closeSheet(); return; }
  if (!(S.route === 'match' && S.tab === 'video') || S.real[S.matchId] || /input|textarea/i.test(e.target.tagName) || !$('#sheet').hidden) return;
  if (e.code === 'Space') { e.preventDefault(); togglePlay(); }
  else if (e.key === 'ArrowRight') { S.video.t = Math.min(CLIP, S.video.t + 5); draw(); }
  else if (e.key === 'ArrowLeft') { S.video.t = Math.max(0, S.video.t - 5); draw(); }
  else if (e.key.toLowerCase() === 'k') jumpEvent(1);
  else if (e.key.toLowerCase() === 'j') jumpEvent(-1);
});
window.addEventListener('resize', placePills);
matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => render());
new MutationObserver(() => render()).observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });

/* A link to a saved analysis (#report/<id>) opens it from this browser, else from the engine; anything else falls back to the sample report */
async function openLinked(id) {
  let row = loadSaved().find(r => r.id === id);
  if (!row && S.engine.base) row = await E.analysis(S.engine.base, id).catch(err => { console.error(err); return null; });
  if (!row) toast('That analysis was not found. Showing the sample match.');
  else {
    S.real[row.id] = { stats: row.stats || {}, players: row.players || [], engine: row.engine || {}, context: row.context || null };
    S.matches.unshift(fromSaved(row)); S.matchId = row.id;
  }
  render();
}

setupNav();
if (LINKED) openLinked(LINKED); else render();
if (S.engine.base) connectEngine(S.engine.base, true);
/* ---------- Recent analyses menu (upload screen's nav button): last 3 saved in this browser, sample matches if none ---------- */
(() => {
  const wrap = $('#recent'), btn = $('#recentBtn'), menu = $('#recentMenu');
  const SAMPLES = [
    { id: '', home: 'Harbor City', away: 'Northgate United', date: 'Sep 28', ready: true, score: [2, 1] },
    { id: '', home: 'Northgate United', away: 'Eastport Athletic', date: 'Sep 21', ready: true, score: [0, 0] },
    { id: '', home: 'Harbor City', away: 'Lakeside Rovers', date: 'Sep 14', ready: true, score: [3, 2] }];
  const dayOf = iso => { const d = new Date(iso); return d.toDateString() === new Date().toDateString() ? 'Today' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); };
  const fromRow = r => ({ id: r.id, home: r.home, away: r.away, date: dayOf(r.created_at), ready: r.status === 'ready', score: null });   // the model doesn't detect goals yet
  let items = null;
  const open = v => { menu.hidden = !v; btn.setAttribute('aria-expanded', v); };
  const paint = () => {
    menu.innerHTML = (items.length ? items.map((m, i) => `<button class="recent-item" role="menuitem" data-i="${i}"><span><b>${esc(m.home)} ${m.ready && m.score ? m.score[0] + '–' + m.score[1] : 'vs'} ${esc(m.away)}</b><small>${esc(m.date)}</small></span><span class="recent-chip${m.ready ? '' : ' busy'}">${m.ready ? 'Ready' : 'Analyzing'}</span></button>`).join('')
      : '<div class="recent-empty">No analyses yet.</div>') + '<a class="recent-all" role="menuitem" href="dashboard#analyses">See all analyses</a>';
  };
  const load = async () => {
    if (items) return;
    items = loadSaved().slice(0, 3).map(fromRow);
    if (!items.length) items = SAMPLES;
    paint();
  };
  const show = () => { wrap.hidden = S.route !== 'upload'; if (wrap.hidden) open(false); };
  btn.onclick = async () => { if (menu.hidden) { menu.innerHTML = '<div class="recent-empty">Loading…</div>'; open(true); await load(); } else open(false); };
  menu.onclick = e => {
    const b = e.target.closest('[data-i]'); if (!b) return;
    const m = items[+b.dataset.i]; open(false);
    if (!m.ready) { go('processing'); return; }
    location.hash = m.id ? '#report/' + m.id : '#report'; location.reload();
  };
  document.addEventListener('pointerdown', e => { if (!wrap.contains(e.target)) open(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && !menu.hidden) { open(false); btn.focus(); } });
  const base = render; render = () => { base(); show(); };
  show();
})();
})();
