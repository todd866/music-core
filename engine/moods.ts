import type { Track } from './space.ts';

// Moods are stations on the tuner. Each one is a soft filter over the map plus an energy
// target; the queue is then built from embedding neighbours around its centroid.
export type Mood = {
  id: string;
  label: string;
  freq: string; // printed on the dial
  member: (t: Track) => boolean;
  arc?: number; // overrides the time-of-day energy target
};

export const MOODS: Mood[] = [
  { id: 'chill', label: 'Chill', freq: '88.3', member: (t) => t.k === 'chill' || t.e < 0.25, arc: 0.25 },
  { id: 'alt', label: 'Alt', freq: '91.7', member: (t) => t.k === 'alt' },
  { id: 'drive', label: 'Drive', freq: '94.5', member: (t) => (t.k === 'alt' || t.k === 'dance') && t.e > 0.45 && t.e < 0.9, arc: 0.65 },
  { id: 'dance', label: 'Dance', freq: '97.9', member: (t) => t.k === 'dance' },
  { id: 'rap', label: 'Rap', freq: '100.1', member: (t) => t.k === 'rap' },
  { id: 'heavy', label: 'Heavy', freq: '103.3', member: (t) => t.h > 0.8, arc: 0.75 },
  { id: 'wide', label: 'Wide', freq: '106.9', member: () => true },
  { id: 'hiphop', label: 'Hip hop', freq: '', member: (t) => t.k === 'rap' || /hip-hop|rap/i.test(t.g) },
  // Home chips
  { id: 'energise', label: 'Energise', freq: '', member: (t) => t.e > 0.55 && t.e < 0.9 && t.k !== 'chill', arc: 0.7 },
  { id: 'focus', label: 'Focus', freq: '', member: (t) => t.e > 0.2 && t.e < 0.55 && t.k !== 'rap' && t.h < 0.6, arc: 0.4 },
  { id: 'workout', label: 'Workout', freq: '', member: (t) => t.e > 0.75 || t.h > 0.85, arc: 0.85 },
  { id: 'party', label: 'Party', freq: '', member: (t) => (t.k === 'dance' || t.k === 'rap') && t.e > 0.5, arc: 0.75 },
];

import { stationById } from './stations.ts';
import { stationsOf } from './source.ts';

export function moodById(id: string | undefined): Mood | undefined {
  if (id?.startsWith('station:')) { // what this station played that's in the library
    const st = stationById(id.slice(8));
    if (!st) return undefined;
    const set = new Set(st.shas);
    const sid = st.id;
    return { id, label: st.name, freq: '', member: (t) => set.has(t.s) || stationsOf(t).includes(sid) };
  }
  return MOODS.find((m) => m.id === id);
}
