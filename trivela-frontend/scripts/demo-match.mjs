// Writes scripts/demo-match.json: the site's demo match (landing/assets/js/app.js sample data) as a saved analysis,
// so the engine can hand the AI coach the same numbers the demo page shows.   node scripts/demo-match.mjs
import { readFileSync, writeFileSync } from 'node:fs';

const src = readFileSync(new URL('../landing/assets/js/app.js', import.meta.url), 'utf8');
const data = src.slice(src.indexOf('/* ---------- Sample data'), src.indexOf('/* ---------- State'));
const { players, STATS, events } = new Function(`${data}; return { players, STATS, events };`)();

const MATCH_S = 90 * 60, CLIP_S = 180, SCALE = MATCH_S / CLIP_S;   // the demo clip stands in for the whole match
const key = p => `t${p.team === 'home' ? p.jersey : 100 + p.jersey}`;
const TYPE = { pass: 'pass', interception: 'won', shot: 'strike', goal: 'strike' };
const r1 = v => Math.round(v * 10) / 10;

const match = {
  home: 'Harbor City', away: 'Northgate United',
  stats: Object.fromEntries(STATS.filter(s => s.h != null).map(s => [s.k, [s.h, s.a]])),
  players: players.map(p => ({ player_key: key(p), team: p.team, jersey: p.jersey, pos: p.pos, distance: r1(p.distance), top_speed: r1(p.top),
    sprints: p.sprints, passes: p.passes, accuracy: p.acc, touches: p.touches, intensity: null })),
  engine: { home_kit: 'a', fps: 25, duration_s: MATCH_S, kit_learned: true, ball: { visible_pct: 92 }, tracks: players.length },
  context: {
    events: events.filter(e => TYPE[e.type]).map(e => ({ t: r1(e.t * SCALE), type: TYPE[e.type], team: e.team,
      player: e.from ? key(e.from) : null, other: e.to ? key(e.to) : null, value: null })),
    secs: Object.fromEntries(players.map(p => [key(p), MATCH_S])),
  },
};
writeFileSync(new URL('./demo-match.json', import.meta.url), JSON.stringify(match) + '\n');
console.log(`demo-match.json: ${match.players.length} players, ${match.context.events.length} events`);
