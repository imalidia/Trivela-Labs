/* The model side of the coach, used by the coach service (coach/server.mjs) and the demo script,
   so both run exactly the same logic. No storage, no limits: the server does those. */
import { LIMITS } from './guard.mjs';
import { runTool } from './tools.mjs';
import { buildRequest, finishOutput } from './prompts.mjs';
import { chatCompletion, toChatBody, parseJson, LlmError } from './llm.mjs';

const RETRIES_429 = 1, BACKOFF_MS = 1500;   // the provider's limits are per key, shared by every visitor
const TOPIC_TIMEOUT_MS = 10_000;            // the on-topic check is skipped if the API is slow (the system prompt still refuses)
const ANSWER_TIMEOUT_MS = 45_000;           // past this the visitor sees "busy, try again" rather than waiting
export const OFF_TOPIC = "I only answer questions about this match's stats. Try: who ran the most?";

/* An error with a status and a message that is safe to show the user */
export class CoachError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

const TOPIC_SYSTEM = 'Decide whether the message is a question or request a football (soccer) match-statistics coach should answer: about this match\'s numbers, players, teams, tactics, or how to read a stat. Short follow-ups such as "why?", "and the other team?" or just a team or player name answering the previous question count as yes. General knowledge, other topics, coding, writing tasks, betting and anything else count as no.';
const TOPIC_SCHEMA = { type: 'object', properties: { about_the_match: { type: 'boolean' } }, required: ['about_the_match'], additionalProperties: false };
const sleep = ms => new Promise(r => setTimeout(r, ms));

/* api: { baseUrl, apiKey, fetchImpl? }. models: { model, chatModel, topicModel, fallback? }. log: (message, detail) => void */
export function createCoach({ api, models, log = () => {} }) {
  let toolsUnsupported = false;   // set once if the API rejects tool calling; chat then sends players and timeline in the facts

  /* The chosen model first; if its service is down or times out, the backup model (models.fallback) answers instead */
  async function callModel(model, req, usage, timeoutMs = ANSWER_TIMEOUT_MS) {
    try {
      return await callOnce(model, req, usage, timeoutMs);
    } catch (err) {
      const down = err instanceof LlmError && (err.status === 0 || err.status >= 500);
      if (!down || !models.fallback || models.fallback === model) throw err;
      log(`${model} unavailable, using ${models.fallback}`, err.message);
      return callOnce(models.fallback, req, usage, timeoutMs);
    }
  }

  async function callOnce(model, req, usage, timeoutMs) {
    for (let attempt = 0; ; attempt++) {
      try {
        const r = await chatCompletion({ ...api, timeoutMs }, toChatBody(model, req));
        usage.input += r.usage.input; usage.output += r.usage.output;
        return r;
      } catch (err) {
        if (err instanceof LlmError && err.status === 429 && attempt < RETRIES_429) { await sleep(BACKOFF_MS * (attempt + 1)); continue; }
        throw err;
      }
    }
  }

  const request = (feature, facts, opts) => {
    try { return buildRequest(feature, facts, opts); } catch (err) { throw new CoachError(422, err.message); }
  };

  /* Cheap gate against using the chat as a free general assistant. Fails open: the system prompt still refuses */
  async function onTopic(question, usage, last = null) {
    // a follow-up ("Trivela FC", "and the other team?") is judged with the question it answers
    const content = last ? `Previous question in this chat: ${String(last).slice(0, 300)}\nNew message: ${question}` : question;
    try {
      const r = await callModel(models.topicModel, { system: [{ text: TOPIC_SYSTEM }], messages: [{ role: 'user', content }],
        max_tokens: 30, output_config: { format: { type: 'json_schema', schema: TOPIC_SCHEMA } } }, usage, TOPIC_TIMEOUT_MS);
      return parseJson(r.text).about_the_match !== false;
    } catch (err) {
      log('on-topic check failed; answering anyway', err.message);
      return true;
    }
  }

  async function generate(feature, facts, usage) {
    const r = await callModel(models.model, request(feature, facts), usage);
    if (r.finish === 'content_filter') throw new CoachError(422, 'The coach could not write this one.');
    if (r.finish === 'length') throw new CoachError(502, 'The answer ran too long. Try again.');
    try { return finishOutput(feature, r.text); } catch { throw new CoachError(502, 'The coach gave an answer we could not read. Try again.'); }
  }

  async function answer(facts, history, question, usage) {
    let req = request('chat', facts, { history, question, tools: !toolsUnsupported });
    let messages = req.messages, calls = 0;
    for (;;) {
      const force = calls >= LIMITS.toolCalls;   // out of tool calls: answer with what it has
      let r;
      try {
        r = await callModel(models.chatModel, { ...req, messages, ...(force ? { tool_choice: 'none' } : {}) }, usage);
      } catch (err) {
        // Some models or servers reject tool definitions: fall back to answering from the fuller facts
        if (err instanceof LlmError && err.status === 400 && req.tools && calls === 0) {
          log('tool calling rejected; switching chat to facts-only', err.message);
          toolsUnsupported = true;
          req = request('chat', facts, { history, question, tools: false });
          messages = req.messages;
          continue;
        }
        throw err;
      }
      if (r.finish === 'content_filter') return OFF_TOPIC;
      if (!r.toolCalls.length || force || !req.tools) return r.text || 'I could not find that in this match\'s stats.';
      calls += r.toolCalls.length;
      messages = [...messages, r.message, ...r.toolCalls.map(c => ({ role: 'tool', tool_call_id: c.id, content: runTool(facts, c.name, c.input) }))];
    }
  }

  return { onTopic, generate, answer };
}

/* Any error -> { status, message } safe to send to the browser. Provider details stay in the server log */
export function toReply(err) {
  if (err instanceof CoachError) return { status: err.status, message: err.message };
  if (err instanceof LlmError) {
    const busy = err.status === 429 || err.status === 0;
    return { status: busy ? 503 : 502, message: busy ? 'The coach is busy. Try again in a minute.' : 'The coach could not answer right now.' };
  }
  return { status: 500, message: 'The coach could not answer right now.' };
}
