// The queue, built on the server from embedding neighbours. Sequenced like a set: an energy
// arc, a smooth bridge from the last song, a left turn every few tracks, no artist repeats,
// a library/new mix, and a hidden 1-in-10 random control pick to test the AI against.
import { centroid, dot, primaryArtist, space, type Loaded, type Track } from './space.ts';
import { moodById, MOODS, type Mood } from './moods.ts';
import { ruleAt, type Rule } from './rules.ts';
import type { Profile } from './profile.ts';
import { genreById } from './genres.ts';
import { stationById } from './stations.ts';
import { sourceOf, stationsOf } from './source.ts';

export type Steer = { s: string; d: 1 | -1; early?: boolean };
export type QueueReq = {
  mood: string;
  anchor?: string; // the song playing now (or last played)
  steer?: Steer[]; // "more like this" / "less like this" since the mood was picked
  point?: { x: number; y: number }; // tapped on the map
  history?: string[]; // this session's played songs, newest last
  n?: number;
  playable?: Set<string>; // songs this client can play now
  tilt?: { heavy?: number; energy?: number; known?: boolean };
  start?: string; // a requested song goes first
  era?: { from: number; to: number }; // "90s only": release years, inclusive // voice: heavier, calmer, "something I know"
};
export type Pick = {
  i: number; s: string; src: 'ai' | 'control'; nw: 0 | 1; reasons: string[]; line: string; slot: number;
  turn: boolean;
};

const ARTIST_GAP = 8;
const CONTROL_SHARE = 0.1;
export const HARD_GATES = new Set(['truncated', 'quarantined']);

export function isNew(t: Track, p: Profile) {
  return t.nw === 1 && !p.heard?.[t.s];
}

function rng(seed: number) {
  let x = seed >>> 0 || 1;
  return () => ((x = (x * 1664525 + 1013904223) >>> 0) / 4294967296);
}

export function buildQueue(req: QueueReq, profile: Profile, now = new Date(), seed = Date.now()): { picks: Pick[]; rule: Rule; mood: Mood } {
  const S = space();
  const rand = rng(seed);
  const mood = moodById(req.mood) ?? MOODS[MOODS.length - 1];
  const rule = ruleAt(now, profile.tz);
  const discovery = profile.style === 'discovery';
  const tilt = req.tilt ?? {};
  const newShare = tilt.known ? 0 : profile.newShare ?? (discovery ? 0.75 : 0.6);
  const n = Math.min(req.n ?? 10, 20);

  const history = (req.history ?? []).filter((s) => S.bySha.has(s));
  const recentIx = new Set<number>(history.map((s) => S.bySha.get(s)!));
  const dayAgo = now.getTime() - 3 * 86400e3;
  for (const [s, ts] of Object.entries(profile.heard ?? {})) if (ts > dayAgo && S.bySha.has(s)) recentIx.add(S.bySha.get(s)!);
  const hardSkips = new Set([...Object.entries(profile.skips ?? {}).filter(([, c]) => c >= 2).map(([s]) => s), ...(profile.dislikes ?? [])]);

  // Target: mood centroid, bent by steers, a map point, and this listener's likes.
  const pool = S.tracks.map((t, i) => i).filter((i) => mood.member(S.tracks[i]));
  let target = centroid(pool.map((i) => S.vec[i]))!;
  const steer = (req.steer ?? []).filter((x) => S.bySha.has(x.s)).slice(-6);
  if (req.point) {
    const near = S.tracks.map((t, i) => [i, (t.x - req.point!.x) ** 2 + (t.yy - req.point!.y) ** 2] as const)
      .sort((a, b) => a[1] - b[1]).slice(0, 12).map(([i]) => S.vec[i]);
    target = centroid(near)!;
  }
  if (steer.length) {
    const t = new Float32Array(target);
    for (const x of steer) {
      const v = S.vec[S.bySha.get(x.s)!];
      for (let k = 0; k < t.length; k++) t[k] += 0.7 * x.d * v[k];
    }
    target = centroid([t])!;
  }
  // Ian's Glancebar likes count as his likes here too; the mate starts with none.
  const likeShas = [...(profile.style === 'home' ? S.tracks.filter((t) => t.gl).map((t) => t.s) : []), ...(profile.likes ?? [])];
  const likedSet = new Set(likeShas);
  const likes = likeShas.slice(-60).filter((s) => S.bySha.has(s)).map((s) => S.bySha.get(s)!);
  const skipped = Object.keys(profile.skips ?? {}).slice(-40).filter((s) => S.bySha.has(s)).map((s) => S.bySha.get(s)!);

  // A station mix: what it actually played, newest first: its plays we fetched (new to him), then
  // its plays in his library; map neighbours of its sound only fill in.
  const stationRank = new Map<string, number>();
  if (mood.id.startsWith('station:')) {
    const id = mood.id.slice(8);
    const finds = S.tracks.filter((t) => stationsOf(t).includes(id)).sort((a, b) => (b.sc?.at ?? '').localeCompare(a.sc?.at ?? ''));
    [...finds.map((t) => t.s), ...(stationById(id)?.shas ?? [])].forEach((x) => { if (!stationRank.has(x)) stationRank.set(x, stationRank.size); });
  }
  const taste = (profile.taste ?? []).map(genreById).filter((g): g is NonNullable<typeof g> => !!g);
  const base = new Float32Array(S.tracks.length);
  const likeNear: (number | undefined)[] = [];
  for (let i = 0; i < S.tracks.length; i++) {
    const t = S.tracks[i];
    let sc = dot(S.vec[i], target);
    if (!req.point && !mood.member(t) && !stationRank.size) sc -= 0.35; // a station mix takes neighbours too
    sc += rule.heavy * t.h + rule.energy * (t.e - 0.5);
    let bestLike = 0, bestLikeIx: number | undefined;
    for (const j of likes) { const d = dot(S.vec[i], S.vec[j]); if (d > bestLike && j !== i) { bestLike = d; bestLikeIx = j; } }
    if (bestLike > 0.6) sc += 0.25 * bestLike;
    likeNear[i] = bestLike > 0.8 ? bestLikeIx : undefined;
    let worstSkip = 0;
    for (const j of skipped) worstSkip = Math.max(worstSkip, dot(S.vec[i], S.vec[j]));
    if (worstSkip > 0.8) sc -= 0.3 * worstSkip;
    if (tilt.heavy) sc += 0.9 * tilt.heavy * (t.h - 0.5);
    if (tilt.energy) sc += 0.7 * tilt.energy * (t.e - 0.5);
    if (tilt.known) sc += t.f || t.p > 0 || t.gl ? 0.4 : -0.4;
    if (taste.length && taste.some((g) => g.member(t))) sc += 0.3;
    if (stationRank.has(t.s)) sc += 0.3 - Math.min(0.2, stationRank.get(t.s)! * 0.002); // newest plays first // what they said they listen to
    if (discovery) sc += 0.15 * t.q - 0.06 * Math.min(t.p, 5) - 0.08 * t.f;
    else sc += 0.03 * t.f + 0.05 * t.q;
    base[i] = sc;
  }

  // Learn fast: early skips push the target away (above) and two in a row in one corner
  // force a left turn out of it for the next few songs.
  const early = steer.filter((x) => x.early).slice(-2).map((x) => S.tracks[S.bySha.get(x.s)!].c);
  const earlyRegion = new Set<number>(early.length === 2 && early[0] === early[1] ? [early[0]] : []);
  for (const x of steer.filter((x) => x.early)) {
    const j = S.bySha.get(x.s)!;
    for (const [nb, sim] of S.tracks[j].n) if (sim > 0.85) base[nb] -= 0.3;
  }

  const lastDecades: number[] = history.slice(-3).map((s) => S.tracks[S.bySha.get(s)!].y).filter(Boolean).map((y) => Math.floor(y! / 10));
  const picks: Pick[] = [];
  const recentArtists: string[] = history.slice(-ARTIST_GAP).map((s) => primaryArtist(S.tracks[S.bySha.get(s)!].a));
  let prev = req.anchor && S.bySha.has(req.anchor) ? S.bySha.get(req.anchor)! : undefined;
  const used = new Set<number>();
  if (prev !== undefined) { recentIx.add(prev); used.add(prev); } // never re-pick the song playing now
  const arcMid = Math.min(0.9, Math.max(0.1, (mood.arc ?? rule.arc) + 0.2 * (tilt.energy ?? 0) + 0.1 * (tilt.heavy ?? 0)));

  if (req.start && S.bySha.has(req.start)) {
    let i = S.bySha.get(req.start)!;
    let why = 'you asked for it';
    const hardBlocked = (idx: number) => Boolean(S.tracks[idx].gx && HARD_GATES.has(S.tracks[idx].gx));
    if (hardBlocked(i) || (req.playable && !req.playable.has(S.tracks[i].s))) {
      // Truncated/quarantined or not streamable on this device yet: its nearest neighbour that is.
      const nb = S.tracks[i].n.find(([j]) => !hardBlocked(j) && (!req.playable || req.playable.has(S.tracks[j].s)));
      if (nb) { why = `closest playable here to ${S.tracks[i].t}`; i = nb[0]; } else i = -1;
    }
    const t = S.tracks[i];
    if (t) picks.push({ i, s: t.s, src: 'ai', nw: isNew(t, profile) ? 1 : 0, reasons: [why], line: S.lines[t.s] ?? `${t.a}, as requested.`, slot: 0, turn: false });
    if (t) { used.add(i); recentArtists.push(primaryArtist(t.a)); prev = i; }
  }
  for (let slot = picks.length; slot < n; slot++) {
    const turn = slot % 5 === 3 || (slot === 0 && earlyRegion.size > 0); // a deliberate left turn every five songs
    const wantNew = rand() < newShare;
    const control = rand() < CONTROL_SHARE;
    const arc = Math.min(0.95, Math.max(0.05, arcMid + 0.15 * Math.sin((2 * Math.PI * slot) / 10)));
    let relaxed = false;
    const ok = (i: number) => {
      const t = S.tracks[i];
      if (used.has(i) || hardSkips.has(t.s)) return false;
      if (!relaxed && recentIx.has(i)) return false;
      if (t.gx && (HARD_GATES.has(t.gx) || !likedSet.has(t.s))) return false; // quality gate; corrupt/truncated files never bypass even when liked
      if (earlyRegion.has(t.c) && slot < 3) return false; // two early skips here: leave the region
      if (req.playable && !req.playable.has(t.s)) return false;
      if (!relaxed && recentArtists.slice(-ARTIST_GAP).includes(primaryArtist(t.a))) return false;
      if (!relaxed && req.era && !(t.y && t.y >= req.era.from && t.y <= req.era.to)) return false;
      // The mate's set moves through eras: no more than three in a row from one decade, unless asked.
      if (!relaxed && discovery && !req.era && t.y && lastDecades.length >= 3 && lastDecades.slice(-3).every((d) => d === Math.floor(t.y! / 10))) return false;
      return true;
    };
    let chosen: number | undefined;
    let isControl = false;
    if (control) {
      const c = pool.filter((i) => ok(i) && !S.tracks[i].gx);
      if (c.length) { chosen = c[Math.floor(rand() * c.length)]; isControl = true; }
    }
    // Pass 0 holds the slot to what the dice asked for (a new find, or his library), so the share
    // slider is what he hears; then any song; then recent songs and artists rather than silence.
    for (let pass = 0; pass < 3 && chosen === undefined; pass++) {
      relaxed = pass === 2;
      let best = -1e9;
      for (let i = 0; i < S.tracks.length; i++) {
        if (!ok(i)) continue;
        const t = S.tracks[i];
        if (pass === 0 && isNew(t, profile) !== wantNew) continue;
        let sc = base[i] - 0.8 * Math.abs(t.e - arc);
        sc += isNew(t, profile) === wantNew ? 0.45 : 0;
        if (stationRank.size && stationRank.has(t.s)) sc += 0.8; // its plays before its sound
        if (prev !== undefined) {
          const link = dot(S.vec[i], S.vec[prev]);
          if (turn) sc += S.tracks[prev].c !== t.c && link > 0.3 && link < 0.75 ? 0.4 : -0.4;
          else sc += 0.6 * link;
        }
        sc += 0.08 * -Math.log(-Math.log(rand() + 1e-9) + 1e-9) * 0.5; // a little Gumbel noise
        if (sc > best) { best = sc; chosen = i; }
      }
    }
    if (chosen === undefined) break;
    const t = S.tracks[chosen];
    const reasons = reasonsFor(S, chosen, prev, turn, likeNear[chosen], rule, profile);
    picks.push({ i: chosen, s: t.s, src: isControl ? 'control' : 'ai', nw: isNew(t, profile) ? 1 : 0, reasons, line: lineFor(S, chosen, prev, reasons), slot, turn });
    used.add(chosen);
    if (t.y) lastDecades.push(Math.floor(t.y / 10));
    recentArtists.push(primaryArtist(t.a));
    prev = chosen;
  }
  return { picks, rule, mood };
}

function reasonsFor(S: Loaded, i: number, prev: number | undefined, turn: boolean, liked: number | undefined, rule: Rule, p: Profile): string[] {
  const t = S.tracks[i];
  const r: string[] = [];
  if (liked !== undefined) r.push(`near your like: ${S.tracks[liked].a}`);
  const src = sourceOf(t);
  if (src) r.push(src.why); // its real source first: the station that played it, the scout, a request
  if (prev !== undefined && !t.px && !S.tracks[prev].px) { // a stand-in vector makes no map claims
    const link = dot(S.vec[i], S.vec[prev]);
    if (turn && S.tracks[prev].c !== t.c) r.push(`left turn into ${S.clusterName[t.c]}`);
    else if (link > 0.8) r.push(`next door to ${S.tracks[prev].a} on the map`);
  }
  if (t.q > 0.85 && !t.px) r.push(`from the map's quiet corner`);
  if (rule.id === 'afternoon' && t.h > 0.75) r.push('afternoon: heavy hours');
  if (rule.id === 'morning' && t.e < 0.35) r.push('morning, easing in');
  if (rule.id === 'late' && t.e < 0.3) r.push('late, low and slow');
  if (!r.length && p.style === 'discovery' && t.p === 0) r.push('a deep cut');
  if (!r.length && t.p >= 3) r.push('in heavy rotation');
  if (!r.length) r.push(t.y ? `${t.g || t.k}, ${t.y}` : t.g || (t.px ? 'new to you' : t.k));
  return r.slice(0, 2);
}

// The DJ line: a "why now" link built from data, then the song's own pre-generated line
// (tools/dj_lines.py, grounded in metadata and neighbours). Nothing waits on a model here.
function lineFor(S: Loaded, i: number, prev: number | undefined, reasons: string[]): string {
  const t = S.tracks[i];
  const own = S.lines[t.s];
  const parts: string[] = [];
  if (prev !== undefined && !t.px && !S.tracks[prev].px) {
    const p = S.tracks[prev];
    const link = dot(S.vec[i], S.vec[prev]);
    const de = t.e - p.e;
    const lift = de > 0.15 ? 'lifting it' : de < -0.15 ? 'bringing it down' : 'holding the level';
    if (reasons.some((x) => x.startsWith('left turn'))) parts.push(`Left turn after ${p.a}.`);
    else if (link > 0.8) parts.push(`Right beside ${p.a} on the map, ${lift}.`);
    else parts.push(`After ${p.a}, ${lift}.`);
  }
  if (own) parts.push(own);
  else if (t.px) parts.push(`${t.a}, new to you${reasons[0] && sourceOf(t) ? `: ${reasons[0]}` : ''}.`);
  else {
    const where = S.clusterName[t.c].split(' · ')[0].toLowerCase();
    parts.push(`${t.a}${t.y ? `, ${t.y}` : ''}, out of ${where}.`);
  }
  if (t.sc?.why && !own && !t.px && !stationsOf(t).length && !t.sc.src.includes(':')) parts.push(t.sc.why); // the scout's own note, not a lane tag
  return parts.join(' ');
}
