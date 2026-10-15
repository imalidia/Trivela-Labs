(() => {
const DEMO = 'app.html#report', NEW = 'app.html#upload';
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const EASE_OUT = 'cubic-bezier(.23,1,.32,1)'; /* same as --ease-out */
const $ = s => document.querySelector(s);
const $$ = s => [...document.querySelectorAll(s)];
const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
const ICON = {
  arrow: '<svg class="arrow" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6"/></svg>',
  run: '<svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5.5v13a1 1 0 001.5.86l10.4-6.5a1 1 0 000-1.72L9.5 4.64A1 1 0 008 5.5z"/></svg>',
  ready: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
};
$('#navNew').href = NEW; $('#latest').href = DEMO;

/* ---------- Sample data: fictional clubs; m1 is the same sample match the demo opens (Harbor City 2–1 Northgate United). Real analyses replace them ---------- */
const RUN_ID = 'm7';
const MATCHES = [ /* newest first; pairs are [home, away] */
  { id: 'm7', seed: 71, home: 'Riverside FC', away: 'Harbor City', date: 'Today', kind: 'Full match', mins: 92, status: 'running',
    result: { score: [1, 2], poss: [47, 53], passes: [402, 471], shots: [8, 13] } },
  { id: 'm1', seed: 11, home: 'Harbor City', away: 'Northgate United', date: 'Sep 28', kind: 'Full match', mins: 90, status: 'ready', score: [2, 1], poss: [56, 44], passes: [498, 391], shots: [14, 9] },
  { id: 'm2', seed: 23, home: 'Northgate United', away: 'Eastport Athletic', date: 'Sep 21', kind: 'Full match', mins: 93, status: 'ready', score: [0, 0], poss: [48, 52], passes: [412, 446], shots: [7, 8] },
  { id: 'm3', seed: 37, home: 'Harbor City', away: 'Lakeside Rovers', date: 'Sep 14', kind: 'Full match', mins: 94, status: 'ready', score: [3, 2], poss: [58, 42], passes: [521, 368], shots: [17, 10] },
  { id: 'm4', seed: 41, home: 'Eastport Athletic', away: 'Riverside FC', date: 'Sep 7', kind: 'Highlights', mins: 24, status: 'ready', score: [1, 1], poss: [51, 49], passes: [133, 128], shots: [5, 6] },
  { id: 'm5', seed: 53, home: 'Lakeside Rovers', away: 'Harbor City', date: 'Aug 31', kind: 'Full match', mins: 91, status: 'ready', score: [0, 2], poss: [45, 55], passes: [389, 476], shots: [6, 15] },
];
const STEPS = ['Detecting players', 'Tracking the ball', 'Assigning teams', 'Detecting passes and shots', 'Computing stats'];

/* The dashboard lists only the newest few analyses, and the newest few events in recent activity */
const MAX_LISTED = 5, MAX_ACTIVITY = 6;

let matches = MATCHES.slice(0, MAX_LISTED), runP = .62, filter = 'all', query = '';
const isReady = m => m.status === 'ready';
const pct = () => Math.round(runP * 100);
const readyText = m => `<b>${esc(m.home)} vs ${esc(m.away)}</b> is ready`;
let activity = [
  { when: 'Today', kind: 'run', text: `Started analyzing <b>${esc(MATCHES[0].home)} vs ${esc(MATCHES[0].away)}</b>` },
  ...MATCHES.filter(isReady).map(m => ({ when: m.date, kind: 'ready', text: readyText(m) }))];

/* ---------- Saved analyses: stored by the engine (Docker), listed here and open to the AI coach. Without an engine the samples stay ---------- */
const E = window.TrivelaEngine, BASE = E.configuredBase();
const C = window.TrivelaCoach;   // AI coach side panel (coach.js), over the saved analyses listed here
const seedOf = id => [...id].reduce((a, c) => (a * 31 + c.charCodeAt(0)) | 0, 7); /* stable heatmap thumbnail per analysis */
const isToday = d => d.toDateString() === new Date().toDateString();
const dayLabel = iso => { const d = new Date(iso); return isToday(d) ? 'Today' : d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' }); };
const whenLabel = iso => { const d = new Date(iso); return isToday(d) ? `Today, ${d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}` : dayLabel(iso); };
/* The model doesn't read goals yet, so a real analysis has no score */
function fromRow(r) {
  const s = r.stats || {};
  return { id: r.id, cloud: true, seed: seedOf(r.id), home: r.home, away: r.away, date: dayLabel(r.created_at), when: whenLabel(r.created_at), kind: 'Model analysis',
    mins: Math.max(1, Math.round((r.duration_s || 0) / 60)), status: 'ready', score: null, poss: s.Possession || [50, 50], passes: s.Passes || [0, 0], shots: s.Shots || [0, 0] };
}
const hrefOf = m => (m.cloud ? `app.html#report/${m.id}` : DEMO);
async function loadSaved() {
  if (!BASE) return;
  C.init(BASE);
  let rows;
  try { rows = await E.analyses(BASE); } catch (err) { console.error(err); toast('Could not reach the model server, so these are sample matches.'); return; }
  const real = rows.slice(0, MAX_LISTED).map(fromRow);
  C.attach(real.map(m => ({ id: m.id, label: `${m.home} vs ${m.away} · ${m.date}` })));
  if (!real.length) return;
  const before = metaValues();
  matches = real;
  activity = real.map(m => ({ when: m.when, kind: 'ready', text: readyText(m) }));
  renderAll(before);
}

/* ---------- Heatmap thumbnails: a few seeded blobs on a 15 × 10 grid, same idea as the demo's 21 × 14 heatmaps ---------- */
function rng(seed) { return () => { seed |= 0; seed = seed + 0x6D2B79F5 | 0; let t = Math.imul(seed ^ seed >>> 15, 1 | seed); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; }; }
const COLS = 15, ROWS = 10, CW = 105 / COLS, CH = 68 / ROWS, heatCache = new Map();
function heatCells(seed) {
  if (heatCache.has(seed)) return heatCache.get(seed);
  const r = rng(seed), blobs = Array.from({ length: 4 }, () => ({ x: 22 + r() * 62, y: 10 + r() * 48, s: 9 + r() * 8, w: .55 + r() * .45 }));
  const raw = Array.from({ length: COLS * ROWS }, (_, k) => {
    const i = k % COLS, j = Math.floor(k / COLS), cx = (i + .5) * CW, cy = (j + .5) * CH;
    return { i, j, v: blobs.reduce((a, b) => a + b.w * Math.exp(-((cx - b.x) ** 2 + (cy - b.y) ** 2) / (2 * b.s * b.s)), 0) };
  });
  const max = Math.max(...raw.map(c => c.v)), cells = raw.map(c => ({ ...c, v: c.v / max })).filter(c => c.v > .14);
  heatCache.set(seed, cells);
  return cells;
}
const PITCH_LINES = '<g class="tl"><rect x="1.5" y="1.5" width="102" height="65" rx="1.5"/><line x1="52.5" y1="1.5" x2="52.5" y2="66.5"/><circle cx="52.5" cy="34" r="9.15"/><rect x="1.5" y="14" width="16" height="40"/><rect x="87.5" y="14" width="16" height="40"/></g>';
const thumb = (m, strength) => `<svg viewBox="0 0 105 68" preserveAspectRatio="none" aria-hidden="true"><rect class="pf" width="105" height="68"/>${heatCells(m.seed).map(c =>
  `<rect class="hc" x="${(c.i * CW).toFixed(2)}" y="${(c.j * CH).toFixed(2)}" width="${CW.toFixed(2)}" height="${CH.toFixed(2)}" fill-opacity="${(c.v * .85 * strength).toFixed(2)}"/>`).join('')}${PITCH_LINES}</svg>`;

/* ---------- Header numbers ---------- */
const META = [
  ['Matches analyzed', v => Math.round(v)], ['Video processed', v => `${v.toFixed(1)} h`],
  ['Player tracks', v => Math.round(v)], ['Passes detected', v => Math.round(v).toLocaleString('en-US')]];
function metaValues() {
  const done = matches.filter(isReady);
  return [done.length, done.reduce((a, m) => a + m.mins, 0) / 60, done.length * 22, done.reduce((a, m) => a + m.passes[0] + m.passes[1], 0)];
}
/* Pass the previous values to count up from them (same quartic ease-out as the landing's big 20) */
function renderMeta(from) {
  const to = metaValues(), start = !reduced && from ? from : to;
  $('#meta').innerHTML = META.map(([k, fmt], i) => `<div><dt>${k}</dt><dd class="num">${fmt(start[i])}</dd></div>`).join('');
  if (start === to) return;
  const dds = $$('#meta dd'), t0 = performance.now(), DUR = 700;
  const tick = now => {
    const p = Math.min(1, (now - t0) / DUR), e = 1 - (1 - p) ** 4;
    dds.forEach((dd, i) => { dd.textContent = META[i][1](from[i] + (to[i] - from[i]) * e); });
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

/* ---------- Past analyses ---------- */
const scoreOf = m => (isReady(m) && m.score ? `<span class="score num">${m.score[0]}–${m.score[1]}</span>` : '<span class="vs">vs</span>');
function row(m) {
  const ready = isReady(m);
  const stats = ready ? [['Possession', m.poss], ['Passes', m.passes], ['Shots', m.shots]].map(([k, v], i) => `<span style="--k:${i}"><small>${k}</small><b class="num">${v[0]}–${v[1]}</b></span>`).join('')
    : '<span class="wait">Stats appear when tracking finishes</span>';
  return `<li data-id="${m.id}"${m.id === justReady ? ' class="just-ready"' : ''}><a class="row" href="${hrefOf(m)}">
    <span class="thumb${ready ? '' : ' busy'}">${thumb(m, ready ? 1 : runP)}</span>
    <span class="row-main"><span class="fixture"><span>${esc(m.home)}</span>${scoreOf(m)}<span>${esc(m.away)}</span></span>
      <span class="row-sub">${esc(m.date)} · ${m.kind} · ${m.mins} min</span></span>
    <span class="row-stats">${stats}</span>
    ${ready ? '<span class="chip ready">Ready</span>' : m.id === RUN_ID ? `<span class="chip busy">Analyzing <span class="num" data-run-pct>${pct()}%</span></span>` : '<span class="chip busy">Analyzing</span>'}
    <span class="go">${ICON.arrow}</span></a></li>`;
}
function renderAnalyses() {
  const q = query.trim().toLowerCase();
  const counts = { all: matches.length, ready: matches.filter(isReady).length, running: matches.filter(m => !isReady(m)).length };
  $$('#seg button').forEach(b => { b.setAttribute('aria-pressed', b.dataset.f === filter); b.querySelector('.n').textContent = counts[b.dataset.f]; });
  $('#anCount').textContent = matches.length;
  const list = matches.filter(m => (filter === 'all' || (filter === 'ready') === isReady(m)) && (!q || `${m.home} ${m.away}`.toLowerCase().includes(q)));
  const why = q ? `No analyses match “${esc(query.trim())}”.` : 'Nothing is analyzing right now.';
  $('#rows').innerHTML = list.length ? list.map(row).join('') : `<li class="none"><p>${why}</p><button class="btn btn-ghost btn-sm" id="showAll">Show all analyses</button></li>`;
}

/* ---------- What the CV is analyzing now: progress creeps forward, then the match lands in the list as Ready ---------- */
function renderRun() {
  const m = matches.find(x => x.id === RUN_ID);
  $('#cvRun').classList.toggle('arrive', !!justReady);
  if (!m || isReady(m)) {
    $('#cvRun').innerHTML = '<div class="run-top" style="--k:0"><div><small>All caught up</small><b>No analysis running</b></div></div><p class="run-meta" style="--k:1">Upload a match video and tracking starts right away.</p>';
    return;
  }
  $('#cvRun').innerHTML = `<div class="run-top"><div><small>Analyzing now</small><b>${esc(m.home)} vs ${esc(m.away)}</b></div><b class="run-pct num" data-run-pct></b></div>
    <div class="run-bar" role="progressbar" aria-label="Analysis progress" aria-valuemin="0" aria-valuemax="100"><i id="runBar"></i></div>
    <p class="run-meta"><span id="runStep"></span><span class="num" id="runEta"></span></p>`;
  updateRun();
}
/* Latest result: the newest finished match. When the running analysis completes it becomes the latest and rises in */
function renderLatest() {
  const m = matches.find(isReady), el = $('#latest');
  el.hidden = !m;
  if (!m) return;
  el.href = hrefOf(m);
  el.classList.toggle('arrive', m.id === justReady);
  const stats = [['Possession', m.poss], ['Passes', m.passes], ['Shots', m.shots]].map(([k, v]) => `<span><small>${k}</small><b class="num">${v[0]}–${v[1]}</b></span>`).join('');
  el.setAttribute('aria-label', `Latest result: ${m.home} ${m.score ? `${m.score[0]}–${m.score[1]}` : 'vs'} ${m.away}. Open the report`);
  el.innerHTML = `<span class="thumb" style="--k:0">${thumb(m, 1)}</span>
    <span class="lt-main" style="--k:1"><small>Latest result</small><span class="fixture"><span>${esc(m.home)}</span>${scoreOf(m)}<span>${esc(m.away)}</span></span>
      <span class="lt-sub">${esc(m.date)} · ${m.kind} · ${m.mins} min</span></span>
    <span class="lt-stats" style="--k:2">${stats}</span>
    <span class="lt-go" style="--k:3">Open report ${ICON.arrow}</span>`;
}
function updateRun() {
  const step = Math.min(STEPS.length - 1, Math.floor(runP * STEPS.length)), bar = $('#runBar');
  $$('[data-run-pct]').forEach(el => el.textContent = `${pct()}%`);
  if (!bar) return;
  bar.style.transform = `scaleX(${runP})`; bar.parentElement.setAttribute('aria-valuenow', pct());
  $('#runStep').textContent = `Step ${step + 1} of ${STEPS.length} · ${STEPS[step]}`;
  $('#runEta').textContent = `About ${Math.max(1, Math.ceil((1 - runP) * 9))} min left`;
}
/* Finishing is the payoff moment, so it gets a beat: the progress card and the row's "Analyzing" parts fade out (160ms),
   then the new state renders with the just-ready entrances and the header numbers count up from their old values */
const TICK_MS = 1500, TICK_STEP = .005, LEAVE_MS = 160;
let justReady = null;
const fadeOut = els => Promise.all(els.map(el => el.animate(
  [{ opacity: 1, transform: 'none' }, { opacity: 0, transform: reduced ? 'none' : 'translateY(-6px)' }],
  { duration: LEAVE_MS, easing: EASE_OUT, fill: 'forwards' }).finished));
const ticker = setInterval(async () => {
  if (!matches.some(x => x.id === RUN_ID)) { clearInterval(ticker); return; }   // real analyses replaced the samples
  runP = Math.min(1, runP + TICK_STEP);
  if (runP < 1) { updateRun(); return; }
  clearInterval(ticker);
  const m = matches.find(x => x.id === RUN_ID), before = metaValues(), runRow = $(`#rows [data-id="${RUN_ID}"]`);
  /* a hidden tab has a frozen animation clock; don't hold the new state hostage to it (the entrances still play on return) */
  if (!document.hidden) await fadeOut([...$('#cvRun').children, ...(runRow ? runRow.querySelectorAll('.chip, .vs, .row-stats') : [])]);
  matches = matches.map(x => x.id === RUN_ID ? { ...x, ...x.result, status: 'ready' } : x);
  activity = [{ when: 'Just now', kind: 'ready', text: readyText(m), fresh: true }, ...activity];
  justReady = RUN_ID; renderAll(before); justReady = null;
  toast(`${m.home} vs ${m.away} is ready`);
}, TICK_MS);

function renderActivity() {
  activity = activity.slice(0, MAX_ACTIVITY);
  $('#feed').innerHTML = activity.map(a => `<li${a.fresh ? ' class="fresh"' : ''}><span class="ic">${ICON[a.kind]}</span><div><p>${a.text}</p><span class="when">${a.when}</span></div></li>`).join('');
  activity = activity.map(a => (a.fresh ? { ...a, fresh: false } : a));
}
function renderAll(metaFrom) { renderMeta(metaFrom); renderRun(); renderLatest(); renderAnalyses(); renderActivity(); }

let toastTimer = 0;
function toast(msg) {
  $('#toastMsg').textContent = msg;
  $('#toast').classList.add('on'); clearTimeout(toastTimer);
  toastTimer = setTimeout(() => $('#toast').classList.remove('on'), 5000);
}
document.addEventListener('click', e => {
  if (e.target.closest('#showAll')) { filter = 'all'; query = ''; $('#q').value = ''; renderAnalyses(); $('#anTitle').focus(); }
});
$('#seg').addEventListener('click', e => { const b = e.target.closest('button'); if (!b) return; filter = b.dataset.f; renderAnalyses(); });
$('#q').addEventListener('input', e => { query = e.target.value; renderAnalyses(); });

/* ---------- Nav: a hover pill that glides between links (same as the landing) ---------- */
{
  const links = $('.nav-links');
  if (matchMedia('(hover:hover) and (pointer:fine)').matches) {
    const hl = document.createElement('span'); hl.className = 'nav-hl fresh'; hl.setAttribute('aria-hidden', 'true'); links.appendChild(hl);
    const to = a => {
      hl.classList.toggle('fresh', hl.style.opacity !== '1');
      hl.style.width = a.offsetWidth + 'px'; hl.style.height = a.offsetHeight + 'px';
      hl.style.transform = `translate(${a.offsetLeft}px,${a.offsetTop}px)`; hl.style.opacity = '1';
    };
    links.querySelectorAll('a').forEach(a => { a.addEventListener('pointerenter', () => to(a)); a.addEventListener('focus', () => { if (a.matches(':focus-visible')) to(a); }); a.addEventListener('blur', () => hl.style.opacity = '0'); });
    links.addEventListener('pointerleave', () => hl.style.opacity = '0');
  }
}

renderAll();
loadSaved();
})();
