// CLI test: same pinned inputs as engine.test.ts's golden case -> identical picks on stdout,
// proving the CLI layer adds no drift. Missing inputs exit 1 with a one-line stderr.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

function fixtureDir() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'engine-cli-'));
  const dims = 4;
  const vec = (seed) => Buffer.from(Array.from({ length: dims }, (_, i) => Math.round(127 * Math.sin(seed + i * 0.7)))).toString('base64');
  const tracks = [1, 2, 3, 4, 5, 6, 7, 8].map((n) => ({
    s: `sha${n}`, a: n % 2 ? `Artist ${n}` : 'Artist 1', t: `Title ${n}`, al: '', y: 1990 + n, g: '', p: n % 3, f: n % 2,
    k: ['chill', 'alt', 'dance', 'rap'][n % 4], key: `artist ${n}|title ${n}`, x: (n - 4.5) / 4, yy: (n % 3) / 3, c: n % 2,
    e: 0.2 + 0.1 * n, h: 0.3, q: 0.5, n: [], v: vec(n), nw: n % 2,
  }));
  fs.writeFileSync(path.join(dir, 'space.json'), JSON.stringify({
    generatedAt: '2026-11-20T00:00:00Z', dims, tracks,
    clusters: [
      { id: 0, n: 4, kind: 'chill', genres: ['ambient'], artists: ['Artist 1'], x: -0.5, y: 0, e: 0.2, h: 0.3 },
      { id: 1, n: 4, kind: 'dance', genres: ['house'], artists: ['Artist 2'], x: 0.5, y: 0.3, e: 0.8, h: 0.4 },
    ],
    stats: { tracks: 8, embedded: 8, catalogue: 8, gaps: { embeddedNotInR2: 0, inR2NotEmbedded: 0 } },
  }));
  fs.writeFileSync(path.join(dir, 'years.json'), JSON.stringify(Object.fromEntries(tracks.map((t) => [t.s, t.y]))));
  return dir;
}

const profile = { user: 'ian', style: 'home', tz: 'Australia/Perth', likes: ['sha2'], dislikes: [], skips: { sha7: 2 }, heard: { sha3: Date.parse('2026-11-19T00:00:00Z') }, events: [] };

test('queue mode: pinned inputs -> golden picks on stdout', () => {
  const dir = fixtureDir();
  const prof = path.join(dir, 'p.json');
  fs.writeFileSync(prof, JSON.stringify(profile));
  const out = JSON.parse(execFileSync('node', ['--experimental-strip-types', path.join(here, 'cli.mjs'),
    'queue', '--mood', 'chill', '--n', '6', '--space', dir, '--profile', prof, '--seed', '42', '--now', '2026-11-20T02:00:00Z'], { encoding: 'utf8' }));
  assert.deepEqual(out.picks.map((p) => ({ s: p.s, slot: p.slot, src: p.src })), [
    { s: 'sha8', slot: 0, src: 'control' }, { s: 'sha5', slot: 1, src: 'ai' }, { s: 'sha4', slot: 2, src: 'ai' },
    { s: 'sha3', slot: 3, src: 'ai' }, { s: 'sha2', slot: 4, src: 'ai' }, { s: 'sha1', slot: 5, src: 'ai' },
  ]);
});

test('missing profile exits 1 with one stderr line', () => {
  const dir = fixtureDir();
  assert.throws(() => execFileSync('node', ['--experimental-strip-types', path.join(here, 'cli.mjs'),
    'queue', '--mood', 'chill', '--space', dir, '--profile', path.join(dir, 'nope.json')]), (e) => {
    assert.match(e.stderr.toString(), /^engine-cli: cannot read --profile .*$/m);
    assert.equal(e.status, 1);
    return true;
  });
});
