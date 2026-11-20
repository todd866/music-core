// The map: track metadata plus int8 PCA vectors, loaded from a space.json bundle.
// Extracted from music-radio/lib/space.ts (20 Nov 2026): the pure loader and vector math
// live here; the R2-backed runtime refresh (refreshExtra/placeUnembedded) stays in
// music-radio, which layers it on top of this package's exports.
import fs from 'node:fs';
import path from 'node:path';

export type Track = {
  s: string; a: string; t: string; al: string; y: number | null; g: string; p: number; f: number;
  k: 'dance' | 'alt' | 'rap' | 'chill'; key: string; x: number; yy: number; c: number;
  e: number; h: number; q: number; n: [number, number][]; v: string;
  nw: 0 | 1; gx?: string; gl?: 1; d?: number; sc?: { src: string; why?: string | null; at?: string; seed?: string | null };
  o?: 1; // his own library (not a find)
  px?: 1; // not embedded yet: placed by its artist / seed / station's songs (no map-neighbour claims)
};
export type Cluster = { id: number; n: number; kind: string; genres: string[]; artists: string[]; x: number; y: number; e: number; h: number };
export type Space = {
  generatedAt: string; dims: number; tracks: Track[]; clusters: Cluster[];
  stats: { tracks: number; embedded: number; catalogue: number; gaps: { embeddedNotInR2: number; inR2NotEmbedded: number } };
};

export type Loaded = Space & { vec: Float32Array[]; bySha: Map<string, number>; clusterName: string[]; lines: Record<string, string> };

let cache: Loaded | null = null;

export type SpaceOpts = {
  dir?: string; // directory holding space.json / lines.json / years.json (default: MUSIC_ENGINE_DATA or cwd/data)
  years?: Record<string, number | null>; // sha -> year override (music-radio passes unified facts)
};

// Years come only from MusicBrainz: first from the caller's facts, falling back to the
// bundled years.json. Library and file-tag years are never shown.
let YEARS: Record<string, number | null> = {};
export const sourcedYear = (sha: string): number | null => YEARS[sha] ?? null;

export const engineDataDir = () => process.env.MUSIC_ENGINE_DATA ?? path.join(process.cwd(), 'data');

export function decodeVec(v: string, dims: number): Float32Array {
  const b = Buffer.from(v, 'base64');
  const f = new Float32Array(dims);
  let n = 0;
  for (let i = 0; i < dims; i++) { f[i] = (b[i] << 24 >> 24) / 127; n += f[i] * f[i]; }
  n = Math.sqrt(n) || 1;
  for (let i = 0; i < dims; i++) f[i] /= n;
  return f;
}

function energyWord(e: number) {
  return e > 0.75 ? 'Bright' : e > 0.55 ? 'Driving' : e > 0.35 ? 'Mid-tempo' : 'Slow';
}

export function space(opts: SpaceOpts = {}): Loaded {
  if (cache && !opts.dir) return cache; // years apply on first build only; runtime refresh mutates the cache in place
  const dir = opts.dir ?? engineDataDir();
  const raw = JSON.parse(fs.readFileSync(path.join(dir, 'space.json'), 'utf8')) as Space;
  try { YEARS = opts.years ?? JSON.parse(fs.readFileSync(path.join(dir, 'years.json'), 'utf8')); } catch { YEARS = opts.years ?? {}; }
  for (const t of raw.tracks) t.y = sourcedYear(t.s);
  const vec = raw.tracks.map((t) => decodeVec(t.v, raw.dims));
  const bySha = new Map(raw.tracks.map((t, i) => [t.s, i] as [string, number]));
  // Name each corner after its energy, genre and an artist no other corner is named after.
  const usedArtists = new Set<string>();
  const order = raw.clusters.map((c, i) => i).sort((a, b) => raw.clusters[b].n - raw.clusters[a].n);
  const clusterName: string[] = [];
  for (const i of order) {
    const c = raw.clusters[i];
    const genre = (c.genres[0] || c.kind).replace('Hip-Hop/Rap', 'hip-hop').replace('R&B/Soul', 'soul').toLowerCase();
    const artist = c.artists.find((a) => !usedArtists.has(a)) ?? c.artists[0];
    usedArtists.add(artist);
    clusterName[i] = `${energyWord(c.e)} ${genre} · ${artist}`;
  }
  let lines: Record<string, string> = {};
  try { lines = JSON.parse(fs.readFileSync(path.join(dir, 'lines.json'), 'utf8')); } catch {}
  cache = { ...raw, vec, bySha, clusterName, lines };
  return cache;
}

export function dot(a: Float32Array, b: Float32Array) {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += a[i] * b[i];
  return s;
}

export function centroid(vs: Float32Array[]): Float32Array | null {
  if (!vs.length) return null;
  const d = vs[0].length;
  const c = new Float32Array(d);
  for (const v of vs) for (let i = 0; i < d; i++) c[i] += v[i];
  let n = 0;
  for (let i = 0; i < d; i++) n += c[i] * c[i];
  n = Math.sqrt(n) || 1;
  for (let i = 0; i < d; i++) c[i] /= n;
  return c;
}

export function primaryArtist(a: string) {
  return a.toLowerCase().split(/,| & | feat\.? | ft\.? | x /)[0].trim();
}
