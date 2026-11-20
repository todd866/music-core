#!/usr/bin/env python3
"""Feed the home worker fetch worker's background lane (about 100 songs a day) with music NEW to the owner, from
what radio is actually playing and from new releases, never from his own library's artists
(owner, 19 Nov: "Try to actually do some new music finding on all these various sources"):
  1. songs played on two or more stations (radio.py's play store), not in the library or already
     offered by the scout, widest support and newest first (about 80 a day);
  2. each station's most-played songs we don't have (its station mix plays these first);
  3. the scout's new-era candidates (new releases, Shazams, taste picks, per-station new plays)
     that it has not acquired;
  4. YouTube Music's recommendations for him (Discover Mix, New Release Mix ...), a weighted hint.
Writes ~/.local/state/music-radio/lane-feed-<date>.json and copies it into the worker's inbox on the
M2, which merges it into lane.json. Acquisition itself is the worker's (amdl).
(Until 19 Nov it also seeded Deezer related artists from his library and likes; that filled the
lane with the back catalogue of artists he already knew, e.g. Mary J. Blige and Kelis.)
  python3 tools/lane_feed.py [--radio 80] [--scout 30] [--dry-run]
"""
import argparse, collections, json, os, re, subprocess, sys, time, urllib.parse, urllib.request
from pathlib import Path

HOME = Path.home()
ROOT = Path(os.environ.get('MUSIC_RADIO_DIR', HOME / 'Projects/music-radio'))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'embed'))
from embed import match_key  # noqa: E402

PLAYS = HOME / '.local/state/music-scout/radio-cache/plays'
LEDGER = HOME / '.local/state/music-scout/ledger.jsonl'
STATE = HOME / '.local/state/music-radio'
UA = 'radio-lane-feed/1.0 (personal music library tool)'
GENERIC = re.compile(r'\b(live|remix|karaoke|tribute|cover|instrumental|edit)\b', re.I)


def deezer(path, cache):
    if path in cache:
        return cache[path]
    time.sleep(1.0)
    try:
        with urllib.request.urlopen(urllib.request.Request(f'https://api.deezer.com/{path}', headers={'user-agent': UA}), timeout=15) as r:
            out = json.loads(r.read())
    except Exception:
        out = {}
    cache[path] = out
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--radio', type=int, default=80)
    ap.add_argument('--scout', type=int, default=30)
    ap.add_argument('--yt', type=int, default=25, help='YouTube Music recommendations a day')
    ap.add_argument('--dry-run', action='store_true')
    ap.add_argument('--thin-budget', type=int, default=60, help='lane songs a day shared among thin areas')
    a = ap.parse_args()
    S = json.loads((ROOT / 'data/space.json').read_text())
    have = {match_key(t['a'], t['t']) for t in S['tracks']}
    have_artists = {match_key(t['a'], '')[0] for t in S['tracks']}
    for l in (LEDGER.read_text().splitlines() if LEDGER.exists() else []):
        try:
            x = json.loads(l); have.add(match_key(x.get('artist', ''), x.get('title', '')))
        except ValueError:
            pass
    p = subprocess.run(['ssh', 'm2', 'cat ~/.local/state/music-radio-fetch/lane-done.jsonl 2>/dev/null'], capture_output=True, text=True)
    for l in p.stdout.splitlines():
        try:
            x = json.loads(l); have.add(match_key(x['artist'], x['title']))
        except (ValueError, KeyError):
            pass

    # 1. multi-station radio plays
    support = collections.defaultdict(lambda: {'stations': set(), 'last': ''})
    names = {}
    for f in PLAYS.glob('*.json'):
        for u in json.loads(f.read_text()).get('units', {}).values():
            for pl in u.get('plays', []):
                k = match_key(pl.get('artist', ''), pl.get('title', ''))
                if not k[0] or not k[1] or GENERIC.search(pl.get('title', '')):
                    continue
                support[k]['stations'].add(f.stem)
                support[k]['last'] = max(support[k]['last'], pl.get('at') or '')
                names[k] = (pl.get('artist', ''), pl.get('title', ''))
    radio = sorted(((k, v) for k, v in support.items() if len(v['stations']) >= 2 and k not in have),
                   key=lambda kv: (len(kv[1]['stations']), kv[1]['last']), reverse=True)
    per_artist = collections.Counter()
    items = []
    for k, v in radio:
        if per_artist[k[0]] >= 2:
            continue
        per_artist[k[0]] += 1
        artist, title = names[k]
        items.append({'song': {'artist': artist, 'title': title}, 'tag': 'radio', 'why': f"{' + '.join(sorted(v['stations']))} · last {v['last'][:10]}" if v['last'] else ' + '.join(sorted(v['stations'])), 'priority': 4})
        if len(items) >= a.radio:
            break

    # 2. the scout's new-era candidates it hasn't acquired (new releases, Shazams, station new plays)
    cand = HOME / '.local/state/music-scout/candidates.json'
    related = []
    try:
        cands = json.loads(cand.read_text()).get('candidates', [])
    except (OSError, ValueError):
        cands = []
    got = 0
    for c in cands:
        if c.get('era') != 'new' or got >= a.scout:
            continue
        k = match_key(c.get('artist', ''), c.get('title', ''))
        if not k[0] or not k[1] or k in have or GENERIC.search(c.get('title', '')):
            continue
        src = c.get('source', '')
        tag = f"station:{src[6:]}" if src.startswith('radio:') else src or 'scout'
        items.append({'song': {'artist': c['artist'], 'title': c['title']}, 'tag': tag, 'why': c.get('why') if not src.startswith('radio:') else None, 'priority': 3})
        have.add(k); got += 1

    # 3. YouTube Music's own recommendations for him (owner, 19 Nov: "youtube music has the best
    # recommendation algorithm"): a weighted hint, since the account is shared. Read-only from his
    # signed-in YouTube Music into ~/.local/state/music-radio/yt-recs.txt (shelf|artist|title; the
    # Chrome cookie export has no youtube.com session, so it is read in the browser, not here).
    # Songs whose artist radio stations also play, or who is in his library, go first.
    yt = STATE / 'yt-recs.txt'
    if yt.exists() and time.time() - yt.stat().st_mtime < 8 * 86400:
        shelf_w = {'X': 3, 'N': 3, 'D': 3, 'H': 2, 'Q': 1, 'S': 1}
        shelf_name = {'X': 'Discover Mix', 'N': 'New Release Mix', 'D': 'Your daily discover', 'H': 'Hip-hop', 'Q': 'Quick picks', 'S': 'My Supermix'}
        radio_artists = {k[0] for k in support}
        recs = []
        for l in yt.read_text().splitlines():
            if l.startswith('#') or l.count('|') != 2:
                continue
            sh, artist, title = l.split('|')
            k = match_key(artist, title)
            if not k[0] or not k[1] or k in have or GENERIC.search(title):
                continue
            w = shelf_w.get(sh, 1) + (3 if k[0] in radio_artists else 0) + (1 if k[0] in have_artists else 0)
            recs.append((w, artist, title, shelf_name.get(sh, sh)))
        per = collections.Counter()
        for w, artist, title, sh in sorted(recs, key=lambda r: -r[0]):
            k = match_key(artist, title)
            if per[k[0]] >= 2 or per['_n'] >= a.yt:
                continue
            per[k[0]] += 1; per['_n'] += 1
            items.append({'song': {'artist': artist, 'title': title}, 'tag': 'ytm', 'why': sh, 'priority': 3.5})
            have.add(k)

    # 4. Thin areas get more (owner rule, 18 Nov: "If it's thin keep building it").
    TARGET_SONGS, TARGET_ARTISTS = 150, 40
    ok = [t for t in S['tracks'] if not t.get('gx')]
    def depth(ts):
        return len(ts), len({t['a'].split(',')[0].split(' & ')[0].strip().lower() for t in ts})
    stations = json.loads((ROOT / 'data/stations.json').read_text()).get('stations', []) if (ROOT / 'data/stations.json').exists() else []
    by_sha = {t['s']: t for t in ok}
    areas = []
    for st in stations:
        n, ar = depth([by_sha[x] for x in st['shas'] if x in by_sha])
        areas.append(('station', st['id'], st['name'], n * 2, ar))  # its mix is its plays plus as many of its sound
    for d0 in range(1960, 2030, 10):
        n, ar = depth([t for t in ok if t.get('y') and d0 <= t['y'] < d0 + 10])
        areas.append(('decade', str(d0), f"{str(d0)[2:]}s", n, ar))
    genres = {'chill': lambda t: t['k'] == 'chill', 'hiphop': lambda t: t['k'] == 'rap', 'rock': lambda t: t['h'] > 0.8,
              'electronic': lambda t: t['k'] == 'dance', 'soul': lambda t: bool(re.search(r'r&b|soul|funk', t.get('g', ''), re.I))}
    for gid, f in genres.items():
        n, ar = depth([t for t in ok if f(t)])
        areas.append(('genre', gid, gid, n, ar))
    thin = [a_ for a_ in areas if a_[0] == 'station' or a_[3] < TARGET_SONGS or a_[4] < TARGET_ARTISTS]  # stations: always a daily share of their new plays
    per_area = max(5, a.thin_budget // max(1, len(thin)))
    station_plays = {}
    for f in PLAYS.glob('*.json'):
        c = collections.Counter(); nm = {}; last = {}
        for u in json.loads(f.read_text()).get('units', {}).values():
            for pl in u.get('plays', []):
                k = match_key(pl.get('artist', ''), pl.get('title', ''))
                if k[0] and k[1] and k not in have and not GENERIC.search(pl.get('title', '')):
                    c[k] += 1; nm[k] = (pl.get('artist', ''), pl.get('title', ''))
                    last[k] = max(last.get(k, ''), pl.get('at') or u.get('at') or '')
        # most played, then most recently played
        station_plays[f.stem] = [(nm[k], n, last[k][:10]) for k, n in sorted(c.items(), key=lambda kv: (kv[1], last[kv[0]]), reverse=True)[:200]]
    queued = {(i.get('song') or {}).get('title') for i in items}
    for kind, aid, name, n, ar in thin:
        if kind == 'station':
            first = 40 if not (STATE / f'station-seeded-{aid}').exists() else per_area  # 40 to start, then a daily share
            got = 0
            for (artist, title), plays, lastp in station_plays.get(aid, []):
                if got >= first:
                    break
                if title in queued:
                    continue
                items.append({'song': {'artist': artist, 'title': title}, 'tag': f'station:{aid}', 'why': f"{name}, {plays} play{'s' if plays != 1 else ''}{f' · last {lastp}' if lastp else ''}", 'priority': 3.5 if aid == 'rtr' else 4})
                queued.add(title); got += 1
            (STATE / f'station-seeded-{aid}').touch()
        # decades and genres: no related-artist seeding from his library (19 Nov); stations fill them.

    # Weighted to new (owner, 19 Nov; the lane's first 1,120 songs were only 27% from 2025-26): every
    # song must be a release of the last two years (the worker checks Apple's release date) except
    # a small share of the widest-supported radio plays, any era; at most 2 songs per artist.
    per_artist, kept, classics = collections.Counter(), [], 0
    for it in items:
        k = match_key(it['song']['artist'], '')[0]
        if per_artist[k] >= 2:
            continue
        per_artist[k] += 1
        if it['tag'] == 'radio' and classics < max(10, len(items) // 8):
            classics += 1  # any era
        else:
            it['max_age'] = 2
        kept.append(it)
    items[:] = kept

    depth_doc = {'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'target': {'songs': TARGET_SONGS, 'artists': TARGET_ARTISTS},
                 'thin': [{'kind': k, 'id': i, 'name': nme, 'songs': n, 'artists': ar} for k, i, nme, n, ar in thin]}
    (STATE / 'depth.json').write_text(json.dumps(depth_doc))
    print(f"thin areas ({len(thin)}): " + ', '.join(f'{nme} {n}/{ar}' for _, _, nme, n, ar in thin))

    out = STATE / f"lane-feed-{time.strftime('%Y-%m-%d')}.json"
    out.write_text(json.dumps(items, indent=1))
    print(f'{len(items)} songs ({collections.Counter(i["tag"].split(":")[0] for i in items)}) -> {out}')
    if not a.dry_run:
        subprocess.run(['ssh', 'm2', 'mkdir -p ~/.local/state/music-radio-fetch/lane-inbox'], check=True)
        subprocess.run(['scp', '-q', str(out), f'worker:.local/state/music-radio-fetch/lane-inbox/{out.name}'], check=True)
        subprocess.run(['scp', '-q', str(STATE / 'depth.json'), 'worker:.local/state/music-radio-fetch/depth.json'], check=True)
        print('sent to the home worker lane inbox')


if __name__ == '__main__':
    main()
