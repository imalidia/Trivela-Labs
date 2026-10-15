(() => {
const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
const $ = s => document.querySelector(s);

const hero = $('#top');

/* ---------- Nav: a hover pill that glides between links ---------- */
{
  const links = $('.nav-links');
  if (links && matchMedia('(hover:hover) and (pointer:fine)').matches) {
    const hl = document.createElement('span'); hl.className = 'nav-hl fresh'; hl.setAttribute('aria-hidden', 'true'); links.appendChild(hl);
    const to = a => {
      const was = hl.style.opacity === '1';
      hl.classList.toggle('fresh', !was); /* first entry appears in place; later moves slide */
      hl.style.width = a.offsetWidth + 'px'; hl.style.height = a.offsetHeight + 'px';
      hl.style.transform = 'translate(' + a.offsetLeft + 'px,' + a.offsetTop + 'px)'; hl.style.opacity = '1';
    };
    links.querySelectorAll('a').forEach(a => { a.addEventListener('pointerenter', () => to(a)); a.addEventListener('focus', () => { if (a.matches(':focus-visible')) to(a); }); a.addEventListener('blur', () => hl.style.opacity = '0'); });
    links.addEventListener('pointerleave', () => hl.style.opacity = '0');
  }
}

/* ---------- Scroll reveals: each block rises in once; the big 25 counts up ---------- */
const EASE_OUT = 'cubic-bezier(.23,1,.32,1)';
const big = $('#big20'), bigNum = $('#big20 .n');
const countUp = () => {
  const t0 = performance.now(), DUR = 1000;
  const tick = now => {
    const p = Math.min(1, (now - t0) / DUR);
    bigNum.textContent = String(Math.round(25 * (1 - Math.pow(1 - p, 4)))).padStart(2, '0'); /* two digits all the way, so the number never changes width */
    if (p < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
};
const revealEls = document.querySelectorAll('[data-reveal]');
if ('IntersectionObserver' in window) {
  if (!reduced) bigNum.textContent = '00';
  const io = new IntersectionObserver(entries => entries.forEach(e => {
    if (!e.isIntersecting) return;
    e.target.classList.add('in'); io.unobserve(e.target);
    if (e.target === big && !reduced) countUp();
  }), { threshold: .15, rootMargin: '0px 0px -6% 0px' });
  revealEls.forEach(el => io.observe(el));
} else revealEls.forEach(el => el.classList.add('in'));

/* ---------- Headline word: crossfades through 14 typefaces, then lands on the brand face and stops ---------- */
const pf = $('.pf'), pfw = pf.parentElement, slot = $('.sz');
const FINAL = { f: '"Bricolage Grotesque"', w: 700, s: 'normal', c: '#8FE8DA' };
const FACES = [
  { f: '"Instrument Serif"', w: 400, s: 'italic', c: '#B9F3E8' },
  { f: '"Bagel Fat One"', w: 400, s: 'normal', c: '#74DCCB' },
  { f: '"Playfair Display"', w: 900, s: 'italic', c: '#A5EFE2' },
  { f: '"VT323"', w: 400, s: 'normal', c: '#8FE8DA' },
  { f: '"Pacifico"', w: 400, s: 'normal', c: '#C4F6EC' },
  { f: '"Archivo Black"', w: 400, s: 'normal', c: '#74DCCB' },
  { f: '"Fraunces"', w: 900, s: 'italic', c: '#B9F3E8' },
  { f: '"Chakra Petch"', w: 700, s: 'normal', c: '#A5EFE2' },
  { f: '"Lobster"', w: 400, s: 'normal', c: '#8FE8DA' },
  { f: '"Caprasimo"', w: 400, s: 'normal', c: '#74DCCB' },
  { f: '"Permanent Marker"', w: 400, s: 'normal', c: '#C4F6EC' },
  { f: '"Gloock"', w: 400, s: 'normal', c: '#A5EFE2' },
  { f: '"Righteous"', w: 400, s: 'normal', c: '#B9F3E8' },
  { f: '"Shrikhand"', w: 400, s: 'normal', c: '#74DCCB' },
];
const HOLD_MS = 300;
const setFace = f => Object.assign(pf.style, { fontFamily: f.f + ',var(--display)', fontWeight: f.w, fontStyle: f.s, fontSize: (f.k || 1) + 'em', top: (f.dy || 0) + 'em', color: f.c });
/* zero-size marker: its left edge measures ex, its bottom edge sits exactly on the baseline */
const marker = (el, css) => { const m = document.createElement('span'); m.style.cssText = 'display:inline-block;height:0;' + css; return el.appendChild(m); };
if (!reduced && document.fonts) {
  Promise.all([FINAL, ...FACES].map(f => document.fonts.load(`${f.s} ${f.w} 1em ${f.f}`, 'differently.'))).then(async () => {
    const ex = marker(slot, 'width:1ex');
    pfw.style.fontSizeAdjust = 'ex-height ' + (ex.getBoundingClientRect().width / parseFloat(getComputedStyle(slot).fontSize)).toFixed(4);
    ex.remove();
    const target = slot.getBoundingClientRect().width;
    const slotBase = marker(slot, 'width:0'), wordBase = marker(pf, 'width:0');
    [...FACES, FINAL].forEach(f => {
      f.k = 1; f.dy = 0; setFace(f);
      f.k = Math.min(1, target / pf.getBoundingClientRect().width); setFace(f);
      f.dy = (slotBase.getBoundingClientRect().bottom - wordBase.getBoundingClientRect().bottom) / parseFloat(getComputedStyle(pf).fontSize);
    });
    slotBase.remove(); wordBase.remove();
    setFace(FINAL);
    const wait = ms => new Promise(r => setTimeout(r, ms));
    const OUT = [{ opacity: 1, filter: 'blur(0px)' }, { opacity: 0, filter: 'blur(3px)' }];
    const IN = [{ opacity: 0, filter: 'blur(3px)' }, { opacity: 1, filter: 'blur(0px)' }];
    /* fade the old face out, swap while invisible, fade the new one in; the landing fade is slower so it settles */
    const show = async (f, landing) => {
      const out = pfw.animate(OUT, { duration: 140, easing: EASE_OUT, fill: 'forwards' });
      await out.finished;
      setFace(f);
      pfw.animate(IN, { duration: landing ? 480 : 260, easing: EASE_OUT }); out.cancel();
      await wait(HOLD_MS);
    };
    await wait(1300);
    for (const f of FACES) await show(f);
    await show(FINAL, true);
  });
}

/* ---------- Hero: live tracking canvas ----------
   A small rule-based match between the demo's two sides (Harbor City attack right, Northgate United attack left), in pitch metres and seconds.
   Movement is steering, never teleporting: every player has a velocity, a top speed for what they are doing (walk, jog, run, sprint),
   limited acceleration, harder braking, and a body that turns toward where it is going. Each side reads a smoothed ball position,
   so the whole block slides a beat after a pass instead of snapping with it.
   With the ball: the team spreads, full-backs push on, two players come to offer angles and a forward times a run in behind the line.
   Without it: the block compacts, the nearest player presses goal-side, the next one screens the closest pass and the rest mark.
   The carrier dribbles into space with visible touches, then passes into feet or ahead of a runner, shoots in range, or is tackled.
   Passes can be cut out; shots end as a goal, a save or a miss; every restart is played.
   The chips read from the same simulation, so possession, passes and top speed always agree with what is on the pitch. */
const W = 105, H = 68, GL = 1.5, SPEED = 1.15, TRACK = 9;
const FORM = [[.03,34],[.22,10],[.2,26],[.2,42],[.22,58],[.42,18],[.38,34],[.42,50],[.64,12],[.68,34],[.64,56]];
const ROLE = ['gk','fb','cb','cb','fb','cm','dm','cm','w','st','w'];
const NUM = [1,2,5,4,3,8,6,10,7,9,11];
const KIT = [{ fill: '#8FE8DA', num: '#012F2B', code: 'HC', pass: '143,232,218' }, { fill: '#F2945C', num: '#06201D', code: 'NU', pass: '242,148,92' }];
const WALK = 0, JOG = 1, RUN = 2, SPRINT = 3;
const PACE = [1.5, 3.2, 5.4, 8.1];             /* top speed per intent, m/s */
const ACCEL = 4.5, BRAKE = 8, PERSONAL = 2.4;   /* m/s², m/s², personal space in m */
const rnd = Math.random, clamp = (v, a, b) => Math.max(a, Math.min(b, v)), dist = (a, b) => Math.hypot(a.x - b.x, a.y - b.y);
const dirOf = team => team ? -1 : 1;
const prog = (team, x) => team ? 1 - x / W : x / W;           /* 0 at own goal, 1 at the goal it attacks */
const fromProg = (team, f) => team ? W - f * W : f * W;
const goalX = team => team ? GL : W - GL;                      /* goal line a team shoots at */
const angDiff = (a, b) => { const d = (a - b) % (2 * Math.PI); return d > Math.PI ? d - 2 * Math.PI : d < -Math.PI ? d + 2 * Math.PI : d; };
const segDist = (p, a, b) => { const dx = b.x - a.x, dy = b.y - a.y, t = clamp(((p.x - a.x) * dx + (p.y - a.y) * dy) / (dx * dx + dy * dy || 1), 0, 1); return { d: Math.hypot(a.x + dx * t - p.x, a.y + dy * t - p.y), t }; };
const P = [0, 1].flatMap(team => FORM.map(([f, y], i) => ({ team, i, n: NUM[i], role: ROLE[i], x: fromProg(team, f), y: team ? H - y : y, vx: 0, vy: 0,
  h: team ? Math.PI : 0, stride: rnd() * 2, ph: rnd() * 7, k: .95 + rnd() * .1, stun: 0 })));
const TEAMS = [P.slice(0, 11), P.slice(11)];
const G = { clock: 0, poss: 0, carrier: P[6], ball: { x: P[6].x + .6, y: P[6].y, h: 0 }, flight: null, restart: null, hold: .8, label: null,
            focus: [{ x: P[6].x, y: P[6].y }, { x: P[6].x, y: P[6].y }], drive: { x: 1, y: 0, pace: RUN, t: 0 }, touch: 0, touchLen: .45,
            press: [], support: [], run: null, runCd: 1, roleT: 0,
            score: [1, 1], minute: 63, possT: [336, 264], passes: 498, top: 0, speed: 0, lines: [], heat: [], trail: [], tAcc: 0, hAcc: 0 };
const say = (text, at) => { G.label = { text, t: 1.8, x: at.x, y: at.y }; };

/* Offside line for `team`'s attackers in progress units: the last outfield defender or the ball, never inside their own half */
function offsideLine(team) {
  let m = .5;
  TEAMS[1 - team].forEach(o => { if (o.i) m = Math.max(m, prog(team, o.x)); });
  return Math.min(.97, Math.max(m, prog(team, G.ball.x)));
}
/* Where a player stands in the team shape: three lines whose height and spacing follow the ball, wide in possession, narrow without */
function home(p, t) {
  const att = G.poss === p.team, f = G.focus[p.team], bp = prog(p.team, f.x);
  const back = clamp(att ? .2 + bp * .4 : .1 + bp * .34, .1, att ? .55 : .42);
  const mid = back + (att ? .17 : .12), fwd = mid + (att ? .2 : .12);
  let depth = { fb: back + (att ? .07 : 0), cb: back, dm: mid - .05, cm: mid, w: fwd - .02, st: fwd }[p.role];
  if (att && p.role !== 'cb') depth = Math.min(depth, offsideLine(p.team) - .012);
  if (G.restart && G.restart.kind === 'kickoff') depth = Math.min(depth, .46);
  const y0 = p.team ? H - FORM[p.i][1] : FORM[p.i][1];
  const width = att ? (p.role === 'w' || p.role === 'fb' ? 1.12 : 1) : .72;
  const y = 34 + (y0 - 34) * width + (f.y - 34) * (att ? .22 : .38) + Math.sin(t * .4 + p.ph) * .9;
  return { x: fromProg(p.team, clamp(depth, .06, .94)) + Math.cos(t * .33 + p.ph) * .6, y: clamp(y, 2.5, H - 2.5) };
}
/* Keeper: on the line tracking the ball, stepping off it as play gets close */
function keeperSpot(p) {
  const b = G.ball, bp = prog(p.team, b.x), close = bp < .25, out = GL + 1 + (close ? (.25 - bp) * 14 : 0);
  return { x: p.team ? W - out : out, y: clamp(34 + (b.y - 34) * (close ? .35 : .15), 28, 40) };
}
/* Where `p` wants to be this instant, and how hard they will run to get there */
function target(p, t) {
  const b = G.ball, r = G.restart, f = G.flight;
  if (r && r.taker === p) return { x: r.at.x, y: r.at.y, pace: RUN };
  if (p.stun > 0) return { x: p.x + p.vx * .3, y: p.y + p.vy * .3, pace: WALK };
  if (p === G.carrier) return p.i === 0 ? { x: p.x, y: p.y, pace: WALK } : { x: p.x + G.drive.x * 8, y: p.y + G.drive.y * 8, pace: G.drive.pace };
  if (f && (f.to === p || f.keeper === p)) return { x: f.end.x, y: f.end.y, pace: SPRINT };
  if (p.i === 0) return { ...keeperSpot(p), pace: RUN };
  const hm = home(p, t);
  if (r) return { ...hm, pace: JOG };
  if (G.poss === p.team) {
    if (G.run && G.run.p === p) return { ...G.run.at, pace: SPRINT };
    const s = G.support.indexOf(p), c = G.carrier;
    if (s >= 0 && c) {
      /* offer an angle: about 11 m away, 40-55 degrees either side of straight ahead, pulled a little toward their slot */
      const ang = (s ? 1 : -1) * (.75 + .2 * Math.sin(t * .5 + p.ph)), d = dirOf(p.team);
      return { x: (c.x + Math.cos(ang) * d * 11) * .65 + hm.x * .35, y: clamp((c.y + Math.sin(ang) * 11) * .65 + hm.y * .35, 3, H - 3), pace: RUN };
    }
    return { ...hm, pace: RUN };
  }
  const pr = G.press.indexOf(p);
  if (pr === 0) {
    const og = { x: p.team ? W - GL : GL, y: 34 }, d = dist(b, og) || 1;
    return { x: b.x + (og.x - b.x) / d * 1.3, y: b.y + (og.y - b.y) / d * 1.3, pace: SPRINT };
  }
  if (pr === 1 && G.carrier) {
    const c = G.carrier, m = TEAMS[c.team].filter(o => o !== c && o.i).reduce((a, o) => dist(o, c) < dist(a, c) ? o : a);
    return { x: c.x + (m.x - c.x) * .45, y: c.y + (m.y - c.y) * .45, pace: RUN };
  }
  /* mark: drift goal-side of the nearest attacker in my zone */
  let mk = null, md = 13;
  TEAMS[1 - p.team].forEach(o => { const d = o.i ? dist(o, hm) : 99; if (d < md) { md = d; mk = o; } });
  if (mk) { hm.x = hm.x * .55 + (mk.x - dirOf(p.team) * 2) * .45; hm.y = hm.y * .55 + mk.y * .45; }
  return { ...hm, pace: RUN };
}
/* Steering: arrive at the target, lean away from anyone too close, accelerate gently and brake hard, turn the body with the run */
function move(p, tg, dt) {
  const dx = tg.x - p.x, dy = tg.y - p.y, d = Math.hypot(dx, dy);
  const want = d < .25 ? 0 : Math.min(PACE[tg.pace] * p.k, d * 1.4);
  let dvx = d ? dx / d * want : 0, dvy = d ? dy / d * want : 0;
  for (const o of P) {
    if (o === p) continue;
    const ox = p.x - o.x, oy = p.y - o.y, od = Math.hypot(ox, oy);
    if (od > 0 && od < PERSONAL) { const s = (PERSONAL - od) / PERSONAL * 2.6; dvx += ox / od * s; dvy += oy / od * s; }
  }
  const sp = Math.hypot(p.vx, p.vy), ax = dvx - p.vx, ay = dvy - p.vy, am = Math.hypot(ax, ay);
  const lim = (dvx * p.vx + dvy * p.vy < sp * sp * .9 ? BRAKE : ACCEL) * dt, q = am > lim ? lim / am : 1;
  p.vx += ax * q; p.vy += ay * q;
  p.x = clamp(p.x + p.vx * dt, .5, W - .5); p.y = clamp(p.y + p.vy * dt, .5, H - .5);
  const s2 = Math.hypot(p.vx, p.vy), face = s2 > .6 ? Math.atan2(p.vy, p.vx) : Math.atan2(G.ball.y - p.y, G.ball.x - p.x);
  p.h += angDiff(face, p.h) * Math.min(1, dt * (s2 > .6 ? 8 : 4));
  p.stride += s2 * dt * 1.6;
}
/* Carrier picks a dribbling line: forward, away from nearby opponents, off the touchline; jogs when tightly marked */
function steerCarrier(c) {
  const d = dirOf(c.team);
  let vx = d, vy = (34 - c.y) * .012, pressure = 99;
  TEAMS[1 - c.team].forEach(o => {
    const ox = c.x - o.x, oy = c.y - o.y, od = Math.hypot(ox, oy) || .1;
    pressure = Math.min(pressure, od);
    if (od < 9) { const w = (9 - od) / 9; vx += ox / od * w * 1.2; vy += oy / od * w * 1.6; }
  });
  if (c.y < 6) vy += .6; if (c.y > H - 6) vy -= .6;
  if (vx * d < 0) vx = d * .15;
  const m = Math.hypot(vx, vy) || 1;
  G.drive = { x: vx / m, y: vy / m, pace: pressure < 4 ? JOG : RUN, t: .45 };
}
function pickRoles() {
  const b = G.ball, c = G.carrier, byBall = (a, o) => dist(a, b) - dist(o, b);
  G.press = G.restart || (G.flight && G.flight.kind === 'shot') ? [] : TEAMS[1 - G.poss].filter(o => o.i).sort(byBall).slice(0, 2);
  G.support = c && c.team === G.poss && c.i ? TEAMS[G.poss].filter(m => m !== c && m.i && !(G.run && G.run.p === m)).sort((a, o) => dist(a, c) - dist(o, c)).slice(0, 2) : [];
}
function gain(p) {
  G.carrier = p; G.poss = p.team; G.touch = 0; G.run = null;
  steerCarrier(p); pickRoles();
}
/* Runs in behind: from midfield, a winger or the striker sprints past the last defender; a pass is only played while they are onside */
function runs(dt) {
  const c = G.carrier;
  if (G.restart || !c || c.team !== G.poss) { G.run = null; return; }
  if (G.run) { if ((G.run.t -= dt) <= 0 || G.run.p === c) G.run = null; return; }
  if ((G.runCd -= dt) > 0) return;
  G.runCd = 1.5 + rnd() * 2;
  const team = G.poss, bp = prog(team, c.x);
  if (bp < .3 || bp > .78) return;
  const opts = TEAMS[team].filter(m => (m.role === 'st' || m.role === 'w') && m !== c), m = opts[Math.floor(rnd() * opts.length)];
  G.run = { p: m, t: 2.8, at: { x: fromProg(team, Math.min(.95, offsideLine(team) + .08)), y: clamp(m.y + (34 - m.y) * .35, 6, H - 6) } };
}
const leadPoint = (c, m) => { const d = dist(c, m), eta = d / (15 + d * .3); return { x: clamp(m.x + dirOf(c.team) * 5 + m.vx * eta, 3, W - 3), y: clamp(m.y + m.vy * eta, 3, H - 3) }; };
function pass(c, m, aim) {
  const d = dist(c, aim), v = 15 + d * .3;
  const end = { x: clamp(aim.x, 3, W - 3), y: clamp(aim.y, 3, H - 3) };
  let cut = null;
  TEAMS[1 - c.team].forEach(o => { const s = segDist(o, c, end); if (s.d < 1.9 && s.t > .15 && (!cut || s.t < cut.t)) cut = { o, t: s.t }; });
  const steal = cut && rnd() < (c.team ? .6 : .4);
  const to = steal ? cut.o : m, stop = steal ? { x: c.x + (end.x - c.x) * cut.t, y: c.y + (end.y - c.y) * cut.t } : end;
  G.flight = { kind: 'pass', team: c.team, from: { x: G.ball.x, y: G.ball.y }, end: stop, to, t: 0, dur: dist(G.ball, stop) / v + .05, lofted: d > 26 };
  G.carrier = null;
}
function shoot(c) {
  const gx = goalX(c.team), dir = dirOf(c.team), aim = 34 + (rnd() - .5) * 10.5, keeper = TEAMS[1 - c.team][0];
  const outcome = Math.abs(aim - 34) > 3.5 ? 'wide' : rnd() < .32 ? 'goal' : 'save';
  const end = outcome === 'save' ? { x: gx - dir * 1.2, y: (keeper.y + aim) / 2 } : { x: gx + dir * (outcome === 'goal' ? .9 : 1.3), y: aim };
  G.flight = { kind: 'shot', team: c.team, from: { x: G.ball.x, y: G.ball.y }, end, outcome, keeper, t: 0, dur: dist(G.ball, end) / 27 + .05 };
  G.carrier = null; G.run = null;
}
function decide(c) {
  const dg = Math.hypot(goalX(c.team) - c.x, 34 - c.y);
  if (c.i && dg < 24 && rnd() < (dg < 17 ? .7 : .3)) return shoot(c);
  const opp = TEAMS[1 - c.team], line = offsideLine(c.team), pressure = Math.min(...opp.map(o => dist(o, c)));
  /* with space ahead, carry it a little longer instead of forcing a pass */
  if (c.i && pressure > 7 && rnd() < .45) { G.hold = .5 + rnd() * .6; return; }
  let best = null, bs = -1e9;
  TEAMS[c.team].forEach(m => {
    if (m === c || m.i === 0) return;
    const through = G.run && G.run.p === m && prog(c.team, m.x) <= line;
    if (!through && m.role !== 'cb' && prog(c.team, m.x) > line + .005) return;   /* offside */
    const aim = through ? leadPoint(c, m) : { x: m.x + m.vx * .5, y: m.y + m.vy * .5 }, d = dist(aim, c);
    if (d < 6 || d > 40) return;
    const lane = Math.min(...opp.map(o => segDist(o, c, aim).d)), open = Math.min(...opp.map(o => dist(o, aim)));
    const s = (prog(c.team, aim.x) - prog(c.team, c.x)) * 40 + Math.min(open, 8) * .9 + Math.min(lane, 6) * 1.3 - d * .06 + (through ? 6 : 0) + rnd() * 4;
    if (s > bs) { bs = s; best = { m, aim }; }
  });
  if (best) pass(c, best.m, best.aim); else G.hold = .35;
}
function land(f) {
  G.lines.push({ a: f.from, b: { x: f.end.x, y: f.end.y }, age: 0, kind: f.kind, team: f.team });
  G.flight = null;
  if (f.kind === 'pass') {
    if (f.to.team === f.team && f.team === 0) G.passes++;
    gain(f.to); G.hold = .45 + rnd() * .7;
    return;
  }
  G.poss = 1 - f.team;
  if (f.outcome === 'goal') { G.score[f.team]++; say('GOAL!', f.end); G.restart = { kind: 'kickoff', team: 1 - f.team, wait: 2.2, at: { x: W / 2, y: 34 } }; }
  else if (f.outcome === 'save') { say('SAVED', f.end); gain(f.keeper); G.hold = 1.3; }
  else { say('OFF TARGET', f.end); G.restart = { kind: 'goalkick', team: 1 - f.team, wait: 1.6, at: { x: f.team ? W - GL - 5.5 : GL + 5.5, y: 34 } }; }
  pickRoles();
}
function restartTick(dt) {
  const r = G.restart;
  if (r.wait > 0) {
    r.wait -= dt;
    if (r.wait <= 0) { G.ball = { x: r.at.x, y: r.at.y, h: 0 }; r.taker = TEAMS[r.team][r.kind === 'goalkick' ? 0 : TRACK]; r.t = 0; }
    return;
  }
  r.t += dt;
  if (dist(r.taker, r.at) < 1.2 || r.t > 4) { G.restart = null; gain(r.taker); G.hold = .6; }
}
function moveBall(dt) {
  const c = G.carrier;
  if (G.flight) {
    const f = G.flight; f.t += dt;
    const e = Math.min(1, f.t / f.dur), q = f.kind === 'shot' ? e : 1 - Math.pow(1 - e, 1.6);
    G.ball = { x: f.from.x + (f.end.x - f.from.x) * q, y: f.from.y + (f.end.y - f.from.y) * q, h: f.lofted ? Math.sin(Math.PI * e) : 0 };
    if (e >= 1) land(f);
    return;
  }
  if (!c) return;
  /* dribble: each touch pushes the ball out in front and the carrier runs onto it; quicker, shorter touches when slow */
  const sp = Math.hypot(c.vx, c.vy);
  if ((G.touch -= dt) <= 0) G.touch = G.touchLen = clamp(.62 - sp * .05, .3, .6);
  const u = 1 - G.touch / G.touchLen, reach = c.i === 0 ? .6 : .55 + Math.sin(Math.PI * u) * Math.min(1.6, .25 + sp * .26);
  G.ball = { x: c.x + Math.cos(c.h) * reach, y: c.y + Math.sin(c.h) * reach, h: 0 };
  if ((G.drive.t -= dt) <= 0) steerCarrier(c);
  const o = G.press[0];
  if (c.i !== 0 && o && o.team !== c.team && dist(o, c) < 1.4 && rnd() < dt * 1.1) { c.stun = .7; gain(o); G.hold = .5 + rnd() * .4; }
  else if ((G.hold -= dt) <= 0) decide(c);
}
function step(dt) {
  const t = (G.clock += dt);
  G.minute += dt / 3;
  if (G.minute >= 90 && !G.flight && !G.restart) { say('FULL TIME', { x: W / 2, y: 34 }); G.score = [0, 0]; G.minute = 0; G.carrier = null; G.poss = 0; G.run = null; G.restart = { kind: 'kickoff', team: 0, wait: 2.4, at: { x: W / 2, y: 34 } }; }
  if (G.restart) restartTick(dt); else G.possT[G.poss] += dt;
  /* each side reads a smoothed ball, the side without it a little quicker */
  G.focus.forEach((fc, team) => { const a = Math.min(1, dt / (team === G.poss ? .5 : .32)); fc.x += (G.ball.x - fc.x) * a; fc.y += (G.ball.y - fc.y) * a; });
  if ((G.roleT -= dt) <= 0) { G.roleT = .3; pickRoles(); }
  runs(dt);
  P.forEach(p => { if (p.stun > 0) p.stun -= dt; move(p, target(p, t), dt); });
  /* nobody walks through anybody */
  for (let i = 0; i < P.length; i++) for (let j = i + 1; j < P.length; j++) {
    const a = P[i], b = P[j], d = dist(a, b) || .01;
    if (d < 1.1) { const k = (1.1 - d) / 2 / d, dx = (b.x - a.x) * k, dy = (b.y - a.y) * k; a.x -= dx; a.y -= dy; b.x += dx; b.y += dy; }
  }
  moveBall(dt);
  /* tracked player's live speed, the fastest run seen, the ball trail and the heatmap */
  const tp = TEAMS[0][TRACK];
  G.speed += (Math.hypot(tp.vx, tp.vy) * 3.6 - G.speed) * Math.min(1, dt * 4);
  P.forEach(p => { G.top = Math.max(G.top, Math.hypot(p.vx, p.vy) * 3.6); });
  if ((G.tAcc += dt) > .03) { G.tAcc = 0; G.trail.push({ x: G.ball.x, y: G.ball.y, h: G.ball.h }); if (G.trail.length > 14) G.trail.shift(); }
  if ((G.hAcc += dt) > .12) { G.hAcc = 0; G.heat.push({ x: tp.x, y: tp.y }); if (G.heat.length > 320) G.heat.shift(); }
  G.lines.forEach(l => l.age += dt); while (G.lines.length && G.lines[0].age > 2.6) G.lines.shift();
  if (G.label && (G.label.t -= dt) <= 0) G.label = null;
}

const cv = $('#cv'), ctx = cv.getContext('2d');
let cw = 0, ch = 0;
function size() { const r = cv.getBoundingClientRect(), d = Math.min(devicePixelRatio || 1, 2); cw = r.width; ch = r.width * H / W; cv.width = Math.round(cw * d); cv.height = Math.round(ch * d); ctx.setTransform(d, 0, 0, d, 0, 0); }
function rrect(x, y, w, h, r) {
  ctx.beginPath();
  if (ctx.roundRect) { ctx.roundRect(x, y, w, h, r); return; }
  ctx.moveTo(x + r, y); ctx.arcTo(x + w, y, x + w, y + h, r); ctx.arcTo(x + w, y + h, x, y + h, r); ctx.arcTo(x, y + h, x, y, r); ctx.arcTo(x, y, x + w, y, r); ctx.closePath();
}
function bracket(x, y, w, h, c, lw) { const l = Math.min(w, h) * .3; ctx.strokeStyle = c; ctx.lineWidth = lw; ctx.lineCap = 'round'; ctx.beginPath();
  ctx.moveTo(x, y + l); ctx.lineTo(x, y); ctx.lineTo(x + l, y); ctx.moveTo(x + w - l, y); ctx.lineTo(x + w, y); ctx.lineTo(x + w, y + l);
  ctx.moveTo(x + w, y + h - l); ctx.lineTo(x + w, y + h); ctx.lineTo(x + w - l, y + h); ctx.moveTo(x + l, y + h); ctx.lineTo(x, y + h); ctx.lineTo(x, y + h - l); ctx.stroke(); }
function pill(text, x, y, bg, fg, sz, align) {
  ctx.font = `700 ${sz}px "DM Sans",sans-serif`;
  const w = ctx.measureText(text).width + 14, h = sz + 8, left = align === 'center' ? x - w / 2 : x;
  ctx.fillStyle = bg; rrect(left, y, w, h, h / 2); ctx.fill();
  ctx.fillStyle = fg; ctx.textBaseline = 'middle'; ctx.textAlign = 'left'; ctx.fillText(text, left + 7, y + h / 2 + .5);
  return w;
}
function drawPitch(k) {
  ctx.fillStyle = '#012F2B'; ctx.fillRect(0, 0, cw, ch);
  const band = (W - 2 * GL) / 12;
  ctx.fillStyle = 'rgba(255,255,255,.025)';
  for (let i = 0; i < 12; i += 2) ctx.fillRect((GL + i * band) * k, GL * k, band * k, (H - 2 * GL) * k);
  ctx.strokeStyle = 'rgba(255,255,255,.42)'; ctx.lineWidth = 1.3; ctx.lineCap = 'butt';
  const R = (x, y, w, h) => ctx.strokeRect(x * k, y * k, w * k, h * k), A = (x, y, r, a, b) => { ctx.beginPath(); ctx.arc(x * k, y * k, r * k, a, b); ctx.stroke(); };
  R(GL, GL, W - 2 * GL, H - 2 * GL); R(GL, 14, 16.5, 40); R(W - GL - 16.5, 14, 16.5, 40); R(GL, 25, 5.5, 18); R(W - GL - 5.5, 25, 5.5, 18);
  ctx.beginPath(); ctx.moveTo(W / 2 * k, GL * k); ctx.lineTo(W / 2 * k, (H - GL) * k); ctx.stroke(); A(W / 2, 34, 9.15, 0, 7);
  const arc = Math.acos(5.5 / 9.15);
  A(GL + 11, 34, 9.15, -arc, arc); A(W - GL - 11, 34, 9.15, Math.PI - arc, Math.PI + arc);
  ctx.fillStyle = 'rgba(255,255,255,.5)';
  [[W / 2, 34], [GL + 11, 34], [W - GL - 11, 34]].forEach(([x, y]) => { ctx.beginPath(); ctx.arc(x * k, y * k, Math.max(1.2, .3 * k), 0, 7); ctx.fill(); });
  ctx.strokeStyle = 'rgba(255,255,255,.75)'; ctx.lineWidth = 1.6; R(GL - 1.2, 30.34, 1.2, 7.32); R(W - GL, 30.34, 1.2, 7.32);
}
/* Players: shadows first so bodies overlap cleanly; a speed streak behind anyone sprinting, a slight bob with each stride,
   and a notch on the rim showing which way they face */
function drawPlayers(k, r) {
  const tp = TEAMS[0][TRACK];
  P.forEach(p => { ctx.fillStyle = 'rgba(0,0,0,.28)'; ctx.beginPath(); ctx.ellipse(p.x * k, p.y * k + r * .95, r, r * .38, 0, 0, 7); ctx.fill(); });
  P.forEach(p => {
    const sp = Math.hypot(p.vx, p.vy), kit = KIT[p.team], x = p.x * k, bob = Math.abs(Math.sin(p.stride * Math.PI)) * Math.min(1, sp / 6) * r * .14, y = p.y * k - bob;
    if (sp > 4.5) {
      const len = (sp - 4.5) * .55 * k, ux = p.vx / sp, uy = p.vy / sp, gr = ctx.createLinearGradient(x, y, x - ux * len, y - uy * len);
      gr.addColorStop(0, `rgba(${kit.pass},.35)`); gr.addColorStop(1, `rgba(${kit.pass},0)`);
      ctx.strokeStyle = gr; ctx.lineWidth = r * 1.3; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(x, y); ctx.lineTo(x - ux * len, y - uy * len); ctx.stroke();
    }
    ctx.fillStyle = kit.fill; ctx.beginPath(); ctx.arc(x, y, r, 0, 7); ctx.fill();
    ctx.strokeStyle = p.i === 0 ? '#FFD60A' : 'rgba(255,255,255,.85)'; ctx.lineWidth = 1.5; ctx.stroke();
    const hx = Math.cos(p.h), hy = Math.sin(p.h), tip = r + Math.max(2.5, r * .45), wing = Math.max(2, r * .38);
    ctx.fillStyle = 'rgba(255,255,255,.92)'; ctx.beginPath();
    ctx.moveTo(x + hx * tip, y + hy * tip); ctx.lineTo(x + hx * r - hy * wing, y + hy * r + hx * wing); ctx.lineTo(x + hx * r + hy * wing, y + hy * r - hx * wing); ctx.closePath(); ctx.fill();
    if (r >= 5.5) { ctx.fillStyle = kit.num; ctx.font = `800 ${Math.round(r * 1.05)}px "DM Sans",sans-serif`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(p.n, x, y + .5); }
    bracket(x - r * 1.8, p.y * k - r * 2.7, r * 3.6, r * 4.5, p === tp ? '#8FE8DA' : p === G.carrier ? 'rgba(255,214,10,.7)' : 'rgba(255,255,255,.22)', p === tp ? 2.2 : 1);
  });
  const fs = Math.max(10, k * 1.35);
  pill(`#${tp.n}  ${G.speed.toFixed(1)} km/h`, clamp(tp.x * k - 34, 6, cw - 110), Math.max(30, tp.y * k - r * 2.7 - fs - 16), '#FFFFFF', '#015C55', fs);
}
function drawBall(k) {
  G.trail.forEach((b, i) => { const f = (i + 1) / G.trail.length; ctx.fillStyle = `rgba(255,214,10,${f * .45})`; ctx.beginPath(); ctx.arc(b.x * k, b.y * k - (b.h || 0) * .9 * k, Math.max(1.5, .45 * k * f), 0, 7); ctx.fill(); });
  const b = G.ball, lift = (b.h || 0) * .9 * k, br = Math.max(3.4, .62 * k) * (1 + (b.h || 0) * .35);
  ctx.fillStyle = 'rgba(0,0,0,.3)'; ctx.beginPath(); ctx.ellipse(b.x * k, b.y * k + br * .6, br, br * .4, 0, 0, 7); ctx.fill();
  ctx.fillStyle = '#FFD60A'; ctx.beginPath(); ctx.arc(b.x * k, b.y * k - lift, br, 0, 7); ctx.fill(); ctx.strokeStyle = '#06201D'; ctx.lineWidth = 1.2; ctx.stroke();
  bracket(b.x * k - 9, b.y * k - lift - 9, 18, 18, '#FFD60A', 1.6);
}
function drawHud(k) {
  if (G.label) {
    const ls = Math.max(11, k * 1.4);
    ctx.globalAlpha = Math.min(1, G.label.t * 3);
    pill(G.label.text, clamp(G.label.x * k, 60, cw - 60), clamp(G.label.y * k - 44, 34, ch - ls - 14), '#FFD60A', '#06201D', ls, 'center');
    ctx.globalAlpha = 1;
  }
  ctx.fillStyle = '#FF5A5F'; ctx.beginPath(); ctx.arc(18, 18, 4, 0, 7); ctx.fill();
  ctx.font = '700 11px "DM Sans",sans-serif'; ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
  ctx.fillText(`LIVE  ${Math.min(90, Math.floor(G.minute) + 1)}'`, 28, 18);
  pill(`${KIT[0].code}  ${G.score[0]} – ${G.score[1]}  ${KIT[1].code}`, cw / 2, 8, 'rgba(255,255,255,.92)', '#012F2B', 12, 'center');
  if (cw > 560) { ctx.font = '700 11px "DM Sans",sans-serif'; ctx.fillStyle = 'rgba(255,255,255,.85)'; ctx.textAlign = 'right'; ctx.fillText('22 PLAYERS · 1 BALL', cw - 14, 18); ctx.textAlign = 'left'; }
}
function drawField() {
  if (!cw) return; const k = cw / W;
  drawPitch(k);
  G.heat.forEach((h, i) => { const r = 6 * k, gr = ctx.createRadialGradient(h.x * k, h.y * k, 0, h.x * k, h.y * k, r); const a = .04 + (i / G.heat.length) * .05; gr.addColorStop(0, `rgba(143,232,218,${a})`); gr.addColorStop(1, 'rgba(143,232,218,0)'); ctx.fillStyle = gr; ctx.fillRect(h.x * k - r, h.y * k - r, r * 2, r * 2); });
  G.lines.forEach(l => { const a = Math.max(0, 1 - l.age / 2.6), shot = l.kind === 'shot'; ctx.strokeStyle = shot ? `rgba(255,214,10,${a})` : `rgba(${KIT[l.team].pass},${a * .85})`; ctx.lineWidth = shot ? 2.4 : 1.8; ctx.setLineDash(shot ? [] : [5, 4]);
    ctx.beginPath(); ctx.moveTo(l.a.x * k, l.a.y * k); ctx.lineTo(l.b.x * k, l.b.y * k); ctx.stroke(); ctx.setLineDash([]); });
  drawPlayers(k, Math.max(4.5, 1.15 * k));
  drawBall(k);
  drawHud(k);
}
/* warm-up so the first frame already has passes and heat */
for (let i = 0; i < 900; i++) step(1 / 60);
const chPos = $('#chPos'), chBar = $('#chPosBar'), chPass = $('#chPass'), chTop = $('#chTop');
function chips() {
  const p = Math.round(100 * G.possT[0] / (G.possT[0] + G.possT[1]));
  chPos.textContent = p + '%'; chBar.style.width = p + '%'; chPass.textContent = G.passes; chTop.textContent = G.top.toFixed(1) + ' km/h';
}
/* Named drawField, not draw: the live demo below declares its own draw() in a block, and in this sloppy-mode script
   that block declaration would overwrite an outer draw once it runs, leaving the loop repainting the wrong thing.
   One animation handle: paused off screen and in hidden tabs; a canvas the browser dropped while the tab slept is repainted on return */
let raf = 0, last = 0, chipT = 0, inView = true;
function frame(ts) {
  raf = requestAnimationFrame(frame);
  const dt = last ? Math.min(.05, (ts - last) / 1000) : 0; last = ts;
  step(dt * SPEED); drawField();
  if ((chipT += dt) > .25) { chipT = 0; chips(); }
}
const play = () => { if (!raf && !reduced && inView && !document.hidden) { last = 0; raf = requestAnimationFrame(frame); } };
const pause = () => { cancelAnimationFrame(raf); raf = 0; };
new ResizeObserver(() => { size(); drawField(); }).observe(cv); size(); drawField(); chips();
cv.addEventListener('contextrestored', drawField);
if (document.fonts) document.fonts.ready.then(drawField);
document.addEventListener('visibilitychange', () => { if (document.hidden) pause(); else { drawField(); play(); } });
if ('IntersectionObserver' in window) new IntersectionObserver(es => { inView = es[es.length - 1].isIntersecting; if (inView) { drawField(); play(); } else pause(); }).observe(cv);
play();
// The card starts tilted back and flattens as you scroll; the pointer adds a slight sideways tilt
const vis = $('#vis');
if (!reduced && matchMedia('(min-width:981px)').matches) {
  const lift = () => { const p = Math.min(1, Math.max(0, window.scrollY / 420)); vis.style.setProperty('--rx', (6 * (1 - p)).toFixed(2) + 'deg'); vis.style.setProperty('--sc', (.97 + .03 * p).toFixed(3)); };
  addEventListener('scroll', lift, { passive: true }); lift();
  if (matchMedia('(hover:hover)').matches) {
    hero.addEventListener('pointermove', e => { const r = hero.getBoundingClientRect(); vis.style.setProperty('--ry', (((e.clientX - r.left) / r.width - .5) * 6).toFixed(2) + 'deg'); });
    hero.addEventListener('pointerleave', () => vis.style.removeProperty('--ry'));
  }
} else { vis.style.setProperty('--rx', '0deg'); vis.style.setProperty('--sc', '1'); }

/* ---------- Football cities map ----------
   Dots come from the dotted-map package (diagonal grid, 60 rows, Mercator crop lng -16..46, lat 31.5..56.5), pre-generated so the page
   ships no map library: each row lists [startColumn, runLength] pairs, and even rows sit half a column to the right.
   City positions are projected with the same crop, in the same 105 × 60 map units as the SVG viewBox. Label side: t/b/l/r. */
const MAP_RUNS = '39,18,62,2,71,34|39,18,70,35|39,6,46,10,64,41|40,5,47,9,63,42|39,5,47,7,63,42|26,2,41,3,47,8,64,41|25,2,47,7,64,41|25,2,28,2,48,6,65,40|24,5,45,1,48,7,56,1,60,2,64,41|24,6,44,2,49,5,56,1,61,44|24,5,43,3,48,5,60,45|25,4,43,4,49,5,60,45|25,4,43,2,47,4,60,45|26,4,43,2,47,2,50,1,61,44|22,1,25,5,43,2,47,1,60,45|22,2,26,5,44,2,55,1,59,46|20,4,27,4,43,3,48,1,53,52|20,4,29,3,44,7,52,53|19,5,28,4,41,64|20,4,26,7,39,66|20,4,26,7,38,67|20,4,27,8,38,67|19,3,25,9,37,68|26,8,38,67|26,8,36,69|26,7,35,70|25,2,34,71|25,1,34,71|29,1,31,74|30,75|26,3,30,75|26,79|27,78|29,76|29,52,83,16,100,5|30,43,74,5,83,15,103,2|30,42,76,2,82,15,102,3|31,18,51,21,76,3,82,14,103,2|30,18,50,21,76,4,81,13,100,5|31,18,52,19,77,1,82,12,100,5|30,13,44,5,52,18,83,11,99,6|30,13,46,4,53,17,85,10,100,5|20,17,39,2,46,4,54,15,85,10,100,5|20,17,44,1,47,4,56,13,87,9,102,3|20,16,43,2,47,4,57,12,87,9,102,3|21,15,44,1,48,6,58,12,76,4,88,9,102,1,104,1|20,15,49,5,58,12,74,7,86,11,104,1|21,12,43,2,51,5,58,7,67,2,71,27|20,12,43,2,52,2,55,1,58,6,68,31,102,3|21,11,44,1,53,1,59,4,68,30,103,2|19,13,43,2,53,2,59,4,67,30,102,3|20,13,60,3,68,29,104,1|20,11,53,1,60,4,67,30,103,2|21,11,49,4,61,2,68,30,104,1|20,10,44,1,50,2,60,2,68,31,103,2|24,6,36,3,40,7,52,1,61,2,69,9,79,22,102,3|24,1,32,14,71,1,75,2,79,26|25,1,31,16,80,25|24,22,64,3,75,2,80,25|24,22,80,25|23,23,79,26|22,25,79,26|20,28,78,27';
const MAP_W = 105, MAP_H = 54, ROW_STEP = Math.sqrt(3) / 2;
const CITIES = {
  Lisbon: [19.87, 44.33, 'r', 1],
  Madrid: [27.07, 41.41, 'l', 1],
  Barcelona: [34.85, 39.71, 'b', 1],
  Marseille: [39.09, 36.29, 'l'],
  Lyon: [38.38, 31.72, 't'],
  Paris: [35.09, 25.66, 'b', 1],
  London: [31.8, 20.18, 'b', 1],
  Manchester: [29.01, 15.89, 't', 1],
  Dublin: [23.68, 16.18, 'l'],
  Amsterdam: [38.46, 18.33, 't', 1],
  Dortmund: [41.87, 20.18, 'l'],
  Berlin: [49.72, 18, 'b', 1],
  Munich: [47.31, 27.1, 'l', 1],
  Milan: [44.14, 32.28, 'r', 1],
  Rome: [48.53, 38.81, 't', 1],
  Napoli: [50.87, 40.66, 'b', 1],
  Vienna: [53.65, 26.96, 't'],
  Budapest: [57.19, 28.36, 'r'],
  Warsaw: [59.8, 18.63, 'l'],
  Bucharest: [66.54, 34.21, 'l'],
  Athens: [63.4, 45.58, 'l', 1],
  Istanbul: [70.35, 40.38, 'r', 1],
  Kyiv: [72.39, 22.41, 'b'],
  Copenhagen: [48.62, 10.86, 'r'],
  Stockholm: [55.9, 1.86, 'b'],
  Beirut: [78.99, 52.27, 'r', 1],
  Algiers: [36.03, 47.63, 'l', 1],
};
const ROUTES = [
  ['Lisbon', 'Madrid'], ['Madrid', 'Barcelona'], ['Barcelona', 'Marseille'], ['Marseille', 'Lyon'], ['Lyon', 'Paris'], ['Paris', 'London'], ['London', 'Manchester'], ['Manchester', 'Dublin'], ['Paris', 'Amsterdam'], ['Amsterdam', 'Dortmund'], ['Dortmund', 'Berlin'], ['Berlin', 'Copenhagen'], ['Copenhagen', 'Stockholm'], ['Lyon', 'Milan'], ['Milan', 'Munich'], ['Munich', 'Berlin'], ['Munich', 'Vienna'], ['Vienna', 'Budapest'], ['Berlin', 'Warsaw'], ['Warsaw', 'Kyiv'], ['Budapest', 'Bucharest'], ['Milan', 'Rome'], ['Rome', 'Napoli'], ['Napoli', 'Athens'], ['Athens', 'Istanbul'], ['Bucharest', 'Istanbul'], ['Istanbul', 'Beirut'], ['Barcelona', 'Algiers'], ['Algiers', 'Rome']];
/* Same timing model as the original component: routes start 0.18s apart, each draws in 2s, all hold, then wipe during a 2s pause */
const STAGGER = .18, DRAW = 2, PAUSE = 2, ALL_DRAWN = ROUTES.length * STAGGER + DRAW, CYCLE = ALL_DRAWN + PAUSE;
const easeInOut = t => t < .5 ? 2 * t * t : 1 - (2 - 2 * t) ** 2 / 2;
/* Quadratic arc bowed upward; the bow grows with distance so short hops (Rome to Napoli) stay low */
const arc = ([x1, y1], [x2, y2]) => {
  const lift = Math.min(9, Math.max(1.6, Math.hypot(x2 - x1, y2 - y1) * .32));
  return `M${x1} ${y1}Q${((x1 + x2) / 2).toFixed(2)} ${(Math.min(y1, y2) - lift).toFixed(2)} ${x2} ${y2}`;
};

let dots = '';
MAP_RUNS.split('|').forEach((row, r) => {
  const runs = row.split(',').map(Number), y = (r * ROW_STEP).toFixed(2), shift = r % 2 === 0 ? .5 : 0;
  for (let i = 0; i < runs.length; i += 2) for (let c = runs[i]; c < runs[i] + runs[i + 1]; c++) dots += `M${c + shift} ${y}h0`;
});
$('#mapDots').innerHTML = `<path d="${dots}" stroke="#fff" stroke-opacity=".28" stroke-width=".56" stroke-linecap="round"/>`;

const names = Object.keys(CITIES);
$('#mapRoutes').innerHTML = `<defs>
  <linearGradient id="routeGrad" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#fff" stop-opacity="0"/><stop offset=".08" stop-color="#fff"/><stop offset=".92" stop-color="#fff"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></linearGradient>
  <filter id="pinGlow" x="-200%" y="-200%" width="500%" height="500%"><feGaussianBlur stdDeviation=".35" result="b"/><feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge></filter></defs>` +
  ROUTES.map(([a, b]) => `<path class="route" pathLength="1" d="${arc(CITIES[a], CITIES[b])}"/>`).join('') +
  ROUTES.map(() => '<circle class="comet" r=".38"/>').join('') +
  names.map((n, i) => { const [x, y] = CITIES[n]; return `<g class="pin" data-city="${n}" style="--d:${(i % 4) * .5}s"><circle class="ring" cx="${x}" cy="${y}" r=".45"/><circle class="core" cx="${x}" cy="${y}" r=".45" filter="url(#pinGlow)"/><circle class="hit" cx="${x}" cy="${y}" r="1.8"/></g>`; }).join('');
$('#mapLabels').innerHTML = names.map((n, i) => { const [x, y, side, major] = CITIES[n];
  return `<span class="map-label ${side}${major ? '' : ' minor'}" data-city="${n}" style="--x:${(x / MAP_W * 100).toFixed(2)};--y:${(y / MAP_H * 100).toFixed(2)};--i:${i}">${n}</span>`; }).join('');

const routeEls = [...document.querySelectorAll('#mapRoutes .route')], cometEls = [...document.querySelectorAll('#mapRoutes .comet')];
const routeLen = routeEls.map(p => p.getTotalLength());
function drawRoutes(t) {
  routeEls.forEach((path, i) => {
    const start = i * STAGGER, end = start + DRAW;
    const drawn = t >= end ? 1 : t > start ? easeInOut((t - start) / DRAW) : 0;
    const wiped = t > ALL_DRAWN ? easeInOut((t - ALL_DRAWN) / PAUSE) : 0;
    path.style.strokeDashoffset = wiped ? -wiped : 1 - drawn; /* wipe clears from the start city, so the line seems to travel on */
    const head = path.getPointAtLength(drawn * routeLen[i]), comet = cometEls[i];
    comet.setAttribute('cx', head.x.toFixed(2)); comet.setAttribute('cy', head.y.toFixed(2));
    comet.style.opacity = t < start ? 0 : t < end ? drawn : t < ALL_DRAWN ? 1 - (t - end) / (ALL_DRAWN - end) : 0;
  });
}
if (reduced) routeEls.forEach(p => p.style.strokeDashoffset = 0);
else {
  /* Runs only while the map is on screen; the clock pauses with it so the cycle resumes where it left off */
  let raf = 0, last = 0, clock = 0;
  const tick = now => { if (last) clock = (clock + (now - last) / 1000) % CYCLE; last = now; drawRoutes(clock); raf = requestAnimationFrame(tick); };
  new IntersectionObserver(es => { const e = es[es.length - 1]; cancelAnimationFrame(raf); last = 0; if (e.isIntersecting) raf = requestAnimationFrame(tick); }).observe($('#map'));
}

/* Hover (or tap on phones) a city: grow its pin, darken its label, thicken its routes, and name it in the phone tip */
let activeCity = null;
function setCity(name) {
  if (name === activeCity) return; activeCity = name;
  document.querySelectorAll('#map [data-city]').forEach(el => el.classList.toggle('on', el.dataset.city === name));
  routeEls.forEach((p, i) => p.classList.toggle('hot', ROUTES[i].includes(name)));
  if (name) $('#mapTip').textContent = name;
}
const pinOf = e => e.target.closest && e.target.closest('.pin');
$('#mapRoutes').addEventListener('pointerover', e => { const p = pinOf(e); if (p) setCity(p.dataset.city); });
$('#mapRoutes').addEventListener('pointerout', e => { if (e.pointerType === 'mouse' && pinOf(e) && !(e.relatedTarget && e.relatedTarget.closest && e.relatedTarget.closest('.pin'))) setCity(null); });
$('#mapRoutes').addEventListener('click', e => { const p = pinOf(e); setCity(p ? p.dataset.city : null); });

/* ---------- How it works: each illustration loops while it is on screen (static final state under reduced motion) ---------- */
if (!reduced) {
  const clamp01 = x => Math.max(0, Math.min(1, x));
  const eio = t => t < .5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2;
  const eo = t => 1 - (1 - t) ** 3;
  const num = (el, name, v) => el.setAttribute(name, v.toFixed(3));
  /* Runs frame(seconds into the cycle) only while `el` is visible; the clock pauses off screen so the cycle resumes where it left off */
  function live(el, period, frame) {
    let raf = 0, last = 0, clock = 0;
    const tick = now => { if (last) clock = (clock + (now - last) / 1000) % period; last = now; frame(clock); raf = requestAnimationFrame(tick); };
    new IntersectionObserver(es => { const e = es[es.length - 1]; cancelAnimationFrame(raf); last = 0; if (e.isIntersecting) raf = requestAnimationFrame(tick); }, { threshold: .2 }).observe(el);
    frame(0);
  }

  /* 1 Upload: arrow bobs, file fades in, bar fills with a live percent, arrow turns into a tick with a ring pulse, then it resets */
  {
    const svg = $('#upSvg'), q = s => svg.querySelector(s);
    const arrow = q('#upArrow'), check = q('#upCheck'), ring = q('#upRing'), pill = q('#upPill'), bar = q('#upBar'), status = q('#upStatus');
    live(svg, 5.2, t => {
      const inn = eo(clamp01(t / .5)), prog = eio(clamp01((t - .5) / 2.8)), done = clamp01((t - 3.4) / .35), out = clamp01((t - 4.7) / .5), rt = clamp01((t - 3.4) / .9);
      num(pill, 'opacity', inn * (1 - out)); pill.setAttribute('transform', `translate(0 ${((1 - inn) * 10).toFixed(2)})`);
      num(bar, 'width', 136 * prog);
      status.textContent = prog >= 1 ? 'Uploaded' : `Uploading ${Math.round(prog * 100)}%`; num(status, 'opacity', inn * (1 - out));
      num(arrow, 'opacity', 1 - done * (1 - out)); arrow.setAttribute('transform', `translate(0 ${(Math.sin(t * 4.8) * 3 * (1 - done)).toFixed(2)})`);
      num(check, 'opacity', done * (1 - out)); num(check, 'stroke-dashoffset', 40 * (1 - eo(done)));
      num(ring, 'r', 26 + 24 * eo(rt)); num(ring, 'opacity', t > 3.4 ? .45 * (1 - rt) : 0);
    });
  }

  /* 2 Track: seven players drift around, a box forms on each one and follows it, the ball is passed between the four light shirts */
  {
    const svg = $('#trkSvg'), root = svg.querySelector('#trk');
    const PL = [[96, 70, 16, 11, 1, 1, 0], [130, 130, 14, 12, 1, 2, 1.7], [214, 90, 18, 12, 1, 1, 3.1], [250, 150, 15, 10, 1, 2, 4.3], [160, 76, 13, 10, 0, 1, 2.2], [196, 140, 14, 11, 0, 2, 5.2], [280, 84, 12, 12, 0, 1, .8]]; /* x, y, drift x, drift y, light shirt, y harmonic, phase */
    const OMEGA = 2 * Math.PI / 7.2, PASS = 1.8, HOLD = .35;
    root.innerHTML = '<line id="trkPass" stroke="#8FE8DA" stroke-width="2" stroke-dasharray="5 5" stroke-linecap="round"/>' +
      PL.map(p => `<circle class="trk-p" r="7" ${p[4] ? 'fill="#fff"' : 'fill="#012F2B" stroke="#fff" stroke-width="1.5"'}/>`).join('') +
      PL.map(() => '<path class="trk-b" d="M-12 -5V-12H-5M5 -12H12V-5M12 5V12H5M-5 12H-12V5" fill="none" stroke="#8FE8DA" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/>').join('') +
      '<g id="trkTag"><rect x="-40" y="0" width="80" height="18" rx="9" fill="#fff"/><text x="0" y="12.5" text-anchor="middle" font-family="DM Sans,sans-serif" font-size="11" font-weight="700" fill="#015C55"></text></g><circle id="trkBall" r="4.5" fill="#FFD60A" stroke="#06201D"/>';
    const dots = [...root.querySelectorAll('.trk-p')], boxes = [...root.querySelectorAll('.trk-b')], pass = root.querySelector('#trkPass'), tag = root.querySelector('#trkTag'), tagText = tag.querySelector('text'), ball = root.querySelector('#trkBall');
    const pos = PL.map(() => [0, 0]);
    live(svg, 7.2, t => {
      const out = clamp01((t - 6.5) / .6);
      PL.forEach((p, i) => {
        const x = p[0] + p[2] * Math.sin(OMEGA * t + p[6]), y = p[1] + p[3] * Math.cos(OMEGA * p[5] * t + p[6] * 1.3), f = eo(clamp01((t - .15 - i * .14) / .55));
        pos[i] = [x, y];
        dots[i].setAttribute('cx', x.toFixed(2)); dots[i].setAttribute('cy', y.toFixed(2));
        boxes[i].setAttribute('transform', `translate(${x.toFixed(2)} ${y.toFixed(2)}) scale(${(1 + .9 * (1 - f)).toFixed(3)})`); num(boxes[i], 'opacity', f * (1 - out));
      });
      const seg = Math.floor(t / PASS) % 4, local = (t % PASS) / PASS, a = pos[seg], b = pos[(seg + 1) % 4], k = eio(clamp01((local - HOLD) / (1 - HOLD)));
      const bx = a[0] + (b[0] - a[0]) * k + 8, by = a[1] + (b[1] - a[1]) * k + 6;
      ball.setAttribute('cx', bx.toFixed(2)); ball.setAttribute('cy', by.toFixed(2));
      pass.setAttribute('x1', a[0].toFixed(2)); pass.setAttribute('y1', a[1].toFixed(2)); pass.setAttribute('x2', b[0].toFixed(2)); pass.setAttribute('y2', b[1].toFixed(2));
      pass.setAttribute('stroke-dashoffset', (-t * 22).toFixed(2)); num(pass, 'opacity', .75 * (1 - out));
      tag.setAttribute('transform', `translate(${pos[2][0].toFixed(2)} ${(pos[2][1] - 34).toFixed(2)})`); num(tag, 'opacity', eo(clamp01((t - .15 - 2 * .14) / .55)) * (1 - out));
      tagText.textContent = `#10 · ${Math.round(30 + 2.5 * Math.sin(OMEGA * 3 * t))} km/h`;
    });
  }

  /* 3 Understand: bars fill in one after another while both numbers count up, hold, then drain and replay */
  {
    const svg = $('#cmpSvg'), bars = [...svg.querySelectorAll('.bar')], cnts = [...svg.querySelectorAll('.cnt')];
    const spec = cnts.map((c, i) => ({ a: +c.dataset.a, b: +c.dataset.b, unit: c.dataset.unit || '', w: +bars[i].dataset.w }));
    live(svg, 6, t => {
      const drain = eio(clamp01((t - 5.3) / .5));
      spec.forEach((s, i) => {
        const v = eio(clamp01((t - i * .22) / 1.5)) * (1 - drain);
        num(bars[i], 'width', s.w * v); cnts[i].textContent = `${Math.round(s.a * v)}${s.unit} · ${Math.round(s.b * v)}${s.unit}`;
      });
    });
  }
}
/* ---------- Vision model window: tabs drive the highlights; the window zooms in on scroll and leans toward the pointer ---------- */
{
  const win = $('#vwin'), shot = $('#vshot'), tabs = [...document.querySelectorAll('.vtab')];
  const layers = [...shot.querySelectorAll('[data-l]')], DUR = 7;
  /* Every ringed player in the marked frame: [ring x, ring y] in source pixels (2000 × 842), team, track id, tag offset. Tags and top-down dots are drawn from this list */
  const RING = [[1318,301,'a','9',0,16],[1413,310,'b','1',0,16],[977,375,'b','11',0,16],[1030,415,'a','4',0,16],[1296,474,'b','7',0,16],[690,444,'b','2',0,16],
    [732,550,'b','5',-34,16],[769,533,'a','6',34,16],[1064,610,'a','8',0,16],[1150,705,'b','10',0,16],[1630,655,'b','14',0,16],[1412,790,'a','12',-110,-12]].sort((p, q) => p[0] - q[0]);
  const NS = 'http://www.w3.org/2000/svg', TRACKED = '12';
  const mk = (tag, attrs, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); parent.appendChild(n); return n; };
  const gTrack = $('#vgTrack'), gDots = $('#vdots');
  RING.forEach(([x, y, team, id, dx, dy], i) => {
    const hit = id === TRACKED;
    const tag = mk('g', { class: 'vtag', style: '--i:' + i, transform: 'translate(' + (x - 48 + dx) + ' ' + (y + dy) + ')' }, gTrack);
    mk('rect', { width: 96, height: 38, rx: 19, fill: hit ? '#8FE8DA' : 'rgba(1,47,43,.82)' }, tag);
    mk('text', { x: 48, y: 28, 'text-anchor': 'middle', fill: hit ? '#012F2B' : '#fff', 'font-size': 25, 'font-weight': 700 }, tag).textContent = '#' + id;
    /* rough top-down position from where the player stands in the frame */
    mk('circle', { class: 'dot', style: '--i:' + i, cx: (14 + (x - 650) * .036).toFixed(1), cy: (6 + (y - 300) * .1).toFixed(1), r: 2.3, fill: team === 'a' ? '#F2945C' : '#fff' }, gDots);
  });
  mk('circle', { class: 'dot', style: '--i:' + RING.length, cx: 37.4, cy: 5, r: 1.4, fill: '#FFD60A' }, gDots);
  let sel = 0, held = 0, shown = reduced;
  function select(i) {
    sel = i; held = 0;
    tabs.forEach((b, k) => { b.setAttribute('aria-selected', k === i); b.tabIndex = k === i ? 0 : -1; b.style.setProperty('--p', reduced && k === i ? 1 : 0); });
    shot.setAttribute('aria-labelledby', tabs[i].id);
    shot.classList.remove('sweep'); void shot.offsetWidth;
    layers.forEach(l => l.classList.toggle('on', shown && +l.dataset.l === i));
    if (shown && !reduced) shot.classList.add('sweep');
  }
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => select(i));
    b.addEventListener('keydown', e => { const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return; e.preventDefault(); const n = (sel + d + tabs.length) % tabs.length; select(n); tabs[n].focus(); });
  });
  select(0);
  if (!reduced) {
    let raf = 0, last = 0, sc = .9, rx = 0, ry = 0, tx = 0, ty = 0;
    const tick = now => {
      const dt = last ? Math.min(.05, (now - last) / 1000) : 0; last = now;
      held += dt; if (held >= DUR) select((sel + 1) % tabs.length);
      tabs[sel].style.setProperty('--p', (held / DUR).toFixed(4));
      /* scroll progress sets the target; every value eases toward its target, so motion always starts from where it is now */
      const r = win.getBoundingClientRect(), p = Math.max(0, Math.min(1, (innerHeight - r.top) / (innerHeight * .75))), k = 1 - Math.exp(-dt * 9);
      sc += (.9 + .1 * p - sc) * k; rx += (tx + (1 - p) * 8 - rx) * k; ry += (ty - ry) * k;
      win.style.transform = 'rotateX(' + rx.toFixed(2) + 'deg) rotateY(' + ry.toFixed(2) + 'deg) scale(' + sc.toFixed(4) + ')';
      raf = requestAnimationFrame(tick);
    };
    new IntersectionObserver(es => { const e = es[es.length - 1]; cancelAnimationFrame(raf); last = 0; if (e.isIntersecting) raf = requestAnimationFrame(tick); }).observe(win);
    /* the scan replays each time the window is mostly in view, so the raw frame is always seen first */
    new IntersectionObserver(es => { const v = es[es.length - 1].intersectionRatio > .45; if (v !== shown) { shown = v; select(sel); } }, { threshold: [0, .45] }).observe(win);
    if (matchMedia('(hover:hover) and (pointer:fine)').matches) {
      win.addEventListener('pointermove', e => { const r = win.getBoundingClientRect(), x = (e.clientX - r.left) / r.width, y = (e.clientY - r.top) / r.height;
        ty = (x - .5) * 5; tx = (.5 - y) * 4; win.style.setProperty('--mx', (x * 100).toFixed(1) + '%'); win.style.setProperty('--my', (y * 100).toFixed(1) + '%'); });
      win.addEventListener('pointerleave', () => { tx = ty = 0; });
    }
  }
}
/* ---------- Live demo: one seeded sample match drives the heatmap, shot map and pass network. Harbor City attack left to right ---------- */
{
  const root = $('#demo'), svg = $('#repSvg'), under = $('#repUnder'), over = $('#repOver'), legend = $('#repLegend'), pitch = $('#repPitch');
  const NS = 'http://www.w3.org/2000/svg';
  const COL = { home: '#8FE8DA', away: '#F2945C' }, NAME = { home: 'Harbor City', away: 'Northgate United' };
  const VIEW = { heat: 'Heatmap', shots: 'Shot map', passes: 'Pass network' };
  const rng = (s => () => { s = s + 0x6D2B79F5 | 0; let t = Math.imul(s ^ s >>> 15, 1 | s); t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t; return ((t ^ t >>> 14) >>> 0) / 4294967296; })(28);
  const rand = (a, b) => a + (b - a) * rng();
  const el = (tag, attrs, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); parent.appendChild(n); return n; };

  /* Heatmaps: a 21 × 14 grid summed from a few gaussian blobs per team, [x, y, spread x, spread y, weight] */
  const BLOBS = {
    home: [[62, 34, 13, 12, 1], [82, 15, 9, 7, .75], [82, 53, 9, 7, .7], [44, 34, 10, 14, .6], [93, 34, 5, 7, .45]],
    away: [[42, 34, 13, 12, 1], [24, 17, 9, 7, .7], [24, 51, 9, 7, .7], [60, 34, 9, 12, .5], [12, 34, 5, 7, .35]],
  };
  const CH = 68 / 14;
  const heatOf = blobs => {
    const cells = [];
    for (let j = 0; j < 14; j++) for (let i = 0; i < 21; i++) {
      const x = i * 5 + 2.5, y = (j + .5) * CH;
      const v = blobs.reduce((s, [cx, cy, sx, sy, w]) => s + w * Math.exp(-((x - cx) ** 2 / (2 * sx * sx) + (y - cy) ** 2 / (2 * sy * sy))), 0);
      cells.push({ i, j, v: v * (.85 + .3 * rng()) });
    }
    const mx = Math.max(...cells.map(c => c.v));
    return cells.map(c => ({ ...c, v: c.v / mx }));
  };
  const HEAT = { home: heatOf(BLOBS.home), away: heatOf(BLOBS.away) };

  /* Shots match the full report: Harbor City 14 (6 on target, 2 goals), Northgate 9 (3 on target, 1 goal). Goals sit closest to goal */
  const SHOTS = [['home', 14, 6, 2], ['away', 9, 3, 1]].flatMap(([team, n, on, goals]) => Array.from({ length: n }, (_, k) => {
    const d = k < goals ? rand(6, 12) : rand(7, 25), y = 34 + (k < goals ? rand(-6, 6) : rand(-16, 16));
    return { team, x: team === 'home' ? 104 - d : 1 + d, y, on: k < on, goal: k < goals };
  }));
  const goalShots = t => SHOTS.filter(s => s.team === t && s.goal);
  const GOALS = [{ team: 'home', min: 23, no: 9, shot: goalShots('home')[0] }, { team: 'away', min: 51, no: 10, shot: goalShots('away')[0] }, { team: 'home', min: 78, no: 7, shot: goalShots('home')[1] }];

  /* Pass networks: average positions ([x, y, shirt]) in a 4-3-3 and a 4-4-2. Links between nearby players, thicker for more passes */
  const SHAPE = {
    home: [[8, 34, 1], [25, 11, 2], [23, 27, 4], [23, 41, 5], [25, 57, 3], [45, 20, 8], [40, 34, 6], [46, 48, 10], [67, 13, 11], [71, 34, 9], [67, 55, 7]],
    away: [[97, 34, 1], [82, 11, 2], [83, 27, 4], [83, 41, 5], [82, 57, 3], [64, 12, 7], [66, 27, 8], [66, 41, 6], [64, 56, 11], [48, 27, 9], [50, 42, 10]],
  };
  const netOf = p => {
    const raw = p.flatMap((a, i) => p.slice(i + 1).map((b, k) => ({ a: i, b: i + 1 + k, d: Math.hypot(a[0] - b[0], a[1] - b[1]) })))
      .filter(e => e.d < 27).map(e => ({ ...e, w: (1 - e.d / 27) * (.5 + rng()) }));
    const mw = Math.max(...raw.map(e => e.w)), edges = raw.map(e => ({ ...e, w: e.w / mw }));
    const inv = p.map((_, i) => edges.reduce((s, e) => s + (e.a === i || e.b === i ? e.w : 0), 0)), mi = Math.max(...inv);
    return { p, edges, inv: inv.map(v => v / mi) };
  };
  const NET = { home: netOf(SHAPE.home), away: netOf(SHAPE.away) };

  let state = { view: 'heat', team: 'home', focus: null };
  const teams = () => state.team === 'both' ? ['home', 'away'] : [state.team];
  const dot = (c, ring) => `<i style="${ring ? `border:1.5px solid ${c}` : `background:${c}`}"></i>`;
  const LEGEND = {
    heat: () => teams().map(t => `<span>${dot(COL[t])}${NAME[t]}</span>`).join('') + '<span>Brighter means more time on that part of the pitch.</span>',
    shots: () => `<span>${dot('#FFD60A')}Goal</span><span>${dot('#9DB5B1')}On target</span><span>${dot('#9DB5B1', true)}Off target</span>`,
    passes: () => '<span>Thicker line: more passes between two players. Bigger circle: more involved.</span>',
  };

  const drawHeat = both => teams().forEach(t => {
    const g = el('g', { class: 'heat', filter: 'url(#repBlur)' }, under);
    /* A power curve keeps the cool zones dark, so the hot spots stand out */
    HEAT[t].filter(c => c.v > .1).forEach(c => el('rect', { x: c.i * 5, y: (c.j * CH).toFixed(2), width: 5.2, height: (CH + .2).toFixed(2), fill: COL[t], 'fill-opacity': (c.v ** 1.4 * (both ? .7 : 1)).toFixed(2) }, g));
  });
  const drawShots = () => SHOTS.filter(s => teams().includes(s.team)).forEach((s, k) => {
    const c = s.goal ? '#FFD60A' : COL[s.team];
    el('circle', { class: 'pop', style: `--d:${k * 35}`, cx: s.x.toFixed(2), cy: s.y.toFixed(2), r: s.goal ? 1.7 : 1.3, fill: s.on ? c : 'none', stroke: s.on ? '#012F2B' : c, 'stroke-width': s.on ? .35 : .45 }, over);
  });
  const drawPasses = () => teams().forEach(t => {
    const { p, edges, inv } = NET[t];
    edges.forEach((e, k) => el('line', { class: 'edge', style: `--d:${k * 14}`, x1: p[e.a][0], y1: p[e.a][1], x2: p[e.b][0], y2: p[e.b][1], pathLength: 1, 'stroke-dasharray': 1,
      stroke: COL[t], 'stroke-opacity': (.25 + .6 * e.w).toFixed(2), 'stroke-width': (.25 + 1.3 * e.w).toFixed(2), 'stroke-linecap': 'round' }, over));
    p.forEach(([x, y, no], i) => {
      const g = el('g', { class: 'pop', style: `--d:${180 + i * 30}` }, over);
      el('circle', { cx: x, cy: y, r: (1.8 + 1.4 * inv[i]).toFixed(2), fill: COL[t], stroke: '#012F2B', 'stroke-width': .4 }, g);
      el('text', { x, y: y + .75, 'text-anchor': 'middle', 'font-size': 2.1, 'font-weight': 700, 'font-family': 'DM Sans,sans-serif', fill: '#012F2B' }, g).textContent = no;
    });
  });
  /* A selected goal gets a pulsing ring and a label, kept inside the pitch */
  const drawFocus = ({ shot, min, no }) => {
    const g = el('g', {}, over), lx = Math.min(Math.max(shot.x, 13), 92), ly = shot.y > 14 ? shot.y - 6.5 : shot.y + 7.5;
    el('circle', { class: 'ring', cx: shot.x.toFixed(2), cy: shot.y.toFixed(2), r: 3.2, fill: 'none', stroke: '#FFD60A', 'stroke-width': .5 }, g);
    el('rect', { class: 'pop', x: lx - 11, y: ly - 2.8, width: 22, height: 5.6, rx: 2.8, fill: '#fff' }, g);
    el('text', { class: 'pop', x: lx, y: ly + .85, 'text-anchor': 'middle', 'font-size': 2.5, 'font-weight': 700, 'font-family': 'DM Sans,sans-serif', fill: '#015C55' }, g).textContent = `${min}' · #${no} scores`;
  };
  function draw() {
    under.replaceChildren(); over.replaceChildren();
    if (state.view === 'heat') drawHeat(state.team === 'both');
    if (state.view === 'shots') drawShots();
    if (state.view === 'passes') drawPasses();
    if (state.focus) drawFocus(state.focus);
    legend.innerHTML = LEGEND[state.view]();
    svg.setAttribute('aria-label', `${VIEW[state.view]} for ${state.team === 'both' ? 'both teams' : NAME[state.team]}`);
  }

  const tabs = [...root.querySelectorAll('[data-view]')], teamBtns = [...root.querySelectorAll('[data-team]')], moms = [...root.querySelectorAll('[data-goal]')];
  const segs = [...root.querySelectorAll('.seg')];
  const placeThumb = seg => {
    const on = seg.querySelector('[aria-selected="true"], [aria-pressed="true"]'), th = seg.querySelector('.thumb');
    th.style.width = on.offsetWidth + 'px'; th.style.transform = `translateX(${on.offsetLeft}px)`;
  };
  let drawn = false;
  function set(next) {
    state = { ...state, ...next };
    tabs.forEach(b => { const on = b.dataset.view === state.view; b.setAttribute('aria-selected', on); b.tabIndex = on ? 0 : -1; });
    teamBtns.forEach(b => b.setAttribute('aria-pressed', b.dataset.team === state.team));
    moms.forEach(b => b.setAttribute('aria-pressed', state.focus === GOALS[+b.dataset.goal]));
    segs.forEach(placeThumb);
    if (drawn) draw();
  }
  tabs.forEach((b, i) => {
    b.addEventListener('click', () => set({ view: b.dataset.view, focus: null }));
    b.addEventListener('keydown', e => {
      const d = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0; if (!d) return;
      e.preventDefault(); const n = tabs[(i + d + tabs.length) % tabs.length]; n.focus(); n.click();
    });
  });
  teamBtns.forEach(b => b.addEventListener('click', () => set({ team: b.dataset.team, focus: null })));
  moms.forEach(b => b.addEventListener('click', () => {
    const g = GOALS[+b.dataset.goal];
    set(state.focus === g ? { focus: null } : { view: 'shots', team: g.team, focus: g });
    /* On phones the pitch sits above the list: bring it back into view so the highlight is seen */
    if (pitch.getBoundingClientRect().top < 0) pitch.scrollIntoView({ block: 'center', behavior: reduced ? 'auto' : 'smooth' });
  }));
  set({});
  requestAnimationFrame(() => segs.forEach(s => s.classList.add('ready')));
  if ('ResizeObserver' in window) { const ro = new ResizeObserver(() => segs.forEach(placeThumb)); segs.forEach(s => ro.observe(s)); }
  /* First draw waits until the pitch is on screen, so its entrance plays where it can be seen */
  const first = () => { drawn = true; draw(); };
  if ('IntersectionObserver' in window) {
    const io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) { io.disconnect(); first(); } }, { threshold: .35 });
    io.observe(pitch);
  } else first();
}
})();
