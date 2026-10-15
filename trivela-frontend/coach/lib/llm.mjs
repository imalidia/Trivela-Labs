/* Talks to any OpenAI-compatible chat API (AMD's model API for Gemma, vLLM, and so on).
   prompts.mjs builds provider-neutral requests; toChatBody turns one into a /chat/completions body.
   The API key only ever travels in the Authorization header and never appears in errors. */

const TIMEOUT_MS = 60_000;
const ERROR_CHARS = 200;
const TEMPERATURE = 0.3;   // steady, factual wording

export class LlmError extends Error {
  constructor(status, message) { super(message); this.status = status; }   // status 0 = network or timeout
}

/* { system, messages, max_tokens, output_config?, tools? } -> a chat/completions request body */
export function toChatBody(model, req) {
  const system = (req.system || []).map(b => b.text).join('\n\n');
  const schema = req.output_config && req.output_config.format && req.output_config.format.schema;
  const messages = [{ role: 'system', content: system }, ...req.messages];
  if (schema) {
    // JSON mode alone doesn't tell the model the shape, so the schema goes at the end of the last user turn
    const last = messages[messages.length - 1];
    messages[messages.length - 1] = { ...last, content: `${last.content}\n\nReply with one JSON object only, matching this JSON schema: ${JSON.stringify(schema)}` };
  }
  return {
    model, messages, max_tokens: req.max_tokens, temperature: TEMPERATURE,
    ...(schema ? { response_format: { type: 'json_object' } } : {}),
    ...(req.tools ? { tools: req.tools.map(t => ({ type: 'function', function: { name: t.name, description: t.description, parameters: t.input_schema } })) } : {}),
    ...(req.tool_choice ? { tool_choice: req.tool_choice } : {}),
  };
}

const safeArgs = raw => { try { const v = JSON.parse(raw || '{}'); return v && typeof v === 'object' ? v : {}; } catch { return {}; } };

/* -> { text, toolCalls: [{ id, name, input }], finish, usage: { input, output }, message } (message: the assistant turn to append) */
export async function chatCompletion({ baseUrl, apiKey, fetchImpl = fetch, timeoutMs = TIMEOUT_MS }, body) {
  const ctrl = new AbortController(), timer = setTimeout(() => ctrl.abort(), timeoutMs);
  let res;
  try {
    res = await fetchImpl(`${String(baseUrl).replace(/\/+$/, '')}/chat/completions`, {
      method: 'POST', signal: ctrl.signal,
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` }, body: JSON.stringify(body),
    });
  } catch (err) {
    throw new LlmError(0, err && err.name === 'AbortError' ? 'The model API timed out.' : 'Could not reach the model API.');
  } finally { clearTimeout(timer); }

  let data = null;
  try { data = await res.json(); } catch { /* handled below */ }
  if (!res.ok) {
    const detail = (data && ((data.error && data.error.message) || data.message || data.detail)) || `HTTP ${res.status}`;
    throw new LlmError(res.status, String(detail).replaceAll(apiKey, '[key]').slice(0, ERROR_CHARS));
  }
  const choice = data && data.choices && data.choices[0];
  if (!choice || !choice.message) throw new LlmError(502, 'The model API sent an empty answer.');

  const m = choice.message;
  const calls = Array.isArray(m.tool_calls) ? m.tool_calls : [];
  return {
    text: String(m.content || '').trim(),
    toolCalls: calls.map(c => ({ id: c.id, name: c.function && c.function.name, input: safeArgs(c.function && c.function.arguments) })),
    finish: choice.finish_reason || 'stop',
    usage: { input: Number(data.usage && data.usage.prompt_tokens) || 0, output: Number(data.usage && data.usage.completion_tokens) || 0 },
    message: { role: 'assistant', content: m.content ?? null, ...(calls.length ? { tool_calls: calls } : {}) },
  };
}

/* Models without a strict JSON mode sometimes wrap JSON in ``` fences or a sentence; take the object out */
export function parseJson(text) {
  const s = String(text).trim().replace(/^```(?:json)?\s*/i, '').replace(/\s*```$/, '');
  try { return JSON.parse(s); } catch { /* fall through */ }
  const start = s.indexOf('{'), end = s.lastIndexOf('}');
  if (start !== -1 && end > start) return JSON.parse(s.slice(start, end + 1));
  throw new Error('No JSON object in the answer.');
}
