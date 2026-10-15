/* Match stats view: the report's Overview tab. Laid out like a match-centre stats page (shot map and top stats side by side,
   grouped stat cards, attacking zones, a per-player table), built only from what Trivela measures. No player ratings.
   render(model, state, pitch) -> html. The app keeps `state` and re-renders when bind() reports a change.
   model: { home, away, color: { home, away }, stats: { name: [home, away] }, defs: [{ g, k, u, d }],
            shots: [{ x, y, team, on, goal?, xg, kmh? }] | null, heat: { home: grid, away: grid } | null, heatSize: [nx, ny],
            players: [{ id, team, name, badge, v: { metric: value } }], playerNote, facts: [[label, value]], note } */
(function (root) {
  'use strict';

  const GROUPS = ['Possession', 'Passing', 'Attacking', 'Set pieces', 'Defending', 'Physical', 'Spatial'];
  const TOP = ['Goals', 'xG', 'Shots', 'Shots on target', 'Touches in opposition box', 'Passes', 'Corners', 'Tackles', 'Distance covered', 'Top speed'];
  const LOWER_BETTER = new Set(['Dispossessed']);
  const NOT_JUDGED = new Set(['Saves', 'Possession']);   // more saves only means more shots faced; possession has its own bar
  const MAX_TOP = 8;
  /* Defensive contribution: every time a player stopped an attack. Worked out here, so past reports get it too */
  const DEF_K = 'Defensive contribution', DEF_PARTS = ['Tackles', 'Interceptions', 'Clearances'], DEF_KEYS = ['tackles', 'interceptions', 'clearances'];
  const DEF_NOTE = 'Defensive contribution = tackles + interceptions + clearances.';

  /* Player table: column key -> [header, unit, decimals]. Tabs follow a match centre (Top stats + five categories) and are
     always shown; a stat the clip didn't measure reads "—". Goalkeeping lists only the two keepers */
  const PCOLS = {
    distance: ['Distance', ' km', 2], top: ['Top speed', ' km/h', 1], sprints: ['Sprints'], sprint_kmh: ['Sprint speed', ' km/h', 1],
    passes: ['Passes'], acc: ['Accuracy', '%'], touches: ['Touches'], shots: ['Shots'], shots_on: ['On target'], goals: ['Goals'], xg: ['xG', '', 2],
    dribbles: ['Dribbles'], box_touches: ['Touches in box'], ft_min: ['Final third', ' min', 1], crosses: ['Crosses'],
    corners: ['Corners'], free_kicks: ['Free kicks'], long_balls_att: ['Long balls'], long_balls: ['Accurate long balls'],
    def: ['Def. contribution'], tackles: ['Tackles'], interceptions: ['Interceptions'], clearances: ['Clearances'],
    won: ['Duels won'], dispossessed: ['Dispossessed'],
    saves: ['Saves'], shots_faced: ['Shots on target faced'], xg_faced: ['xG faced', '', 2],
  };
  const NO_BEST = new Set(['dispossessed', 'shots_faced', 'xg_faced']);   // more isn't better, so no "best" pill
  const PTABS = [
    ['top', 'Top stats', ['distance', 'top', 'sprints', 'passes', 'acc', 'touches', 'shots', 'xg', 'def']],
    ['attack', 'Attack', ['goals', 'shots', 'shots_on', 'xg', 'dribbles', 'box_touches', 'ft_min', 'crosses', 'corners', 'free_kicks']],
    ['passing', 'Passes', ['passes', 'acc', 'long_balls_att', 'long_balls', 'crosses', 'touches']],
    ['defending', 'Defense', ['def', 'tackles', 'interceptions', 'clearances']],
    ['duels', 'Duels', ['won', 'dispossessed', 'tackles', 'dribbles', 'sprints', 'sprint_kmh']],
    ['keepers', 'Goalkeeping', ['saves', 'shots_faced', 'xg_faced', 'touches', 'passes', 'acc', 'long_balls_att', 'distance']],
  ];

  const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const isNum = v => typeof v === 'number' && Number.isFinite(v);
  const fmt = (v, d, u = '') => (isNum(v) ? (d ? v.toFixed(d) : String(Math.round(v * 100) / 100)) + u : '—');
  const W = 105, H = 68;

  /* ---------- Team stat rows ---------- */
  function leader(k, h, a) {
    if (NOT_JUDGED.has(k) || !isNum(h) || !isNum(a) || h === a) return null;
    return (LOWER_BETTER.has(k) ? h < a : h > a) ? 'home' : 'away';
  }
  /* A stat the engine didn't measure for this clip still gets its row, showing "—", so every report has the same layout */
  function statRow(model, def) {
    const missing = !has(model, def.k), [h, a] = missing ? [null, null] : model.stats[def.k], lead = leader(def.k, h, a);
    const acc = !missing && def.k === 'Passes' && model.stats['Pass accuracy'];   // "423 (85%)", like accurate passes on a match centre
    const val = (v, i) => fmt(v, def.d, def.u) + (acc ? ` <small>(${acc[i]}%)</small>` : '');
    const cell = (side, i, v) => `<span class="sv-v${lead === side ? ' lead' : ''}"${lead === side ? ` style="--c:${model.color[side]}"` : ''}>${val(v, i)}</span>`;
    return `<div class="sv-row${missing ? ' off' : ''}">${cell('home', 0, h)}<span class="sv-k">${esc(def.k)}</span>${cell('away', 1, a)}</div>`;
  }
  function possBar(model, label = true) {
    const p = model.stats.Possession;
    if (!p) return '';
    const seg = (side, v) => `<span style="flex:${Math.max(v, 18)} 1 0;background:${model.color[side]}">${v}%</span>`;
    return `${label ? '<div class="sv-poss-k">Possession</div>' : ''}<div class="sv-poss" role="img" aria-label="Possession ${p[0]}% to ${p[1]}%">${seg('home', p[0])}${seg('away', p[1])}</div>`;
  }
  const defOf = (model, k) => model.defs.find(d => d.k === k) || { k };
  const has = (model, k) => Array.isArray(model.stats[k]);
  const known = (model, k) => has(model, k) || model.defs.some(d => d.k === k);

  /* The top stats in TOP order; when there are more than fit, the unmeasured ones make room first */
  function topStats(model) {
    const list = TOP.filter(k => known(model, k)), missing = list.filter(k => !has(model, k));
    const cut = new Set(missing.slice(Math.max(0, missing.length - Math.max(0, list.length - MAX_TOP))));
    const rows = list.filter(k => !cut.has(k)).slice(0, MAX_TOP).map(k => statRow(model, defOf(model, k))).join('');
    return `<section class="sv-card"><h3 class="sv-title">Top stats</h3>${possBar(model)}<div class="sv-rows">${rows}</div></section>`;
  }
  function groupCards(model) {
    const groups = GROUPS.map(g => [g, model.defs.filter(d => d.g === g && d.k !== 'Possession')]).filter(([g, list]) => list.length || (g === 'Possession' && has(model, 'Possession')));
    // Possession % reads as a split bar, at the top of its own card as well as in Top stats
    return groups.map(([g, list]) => `<section class="sv-card"><h3 class="sv-title">${esc(g)}</h3>${g === 'Possession' ? possBar(model, false) : ''}<div class="sv-rows">${list.map(d => statRow(model, d)).join('')}</div>${list.some(d => d.k === DEF_K) ? `<p class="sv-cap">${DEF_NOTE}</p>` : ''}</section>`).join('');
  }

  /* Numbers worked out here, so past reports get them too: Defensive contribution for the teams (top of Defending) and players,
     final-third time in minutes, and what each keeper faced (the other side's shots on target and xG) */
  function withDerived(model) {
    return derivePlayers(deriveTeam(model));
  }
  function deriveTeam(model) {
    if (has(model, DEF_K) || model.defs.some(d => d.k === DEF_K)) return model;
    const parts = DEF_PARTS.filter(k => has(model, k)), defs = [{ g: 'Defending', k: DEF_K }, ...model.defs];
    if (!parts.length) return DEF_PARTS.some(k => known(model, k)) ? { ...model, defs } : model;   // its row, as "—"
    const sum = i => parts.reduce((t, k) => t + (+model.stats[k][i] || 0), 0);
    return { ...model, stats: { ...model.stats, [DEF_K]: [sum(0), sum(1)] }, defs };
  }
  function derivePlayers(model) {
    const extra = v => {
      const got = DEF_KEYS.filter(k => isNum(v[k]));
      return { ...v, ...(got.length ? { def: got.reduce((t, k) => t + v[k], 0) } : {}), ...(isNum(v.final_third_s) ? { ft_min: v.final_third_s / 60 } : {}) };
    };
    const faced = (k, team) => (has(model, k) ? model.stats[k][team === 'home' ? 1 : 0] : undefined);
    return { ...model,
      players: (model.players || []).map(p => ({ ...p, v: extra(p.v) })),
      keepers: (model.keepers || []).map(p => ({ ...p, v: { shots_faced: faced('Shots on target', p.team), xg_faced: faced('xG', p.team), ...extra(p.v) } })) };
  }

  /* An empty pitch with a note on it, for a map this clip couldn't make */
  const offPitch = (pitch, label, msg) => `<div class="sv-pitch sv-off">${pitch('', label)}<p>${esc(msg)}</p></div>`;

  /* ---------- Shot map: every shot on one pitch, step through them one by one ---------- */
  function shotCard(model, st, pitch) {
    const head = n => `<div class="sv-shot-head"><h3 class="sv-title">Shot map</h3>${n}</div>`;
    if (!model.shots) return `<section class="sv-card sv-shotcard">${head('')}${offPitch(pitch, 'Shot map', 'Shots can\'t be placed for this clip because the model couldn\'t map the pitch. Wide-angle footage works best.')}</section>`;
    if (!model.shots.length) return `<section class="sv-card sv-shotcard">${head('')}<div class="sv-pitch">${pitch('', 'Shot map')}</div><p class="sv-empty">No shots were mapped in this clip.</p></section>`;
    const shots = model.shots, i = Math.min(Math.max(0, st.shot || 0), shots.length - 1), s = shots[i];
    const dot = (x, j) => {
      const c = model.color[x.team], r = (1.1 + Math.sqrt(Math.max(0, x.xg || 0)) * 2.6).toFixed(2), sel = j === i, full = x.on || x.goal;
      return `<g class="sv-dot${sel ? ' sel' : ''}" data-sv-shot="${j}">${sel ? `<circle cx="${x.x}" cy="${x.y}" r="${(+r + 1.6).toFixed(2)}" fill="none" stroke="var(--fg)" stroke-width=".45"/>` : ''}<circle cx="${x.x}" cy="${x.y}" r="${r}" fill="${full ? c : 'var(--surface-2)'}" fill-opacity="${full ? .9 : 1}" stroke="${c}" stroke-width=".6"/>${x.goal ? `<circle cx="${x.x}" cy="${x.y}" r=".9" fill="#fff"/>` : ''}</g>`;
    };
    const ordered = shots.map((x, j) => [x, j]).sort((a, b) => (a[1] === i) - (b[1] === i));   // the selected shot is drawn on top
    const team = s.team === 'home' ? model.home : model.away;
    const facts = [['Result', s.goal ? 'Goal' : s.on ? 'On target' : 'Off target'], ['xG', isNum(s.xg) ? s.xg.toFixed(2) : '—'], ...(s.kmh ? [['Ball speed', `${Math.round(s.kmh)} km/h`]] : [])];
    const nav = `<div class="sv-step"><button data-sv-step="-1" aria-label="Previous shot"${i === 0 ? ' disabled' : ''}>‹</button><span class="num">${i + 1} / ${shots.length}</span><button data-sv-step="1" aria-label="Next shot"${i === shots.length - 1 ? ' disabled' : ''}>›</button></div>`;
    return `<section class="sv-card sv-shotcard">${head(nav)}
      <div class="sv-shot-who"><span class="sv-sw" style="background:${model.color[s.team]}"></span><b>${esc(team)}</b></div>
      <dl class="sv-shot-facts">${facts.map(([k, v]) => `<div><dt>${k}</dt><dd class="num">${esc(v)}</dd></div>`).join('')}</dl>
      <div class="sv-pitch">${pitch(ordered.map(([x, j]) => dot(x, j)).join(''), 'Shot map')}</div>
      <div class="sv-legend"><span><i class="f"></i>On target</span><span><i></i>Off target</span><span>Bigger = higher xG</span></div></section>`;
  }

  /* ---------- Attacking zones: where each side had the ball in the opponent's half, by lane ---------- */
  function lanes(grid, [nx, ny], side) {
    if (!Array.isArray(grid) || grid.length !== nx * ny) return null;
    const sums = [0, 0, 0];
    grid.forEach((v, n) => {
      const x = ((n % nx) + .5) / nx * W, lane = Math.min(2, Math.floor(((Math.floor(n / nx) + .5) / ny) * 3));
      if (side === 'home' ? x > W / 2 : x < W / 2) sums[lane] += +v || 0;   // home attacks left to right
    });
    const t = sums[0] + sums[1] + sums[2];
    if (!t) return null;
    const a = Math.round(sums[0] / t * 100), b = Math.round(sums[1] / t * 100);
    return [a, b, 100 - a - b];
  }
  function zonesCard(model, pitch) {
    const hz = model.heat && model.heatSize && lanes(model.heat.home, model.heatSize, 'home'), az = model.heat && model.heatSize && lanes(model.heat.away, model.heatSize, 'away');
    if (!hz && !az) return `<section class="sv-card"><h3 class="sv-title">Attacking zones</h3>${offPitch(pitch, 'Attacking zones', 'Needs the pitch mapping, which this clip doesn\'t have.')}</section>`;
    const pill = (x, y, v, side, right) => `<g transform="translate(${x} ${y})"><rect x="-9.5" y="-3.6" width="19" height="7.2" rx="3.6" fill="${model.color[side]}"/><text x="0" y="1.3" text-anchor="middle" font-size="3.5" font-weight="700" fill="#fff">${right ? `${v}% →` : `← ${v}%`}</text></g>`;
    const inner = `<g stroke="var(--fg-3)" stroke-width=".3" stroke-dasharray="1.2 1.2"><line x1="0" y1="${H / 3}" x2="${W}" y2="${H / 3}"/><line x1="0" y1="${2 * H / 3}" x2="${W}" y2="${2 * H / 3}"/></g>`
      + [0, 1, 2].map(l => { const y = (l + .5) * H / 3; return (hz ? pill(78.75, y, hz[l], 'home', true) : '') + (az ? pill(26.25, y, az[l], 'away', false) : ''); }).join('');
    return `<section class="sv-card"><h3 class="sv-title">Attacking zones</h3><div class="sv-pitch">${pitch(inner, 'Attacking zones')}</div>
      <p class="sv-cap">Share of each team's time on the ball in the opponent's half, by lane. ${esc(model.home)} attack left to right.</p></section>`;
  }

  /* ---------- Player stats: one table, a tab per stat family, sortable ---------- */
  function playerTable(model, st) {
    if (!(model.players || []).length && !(model.keepers || []).length) return '';
    const cur = PTABS.find(t => t[0] === st.ptab) || PTABS[0], gk = cur[0] === 'keepers', list = (gk ? model.keepers : model.players) || [];
    const chips = `<div class="sv-chips" role="tablist">${PTABS.map(([id, label]) => `<button role="tab" data-sv-ptab="${id}" aria-selected="${id === cur[0]}">${label}</button>`).join('')}</div>`;
    const wrap = body => `<section class="sv-card sv-players"><h3 class="sv-title">Player stats</h3>${chips}${body}</section>`;
    if (!list.length) return wrap(`<p class="sv-empty">${gk ? 'The goalkeepers weren\'t identified in this clip. The model finds them when it can map the pitch, so wide-angle footage works best.' : 'No outfield players were tracked long enough to list.'}</p>`);
    const cols = cur[2], picked = st.psort && cols.includes(st.psort.k);
    const sk = picked ? st.psort.k : cols[0], dir = picked ? st.psort.dir : -1;
    const val = (p, k) => (isNum(p.v[k]) ? p.v[k] : -1);
    const rows = [...list].sort((a, b) => (val(a, sk) - val(b, sk)) * dir);
    const best = Object.fromEntries(cols.map(k => [k, Math.max(...list.map(p => val(p, k)))]));
    const th = cols.map(k => `<th class="r"${k === sk ? ` aria-sort="${dir > 0 ? 'ascending' : 'descending'}"` : ''}><button data-sv-psort="${k}">${PCOLS[k][0]}${k === sk ? (dir > 0 ? ' ↑' : ' ↓') : ''}</button></th>`).join('');
    const cell = (p, k) => { const v = p.v[k], top = !NO_BEST.has(k) && isNum(v) && v > 0 && v === best[k]; return `<td class="r num"><span class="${top ? 'sv-best' : ''}">${fmt(v, PCOLS[k][2], PCOLS[k][1])}</span></td>`; };
    const tr = rows.map(p => `<tr data-player="${esc(p.id)}" tabindex="0"><td><span class="sv-pl"><span class="sv-jersey" style="background:${model.color[p.team]}">${esc(p.badge)}</span><span><b>${esc(p.name)}</b><small>${esc(p.team === 'home' ? model.home : model.away)}</small></span></span></td>${cols.map(k => cell(p, k)).join('')}</tr>`).join('');
    const note = gk ? 'Each side\'s keeper is the one the model saw in front of that goal for longest. Shots and xG faced are the other side\'s.' : model.playerNote;
    return wrap(`<div class="sv-table"><table><thead><tr><th>${gk ? 'Goalkeeper' : 'Player'}</th>${th}</tr></thead><tbody>${tr}</tbody></table></div>
      ${note ? `<p class="sv-cap">${esc(note)}</p>` : ''}`);
  }

  /* ---------- Player card (opens from a table row): who he is, a heatmap (whole match / on / off the ball), only his own stats ---------- */
  const HEAT_MODES = [['all', 'Whole match'], ['on', 'On the ball'], ['off', 'Off the ball']];
  const HEAT_MIN = 20, HEAT_HOT = 62;   // grid values are 0..99: below MIN isn't drawn, above HOT glows yellow
  /* Soft blobs in the team colour, hot cells glowing ball-yellow, blurred together */
  function heatBlobs(grid, [nx, ny], color) {
    if (!Array.isArray(grid) || grid.length !== nx * ny || !grid.some(v => v > 0)) return '';
    const w = W / nx, h = H / ny, r = (Math.max(w, h) * .8).toFixed(2);
    const at = (n, v, fill, o) => `<circle cx="${(((n % nx) + .5) * w).toFixed(2)}" cy="${((Math.floor(n / nx) + .5) * h).toFixed(2)}" r="${r}" fill="${fill}" fill-opacity="${o.toFixed(2)}"/>`;
    // A power curve keeps rarely-visited cells faint, so where he really played stands out
    const base = grid.map((v, n) => (v > HEAT_MIN ? at(n, v, color, .1 + Math.pow(v / 99, 1.6) * .7) : '')).join('');
    const hot = grid.map((v, n) => (v > HEAT_HOT ? at(n, v, 'var(--ball)', (v - HEAT_HOT) / (99 - HEAT_HOT) * .8) : '')).join('');
    return `<defs><filter id="pcBlur" x="-10%" y="-10%" width="120%" height="120%"><feGaussianBlur stdDeviation="2.4"/></filter>
      <clipPath id="pcClip"><rect x="0" y="0" width="${W}" height="${H}" rx="1"/></clipPath></defs><g clip-path="url(#pcClip)"><g filter="url(#pcBlur)">${base}${hot}</g></g>`;
  }
  /* His stats in the table's categories, each stat once, only what was measured */
  function cardSections(p, gk) {
    const seen = new Set(), tabs = gk ? [PTABS[PTABS.length - 1], ...PTABS.slice(0, -1)] : PTABS.slice(0, -1);
    return tabs.map(([, label, cols]) => [label, cols.filter(k => isNum(p.v[k]) && !seen.has(k) && seen.add(k))]).filter(([, cols]) => cols.length)
      .map(([label, cols]) => `<section class="pc-sec"><h3>${label}</h3><dl class="pc-list">${cols.map(k => `<div><dt>${PCOLS[k][0]}</dt><dd class="num">${fmt(p.v[k], PCOLS[k][2], PCOLS[k][1])}</dd></div>`).join('')}</dl></section>`).join('');
  }
  /* -> { id, label, html } for player `id` (outfield or keeper), or null. st: { mode }. ‹ › step through players, then keepers */
  function playerCard(raw, id, st, pitch) {
    const model = withDerived(raw), all = [...model.players, ...model.keepers], i = all.findIndex(p => p.id === id);
    if (i < 0) return null;
    const p = all[i], gk = i >= model.players.length, mode = HEAT_MODES.some(m => m[0] === st.mode) ? st.mode : 'all';
    const size = model.playerHeatSize, blobs = size ? heatBlobs((p.heat || {})[mode], size, model.color[p.team]) : '';
    const why = !size ? 'No heatmap: the model couldn\'t map the pitch for this clip.'
      : blobs ? '' : mode === 'all' ? 'Not on screen long enough for a heatmap.' : 'This report has no on/off-the-ball split yet. Analyse the match again to get it.';
    const nav = (d, label, sym) => { const to = all[i + d]; return `<button class="pc-nav" ${to ? `data-pc-go="${esc(to.id)}"` : 'disabled'} aria-label="${label}">${sym}</button>`; };
    const touches = isNum(p.v.touches) ? `<span class="pc-touch">Touches <b class="num">${p.v.touches}</b></span>` : '';
    return { id: p.id, label: p.name, html: `<div class="pc">
      <div class="pc-head">${nav(-1, 'Previous player', '‹')}<div class="pc-id"><span class="sv-jersey pc-badge" style="background:${model.color[p.team]}">${esc(p.badge)}</span>
        <h2>${esc(p.name)}</h2><span>${esc(p.team === 'home' ? model.home : model.away)}</span></div>${nav(1, 'Next player', '›')}</div>
      <section class="pc-sec"><div class="pc-sec-h"><h3>Heatmap</h3>${touches}</div>
        <div class="sv-chips pc-modes" role="tablist">${HEAT_MODES.map(([k, l]) => `<button role="tab" data-pc-mode="${k}" aria-selected="${k === mode}">${l}</button>`).join('')}</div>
        <div class="sv-pitch${why ? ' sv-off' : ''}">${pitch(blobs, `${p.name} heatmap`)}${why ? `<p>${esc(why)}</p>` : ''}</div>
        <p class="sv-cap">${esc(model.home)} attack left to right.</p></section>
      ${cardSections(p, gk) || '<p class="sv-empty">No stats were measured for this player.</p>'}
      <div class="pc-foot"><span>${esc(model.cardNote || '')}</span><button class="btn btn-ghost" id="cancelSheet">Done</button></div></div>` };
  }

  function render(raw, st, pitch) {
    const model = withDerived(raw);
    const facts = model.facts && model.facts.length
      ? `<section class="sv-card sv-foot"><h3 class="sv-title">From the model</h3><dl>${model.facts.map(([k, v]) => `<div><dt>${esc(k)}</dt><dd class="num">${esc(v)}</dd></div>`).join('')}</dl>${model.note ? `<p class="sv-cap">${esc(model.note)}</p>` : ''}</section>` : '';
    const gaps = model.defs.some(d => !has(model, d.k)) ? '<span class="sv-gap">— not measured in this clip</span>' : '';
    return `<div class="sv" id="statsView">
      <div class="sv-key">${gaps}<span><span class="sv-sw" style="background:${model.color.home}"></span>${esc(model.home)}</span><span><span class="sv-sw" style="background:${model.color.away}"></span>${esc(model.away)}</span></div>
      <div class="sv-hero">${shotCard(model, st, pitch)}${topStats(model)}</div>
      <div class="sv-groups">${groupCards(model)}${zonesCard(model, pitch)}</div>
      ${playerTable(model, st)}
      ${facts}</div>`;
  }

  /* Clicks inside the view update `st`, then onChange() re-renders */
  function bind(el, st, onChange) {
    if (!el) return;
    el.addEventListener('click', e => {
      const t = e.target.closest('[data-sv-shot],[data-sv-step],[data-sv-ptab],[data-sv-psort]');
      if (!t || t.disabled) return;
      if (t.dataset.svShot != null) st.shot = +t.dataset.svShot;
      else if (t.dataset.svStep) st.shot = (st.shot || 0) + +t.dataset.svStep;
      else if (t.dataset.svPtab) { st.ptab = t.dataset.svPtab; st.psort = null; }
      else if (t.dataset.svPsort) { const k = t.dataset.svPsort; st.psort = { k, dir: st.psort && st.psort.k === k ? -st.psort.dir : -1 }; }
      onChange();
    });
  }

  const api = { render, bind, lanes, leader, withDerived, playerCard };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.TrivelaStatsView = api;
})(typeof self !== 'undefined' ? self : this);
