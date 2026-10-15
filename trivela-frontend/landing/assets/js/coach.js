/* AI coach: a headline feature of every saved match. Two ways in:
   - the "Ask AI coach" button in the match header,
   - the "/" key from anywhere.
   All open the side panel: a chat about the match whose write-ups (report, scouting, commentary, tips) land in it.
   Talks only to the engine's /api/coach, which passes the saved match to the coach service (API key and limits live there).
   Every piece of model text is escaped before it touches the page.
   API: init(engineBase) once; attach([{ id, label }], currentId) / detach() as the page changes; open({ ask, run }). */
(function (root) {
  'use strict';

  const MAX_CHARS = 600;   // matches LIMITS.questionChars on the server
  const CLOSE_MS = 280;    // the panel leaves the way it came; matches the CSS transition
  const ACTIONS = [
    { feature: 'report', label: 'Match report', blurb: 'Who controlled it, and why', icon: '<path d="M7 4h7l4 4v12H7z"/><path d="M14 4v4h4M10 12h5M10 16h5"/>' },
    { feature: 'scouting', label: 'Scouting cards', blurb: 'A line on every player', icon: '<circle cx="12" cy="8" r="3.2"/><path d="M5.5 19.5a6.5 6.5 0 0 1 13 0"/>' },
    { feature: 'commentary', label: 'Stats commentary', blurb: 'The match as a timeline', icon: '<circle cx="12" cy="12" r="8"/><path d="M12 7.5V12l3 2"/>' },
    { feature: 'tips', label: 'Tips for next match', blurb: 'Three fixes backed by numbers', icon: '<path d="M9.5 17.5h5M10 20.5h4"/><path d="M12 3.5a5.5 5.5 0 0 0-3 10.1V15h6v-1.4a5.5 5.5 0 0 0-3-10.1z"/>' },
  ];
  const SUGGESTIONS = ['Who ran the most?', 'When did we lose the ball most?', 'Who was on the ball most?', 'How reliable is this data?'];
  const svg = (paths, size = 18) => `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
  const SEND = svg('<path d="M5 12h13M13 6l6 6-6 6"/>');
  const CLOSE = svg('<path d="M6 6l12 12M18 6L6 18"/>');

  const MARK = '<svg width="20" height="20" viewBox="0 0 24 24" aria-hidden="true"><circle cx="9" cy="14.5" r="5.2" fill="none" stroke="currentColor" stroke-width="3.2"/><rect x="9" y="7.6" width="13" height="4.8" rx="1.3" fill="currentColor"/><circle cx="9" cy="14.5" r="1.7" fill="currentColor"/><circle cx="4.6" cy="5.6" r="1.9" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>';

  let cfg = null;                          // { url }
  let matches = [], current = null, open = false, closeTimer = null;
  const state = {};                        // matchId -> { log: [{ who, html }], chatId, busy, error }
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const label = id => (/^t\d+$/.test(id) ? `Track ${id.slice(1)}` : id);
  const of = id => state[id] || (state[id] = { log: [], chatId: null, busy: false, error: null });
  const $ = id => document.getElementById(id);
  const typing = el => el && (/^(input|textarea|select)$/i.test(el.tagName) || el.isContentEditable);

  /* POST to the coach. Resolves with data, or throws an Error carrying the server's friendly message */
  async function call(body) {
    let res;
    try {
      res = await fetch(cfg.url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    } catch (_) { throw new Error('Can\'t reach the AI coach. Check your connection.'); }
    let out = null;
    try { out = await res.json(); } catch (_) { /* not JSON: handled below */ }
    if (!res.ok || !out || !out.success) throw new Error((out && out.error) || `The AI coach answered ${res.status}.`);
    return out.data;
  }

  /* ---------- what a reply looks like ---------- */
  function outputHtml(feature, data) {
    const head = t => `<b class="cp-kind">${t}</b>`;
    if (feature === 'report') return head('Match report') + data.text.split(/\n{2,}/).map(p => `<p>${esc(p)}</p>`).join('');
    if (feature === 'scouting') return head('Scouting cards') + `<ul class="cp-cards">${(data.cards || []).map(c => `<li><b>${esc(label(c.player))}</b><span>${esc(c.line)}</span></li>`).join('')}</ul>`;
    if (feature === 'commentary') return head('Stats commentary') + `<ol class="cp-timeline">${(data.lines || []).map(l => `<li><span class="num">${esc(l.minute)}</span><span>${esc(l.text)}</span></li>`).join('')}</ol>`;
    if (feature === 'tips') return head('Tips for next match') + ((data.tips || []).length ? `<ol class="cp-tips">${data.tips.map(t => `<li>${esc(t)}</li>`).join('')}</ol>` : '<p>No tip came back with a number to back it, so none are shown. Try again.</p>');
    return '';
  }

  const tilesHtml = (cls, disabled) => `<div class="${cls}">${ACTIONS.map(a => `<button type="button" data-run="${a.feature}" ${disabled ? 'disabled' : ''}>
      <span class="cp-tile-ic">${svg(a.icon, 20)}</span><b>${a.label}</b><span>${a.blurb}</span></button>`).join('')}</div>`;

  /* ---------- the panel and the "/" key ---------- */
  function mount() {
    if ($('coachPanel')) return;
    document.body.insertAdjacentHTML('beforeend', `
      <div class="cp-scrim" id="coachScrim" hidden></div>
      <aside class="cp" id="coachPanel" role="dialog" aria-modal="false" aria-labelledby="coachTitle" hidden>
        <header class="cp-head"><span class="cp-mark">${MARK}</span>
          <div class="cp-head-main"><h2 id="coachTitle">AI coach</h2><div id="coachMatch"></div></div>
          <button class="cp-x" id="coachClose" type="button" aria-label="Close the AI coach">${CLOSE}</button></header>
        <div class="cp-log" id="coachLog" aria-live="polite"></div>
        <div class="cp-actions" id="coachActions"></div>
        <form class="cp-form" id="coachForm"><label class="cp-sr" for="coachQ">Ask about this match</label>
          <input id="coachQ" maxlength="${MAX_CHARS}" autocomplete="off" placeholder="Ask about this match…">
          <button type="submit" aria-label="Send">${SEND}</button></form>
        <p class="cp-foot"><span id="coachCount">0</span>/${MAX_CHARS} · From this match's stats only · Google Gemma, powered by AMD · AI can be wrong</p>
      </aside>`);
    $('coachClose').onclick = () => toggle(false);
    $('coachScrim').onclick = () => toggle(false);
    $('coachForm').onsubmit = e => { e.preventDefault(); ask($('coachQ').value); };
    $('coachQ').oninput = () => { $('coachCount').textContent = $('coachQ').value.length; };
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && open) { toggle(false); return; }
      if (e.key === '/' && !open && current && !typing(document.activeElement) && !e.metaKey && !e.ctrlKey && !e.altKey) { e.preventDefault(); toggle(true); }
    });
  }

  function toggle(want) {
    if (want === open) { if (want) $('coachQ').focus(); return; }
    open = want;
    clearTimeout(closeTimer);
    const panel = $('coachPanel');
    $('coachScrim').hidden = !want;
    document.body.classList.toggle('cp-open', want);
    document.querySelectorAll('[data-coach-open]').forEach(b => b.setAttribute('aria-expanded', want));
    if (want) {
      panel.hidden = false;
      void panel.offsetWidth;          // commit the off-screen start position, so the slide plays (no frame wait: rAF can be paused)
      panel.classList.add('in');       // enter from the right
      paint();
      $('coachQ').focus();
    } else {
      panel.classList.remove('in');                              // and leave the same way
      closeTimer = setTimeout(() => { if (!open) panel.hidden = true; }, CLOSE_MS);
    }
    window.dispatchEvent(new Event('resize'));   // the page got narrower or wider: let charts and tab pills re-measure
  }

  function paint() {
    if (!current || !$('coachPanel')) return;
    const s = of(current), busy = s.busy;
    const m = matches.find(x => x.id === current);
    $('coachMatch').innerHTML = matches.length > 1
      ? `<label class="cp-sr" for="coachPick">Match</label><select id="coachPick">${matches.map(x => `<option value="${esc(x.id)}"${x.id === current ? ' selected' : ''}>${esc(x.label)}</option>`).join('')}</select>`
      : `<span class="cp-chip">${esc(m ? m.label : '')}</span>`;
    const pick = $('coachPick');
    if (pick) pick.onchange = () => { current = pick.value; paint(); };

    const empty = `<div class="cp-welcome"><h3 class="cp-hello">What do you want to know about this match?</h3>
      <p class="cp-lede">I've read every number from it. Ask anything, or start here.</p>
      ${tilesHtml('cp-welcome-tiles', busy)}
      <div class="cp-chips">${SUGGESTIONS.map(q => `<button type="button" data-ask="${esc(q)}">${esc(q)}</button>`).join('')}</div></div>`;
    $('coachLog').innerHTML = (s.log.length ? s.log.map(e => `<div class="cp-msg ${e.who}">${e.html}</div>`).join('') : empty)
      + (busy ? '<div class="cp-msg coach cp-typing" aria-label="The coach is writing"><i></i><i></i><i></i></div>' : '')
      + (s.error ? `<div class="cp-msg error" role="alert">${esc(s.error)}</div>` : '');
    // Once the chat has started, the write-ups stay one tap away as compact chips above the input
    $('coachActions').innerHTML = s.log.length ? ACTIONS.map(a => `<button type="button" data-run="${a.feature}" ${busy ? 'disabled' : ''}>${a.label}</button>`).join('') : '';
    $('coachActions').hidden = !s.log.length;
    $('coachQ').disabled = busy;
    $('coachForm').querySelector('button').disabled = busy;

    $('coachPanel').querySelectorAll('[data-ask]').forEach(b => { b.onclick = () => ask(b.dataset.ask); });
    $('coachPanel').querySelectorAll('[data-run]').forEach(b => { b.onclick = () => run(b.dataset.run); });
    const log = $('coachLog'); log.scrollTop = log.scrollHeight;
  }

  /* ---------- actions ---------- */
  async function send(matchId, userHtml, body, toHtml) {
    const s = of(matchId);
    if (s.busy) return;
    s.log = [...s.log, { who: 'me', html: userHtml }];
    s.busy = true; s.error = null; paint();
    try { s.log = [...s.log, ...toHtml(await call({ match_id: matchId, ...body }))]; }
    catch (err) { s.error = err.message; }
    s.busy = false;
    if (current === matchId && open) { paint(); $('coachQ').focus(); }
  }

  function ask(raw) {
    const q = String(raw || '').trim(), id = current;
    if (!q || !id) return;
    if (q.length > MAX_CHARS) { of(id).error = `Keep questions under ${MAX_CHARS} characters.`; paint(); return; }
    $('coachQ').value = ''; $('coachCount').textContent = '0';
    send(id, esc(q), { feature: 'chat', question: q, chat_id: of(id).chatId }, data => {
      if (data.chat_id) of(id).chatId = data.chat_id;
      return [{ who: data.blocked ? 'coach blocked' : 'coach', html: `<p>${esc(data.answer)}</p>` }];
    });
  }

  function run(feature) {
    const a = ACTIONS.find(x => x.feature === feature), id = current;
    if (a && id) send(id, esc(a.label), { feature }, data => [{ who: 'coach', html: outputHtml(feature, data) }]);
  }

  /* Open the panel, optionally asking a question or running a write-up straight away */
  function openWith({ ask: question, run: feature } = {}) {
    if (!current) return;
    toggle(true);
    if (question) ask(question);
    else if (feature) run(feature);
  }

  /* ---------- page API ---------- */
  /* engineBase: the model server's address; the coach is reached through it */
  function init(engineBase) {
    cfg = { url: `${engineBase}/api/coach` };
  }

  /* list: saved matches the coach can talk about [{ id, label }]; currentId: the one to open on.
     Pages re-render, so this is called again each time */
  function attach(list, currentId) {
    if (!cfg || !list.length) { detach(); return; }
    mount();
    matches = list;
    if (!current || !list.some(m => m.id === current) || (currentId && currentId !== current && list.length === 1)) current = currentId || list[0].id;
    document.querySelectorAll('[data-coach-open]').forEach(b => { b.onclick = () => openWith(); b.setAttribute('aria-expanded', open); });
    if (open) paint();
  }

  function detach() {
    if (!$('coachPanel')) return;
    if (open) toggle(false);
    matches = []; current = null;
  }

  root.TrivelaCoach = { init, attach, detach, open: openWith };
})(typeof self !== 'undefined' ? self : this);
