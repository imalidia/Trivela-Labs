// Run: npm test. The OpenAI-compatible adapter, with fetch stubbed: no network, no key.
import test from 'node:test';
import assert from 'node:assert/strict';
import { toChatBody, chatCompletion, parseJson, LlmError } from '../coach/lib/llm.mjs';
import { buildFacts } from '../coach/lib/facts.mjs';
import { buildRequest } from '../coach/lib/prompts.mjs';

const facts = () => buildFacts({ home: 'Harbor City', away: 'Northgate', stats: { Possession: [60, 40] }, players: [], context: {} });
const reply = (body, status = 200) => async () => new Response(JSON.stringify(body), { status });
const ok = message => reply({ choices: [{ message, finish_reason: message.tool_calls ? 'tool_calls' : 'stop' }], usage: { prompt_tokens: 120, completion_tokens: 30 } });

test('toChatBody: system first, tools as functions, JSON mode for structured features', () => {
  const chat = toChatBody('Gemma-4-31B-IT', buildRequest('chat', facts(), { question: 'who ran most?' }));
  assert.equal(chat.model, 'Gemma-4-31B-IT');
  assert.equal(chat.messages[0].role, 'system');
  assert.match(chat.messages[0].content, /match coach/);
  assert.equal(chat.tools[0].type, 'function');
  assert.equal(chat.tools[0].function.name, 'get_team_comparison');
  assert.ok(chat.tools[0].function.parameters.properties);
  const tips = toChatBody('m', buildRequest('tips', facts()));
  assert.deepEqual(tips.response_format, { type: 'json_object' });
  assert.equal(tips.tools, undefined);
  assert.ok(tips.max_tokens > 0);
});

test('chatCompletion: returns text, tool calls and token usage', async () => {
  const r = await chatCompletion({ baseUrl: 'https://x/v1', apiKey: 'k', fetchImpl: ok({ content: 'Track 5 ran most.' }) }, { model: 'm', messages: [] });
  assert.deepEqual(r, { text: 'Track 5 ran most.', toolCalls: [], finish: 'stop', usage: { input: 120, output: 30 }, message: { role: 'assistant', content: 'Track 5 ran most.' } });
  const t = await chatCompletion({ baseUrl: 'https://x/v1', apiKey: 'k', fetchImpl: ok({ content: null, tool_calls: [{ id: 'c1', type: 'function', function: { name: 'get_player', arguments: '{"player":"t5"}' } }] }) }, { model: 'm', messages: [] });
  assert.deepEqual(t.toolCalls, [{ id: 'c1', name: 'get_player', input: { player: 't5' } }]);
});

test('chatCompletion: bad tool arguments become an empty input, not a crash', async () => {
  const t = await chatCompletion({ baseUrl: 'https://x/v1', apiKey: 'k', fetchImpl: ok({ content: null, tool_calls: [{ id: 'c1', function: { name: 'get_player', arguments: '{oops' } }] }) }, { model: 'm', messages: [] });
  assert.deepEqual(t.toolCalls[0].input, {});
});

test('chatCompletion: sends the key only as a bearer header, to <base>/chat/completions', async () => {
  let seen;
  const fetchImpl = async (url, init) => { seen = { url, auth: init.headers.Authorization, body: JSON.parse(init.body) }; return ok({ content: 'hi' })(); };
  await chatCompletion({ baseUrl: 'https://x/v1/', apiKey: 'secret', fetchImpl }, { model: 'm', messages: [] });
  assert.equal(seen.url, 'https://x/v1/chat/completions');
  assert.equal(seen.auth, 'Bearer secret');
  assert.ok(!JSON.stringify(seen.body).includes('secret'));
});

test('chatCompletion: provider errors keep the status and never echo the key', async () => {
  await assert.rejects(chatCompletion({ baseUrl: 'https://x/v1', apiKey: 'secret', fetchImpl: reply({ error: { message: 'tools not supported' } }, 400) }, { model: 'm', messages: [] }),
    e => e instanceof LlmError && e.status === 400 && /tools not supported/.test(e.message) && !e.message.includes('secret'));
  await assert.rejects(chatCompletion({ baseUrl: 'https://x/v1', apiKey: 'k', fetchImpl: async () => { throw new Error('net down'); } }, { model: 'm', messages: [] }),
    e => e instanceof LlmError && e.status === 0);
});

test('parseJson: accepts fenced or chatty JSON from models without a strict JSON mode', () => {
  assert.deepEqual(parseJson('{"tips":["a 1"]}'), { tips: ['a 1'] });
  assert.deepEqual(parseJson('```json\n{"tips":["a 1"]}\n```'), { tips: ['a 1'] });
  assert.deepEqual(parseJson('Sure! {"cards":[]} Hope that helps'), { cards: [] });
  assert.throws(() => parseJson('no json here'));
});

test('createCoach: when the main model is down, the backup model answers', async () => {
  const { createCoach } = await import('../coach/lib/run.mjs');
  const asked = [];
  const fetchImpl = async (url, init) => {
    const model = JSON.parse(init.body).model; asked.push(model);
    return model === 'Gemma-4-31B-IT'
      ? new Response(JSON.stringify({ error: { message: 'No available workers' } }), { status: 500 })
      : ok({ content: 'Harbor City had more of the ball.' })();
  };
  const logs = [];
  const coach = createCoach({ api: { baseUrl: 'https://x/v1', apiKey: 'k', fetchImpl },
    models: { model: 'Gemma-4-31B-IT', chatModel: 'Gemma-4-31B-IT', topicModel: 'Gemma-4-31B-IT', fallback: 'DeepSeek-V4-Flash' }, log: m => logs.push(m) });
  const out = await coach.generate('report', facts(), { input: 0, output: 0 });
  assert.equal(out.text, 'Harbor City had more of the ball.');
  assert.deepEqual(asked, ['Gemma-4-31B-IT', 'DeepSeek-V4-Flash']);
  assert.match(logs[0], /Gemma-4-31B-IT unavailable, using DeepSeek-V4-Flash/);
});

test('chat with tools: the player table still travels with the question', () => {
  const req = buildRequest('chat', facts(), { question: 'who ran most?' });
  assert.ok(req.tools);
  assert.match(req.messages.at(-1).content, /"players"/);
});

test('chat without tools: players and timeline go into the facts instead', () => {
  const req = buildRequest('chat', facts(), { question: 'q', tools: false });
  assert.equal(req.tools, undefined);
  assert.match(req.messages.at(-1).content, /"players"/);
  assert.match(req.messages.at(-1).content, /"timeline"/);
});
