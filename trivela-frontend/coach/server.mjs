// AI coach service. POST /api/coach { match_id, feature, question?, chat_id?, refresh?, match } -> { success, data, error }
// Runs in Docker next to the engine (CV/docker-compose.yml), which is its only caller: the engine reads the saved analysis,
// adds it as `match`, and passes the caller's address in X-Visitor. The browser never writes a prompt (ai-rules.md rule 13).
// Holds the model API key (coach/.env). Limits, cached write-ups and chat history live in memory, so a restart resets them.
//
// Dev without Docker:  npm run coach:dev  -> http://localhost:5600, the site plus a stand-in engine with the sample match.
import http from 'node:http';
import { createHash, randomUUID } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { buildFacts } from './lib/facts.mjs';
import { checkQuestion, LIMITS } from './lib/guard.mjs';
import { FEATURES } from './lib/prompts.mjs';
import { createCoach, CoachError, OFF_TOPIC, toReply } from './lib/run.mjs';

const SITE = process.argv.includes('--site');
const env = { ...readEnvFile(new URL('.env', import.meta.url)), ...process.env };   // the real environment (Docker env_file) wins
const PORT = Number(env.PORT || 5600), HOST = SITE ? '127.0.0.1' : '0.0.0.0';
const MAX_BODY = 1024 * 1024;                     // a saved match with its event log fits easily
const ESTIMATE_TOKENS = 8000;                     // a running model call counts as this many tokens until it settles
const DAILY_TOKEN_BUDGET = Number(env.AI_DAILY_TOKEN_BUDGET || 2_000_000);
const MAX_CACHED = 300, MAX_CHATS = 2000;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const LIMIT = {
  burst: [429, 'Slow down a little. Try again in a minute.'],
  daily: [429, 'That\'s today\'s limit for this. Come back tomorrow.'],
  visitor_tokens: [429, 'You\'ve used today\'s AI allowance. Come back tomorrow.'],
  budget: [503, 'The AI coach is resting for today. It will be back tomorrow.'],
};

function readEnvFile(url) {
  try {
    return Object.fromEntries(readFileSync(url, 'utf8').split(/\r?\n/).filter(l => /^\s*[A-Z_]+=/.test(l))
      .map(l => { const i = l.indexOf('='); return [l.slice(0, i).trim(), l.slice(i + 1).trim()]; }));
  } catch { return {}; }
}

const ready = !!(env.AI_API_KEY && env.AI_BASE_URL);
const model = env.AI_MODEL || 'Gemma-4-31B-IT';
const coach = createCoach({
  api: { baseUrl: env.AI_BASE_URL, apiKey: env.AI_API_KEY },
  models: { model, chatModel: env.AI_CHAT_MODEL || model, topicModel: env.AI_TOPIC_MODEL || model, fallback: env.AI_FALLBACK_MODEL || null },
  log: (m, d) => console.error(m, d),
});
const sha = text => createHash('sha256').update(text).digest('hex').slice(0, 32);
const capped = (map, max) => { while (map.size > max) map.delete(map.keys().next().value); };

/* ---------- limits (rule 13): per visitor per minute and per day, plus one daily token budget for everyone ---------- */
/* A visitor is a salted hash of their address: without logins a determined caller can rotate it; the budget is the hard cap */
const today = () => new Date().toISOString().slice(0, 10);
let day = today(), spent = 0, pending = 0;
const visitors = new Map();
function visitor(address) {
  if (day !== today()) { day = today(); spent = 0; visitors.clear(); }
  const id = sha((env.AI_VISITOR_SALT || env.AI_API_KEY || '') + address);
  if (!visitors.has(id)) visitors.set(id, { id, recent: [], chat: 0, fresh: 0, refresh: 0, tokens: 0 });
  return visitors.get(id);
}
function burst(v) {
  const now = Date.now();
  v.recent = v.recent.filter(t => now - t < 60_000);
  if (v.recent.length >= LIMITS.perMinute) throw new CoachError(...LIMIT.burst);
  v.recent.push(now);
}
/* Books a model call before it runs and settles it with the real tokens afterwards, also when it fails */
async function booked(v, kind, work) {
  const cap = { chat: LIMITS.dailyQuestions, fresh: LIMITS.dailyFresh, refresh: LIMITS.dailyGenerations }[kind];
  if (v[kind] >= cap) throw new CoachError(...LIMIT.daily);
  if (v.tokens >= LIMITS.dailyVisitorTokens) throw new CoachError(...LIMIT.visitor_tokens);
  if (spent + pending + ESTIMATE_TOKENS > DAILY_TOKEN_BUDGET) throw new CoachError(...LIMIT.budget);
  v[kind] += 1; pending += ESTIMATE_TOKENS;
  const usage = { input: 0, output: 0 };
  try { return await work(usage); } finally {
    pending -= ESTIMATE_TOKENS;
    const used = usage.input + usage.output;
    v.tokens += used; spent += used;
    console.log(`${kind}: ${usage.input} in / ${usage.output} out tokens, visitor ${v.id.slice(0, 8)}, today ${spent}`);   // no question text
  }
}

/* ---------- features ---------- */
const cache = new Map();   // `${facts hash}:${feature}` -> write-up: generated once per match, then free
const chats = new Map();   // chat id -> { visitor, facts, turns }: history lives here, so nobody can feed the model a made-up conversation

async function writeUp(v, facts, feature, refresh) {
  const key = `${sha(JSON.stringify(facts))}:${feature}`;
  if (!refresh && cache.has(key)) return { ...cache.get(key), cached: true };
  const output = await booked(v, refresh ? 'refresh' : 'fresh', usage => coach.generate(feature, facts, usage));
  cache.set(key, output); capped(cache, MAX_CACHED);
  return { ...output, cached: false };
}

async function chat(v, facts, rawQuestion, chatId) {
  const check = checkQuestion(rawQuestion);   // blocked in code: costs nothing, not added to the history
  const factsKey = sha(JSON.stringify(facts));
  const prev = typeof chatId === 'string' ? chats.get(chatId) : null;
  const own = prev && prev.visitor === v.id && prev.facts === factsKey ? prev : null;
  if (!check.ok) return { answer: check.message, blocked: check.code, chat_id: own ? chatId : null };
  const turns = own ? own.turns : [];
  if (turns.length >= LIMITS.questionsPerChat) throw new CoachError(429, `This chat has reached ${LIMITS.questionsPerChat} questions. Start a new one.`);

  const result = await booked(v, 'chat', async usage =>
    (await coach.onTopic(check.text, usage)) ? { reply: await coach.answer(facts, turns, check.text, usage) } : { reply: OFF_TOPIC, offTopic: true });
  if (result.offTopic) return { answer: result.reply, blocked: 'off_topic', chat_id: own ? chatId : null };

  const id = own ? chatId : randomUUID(), next = [...turns, { q: check.text, a: result.reply }];
  chats.delete(id); chats.set(id, { visitor: v.id, facts: factsKey, turns: next }); capped(chats, MAX_CHATS);
  return { answer: result.reply, chat_id: id, questions_left: LIMITS.questionsPerChat - next.length };
}

async function handle(body, address) {
  if (!ready) throw new CoachError(503, 'The AI coach is not set up yet.');
  const feature = String(body.feature || '');
  if (!UUID.test(String(body.match_id || ''))) throw new CoachError(400, 'match_id must be a saved match id.');
  if (!(feature in FEATURES)) throw new CoachError(400, `feature must be one of: ${Object.keys(FEATURES).join(', ')}.`);
  if (!body.match || typeof body.match !== 'object') throw new CoachError(400, 'The match data is missing.');
  const v = visitor(address);
  burst(v);
  const facts = buildFacts(body.match);   // whitelists and clamps every field before it can reach a prompt
  return feature === 'chat' ? chat(v, facts, body.question, body.chat_id) : writeUp(v, facts, feature, body.refresh === true);
}

/* ---------- HTTP ---------- */
const send = (res, status, body, type = 'application/json') =>
  res.writeHead(status, { 'Content-Type': type, 'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff' }).end(body);

async function readJson(req) {
  let raw = '';
  for await (const chunk of req) { raw += chunk; if (raw.length > MAX_BODY) throw new CoachError(413, 'That request is too big.'); }
  try { return JSON.parse(raw); } catch { throw new CoachError(400, 'The request is not valid JSON.'); }
}

async function coachEndpoint(req, res, address, addMatch = body => body) {
  let status = 200, out;
  try {
    out = { success: true, data: await handle(addMatch(await readJson(req)), address), error: null };
  } catch (err) {
    if (!(err instanceof CoachError)) console.error('ai-coach failed', err);   // provider details stay in the log, never the key
    const r = toReply(err);
    status = r.status; out = { success: false, data: null, error: r.message };
  }
  send(res, status, JSON.stringify(out));
}

const site = SITE ? (await import('./dev-site.mjs')).createDevSite(fileURLToPath(new URL('../landing', import.meta.url))) : null;

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://localhost');
  if (req.method === 'GET' && url.pathname === '/health') return send(res, 200, JSON.stringify({ ok: true, ready }));
  if (req.method === 'POST' && url.pathname === '/api/coach') {
    if (site) return coachEndpoint(req, res, 'dev', site.addMatch);
    return coachEndpoint(req, res, String(req.headers['x-visitor'] || req.socket.remoteAddress || 'unknown'));
  }
  if (site) return site.handle(req, res, url, send);
  send(res, 404, JSON.stringify({ success: false, data: null, error: 'Not found.' }));
}).listen(PORT, HOST, () => {
  console.log(`AI coach on ${HOST}:${PORT}${ready ? ` (model ${model})` : ' - not set up: AI_API_KEY and AI_BASE_URL are empty'}`);
  if (site) console.log(`Dev site: http://localhost:${PORT}/dashboard.html  (the sample match is saved; the video engine is not running)`);
});
