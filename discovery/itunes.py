"""Public Apple iTunes Search / Lookup API helpers (metadata only, no auth)."""
from __future__ import annotations

import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

_LIB = Path(__file__).resolve().parent.parent / "library"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))
import offline_cache as oc  # noqa: E402

ITUNES_API = "https://itunes.apple.com/search"
ITUNES_LOOKUP = "https://itunes.apple.com/lookup"
_ITUNES_MIN_GAP = 3.2  # seconds between catalogue calls (~20/min)
_itunes_last = 0.0

_COMPILATION_NAME = re.compile(
    r"\b(?:compilations?|hits|best of|now that['’]?s|now\s+\d{1,3}|various artists)\b",
    re.I,
)
_YEAR = re.compile(r"\b(?:(?:19|20)\d{2}s?|[1-9]0s)\b", re.I)
_ANTHOLOGY = re.compile(
    r"\b(?:hits|anthems|classics|anthology|collection|annual|best|now)\b",
    re.I,
)
_MIXED_TAG = re.compile(r"\s*[\(\[]mixed[\)\]]", re.IGNORECASE)
_ALBUM_TAIL = re.compile(r"\s*-\s*(?:single|ep)\s*$|\s*[\(\[][^\)\]]*[\)\]]\s*$", re.IGNORECASE)


class LookupFailed(Exception):
    """The catalogue could not be asked (rate limit, network). Not the same as 'no such song'."""


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


def _fold_variants(values) -> set[str]:
    return {f for f in (oc.match_fold(v) for v in values) if f}


def _title_agrees(got: str, wanted: str) -> bool:
    got, wanted = _MIXED_TAG.sub("", got), _MIXED_TAG.sub("", wanted)
    return bool(_fold_variants(oc.title_match_variants(got)) & _fold_variants(oc.title_match_variants(wanted)))


def _artist_agrees(got: str, wanted: str) -> bool:
    a = _fold_variants([got, oc.split_primary_artist(got)])
    b = _fold_variants([wanted, oc.split_primary_artist(wanted)])
    return bool(a & b)


def _same_song(res: dict, artist: str, title: str) -> bool:
    return _title_agrees(str(res.get("trackName", "")), title) and _artist_agrees(
        str(res.get("artistName", "")), artist
    )


def _song_score(res: dict, artist: str, title: str, album: str) -> float:
    score = 0.0
    if _norm(res.get("artistName", "")) == _norm(artist):
        score += 0.5
    if _norm(res.get("trackName", "")) == _norm(title):
        score += 0.5
    if album:
        wanted, got = _norm(album), _norm(res.get("collectionName", ""))
        if wanted and got and (wanted in got or got in wanted):
            score += 0.3
    if res.get("collectionArtistName"):
        score -= 0.25
    return score


def _year_anthology(name: str) -> bool:
    years = list(_YEAR.finditer(name))
    kinds = list(_ANTHOLOGY.finditer(name))
    for year in years:
        for kind in kinds:
            if max(year.start(), kind.start()) - min(year.end(), kind.end()) <= 40:
                return True
    return False


def _is_compilation(res: dict) -> bool:
    if _norm(str(res.get("collectionArtistName") or "")) == "various artists":
        return True
    name = str(res.get("collectionName") or "")
    return bool(_COMPILATION_NAME.search(name)) or _year_anthology(name)


def _itunes_get(url: str, params: dict) -> list[dict] | None:
    global _itunes_last
    query = urllib.parse.urlencode(params)
    for attempt in (1, 2):
        wait = _ITUNES_MIN_GAP - (time.monotonic() - _itunes_last)
        if wait > 0:
            time.sleep(wait)
        _itunes_last = time.monotonic()
        try:
            with urllib.request.urlopen(f"{url}?{query}", timeout=30) as r:
                return json.load(r)["results"]
        except urllib.error.HTTPError as e:
            if e.code in (403, 429) and attempt == 1:
                time.sleep(60)
                continue
            return None
        except Exception:
            return None
    return None


def _itunes_search(artist: str, title: str, country: str = "au") -> list[dict] | None:
    return _itunes_get(
        ITUNES_API, {"term": f"{artist} {title}", "entity": "song", "country": country, "limit": 25}
    )


def _album_core(name: str) -> str:
    text = str(name or "")
    while True:
        cut = _ALBUM_TAIL.sub("", text).strip()
        if cut == text or not cut:
            return text.strip()
        text = cut


def _itunes_artist_id(artist: str, song_results: list[dict] | None, country: str = "au") -> int | None | bool:
    for row in song_results or []:
        if row.get("artistId") and _artist_agrees(str(row.get("artistName", "")), artist):
            return row["artistId"]
    found = _itunes_get(ITUNES_API, {"term": artist, "entity": "musicArtist", "country": country, "limit": 5})
    if found is None:
        return False
    for row in found:
        if row.get("artistId") and _artist_agrees(str(row.get("artistName", "")), artist):
            return row["artistId"]
    return None


def _itunes_album_tracks(artist: str, album: str, song_results: list[dict] | None = None, country: str = "au") -> list[dict] | None:
    artist_id = _itunes_artist_id(artist, song_results, country=country)
    if artist_id is False:
        return None
    if not artist_id:
        return []
    wanted = oc.match_fold(_album_core(album))
    tracks: list[dict] = []
    if wanted:
        albums = _itunes_get(ITUNES_LOOKUP, {"id": artist_id, "entity": "album", "country": country, "limit": 200})
        if albums is None:
            return None
        looked = 0
        for row in albums:
            if looked >= 2 or row.get("wrapperType") != "collection":
                continue
            got = oc.match_fold(_album_core(str(row.get("collectionName", ""))))
            if not got or not (got == wanted or got.startswith(wanted) or wanted.startswith(got)):
                continue
            looked += 1
            songs = _itunes_get(ITUNES_LOOKUP, {"id": row.get("collectionId"), "entity": "song", "country": country, "limit": 200})
            if songs is None:
                return None
            tracks.extend(r for r in songs if r.get("wrapperType") == "track")
    if not tracks:
        songs = _itunes_get(ITUNES_LOOKUP, {"id": artist_id, "entity": "song", "country": country, "limit": 200})
        if songs is None:
            return None
        tracks.extend(r for r in songs if r.get("wrapperType") == "track")
    return tracks


def itunes_albums(artist: str, per_artist: int, country: str = "au") -> list[dict]:
    q = urllib.parse.urlencode({"term": artist, "entity": "album", "country": country, "limit": 25})
    with urllib.request.urlopen(f"{ITUNES_API}?{q}", timeout=30) as r:
        results = json.load(r)["results"]
    albums: dict[str, dict] = {}
    for res in results:
        if res.get("wrapperType") != "collection":
            continue
        if res.get("artistName", "").lower() != artist.lower():
            continue
        albums.setdefault(str(res["collectionId"]), res)

    def is_album(a: dict) -> bool:
        return a.get("trackCount", 0) >= 6

    ordered = sorted(
        [a for a in albums.values() if is_album(a)], key=lambda a: a.get("releaseDate", ""), reverse=True
    ) + sorted(
        [a for a in albums.values() if not is_album(a)], key=lambda a: a.get("releaseDate", ""), reverse=True
    )
    return ordered[:per_artist]


def itunes_song(artist: str, title: str, album: str = "", country: str = "au") -> list[dict]:
    raw = _itunes_search(artist, title, country=country)
    failed = raw is None
    results = [r for r in raw or [] if r.get("wrapperType") == "track" and _same_song(r, artist, title)]
    on_album = False
    if results and album:
        wanted = oc.match_fold(_album_core(album))
        on_album = any(oc.match_fold(_album_core(str(r.get("collectionName", "")))) == wanted for r in results)
    if not results or (album and not on_album):
        more = _itunes_album_tracks(artist, album, raw, country=country)
        failed = failed or more is None
        seen_ids = {r.get("trackId") for r in results}
        results += [r for r in more or [] if _same_song(r, artist, title) and r.get("trackId") not in seen_ids]
    if not results:
        if failed:
            raise LookupFailed(f"{artist} — {title}")
        return []
    scored = []
    for i, res in enumerate(results):
        if res.get("wrapperType") != "track" or not _same_song(res, artist, title):
            continue
        score = _song_score(res, artist, title, album)
        if score >= 0.5:
            scored.append((score, i, res))
    originals = [row for row in scored if not _is_compilation(row[2])]
    pool = originals if originals else scored
    pool.sort(key=lambda row: (-row[0], row[1]))
    seen: set = set()
    ranked: list[dict] = []
    for _, _, res in pool:
        tid = res.get("trackId")
        if tid in seen:
            continue
        seen.add(tid)
        ranked.append(res)
    return ranked


def itunes_song_best(artist: str, title: str, album: str = "", country: str = "au") -> dict | None:
    try:
        found = itunes_song(artist, title, album, country=country)
    except LookupFailed:
        return None
    return found[0] if found else None
