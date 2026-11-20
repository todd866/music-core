#!/usr/bin/env node
// The engine CLI for the Macs (and any runtime with a local store copy): builds a queue from
// a local space bundle and a listener profile or taste snapshot, printing queue JSON to stdout.
// Run with: node --experimental-strip-types cli.mjs queue --mood chill ...
import { buildQueue } from './engine.ts';
import { space } from './space.ts';
import { readFileSync } from 'node:fs';

const arg = (name) => {
  const i = process.argv.indexOf(`--${name}`);
  return i > 0 ? process.argv[i + 1] : undefined;
};
const fail = (msg) => { process.stderr.write(`engine-cli: ${msg}\n`); process.exit(1); };

const mode = process.argv[2];
if (mode !== 'queue') fail(`unknown mode ${JSON.stringify(mode ?? '')} — usage: queue --mood <id> [--space DIR] [--profile PATH | --store DIR --listener NAME] [--n 10] [--seed N] [--now ISO] [--playable PATH]`);

const dir = arg('space');
const profilePath = arg('profile');
const store = arg('store');
const listener = arg('listener');
let profile;
if (profilePath) {
  try { profile = JSON.parse(readFileSync(profilePath, 'utf8')); } catch (e) { fail(`cannot read --profile ${profilePath}: ${e.message}`); }
} else if (store && listener) {
  const p = `${store.replace(/\/$/, '')}/music/taste/${listener}/snapshot.json`;
  try { profile = JSON.parse(readFileSync(p, 'utf8')); } catch (e) { fail(`cannot read snapshot ${p}: ${e.message}`); }
} else fail('need --profile PATH or --store DIR --listener NAME');

let playable;
const playablePath = arg('playable');
if (playablePath) {
  try { playable = new Set(JSON.parse(readFileSync(playablePath, 'utf8')).shas ?? []); } catch (e) { fail(`cannot read --playable ${playablePath}: ${e.message}`); }
}

const req = {
  mood: arg('mood') ?? fail('--mood is required'),
  n: arg('n') ? Number(arg('n')) : undefined,
  anchor: arg('anchor'),
  start: arg('start'),
  playable, // songs this Mac can actually play; without it the queue may pick unplayable tracks
};
const seed = arg('seed') ? Number(arg('seed')) : Date.now();
const now = arg('now') ? new Date(arg('now')) : new Date();

// --space must steer every reader, including stations.json (engineDataDir), not just space().
if (dir) process.env.MUSIC_ENGINE_DATA = dir;
space(dir ? { dir } : {});
const out = buildQueue(req, profile, now, seed);
process.stdout.write(JSON.stringify(out, null, 2) + '\n');
