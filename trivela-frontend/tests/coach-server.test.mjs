import test from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const SERVER = fileURLToPath(new URL('../coach/server.mjs', import.meta.url));
const SAMPLE_ID = '00000000-0000-4000-8000-000000000001';
const PORT = 5600 + 1 + Math.floor(Math.random() * 300);
const base = `http://127.0.0.1:${PORT}`;

/* The dev site with no model key: everything but the model call works, and the coach says it isn't set up */
async function startServer() {
  const proc = spawn(process.execPath, [SERVER, '--site'], { env: { ...process.env, PORT: String(PORT), AI_API_KEY: '', AI_BASE_URL: '' }, stdio: 'ignore' });
  for (let i = 0; i < 50; i++) {
    try { if ((await fetch(`${base}/health`)).ok) return proc; } catch { /* not up yet */ }
    await new Promise(r => setTimeout(r, 100));
  }
  proc.kill();
  throw new Error('coach server did not start');
}

test('coach dev server: saved analyses, health and the coach endpoint', async t => {
  const proc = await startServer();
  t.after(() => proc.kill());

  const health = await (await fetch(`${base}/health`)).json();
  assert.deepEqual(health, { ok: true, ready: false });

  const list = await (await fetch(`${base}/api/analyses`)).json();
  assert.ok(list.some(r => r.id === SAMPLE_ID && r.home && r.stats), 'the sample match is listed');
  assert.ok(!('players' in list[0]), 'the list leaves out the heavy fields');

  const saved = await fetch(`${base}/api/analyses/${SAMPLE_ID}`);
  assert.equal(saved.status, 200);
  assert.equal((await fetch(`${base}/api/analyses/not-an-id`)).status, 400);

  const coach = await fetch(`${base}/api/coach`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ match_id: SAMPLE_ID, feature: 'report' }) });
  assert.equal(coach.status, 503);
  assert.deepEqual(await coach.json(), { success: false, data: null, error: 'The AI coach is not set up yet.' });

  const page = await fetch(`${base}/dashboard.html`);
  assert.equal(page.status, 200);
  assert.equal((await fetch(`${base}/..%2Fcoach%2F.env`)).status, 404, 'files outside landing/ are never served');
});
