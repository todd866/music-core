# Acquisition source (bring your own)

`music-core` catalogues, embeds, discovers, and syncs music across your own devices and private bucket. How audio files enter your library is up to you — rip your CDs, export purchases, or connect any source you are licensed to use. **No acquisition or DRM-bypass tooling ships in this repository.**

## Lane inbox contract

`discovery/scout.py` (with `"acquire_via": "lane"`) and `discovery/lane_feed.py` emit ranked recommendations as plain JSON files into a configurable inbox directory (`lane_inbox`):

```json
[
  {
    "song": { "artist": "Artist Name", "title": "Track Title" },
    "tag": "radio",
    "why": "kexp + pbs · last 2026-11-18",
    "priority": 4,
    "max_age": 2
  }
]
```

Any downstream worker you provide can consume that queue, place lossless masters into your configured `masters_roots` directory, and let `library/push.sh` + `embed/nightly.sh` catalogue, embed, and publish them to every connected front end.
