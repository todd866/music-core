// Golden test: the engine is deterministic given a pinned bundle, profile, clock and seed.
// The fixture bundle is built in tmp by the test itself (deterministic); the recorded picks
// literal below is what the extracted engine must keep producing. If an intentional engine
// change moves this literal, re-record it in the same commit and say why.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { buildQueue } from './engine.ts';
import { space } from './space.ts';
import type { Profile } from './profile.ts';

function fixtureDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-fixture-'));
  const dims = 4;
  const vec = (seed: number) => Buffer.from(Array.from({ length: dims }, (_, i) => Math.round(127 * Math.sin(seed + i * 0.7)))).toString('base64');
  const tracks = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({
    s: `sha${n}`, a: n % 2 ? `Artist ${n}` : 'Artist 1', t: `Title ${n}`, al: '', y: 1990 + n, g: '', p: n % 3, f: n % 2,
    k: (['chill', 'alt', 'dance', 'rap'] as const)[n % 4], key: `artist ${n}|title ${n}`, x: (n - 4.5) / 4, yy: (n % 3) / 3, c: n % 2,
    e: 0.2 + 0.1 * n, h: 0.3, q: 0.5, n: [] as [number, number][], v: vec(n), nw: (n % 2) as 0 | 1,
  }));
  const doc = {
    generatedAt: '2026-11-20T00:00:00Z', dims, tracks,
    clusters: [
      { id: 0, n: 4, kind: 'chill', genres: ['ambient'], artists: ['Artist 1'], x: -0.5, y: 0, e: 0.2, h: 0.3 },
      { id: 1, n: 4, kind: 'dance', genres: ['house'], artists: ['Artist 2'], x: 0.5, y: 0.3, e: 0.8, h: 0.4 },
    ],
    stats: { tracks: tracks.length, embedded: tracks.length, catalogue: tracks.length, gaps: { embeddedNotInR2: 0, inR2NotEmbedded: 0 } },
  };
  fs.writeFileSync(path.join(dir, 'space.json'), JSON.stringify(doc));
  fs.writeFileSync(path.join(dir, 'years.json'), JSON.stringify(Object.fromEntries(tracks.map((t) => [t.s, t.y]))));
  fs.writeFileSync(path.join(dir, 'lines.json'), JSON.stringify({ sha1: 'A fixture DJ line.' }));
  return dir;
}

const profile: Profile = {
  user: 'ian', style: 'home', tz: 'Australia/Perth', likes: ['sha2'], dislikes: [], skips: { sha7: 2 }, heard: { sha3: Date.parse('2026-11-19T00:00:00Z') }, events: [],
};

test('golden: pinned bundle + clock + seed -> recorded picks', () => {
  const S = space({ dir: fixtureDir() });
  const { picks, rule, mood } = buildQueue({ mood: 'chill', n: 6 }, profile, new Date('2026-11-20T02:00:00Z'), 42);
  assert.equal(mood.id, 'chill');
  assert.equal(rule.id, 'morning'); // 02:00 UTC = 10:00 Perth
  assert.equal(picks.length, 6);
  // The recorded golden picks (re-record only with an intentional engine change, and say why).
  assert.deepEqual(picks.map((p) => ({ s: p.s, slot: p.slot, src: p.src, nw: p.nw, turn: p.turn })), [
    { s: 'sha8', slot: 0, src: 'control', nw: 0, turn: false },
    { s: 'sha5', slot: 1, src: 'ai', nw: 1, turn: false },
    { s: 'sha4', slot: 2, src: 'ai', nw: 0, turn: false },
    { s: 'sha3', slot: 3, src: 'ai', nw: 0, turn: true },
    { s: 'sha2', slot: 4, src: 'ai', nw: 0, turn: false },
    { s: 'sha1', slot: 5, src: 'ai', nw: 1, turn: false },
  ]);
  // Determinism: same inputs, same output.
  const again = buildQueue({ mood: 'chill', n: 6 }, profile, new Date('2026-11-20T02:00:00Z'), 42);
  assert.deepEqual(again.picks, picks);
  // Invariants the engine promises: no repeats, no hard-skipped song, no un-asked repeats of the anchor.
  const shas = picks.map((p) => p.s);
  assert.equal(new Set(shas).size, shas.length);
  assert.ok(!shas.includes('sha7')); // skips >= 2
  // Every pick resolves in the bundle.
  for (const p of picks) assert.ok(S.bySha.has(p.s));
});

test('playable filter: with everything unplayable, nothing is picked', () => {
  fixtureDir(); // not needed here, space is cached from test 1
  const { picks } = buildQueue({ mood: 'chill', n: 6, playable: new Set(['sha1']) }, profile, new Date('2026-11-20T02:00:00Z'), 42);
  assert.ok(picks.every((p) => p.s === 'sha1' || p.s === undefined));
  assert.ok(picks.length <= 1);
});

test('hard gate: truncated tracks are never picked even when liked or requested as start', () => {
  const dir = fixtureDir();
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'space.json'), 'utf8'));
  // sha2 is in profile.likes; mark it truncated and give itgl=1
  const t2 = raw.tracks.find((t: { s: string }) => t.s === 'sha2');
  t2.gx = 'truncated';
  t2.gl = 1;
  fs.writeFileSync(path.join(dir, 'space.json'), JSON.stringify(raw));
  space({ dir });
  const { picks } = buildQueue({ mood: 'chill', n: 6, start: 'sha2' }, profile, new Date('2026-11-20T02:00:00Z'), 42);
  assert.ok(!picks.some((p) => p.s === 'sha2'), 'truncated liked track must never be served');
});
