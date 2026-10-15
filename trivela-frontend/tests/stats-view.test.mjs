import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';

const SV = createRequire(import.meta.url)('../landing/assets/js/stats-view.js');
const pitch = (inner, label) => `<svg aria-label="${label}">${inner}</svg>`;
const base = () => ({
  home: 'Harbor City', away: 'Northgate', color: { home: '#015C55', away: '#E07A3F' },
  defs: [{ g: 'Possession', k: 'Possession', u: '%' }, { g: 'Possession', k: 'Dispossessed' }, { g: 'Passing', k: 'Passes' }, { g: 'Passing', k: 'Pass accuracy', u: '%' }, { g: 'Defending', k: 'Saves' }],
  stats: { Possession: [58, 42], Dispossessed: [9, 4], Passes: [166, 120], 'Pass accuracy': [83, 79], Saves: [2, 5] },
  shots: null, heat: null, players: [], facts: [],
});

test('the side with the higher value leads, except where fewer is better', () => {
  assert.equal(SV.leader('Passes', 166, 120), 'home');
  assert.equal(SV.leader('Dispossessed', 9, 4), 'away');
  assert.equal(SV.leader('Saves', 2, 5), null);
  assert.equal(SV.leader('Passes', 10, 10), null);
});

test('attacking lanes only count the opponent half and add up to 100', () => {
  const nx = 4, ny = 3, grid = Array(nx * ny).fill(0);
  grid[0 * nx + 3] = 10; grid[1 * nx + 3] = 30; grid[2 * nx + 2] = 60; grid[1 * nx + 0] = 999;   // the 999 is in home's own half
  assert.deepEqual(SV.lanes(grid, [nx, ny], 'home'), [10, 30, 60]);
  assert.equal(SV.lanes(Array(5).fill(1), [nx, ny], 'home'), null);
});

test('a report without pitch mapping keeps the same layout, with notes and dashes for what is missing', () => {
  const m = { ...base(), defs: [...base().defs, { g: 'Attacking', k: 'Shots' }, { g: 'Attacking', k: 'Shots on target' }] };
  const html = SV.render(m, { shot: 0, ptab: 'top' }, pitch);
  assert.match(html, /Shot map[\s\S]*couldn&#39;t map the pitch/);
  assert.match(html, /Attacking zones[\s\S]*Needs the pitch mapping/);
  assert.match(html, /sv-row off"><span class="sv-v">—<\/span><span class="sv-k">Shots<\/span>/);
  assert.match(html, /not measured in this clip/);
  assert.match(html, /166 <small>\(83%\)<\/small>/);
});

test('top stats keep measured rows first when there are more than fit', () => {
  const defs = ['xG', 'Shots', 'Shots on target', 'Touches in opposition box', 'Passes', 'Corners', 'Tackles', 'Distance covered', 'Top speed'].map(k => ({ g: 'Attacking', k }));
  const m = { ...base(), defs, stats: { Passes: [1, 2], 'Top speed': [30, 31] } };
  const top = SV.render(m, { shot: 0 }, pitch).split('Top stats')[1].split('</section>')[0];
  assert.match(top, /Top speed/);   // measured, so it stays although it is ninth
  assert.doesNotMatch(top, /Distance covered/);   // the last unmeasured one made room
  assert.equal((top.match(/class="sv-row[ "]/g) || []).length, 8);
});

test('shots are drawn and the selected one is stepped within range', () => {
  const m = { ...base(), shots: [{ x: 90, y: 30, team: 'home', on: true, xg: .3 }, { x: 12, y: 40, team: 'away', on: false, xg: .05, kmh: 88 }] };
  const html = SV.render(m, { shot: 9, ptab: 'top' }, pitch);
  assert.match(html, /2 \/ 2/);
  assert.match(html, /88 km\/h/);
  assert.equal((html.match(/data-sv-shot=/g) || []).length, 2);
});

test('defensive contribution adds tackles, interceptions and clearances, for teams and players', () => {
  const m = { ...base(), stats: { ...base().stats, Tackles: [10, 12], Interceptions: [5, 3], Clearances: [8, 14] },
    defs: [...base().defs, { g: 'Defending', k: 'Tackles' }, { g: 'Defending', k: 'Interceptions' }, { g: 'Defending', k: 'Clearances' }],
    players: [{ id: 't1', team: 'home', badge: 1, name: 'Track 1', v: { tackles: 2, interceptions: 1 } }, { id: 't2', team: 'away', badge: 2, name: 'Track 2', v: { distance: 4 } }] };
  const d = SV.withDerived(m);
  assert.deepEqual(d.stats['Defensive contribution'], [23, 29]);
  assert.equal(d.defs[0].k, 'Defensive contribution');
  assert.equal(d.players[0].v.def, 3);
  assert.equal(d.players[1].v.def, undefined);
  assert.equal(m.stats['Defensive contribution'], undefined);   // the input is left as it was
  assert.match(SV.render(m, { shot: 0, ptab: 'defending' }, pitch), /Def\. contribution/);
  assert.equal(SV.withDerived(base()).stats['Defensive contribution'], undefined);   // nothing to add without the parts
});

const withPeople = () => ({ ...base(),
  stats: { ...base().stats, 'Shots on target': [6, 3], xG: [1.4, .6] },
  players: [{ id: 't1', team: 'home', badge: 1, name: 'Track 1', v: { distance: 3.2, passes: 12, final_third_s: 90, tackles: 2, interceptions: 1 } }],
  keepers: [{ id: 't5', team: 'away', badge: 'GK', name: 'Goalkeeper', v: { saves: 4, touches: 9 } }] });

test('player stats always show the six match-centre tabs', () => {
  const html = SV.render({ ...base(), players: [{ id: 't1', team: 'home', badge: 1, name: 'Track 1', v: { distance: 1 } }] }, { ptab: 'duels' }, pitch);
  const tabs = [...html.matchAll(/data-sv-ptab="[^"]+"[^>]*>([^<]+)</g)].map(m => m[1]);
  assert.deepEqual(tabs, ['Top stats', 'Attack', 'Passes', 'Defense', 'Duels', 'Goalkeeping']);
  assert.match(html, /Duels won/);
  assert.match(html, /<td class="r num"><span class="">—<\/span>/);   // not measured: a dash, not a missing column
});

test('goalkeeping lists only the keepers, with what each faced from the other side', () => {
  const html = SV.render(withPeople(), { ptab: 'keepers' }, pitch);
  assert.match(html, /<th>Goalkeeper<\/th>/);
  assert.doesNotMatch(html, /Track 1/);
  const row = html.split('<tbody>')[1];
  assert.match(row, />4<\/span>/);       // saves
  assert.match(row, />6<\/span>/);       // the keeper is away, so he faced home's 6 shots on target
  assert.match(row, />1\.40<\/span>/);   // and home's xG
  assert.match(row, /data-player="t5"/);   // keepers open their card too
});

test('player card: name and team, on/off-the-ball heatmap, only his measured stats, no rating or bio', () => {
  const m = { ...withPeople(), playerHeatSize: [2, 1] };
  m.players[0].heat = { all: [99, 40], on: [99, 0], off: [0, 99] };
  const card = SV.playerCard(m, 't1', { mode: 'on' }, pitch);
  assert.equal(card.label, 'Track 1');
  assert.match(card.html, /<h2>Track 1<\/h2><span>Harbor City<\/span>/);
  assert.match(card.html, /data-pc-mode="on" aria-selected="true"/);
  assert.equal((card.html.match(/<circle /g) || []).length, 2);   // the one on-ball cell: a team-colour blob and a hot core
  assert.match(card.html, /Def\. contribution<\/dt><dd class="num">3/);
  assert.match(card.html, /Final third<\/dt><dd class="num">1\.5 min/);
  assert.doesNotMatch(card.html, /Shots<\/dt>/);   // not measured for him, so not listed
  assert.doesNotMatch(card.html, /rating|Position|Country|Age/i);
  assert.match(card.html, /data-pc-go="t5"/);      // › goes on to the keeper
  assert.match(card.html, /class="pc-nav" disabled aria-label="Previous player"/);
});

test('player card for a keeper leads with goalkeeping, and explains a missing on/off split', () => {
  const m = { ...withPeople(), playerHeatSize: [2, 1] };
  m.keepers[0].heat = { all: [50, 99] };
  const card = SV.playerCard(m, 't5', { mode: 'off' }, pitch);
  assert.match(card.html, /<h3>Goalkeeping<\/h3><dl class="pc-list"><div><dt>Saves<\/dt>/);
  assert.match(card.html, /no on\/off-the-ball split yet/);
  assert.equal(SV.playerCard(m, 'nobody', {}, pitch), null);
});

test('outfield tabs work out final-third minutes and defensive contribution per player', () => {
  const attack = SV.render(withPeople(), { ptab: 'attack' }, pitch).split('<tbody>')[1];
  assert.match(attack, /1\.5 min/);
  const defense = SV.render(withPeople(), { ptab: 'defending' }, pitch).split('<tbody>')[1];
  assert.match(defense, /sv-best">3</);
});

test('the goalkeeping tab explains when no keeper was identified', () => {
  const html = SV.render({ ...withPeople(), keepers: [] }, { ptab: 'keepers' }, pitch);
  assert.match(html, /goalkeepers weren't identified/);
});

test('player table has no rating column and escapes names', () => {
  const m = { ...base(), players: [{ id: 't1', team: 'home', badge: 1, name: '<b>x</b>', v: { distance: 3.2, passes: 12 } }] };
  const html = SV.render(m, { shot: 0, ptab: 'top' }, pitch);
  assert.doesNotMatch(html, /rating/i);
  assert.match(html, /&lt;b&gt;x&lt;\/b&gt;/);
});
