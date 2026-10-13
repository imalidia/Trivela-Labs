/* Trivela chat assistant: a round button that opens a small chat panel. Loaded by every Trivela page. */
(function () {
  if (window.__trivelaChat) return;
  window.__trivelaChat = true;

  const css = `
  .tc-btn{position:fixed;right:20px;bottom:20px;width:58px;height:58px;border-radius:50%;border:0;cursor:pointer;
    background:linear-gradient(135deg,#0a8a7e,#015C55);box-shadow:0 8px 24px rgba(1,92,85,.35);z-index:9999;
    display:flex;align-items:center;justify-content:center;transition:transform .15s}
  .tc-btn:hover{transform:scale(1.06)}
  .tc-btn:focus-visible{outline:3px solid #7fd6cc;outline-offset:3px}
  .tc-panel{position:fixed;right:20px;bottom:90px;width:min(370px,calc(100vw - 40px));height:min(540px,70vh);
    background:#fff;border:1px solid rgba(1,92,85,.18);border-radius:16px;box-shadow:0 18px 50px rgba(6,32,29,.22);
    z-index:9999;display:none;flex-direction:column;overflow:hidden;font:14px/1.45 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#06201D}
  .tc-panel.open{display:flex}
  .tc-head{background:#015C55;color:#fff;padding:12px 14px;display:flex;align-items:center;gap:10px}
  .tc-head b{font-size:15px;display:block}
  .tc-head small{opacity:.8;font-size:11px}
  .tc-x{margin-left:auto;background:none;border:0;color:#fff;font-size:20px;cursor:pointer;line-height:1;padding:2px 6px}
  .tc-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px;background:#f6fbfa}
  .tc-msg{max-width:86%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;word-wrap:break-word}
  .tc-user{align-self:flex-end;background:#015C55;color:#fff;border-bottom-right-radius:4px}
  .tc-bot{align-self:flex-start;background:#fff;border:1px solid rgba(1,92,85,.14);border-bottom-left-radius:4px}
  .tc-sys{align-self:center;font-size:12px;color:#4b6461;text-align:center}
  .tc-opt{padding:8px 12px;border-top:1px solid rgba(1,92,85,.12);font-size:12.5px;color:#2f4a46;display:flex;align-items:center;gap:6px}
  .tc-opt input{accent-color:#015C55}
  .tc-form{display:flex;gap:8px;padding:10px;border-top:1px solid rgba(1,92,85,.12);background:#fff}
  .tc-form input{flex:1;border:1px solid rgba(1,92,85,.25);border-radius:10px;padding:9px 11px;font:inherit;color:#06201D}
  .tc-form input:focus{outline:2px solid #7fd6cc;border-color:#015C55}
  .tc-form button{background:#015C55;color:#fff;border:0;border-radius:10px;padding:0 14px;font-weight:600;font-size:14px;cursor:pointer}
  .tc-form button:disabled,.tc-form input:disabled{opacity:.5;cursor:not-allowed}`;

  const style = document.createElement('style');
  style.textContent = css;
  document.head.appendChild(style);

  const ICON = '<svg width="30" height="30" viewBox="0 0 32 32" aria-hidden="true">' +
    '<path d="M16 5c-6.1 0-11 4.2-11 9.4 0 2.6 1.3 5 3.5 6.7L7.5 27l5.4-2.6c.9.2 1.8.3 3.1.3 6.1 0 11-4.2 11-9.4S22.1 5 16 5z" fill="#fff"/>' +
    '<circle cx="11.5" cy="14.5" r="1.7" fill="#015C55"/><circle cx="16" cy="14.5" r="1.7" fill="#015C55"/>' +
    '<circle cx="20.5" cy="14.5" r="1.7" fill="#015C55"/></svg>';

  const btn = document.createElement('button');
  btn.className = 'tc-btn';
  btn.setAttribute('aria-label', 'Open the Trivela assistant');
  btn.setAttribute('aria-expanded', 'false');
  btn.innerHTML = ICON;

  const panel = document.createElement('div');
  panel.className = 'tc-panel';
  panel.setAttribute('role', 'dialog');
  panel.setAttribute('aria-label', 'Trivela assistant');
  panel.innerHTML =
    '<div class="tc-head"><div style="flex:1"><b>Trivela assistant</b><small id="tcModel">Ask about football or the app</small></div>' +
    '<button class="tc-x" aria-label="Close">&times;</button></div>' +
    '<div class="tc-log" id="tcLog"></div>' +
    '<label class="tc-opt"><input type="checkbox" id="tcUse" disabled> <span id="tcUseLabel">Use my analysed video (none yet)</span></label>' +
    '<form class="tc-form" id="tcForm"><input id="tcInput" type="text" placeholder="Ask a question..." maxlength="4000" autocomplete="off">' +
    '<button type="submit" id="tcSend">Send</button></form>';

  document.body.appendChild(btn);
  document.body.appendChild(panel);

  const $ = id => panel.querySelector(id);
  const log = $('#tcLog'), input = $('#tcInput'), send = $('#tcSend'), useBox = $('#tcUse');
  const history = [];
  let configured = false, loaded = false;

  function add(role, text) {
    const d = document.createElement('div');
    d.className = 'tc-msg ' + (role === 'user' ? 'tc-user' : role === 'sys' ? 'tc-sys' : 'tc-bot');
    d.textContent = text;
    log.appendChild(d);
    log.scrollTop = log.scrollHeight;
    return d;
  }

  async function loadStatus() {
    try {
      const r = await fetch('/api/chat/status');
      const s = await r.json();
      configured = !!s.configured;
      useBox.disabled = !s.has_analysis;
      $('#tcUseLabel').textContent = s.has_analysis ? 'Use my analysed video' : 'Use my analysed video (none yet)';
      if (s.model) $('#tcModel').textContent = 'Model: ' + s.model;
      if (!configured) {
        add('sys', 'The assistant is not set up yet. Add AMD_API_BASE and AMD_API_KEY to the .env file and restart.');
        input.disabled = true; send.disabled = true;
      }
    } catch (_) {
      add('sys', 'Could not reach the Trivela server.');
    }
    loaded = true;
  }

  function open() {
    panel.classList.add('open');
    btn.setAttribute('aria-expanded', 'true');
    if (!loaded) {
      add('bot', 'Hi, I am the Trivela assistant. Ask me about football tactics or how to use the app. Tick "Use my analysed video" to ask about your last analysis.');
      loadStatus();
    }
    input.focus();
  }
  function close() {
    panel.classList.remove('open');
    btn.setAttribute('aria-expanded', 'false');
    btn.focus();
  }

  btn.addEventListener('click', () => panel.classList.contains('open') ? close() : open());
  $('.tc-x').addEventListener('click', close);
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && panel.classList.contains('open')) close(); });

  $('#tcForm').addEventListener('submit', async e => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text || !configured) return;
    input.value = '';
    add('user', text);
    history.push({ role: 'user', content: text });
    const pending = add('bot', '...');
    send.disabled = true;
    try {
      const r = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ messages: history.slice(-20), use_analysis: useBox.checked && !useBox.disabled })
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || ('Error ' + r.status));
      pending.textContent = data.reply;
      history.push({ role: 'assistant', content: data.reply });
    } catch (err) {
      pending.className = 'tc-msg tc-sys';
      pending.textContent = 'Sorry, that did not work: ' + err.message;
      history.pop();
    } finally {
      send.disabled = false;
      input.focus();
    }
  });
})();
