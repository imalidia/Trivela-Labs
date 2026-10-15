/* The coach's understanding of one match, worked out in code so the model never does the maths.
   Input: a saved analysis { home, away, stats, engine, players, context }, as the engine stores it.
   Output: plain facts the model narrates: comparison, summary, players, timeline, quality. */

import { CATALOG, VISUALS, describe } from './catalog.mjs';

/* Gap size -> verdict. Our own cut-offs for one match, not a published standard. Each list is [level, slight edge, clear edge] upper bounds.
   Possession gaps count double (one side's gain is the other's loss): 58-42 is a 16-point gap, a clear edge, not dominance */
const CUTOFFS = { possession: [4, 10, 20], points: [3, 8, 15], kmh: [1, 2.5, 4], xg: [0.2, 0.5, 1], share: [0.53, 0.58, 0.66] };
const MAX_TAGS = 3;
const VERDICTS = ['level', 'slight edge', 'clear edge', 'dominant'];
const WEIGHT = { level: 0, 'slight edge': 1, 'clear edge': 2, dominant: 3 };

const BRIEF_SECS = 180;          // a track shorter than this is too little to rank or describe
const SPELL_SECS = 300;          // possession losses are counted in 5-minute spells
const TOP_SHARE = 0.25;          // "top of the team" = the top quarter, at least one player
const BALL_POOR = 60, BALL_FAIR = 80, SHORT_CLIP_MIN = 10;
const MAX_STRIKES_LISTED = 40;

const num = v => (Number.isFinite(+v) ? +v : 0);
const round = (v, d = 1) => +num(v).toFixed(d);
export const clock = secs => `${Math.floor(secs / 60)}:${String(Math.floor(secs % 60)).padStart(2, '0')}`;

function verdict(kind, a, b) {
  const gap = kind === 'share' ? (a + b ? Math.max(a, b) / (a + b) : 0.5) : Math.abs(a - b);
  const i = CUTOFFS[kind].findIndex(limit => gap <= limit);
  return VERDICTS[i === -1 ? 3 : i];
}

/* Only the stats this match has, in catalog order, then any the catalog doesn't know.
   more = who has the bigger number. leader = who comes out better (null when more is not automatically better) */
function compare(stats) {
  const order = name => { const i = CATALOG.findIndex(d => d.stat === describe(name).stat); return i === -1 ? CATALOG.length : i; };
  return Object.entries(stats)
    .filter(([, v]) => Array.isArray(v) && v.length === 2 && v.every(x => Number.isFinite(+x)))
    .sort(([a], [b]) => order(a) - order(b))
    .map(([name, pair]) => {
      const d = describe(name), [home, away] = pair.map(num);
      const v = verdict(d.kind, home, away);
      const more = v === 'level' ? 'level' : home > away ? 'home' : 'away';
      const flip = { home: 'away', away: 'home', level: 'level' };
      const leader = d.better === 'neutral' ? null : d.better === 'lower' ? flip[more] : more;
      return { stat: d.stat, unit: d.unit, group: d.group, home, away, more, leader, verdict: v };
    });
}

function groupLeader(comparison, group) {
  const rows = comparison.filter(c => c.group === group && c.leader);
  if (!rows.length) return null;                             // this match has no stats of that kind
  const score = { home: 0, away: 0 };
  for (const c of rows) if (c.leader !== 'level') score[c.leader] += WEIGHT[c.verdict];
  return score.home === score.away ? 'even' : score.home > score.away ? 'home' : 'away';
}

function headline(names, lead) {
  const parts = [];
  if (lead.on_ball) parts.push(lead.on_ball === 'even' ? 'Neither side had clearly more of the ball' : `${names[lead.on_ball]} had more of the ball`);
  if (lead.work_rate) parts.push(lead.work_rate === 'even' ? 'the running was even' : `${names[lead.work_rate]} did more of the running`);
  if (lead.attacking && lead.attacking !== 'even') parts.push(`${names[lead.attacking]} created more in attack`);
  if (lead.defending && lead.defending !== 'even') parts.push(`${names[lead.defending]} won more of the battles for the ball`);
  return parts.length ? parts.join('; ') + '.' : 'Not enough stats to compare the two sides.';
}

/* Rank 1 = best within the player's team. Brief tracks and missing values get null */
function rankWithin(rows, key) {
  const ranked = rows.filter(r => r[key] != null).sort((a, b) => b[key] - a[key]);
  return id => { const i = ranked.findIndex(r => r.id === id); return i === -1 ? null : i + 1; };
}

function playerFacts(players, secs) {
  const rows = players.map(p => {
    const id = p.player_key, s = secs[id];
    const minutes = s == null ? null : round(s / 60);
    const brief = s != null && s < BRIEF_SECS;
    const perMin = v => (minutes && !brief ? num(v) / minutes : null);
    return {
      id, label: `ID ${id.slice(1)}`, team: p.team, minutes, brief,
      distance_km: num(p.distance), top_speed: num(p.top_speed), sprints: num(p.sprints), passes: num(p.passes),
      accuracy: p.accuracy == null ? null : num(p.accuracy), touches: num(p.touches),
      // Per minute when we know the time on screen, so a 10-minute track is not compared with a 60-minute one
      rate: { distance: brief ? null : perMin(p.distance) ?? num(p.distance), touches: brief ? null : perMin(p.touches) ?? num(p.touches),
        sprints: brief ? null : perMin(p.sprints) ?? num(p.sprints), passes: brief ? null : perMin(p.passes) ?? num(p.passes),
        top_speed: brief ? null : num(p.top_speed) },
    };
  });

  return rows.map(r => {
    const mates = rows.filter(x => x.team === r.team), rankable = mates.filter(x => !x.brief);
    const top = Math.max(1, Math.round(rankable.length * TOP_SHARE));
    const ranks = Object.fromEntries(['distance', 'top_speed', 'sprints', 'passes', 'touches']
      .map(k => [k, rankWithin(mates.map(x => ({ id: x.id, v: x.rate[k] })), 'v')(r.id)]));
    const isTop = k => ranks[k] != null && ranks[k] <= top;
    const isBottom = k => ranks[k] != null && rankable.length >= 4 && ranks[k] > rankable.length - top;
    const tries = r.accuracy ? r.passes / (r.accuracy / 100) : 0;

    const tags = r.brief ? ['brief track'] : [
      isTop('distance') && 'high work rate',
      (ranks.top_speed === 1 || r.top_speed >= 30) && 'top-end pace',
      isTop('sprints') && r.sprints >= 3 && 'repeat sprinter',
      isTop('touches') && 'on the ball a lot',
      isTop('passes') && r.passes >= 5 && 'link player',
      r.accuracy != null && r.accuracy >= 85 && r.passes >= 8 && 'tidy passer',
      r.accuracy != null && r.accuracy < 60 && tries >= 8 && 'loose in possession',
      isBottom('touches') && !isTop('passes') && 'low involvement',
    ].filter(Boolean).slice(0, MAX_TAGS);

    const { rate, brief, ...rest } = r;
    return { ...rest, ranks, team_size: mates.length, tags };
  });
}

function timelineFacts(events) {
  const lost = { home: {}, away: {} };
  for (const e of events) {
    if (e.type !== 'won' || !e.team) continue;
    const loser = e.team === 'home' ? 'away' : 'home';
    const spell = Math.floor(e.t / SPELL_SECS);
    lost[loser][spell] = (lost[loser][spell] || 0) + 1;
  }
  const spells = side => Object.entries(lost[side]).map(([s, n]) => ({ from_min: s * 5, to_min: s * 5 + 5, lost: n })).sort((a, b) => a.from_min - b.from_min);
  const worst = side => spells(side).reduce((w, s) => (!w || s.lost > w.lost ? s : w), null);

  // Longest run of passes by one side before the other side touched it
  let best = null, run = null;
  for (const e of events) {
    if (e.type === 'pass' && e.team && run && run.team === e.team) run.passes += 1;
    else if (e.type === 'pass' && e.team) run = { team: e.team, passes: 1, t: e.t };
    else if (e.type === 'won') run = null;
    if (run && (!best || run.passes > best.passes)) best = { ...run };
  }

  // What each side did in every 5-minute spell: the outline the commentary is written from
  const bySpell = {};
  for (const e of events) {
    if (!e.team || !['pass', 'won', 'strike'].includes(e.type)) continue;
    const s = Math.floor(e.t / SPELL_SECS);
    const row = bySpell[s] || (bySpell[s] = { from_min: s * 5, to_min: s * 5 + 5, pass: { home: 0, away: 0 }, won: { home: 0, away: 0 }, strike: { home: 0, away: 0 } });
    row[e.type][e.team] += 1;
  }

  return {
    by_spell: Object.values(bySpell).sort((a, b) => a.from_min - b.from_min)
      .map(({ pass, won, strike, ...r }) => ({ ...r, passes: pass, balls_won: won, strikes: strike })),
    possession_lost: { home: spells('home'), away: spells('away') },
    worst_spell: { home: worst('home'), away: worst('away') },
    strikes: events.filter(e => e.type === 'strike' && e.team).slice(0, MAX_STRIKES_LISTED).map(e => ({ minute: clock(e.t), team: e.team, ball_kmh: e.value })),
    longest_move: best && { team: best.team, passes: best.passes, starts: clock(best.t) },
  };
}

function qualityFacts(engine, hasEvents, players) {
  const notes = [], levels = [];
  const flag = (level, note) => { levels.push(level); notes.push(note); };
  const ball = num(engine.ball && engine.ball.visible_pct), mins = num(engine.duration_s) / 60;

  if (ball && ball < BALL_POOR) flag('poor', `The ball was visible only ${ball}% of the time, so possession and passing numbers are rough.`);
  else if (ball && ball < BALL_FAIR) flag('fair', `The ball was visible ${ball}% of the time; on-ball numbers may miss some moments.`);
  if (engine.kit_learned === false) flag('fair', 'The model did not learn the two kit colours, so some players may be on the wrong team.');
  if (mins && mins < SHORT_CLIP_MIN) flag('fair', `Only ${round(mins)} minutes of video: a small sample.`);
  if (num(engine.swaps_fixed) > 0) flag('good', `Player IDs swapped ${engine.swaps_fixed} times and were corrected; a track can still mix two players.`);
  if (!hasEvents) flag('fair', 'No event log was saved for this match, so there is no timeline (when things happened).');
  const brief = players.filter(p => p.tags[0] === 'brief track').length;
  if (brief) flag('good', `${brief} track(s) were on screen under ${BRIEF_SECS / 60} minutes and are left out of rankings.`);

  const level = levels.includes('poor') ? 'poor' : levels.includes('fair') ? 'fair' : 'good';
  return { level, ball_visible_pct: ball || null, notes };
}

/* Saved analyses come from browsers, so everything is whitelisted and clamped here,
   before it can reach a prompt or a tool result: sizes bounded, types checked, unknown fields dropped */
const CLAMP = { name: 40, stats: 40, statName: 40, players: 60, events: 3000, maxSecs: 4 * 3600 };
const SIDES = new Set(['home', 'away']);
const EVENT_TYPES = new Set(['pass', 'won', 'strike', 'sprint']);
const TRACK = /^t\d{1,10}$/;   // the engine's track ids, e.g. t12 or t10000001
const finite = v => v !== null && v !== '' && Number.isFinite(+v);
const cleanName = (v, fallback) => String(v ?? '').replace(/[\u0000-\u001f<>{}]/g, '').trim().slice(0, CLAMP.name) || fallback;
const cleanStat = v => String(v).replace(/[\u0000-\u001f]/g, '').trim().slice(0, CLAMP.statName) || 'Stat';   // keeps ">" as in "Sprints >25 km/h"

function clean(match) {
  const stats = Object.entries(match.stats && typeof match.stats === 'object' ? match.stats : {})
    .filter(([, v]) => Array.isArray(v) && v.length === 2 && v.every(finite))
    .slice(0, CLAMP.stats).map(([k, v]) => [cleanStat(k), v.map(Number)]);
  const players = (Array.isArray(match.players) ? match.players : [])
    .filter(p => p && TRACK.test(p.player_key) && SIDES.has(p.team)).slice(0, CLAMP.players);
  const ctx = match.context && typeof match.context === 'object' ? match.context : {};
  const events = (Array.isArray(ctx.events) ? ctx.events : [])
    .filter(e => e && EVENT_TYPES.has(e.type) && finite(e.t) && +e.t >= 0 && +e.t <= CLAMP.maxSecs).slice(0, CLAMP.events)
    .map(e => ({ t: +e.t, type: e.type, team: SIDES.has(e.team) ? e.team : null, player: TRACK.test(e.player) ? e.player : null,
      other: TRACK.test(e.other) ? e.other : null, value: finite(e.value) ? +e.value : null }));
  const secs = Object.fromEntries(Object.entries(ctx.secs && typeof ctx.secs === 'object' ? ctx.secs : {})
    .filter(([k, v]) => TRACK.test(k) && finite(v)).slice(0, CLAMP.players).map(([k, v]) => [k, +v]));
  const engine = match.engine && typeof match.engine === 'object' ? match.engine : {};
  return { home: cleanName(match.home, 'Home'), away: cleanName(match.away, 'Away'), stats: Object.fromEntries(stats), players, engine, events, secs };
}

export function buildFacts(raw) {
  const match = clean(raw);
  const names = { home: match.home, away: match.away };
  const engine = match.engine, context = { secs: match.secs };
  const events = [...match.events].sort((a, b) => a.t - b.t);
  const comparison = compare(match.stats || {});
  const lead = { on_ball: groupLeader(comparison, 'possession'), work_rate: groupLeader(comparison, 'physical'),
    attacking: groupLeader(comparison, 'attacking'), defending: groupLeader(comparison, 'defending') };
  const players = playerFacts(match.players, context.secs);
  const has = new Set(comparison.map(c => c.stat));

  return {
    teams: names,
    duration_min: engine.duration_s ? Math.round(num(engine.duration_s) / 60) : null,
    tracked: [...has],
    not_tracked: CATALOG.map(d => d.stat).filter(s => !has.has(s)),
    visuals: VISUALS,
    comparison,
    summary: { ...lead, headline: headline(names, lead) },
    players,
    timeline: timelineFacts(events),
    events,
    quality: qualityFacts(engine, events.length > 0, players),
  };
}
