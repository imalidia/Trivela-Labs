// Shows what the AI coach understands about a match and the exact request it would send. No API call, no cost.
//   npm run coach:demo                         -> the facts worked out from the sample match
//   npm run coach:demo -- report               -> the request for a feature (report | scouting | tips | commentary)
//   npm run coach:demo -- chat "who ran the most?"
//   npm run coach:demo -- facts path/to/match.json   (a saved match: { home, away, stats, engine, players, context })
import { readFileSync } from 'node:fs';
import { buildFacts } from '../coach/lib/facts.mjs';
import { checkQuestion } from '../coach/lib/guard.mjs';
import { FEATURES, buildRequest } from '../coach/lib/prompts.mjs';

const SAMPLE = new URL('./sample-match.json', import.meta.url);
const [mode = 'facts', arg] = process.argv.slice(2);

function load(path) {
  try { return JSON.parse(readFileSync(path, 'utf8')); } catch (err) {
    console.error(`Could not read ${path}: ${err.message}`);
    process.exit(1);
  }
}

const facts = buildFacts(load(mode === 'facts' && arg ? arg : SAMPLE));
const show = (title, value) => console.log(`\n== ${title}\n${typeof value === 'string' ? value : JSON.stringify(value, null, 2)}`);

if (mode === 'facts') {
  show('Headline', facts.summary.headline);
  show('Comparison', facts.comparison.map(c => `${c.stat.padEnd(26)} ${String(c.home).padStart(7)} ${String(c.away).padStart(7)}   ${c.verdict}${c.leader === null ? ' (not judged)' : c.leader === 'level' ? '' : ' for ' + c.leader}`).join('\n'));
  show('Players', facts.players.map(p => `${p.label.padEnd(9)} ${p.team.padEnd(5)} ${String(p.minutes ?? '?').padStart(5)} min   ${p.tags.join(', ') || '-'}`).join('\n'));
  show('Timeline', facts.timeline);
  show('Data quality', facts.quality);
  show('Not tracked for this match', facts.not_tracked.join(', '));
} else if (mode === 'chat') {
  const check = checkQuestion(arg);
  if (!check.ok) show(`Blocked in code (${check.code}), no model call`, check.message);
  else show('Request', { ...buildRequest('chat', facts, { question: check.text }), system: '(system prompt, cached)', tools: '(7 tools)' });
} else if (FEATURES[mode]) {
  const req = buildRequest(mode, facts);
  show('System prompt (cached, same for every request)', req.system[0].text);
  show('Request', { ...req, system: '(above)' });
} else {
  console.error(`Unknown mode "${mode}". Use: facts | ${Object.keys(FEATURES).join(' | ')}`);
  process.exit(1);
}
