/* Chat tools: the model looks numbers up here instead of guessing (hard rule 1).
   Every tool reads the facts from facts.mjs; none writes anything or reaches the network. */
import { describe } from './catalog.mjs';
import { PLAYBOOK, TOPICS } from './knowledge.mjs';
import { clock } from './facts.mjs';

const MAX_EVENTS_SHOWN = 25, MAX_RANKED = 10;
const METRICS = { distance: 'distance_km', top_speed: 'top_speed', sprints: 'sprints', passes: 'passes', accuracy: 'accuracy', touches: 'touches' };
const nullable = (type, extra = {}) => ({ type: [type, 'null'], ...extra });
const schema = (properties = {}) => ({ type: 'object', properties, required: Object.keys(properties), additionalProperties: false });

export const TOOLS = [
  { name: 'get_team_comparison', strict: true,
    description: 'Home vs away numbers for this match, with who leads and by how much. Pass a stat name for one row, or null for every stat this match has.',
    input_schema: schema({ stat: nullable('string') }) },
  { name: 'get_player', strict: true,
    description: 'Everything measured for one tracked player: totals, minutes on screen, rank within their team and plain tags. Accepts "t12", "ID 12", "Track 12" or "12".',
    input_schema: schema({ player: { type: 'string' } }) },
  { name: 'rank_players', strict: true,
    description: 'Players ranked by one metric (totals). team null = both teams. Tracks under 3 minutes on screen are left out of "least".',
    input_schema: schema({
      metric: { type: 'string', enum: Object.keys(METRICS) }, team: nullable('string', { enum: ['home', 'away', null] }),
      order: { type: 'string', enum: ['most', 'least'] }, limit: { type: 'integer' } }) },
  { name: 'get_events', strict: true,
    description: 'The event log, filtered. Types: pass (player passed to other), won (team/player won the ball from other), strike (a hard kick; value = ball km/h), sprint (value = km/h). team = the side that made the event. Minutes are from the start of the video.',
    input_schema: schema({
      type: nullable('string', { enum: ['pass', 'won', 'strike', 'sprint', null] }), team: nullable('string', { enum: ['home', 'away', null] }),
      from_minute: nullable('number'), to_minute: nullable('number') }) },
  { name: 'get_timeline', strict: true,
    description: 'When things happened: possession lost per 5-minute spell, the worst spell for each side, every strike, and the longest passing move.',
    input_schema: schema() },
  { name: 'get_data_quality', strict: true,
    description: 'How reliable this match\'s numbers are (ball visibility, ID swaps, short clips) and which stats are not tracked for this match.',
    input_schema: schema() },
  { name: 'lookup_knowledge', strict: true,
    description: 'How a coach reads one stat: what it means, what to watch out for, and a coaching line.',
    input_schema: schema({ topic: { type: 'string', enum: TOPICS } }) },
];

const json = v => JSON.stringify(v);
const findPlayer = (facts, raw) => {
  const id = 't' + String(raw).replace(/^\s*(track|id|t|#)\s*/i, '').trim();
  return facts.players.find(p => p.id === id.toLowerCase());
};
const named = (facts, p) => ({ ...p, team: facts.teams[p.team] || p.team, side: p.team });

const RUN = {
  get_team_comparison: (facts, { stat }) => {
    if (stat == null) return json({ teams: facts.teams, rows: facts.comparison });
    const row = facts.comparison.find(c => c.stat === describe(stat).stat);
    return row ? json({ teams: facts.teams, row }) : json({ error: `${stat} is not tracked for this match.`, tracked: facts.tracked });
  },
  get_player: (facts, { player }) => {
    const p = findPlayer(facts, player);
    return p ? json(named(facts, p)) : json({ error: `${player} is not tracked in this match.`, players: facts.players.map(x => x.id) });
  },
  rank_players: (facts, { metric, team, order, limit }) => {
    const key = METRICS[metric];
    const n = Math.min(MAX_RANKED, Math.max(1, Math.round(+limit) || 3));
    const rows = facts.players
      .filter(p => (!team || p.team === team) && p[key] != null && !(order === 'least' && p.tags[0] === 'brief track'))
      .sort((a, b) => (order === 'least' ? a[key] - b[key] : b[key] - a[key]))
      .slice(0, n);
    return json(rows.map(p => ({ id: p.id, label: p.label, team: facts.teams[p.team], [metric]: p[key], minutes: p.minutes })));
  },
  get_events: (facts, { type, team, from_minute, to_minute }) => {
    if (!facts.events.length) return json({ error: 'No event log was saved for this match.' });
    const from = from_minute == null ? -Infinity : from_minute * 60, to = to_minute == null ? Infinity : to_minute * 60;
    const hits = facts.events.filter(e => (!type || e.type === type) && (!team || e.team === team) && e.t >= from && e.t < to);
    return json({ total: hits.length, shown: hits.slice(0, MAX_EVENTS_SHOWN).map(e => ({ ...e, minute: clock(e.t), team: facts.teams[e.team] || null })) });
  },
  get_timeline: facts => json({ teams: facts.teams, ...facts.timeline }),
  get_data_quality: facts => json({ ...facts.quality, not_tracked: facts.not_tracked, visuals: facts.visuals }),
  lookup_knowledge: (facts, { topic }) => (PLAYBOOK[topic] ? json(PLAYBOOK[topic]) : json({ error: `No notes on ${topic}.`, topics: TOPICS })),
};

/* -> a string for the tool_result block. Never throws: a bad call becomes an error the model can read */
export function runTool(facts, name, input) {
  const run = RUN[name];
  if (!run) return json({ error: `Unknown tool: ${name}` });
  try { return run(facts, input || {}); } catch (err) { return json({ error: `The ${name} tool failed: ${err.message}` }); }
}
