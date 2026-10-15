/* Dev only (npm run coach:dev): the site from landing/ plus a stand-in for the engine's saved-analyses API, in memory,
   with the sample match already saved. So the dashboard and the AI coach can be tried without Docker or a video model. */
import { readFileSync, existsSync, statSync } from 'node:fs';
import { resolve, extname, sep } from 'node:path';

const SAMPLE_ID = '00000000-0000-4000-8000-000000000001';
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TYPES = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.jpg': 'image/jpeg', '.json': 'application/json', '.svg': 'image/svg+xml' };
const LIST_FIELDS = ['id', 'home', 'away', 'hc', 'ac', 'source_name', 'created_at', 'status', 'stats'];
const json = data => JSON.stringify(data);

export function createDevSite(root) {
  const sample = JSON.parse(readFileSync(new URL('../scripts/sample-match.json', import.meta.url), 'utf8'));
  const analyses = new Map([[SAMPLE_ID, { id: SAMPLE_ID, hc: null, ac: null, source_name: 'Sample (dev)', created_at: new Date().toISOString(), status: 'ready', ...sample }]]);

  /* the same match data the engine adds before forwarding to the coach */
  function addMatch(body) {
    const row = analyses.get(String(body.match_id || ''));
    return row ? { ...body, match: { home: row.home, away: row.away, stats: row.stats, players: row.players, engine: row.engine, context: row.context } } : body;
  }

  async function handle(req, res, url, send) {
    if (url.pathname === '/assets/js/config.js') return send(res, 200, 'window.TRIVELA_CONFIG = { engineUrl: location.origin };', 'text/javascript');
    if (url.pathname === '/api/analyses' && req.method === 'GET') {
      return send(res, 200, json([...analyses.values()].reverse().map(r => ({ ...Object.fromEntries(LIST_FIELDS.map(k => [k, r[k]])), duration_s: r.engine && r.engine.duration_s }))));
    }
    const one = /^\/api\/analyses\/([^/]+)$/.exec(url.pathname);
    if (one) {
      const id = one[1];
      if (!UUID.test(id)) return send(res, 400, json({ detail: 'Not a saved analysis id.' }));
      if (req.method === 'GET') return analyses.has(id) ? send(res, 200, json(analyses.get(id))) : send(res, 404, json({ detail: 'That analysis was not found.' }));
      if (req.method === 'PUT') {
        let raw = ''; for await (const c of req) raw += c;
        analyses.set(id, { ...JSON.parse(raw), id, status: 'ready' });
        return send(res, 200, json({ status: 'saved', id }));
      }
    }
    if (url.pathname.startsWith('/api/')) return send(res, 503, json({ detail: 'The video engine is not running in dev mode.' }));

    const path = resolve(root, '.' + decodeURIComponent(url.pathname === '/' ? '/dashboard.html' : url.pathname));
    if (!(path === root || path.startsWith(root + sep)) || !existsSync(path) || !statSync(path).isFile()) return send(res, 404, 'Not found', 'text/plain');
    send(res, 200, readFileSync(path), TYPES[extname(path)] || 'application/octet-stream');
  }

  return { addMatch, handle };
}
