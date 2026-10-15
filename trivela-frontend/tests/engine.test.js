// Run: npm test
const test = require('node:test');
const assert = require('node:assert/strict');
const { toReport, normalizeBase, phaseStep, STAT_DEFS } = require('../landing/assets/js/engine.js');

// Summary shaped like the engine's /api/summary. Team codes: 0 = darker kit (A), 1 = lighter kit (B), 2 = GK, 3 = official
const summary = () => ({
  fps: 25, total_frames: 3000, tracker: 'BoT-SORT', mode: 'balanced', kit_learned: true,
  team_colors: { team_a: '#1a2b3c', team_b: '#f0f0f0', goalkeeper: '#d946ef', official: '#facc15' },
  team_info: { 1: 0, 2: 0, 3: 1, 4: 1, 5: 2, 6: 3, 7: 0 },
  events: [
    [10, 'pass', 2, 0, 1], [20, 'pass', 1, 0, 2], [30, 'won', 3, 1, 1], [40, 'pass', 4, 1, 3],
    [50, 'strike', -1, 1, 88.2], [60, 'strike', -1, 0, 91.0], [70, 'sprint', 1, 0, 27.4], [80, 'strike', -1, -1, 80],
  ],
  stats: {
    possession_a: 58.4, possession_b: 41.6, passes_a: 2, passes_b: 1, gains_a: 0, gains_b: 1, pass_acc_a: 66.7, pass_acc_b: 100,
    ball_visible_pct: 71.2, ball_max_kmh: 91, ball_avg_kmh: 22.5, strikes: 3, sprints: 1, swaps_fixed: 2, wide_frames: 2800, wide_pct: 93.3,
    players: {
      1: { touches: 5, on_ball_s: 4, dist_m: 410, top_kmh: 29.1, sprints: 2, secs: 110 },
      2: { touches: 3, on_ball_s: 2, dist_m: 300, top_kmh: 26.0, sprints: 1, secs: 100 },
      3: { touches: 4, on_ball_s: 3, dist_m: 350, top_kmh: 31.5, sprints: 3, secs: 90 },
      4: { touches: 2, on_ball_s: 1, dist_m: 200, top_kmh: 24.0, sprints: 0, secs: 80 },
      5: { touches: 1, on_ball_s: 1, dist_m: 50, top_kmh: 12.0, sprints: 0, secs: 120 },
      6: { touches: 0, on_ball_s: 0, dist_m: 500, top_kmh: 20.0, sprints: 0, secs: 120 },
      7: { touches: 0, on_ball_s: 0, dist_m: 10, top_kmh: 8.0, sprints: 0, secs: 1.2 },
    },
  },
});

test('maps team A to home when the home side wears the darker kit', () => {
  const r = toReport(summary(), 'a');
  assert.deepEqual(r.stats.Possession, [58, 42]);
  assert.deepEqual(r.stats.Passes, [2, 1]);
  assert.deepEqual(r.stats['Pass accuracy'], [67, 100]);
  assert.deepEqual(r.stats['Balls won'], [0, 1]);
  assert.deepEqual(r.stats.Strikes, [1, 1]); // the team -1 strike is not counted for either side
  assert.deepEqual(r.stats.Touches, [8, 6]); // outfield tracks only (no GK, no official)
  assert.deepEqual(r.stats['Distance covered'], [0.72, 0.55]);
  assert.deepEqual(r.stats['Top speed'], [29.1, 31.5]);
  assert.deepEqual(r.stats['Sprints >25 km/h'], [3, 3]);
});

test('swaps every pair when the home side wears the lighter kit', () => {
  const a = toReport(summary(), 'a').stats, b = toReport(summary(), 'b').stats;
  for (const k of Object.keys(a)) assert.deepEqual(b[k], [a[k][1], a[k][0]], k);
});

test('player rows: outfield tracks of at least 3 s, with passes and accuracy from events', () => {
  const rows = toReport(summary(), 'a').players;
  assert.deepEqual(rows.map(p => p.player_key), ['t1', 't2', 't3', 't4']); // sorted by time on screen; GK 5, ref 6, short track 7 left out
  const p1 = rows[0];
  assert.equal(p1.team, 'home');
  assert.equal(p1.jersey, null);
  assert.equal(p1.distance, 0.41);
  assert.equal(p1.top_speed, 29.1);
  assert.equal(p1.passes, 1);         // event [20,'pass',2,0,1]: passer 1
  assert.equal(p1.accuracy, 50);      // 1 completed, 1 lost ([30,'won',3,1,1])
  assert.equal(rows[2].team, 'away');
  assert.equal(rows[3].accuracy, null); // track 4 never passed
});

test('engine info keeps what the report shows besides the stats', () => {
  const e = toReport(summary(), 'b', 'https://x.trycloudflare.com').engine;
  assert.equal(e.base, 'https://x.trycloudflare.com');
  assert.equal(e.duration_s, 120);
  assert.deepEqual(e.kit, { home: '#f0f0f0', away: '#1a2b3c' });
  assert.equal(e.ball.max_kmh, 91);
  assert.equal(e.tracks, 5); // every outfield track, including ones too short for the player table
  assert.equal(e.home_kit, 'b');
});

test('context keeps the event log in seconds and home/away sides, for the AI coach', () => {
  const { events, secs } = toReport(summary(), 'b').context;   // home wears the lighter kit: engine team 1 = home
  assert.deepEqual(events[0], { t: 0.4, type: 'pass', team: 'away', player: 't1', other: 't2', value: null }); // passer, then receiver
  assert.deepEqual(events[2], { t: 1.2, type: 'won', team: 'home', player: 't3', other: 't1', value: null });   // winner, then loser
  assert.deepEqual(events[4], { t: 2, type: 'strike', team: 'home', player: null, other: null, value: 88.2 });   // ball speed
  assert.deepEqual(events[6], { t: 2.8, type: 'sprint', team: 'away', player: 't1', other: null, value: 27.4 });
  assert.equal(events[7].team, null);                       // a strike by no side
  assert.deepEqual(secs, { t1: 110, t2: 100, t3: 90, t4: 80 }); // same tracks as the player table
});

test('copes with an empty or partial summary', () => {
  const r = toReport({ stats: {} }, 'a');
  assert.deepEqual(r.stats.Possession, [0, 0]);
  assert.deepEqual(r.players, []);
  assert.deepEqual(r.context, { events: [], secs: {} });
  assert.equal(r.engine.maps, null);
  assert.deepEqual(r.engine.players, {});
});

// The engine's match-analytics pass (pitch mapping): team stats, per-player rows and pitch maps
const HEAT_A = Array.from({ length: 6 }, (_, i) => i * 10);   // a 3x2 grid, row-major
const withAnalytics = () => {
  const s = summary();
  Object.assign(s.stats, {
    shots_a: 3, shots_b: 1, shots_on_a: 2, shots_on_b: 0, xg_a: 0.734, xg_b: 0.08, tackles_a: 4, tackles_b: 6,
    interceptions_a: 2, interceptions_b: 1, clearances_a: 0, clearances_b: 3, dispossessed_a: 6, dispossessed_b: 4,
    dribbles_a: 1, dribbles_b: 0, crosses_a: 2, crosses_b: 1, crosses_ok_a: 1, crosses_ok_b: 0, box_touches_a: 5, box_touches_b: 1,
    saves_a: 0, saves_b: 2, corners_a: 2, corners_b: 0, free_kicks_a: 1, free_kicks_b: 2, long_balls_a: 3, long_balls_b: 1,
    long_balls_att_a: 5, long_balls_att_b: 4, final_third_s_a: 93, final_third_s_b: 30, fastest_strike_a: 96.4, fastest_strike_b: 71,
    def_actions_a: 6, def_actions_b: 10, sprint_kmh_a: 27.9, sprint_kmh_b: 26.4,
  });
  Object.assign(s.stats.players[1], { shots: 2, shots_on: 1, xg: 0.51, tackles: 1, interceptions: 0, final_third_s: 12.5, heat: [0, 99, 0, 0, 0, 40] });
  s.maps = {
    heat_size: [3, 2], player_heat_size: [3, 2],
    heat: { a_all: HEAT_A, a_on: HEAT_A, a_off: HEAT_A, b_all: [99, 0, 0, 0, 0, 0], b_on: [0, 0, 0, 0, 0, 0], b_off: [99, 0, 0, 0, 0, 0] },
    passes: [[20, 30, 60, 30, 0], [80, 10, 70, 12, 1], [1, 1, 2, 2, 3]],
    shots: [[94, 34, 0, 1, 0.31, 88], [20, 40, 1, 0, 0.05, 70]],
  };
  s.pitch = { estimated: true, anchored_pct: 62.5, flipped: false };
  return s;
};

test('analytics stats are mapped to home/away, rounded, and listed in report order', () => {
  const st = toReport(withAnalytics(), 'a').stats;
  assert.deepEqual(st.Shots, [3, 1]);
  assert.deepEqual(st['Shots on target'], [2, 0]);
  assert.deepEqual(st.xG, [0.73, 0.08]);
  assert.deepEqual(st.Saves, [0, 2]);
  assert.deepEqual(st['Long balls'], [5, 4]);                 // attempted long balls
  assert.deepEqual(st['Time in final third'], [1.6, 0.5]);     // seconds -> minutes
  assert.deepEqual(st['Avg sprint speed'], [27.9, 26.4]);
  const sw = toReport(withAnalytics(), 'b').stats;
  assert.deepEqual(sw.Shots, [1, 3]);
  assert.deepEqual(sw.Saves, [2, 0]);
  const order = STAT_DEFS.map(d => d.k).filter(k => k in st);
  assert.deepEqual(Object.keys(st), order);
});

test('without the analytics pass the report leaves those stats out instead of showing zeros', () => {
  const st = toReport(summary(), 'a').stats;
  for (const k of ['Shots', 'xG', 'Tackles', 'Saves', 'Corners', 'Time in final third']) assert.equal(k in st, false, k);
  assert.equal(Object.keys(st).length, 9);
});

test('pitch maps: home attacks left to right, so team B as home is turned 180 degrees', () => {
  const a = toReport(withAnalytics(), 'a').engine.maps;
  assert.deepEqual(a.size, [3, 2]);
  assert.deepEqual(a.heat.home.all, HEAT_A);
  assert.deepEqual(a.passes, [[20, 30, 60, 30, 'home'], [80, 10, 70, 12, 'away']]);   // the official's (team 3) pass is dropped
  assert.deepEqual(a.shots[0], { x: 94, y: 34, team: 'home', on: true, xg: 0.31, kmh: 88 });
  assert.equal(a.anchored_pct, 62.5);
  const b = toReport(withAnalytics(), 'b').engine.maps;
  assert.deepEqual(b.heat.home.all, [99, 0, 0, 0, 0, 0].reverse());
  assert.deepEqual(b.heat.away.all, [...HEAT_A].reverse());
  assert.deepEqual(b.passes[0], [85, 38, 45, 38, 'away']);
  assert.deepEqual(b.shots[1], { x: 85, y: 28, team: 'home', on: false, xg: 0.05, kmh: 70 });
});

test('the summary passed in is never modified', () => {
  const s = withAnalytics(), before = JSON.stringify(s);
  toReport(s, 'b');
  assert.equal(JSON.stringify(s), before);
});

test('per-player analytics and heat grid are kept in engine.players', () => {
  const e = toReport(withAnalytics(), 'b').engine;
  assert.deepEqual(e.players.t1, { shots: 2, shots_on: 1, xg: 0.51, tackles: 1, interceptions: 0, final_third_s: 12.5, on_ball_s: 4, heat: [40, 0, 0, 0, 99, 0] });
  assert.deepEqual(e.players.t2, { on_ball_s: 2 });               // no analytics numbers for track 2, only time on the ball
  assert.equal('t5' in e.players, false);                        // goalkeepers aren't listed
});

test('on- and off-the-ball heat grids are kept and turned with the rest, for players and keepers', () => {
  const s = withAnalytics();
  Object.assign(s.stats.players[1], { heat_on: [0, 99, 0, 0, 0, 0], heat_off: [0, 0, 0, 0, 0, 99] });
  Object.assign(s.stats.players[5], { gk_side: 0, heat_on: [99, 0, 0, 0, 0, 0] });
  const b = toReport(s, 'b').engine;   // home wears the lighter kit: grids turn 180 degrees
  assert.deepEqual([b.players.t1.heat_on, b.players.t1.heat_off], [[0, 0, 0, 0, 99, 0], [99, 0, 0, 0, 0, 0]]);
  assert.deepEqual(b.keepers[0].heat_on, [0, 0, 0, 0, 0, 99]);
  assert.deepEqual(toReport(s, 'a').engine.players.t1.heat_on, [0, 99, 0, 0, 0, 0]);
});

test('keepers: one per side from the analytics pass, kept apart from the outfield rows', () => {
  const s = withAnalytics();
  Object.assign(s.stats.players[5], { gk_side: 1, saves: 2, long_balls_att: 3 });   // keeper 5 stands in team B's goal
  s.team_info[8] = 2; s.stats.players[8] = { touches: 1, dist_m: 20, top_kmh: 9, sprints: 0, secs: 40, gk_side: 1, saves: 0 };   // a shorter track of the same goal
  s.team_info[9] = 2; s.stats.players[9] = { touches: 1, dist_m: 20, top_kmh: 9, sprints: 0, secs: 90 };   // no side: older engine
  const a = toReport(s, 'a');
  assert.deepEqual(a.engine.keepers.map(k => [k.player_key, k.team, k.pos, k.saves, k.long_balls_att]), [['t5', 'away', 'GK', 2, 3]]);
  assert.ok(!a.players.some(p => p.player_key === 't5'));
  assert.equal(toReport(s, 'b').engine.keepers[0].team, 'home');   // B is home when home wears the lighter kit
  assert.deepEqual(toReport(summary(), 'a').engine.keepers, []);
});

test('player rows count the duels each player won', () => {
  const rows = toReport(summary(), 'a').players;
  assert.equal(rows.find(p => p.player_key === 't3').won, 1);   // [30,'won',3,1,1]: 3 won it from 1
  assert.equal(rows.find(p => p.player_key === 't1').won, 0);
});

test('normalizeBase accepts a pasted tunnel URL and rejects anything else', () => {
  assert.equal(normalizeBase(' https://abc-def.trycloudflare.com/ '), 'https://abc-def.trycloudflare.com');
  assert.equal(normalizeBase('abc-def.trycloudflare.com'), 'https://abc-def.trycloudflare.com');
  assert.equal(normalizeBase('http://localhost:8000/'), 'http://localhost:8000');
  assert.equal(normalizeBase('localhost:8765'), 'http://localhost:8765');   // a local server has no TLS
  assert.equal(normalizeBase('127.0.0.1:8765/api'), 'http://127.0.0.1:8765');
  assert.equal(normalizeBase('javascript:alert(1)'), null);
  assert.equal(normalizeBase(''), null);
});

test('phaseStep places engine progress on the five processing steps', () => {
  assert.equal(phaseStep({ status: 'scanning', progress: 0 }), 0.05);
  assert.equal(phaseStep({ status: 'processing', progress: 50 }), 0.15 + 0.8 * 0.5);
  assert.equal(phaseStep({ status: 'finalizing', progress: 99.9 }), 0.97);
  assert.equal(phaseStep({ status: 'completed', progress: 100 }), 1);
});

test('player rows carry the numbers drawn in the video', () => {
  const rows = toReport({ ...summary(), player_no: { 1: 3, 2: 1, 3: 2 } }, 'a').players;
  const no = Object.fromEntries(rows.map(r => [r.player_key, r.jersey]));
  assert.equal(no.t1, 3); assert.equal(no.t2, 1); assert.equal(no.t3, 2);
  assert.equal(no.t4, null);   // no number from the engine (older summaries): the app falls back to the track id
});
