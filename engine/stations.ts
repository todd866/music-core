// Station mixes: what each station actually played (data/stations.json, built by
// tools/build_stations.py). Extracted from music-radio/lib/stations.ts; the data dir is
// resolved the same way as space.ts (MUSIC_ENGINE_DATA or cwd/data).
import fs from 'node:fs';
import path from 'node:path';
import { engineDataDir } from './space.ts';

export type Station = { id: string; name: string; color: string; live: string | null; liveType: 'hls' | 'direct'; logo: boolean; count: number; shas: string[]; played: Record<string, string>; missing?: { artist: string; title: string; plays: number; art: string | null }[] };
let cache: Station[] | null = null;
export function stations(): Station[] {
  if (cache) return cache;
  try { cache = JSON.parse(fs.readFileSync(path.join(engineDataDir(), 'stations.json'), 'utf8')).stations; } catch { cache = []; }
  return cache!;
}
export const stationById = (id: string) => stations().find((s) => s.id === id);
