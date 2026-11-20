import type { Track } from './space.ts';

// Where a find came from, from its own record (scout ledger or the lane/request log): a short chip
// and a "why" phrase. Never inferred: a tag we can't name says "new find", nothing more.
const NAMES: Record<string, string> = { bbc6: '6 Music', triplej: 'triple j', doublej: 'Double J', kexp: 'KEXP', nts: 'NTS', wfmu: 'WFMU', rrr: 'RRR', pbs: 'PBS', '4zzz': '4ZZZ', rtr: 'RTRFM', kcrw: 'KCRW', rp: 'Radio Paradise' };
export const stationName = (id: string) => NAMES[id] ?? id.toUpperCase(); // names as in tools/build_stations.py
const list = (xs: string[]) => (xs.length <= 1 ? xs.join('') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);

// Stations a find was played on, by id (from its source tag and note).
export function stationsOf(t: Pick<Track, 'sc'>): string[] {
  const src = t.sc?.src ?? '';
  if (src.startsWith('station:')) return [src.slice(8)];
  if (src.startsWith('radio:')) return [src.slice(6).toLowerCase()];
  if (src === 'triplej') return ['triplej'];
  if (src === 'radio' && t.sc?.why) return t.sc.why.split('·')[0].split('+').map((x) => x.trim()).filter((x) => /^[a-z0-9]+$/.test(x));
  return [];
}

export function sourceOf(t: Pick<Track, 'sc'>): { chip: string; why: string } | null {
  const src = t.sc?.src;
  if (!src) return null;
  const st = stationsOf(t);
  if (st.length) {
    const plays = src.startsWith('station:') ? t.sc?.why?.match(/(\d+) plays?/)?.[1] : undefined;
    const last = t.sc?.why?.match(/last (\d{4}-\d{2}-\d{2})/)?.[1]; // the play store's date, when the feed recorded it
    const when = last ? new Date(`${last}T00:00:00Z`).toLocaleDateString('en-AU', { day: 'numeric', month: 'short', timeZone: 'UTC' }) : '';
    const names = st.map(stationName);
    const extra = [plays ? `${plays} play${plays === '1' ? '' : 's'}` : '', when ? `last ${when}` : ''].filter(Boolean).join(', ');
    return { chip: names.length > 1 ? `${names[0]} +${names.length - 1}` : names[0], why: `played on ${list(names)}${extra ? ` (${extra})` : ''}` };
  }
  if (src === 'shazam') return { chip: 'Shazam', why: 'from a Shazam' };
  if (src === 'request') return { chip: 'Just fetched', why: 'just pulled this in for you' };
  if (src === 'ytm') return { chip: 'YouTube Music', why: `recommended by YouTube Music${t.sc?.why ? ` (${t.sc.why})` : ''}` };
  if (src === 'new-release') return { chip: 'New release', why: 'a new release the scout found' };
  return { chip: 'New find', why: 'a new find, not in your library' };
}
