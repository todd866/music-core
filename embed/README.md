# music-embed

Resumable CLI to embed every track in the Glancebar music library with
`gemini-embedding-2` (audio in, 3072-D vectors) via the Gemini **Batch API**.

State and outputs live under `~/.local/state/music-embed/` by default.
Catalogue input: `~/.local/state/music-library/catalogue.json`.
Masters root: `/Volumes/Music/Masters`.

## Setup

```bash
STATE=~/.local/state/music-embed
python3 -m venv "$STATE/venv"
"$STATE/venv/bin/pip" install google-genai
export GEMINI_API_KEY=...   # never commit or log this
```

Run commands with `"$STATE/venv/bin/python"` or activate the venv.

## Pipeline

```bash
python3 tools/music-embed/embed.py plan
python3 tools/music-embed/embed.py clips          # --limit N for trials
python3 tools/music-embed/embed.py build
python3 tools/music-embed/embed.py cost
python3 tools/music-embed/embed.py preflight      # live API check (2 clips)
python3 tools/music-embed/embed.py submit         # --dry-run
python3 tools/music-embed/embed.py poll
python3 tools/music-embed/embed.py collect
python3 tools/music-embed/embed.py neighbours "artist title" -k 5
```

After failures: `build --only-failed` from `errors.jsonl`, then `submit --resubmit shard-NNN`.

## Nightly map refresh (incremental)

New songs are embedded every night without rewriting the existing rows.
launchd `com.example.music-embed` on the main machine (plist here; `utc-daily --at 20:45`, after
the 19:40Z push writes the catalogue) runs `nightly-kick.sh`, which:

1. counts songs downloaded on the main machine (hot set, pending Apple Music pulls) that the bucket's
   catalogue lacks (`embed.py local-gap`) and hands that to the home worker;
2. runs `nightly.sh` on the home worker over ssh (as the push does: a LaunchAgent on the home worker cannot read
   the masters drive, ledger W17). That script reads the key from `~/.config/glancebar/gemini.env`
   (mode 600, M2 only), skips fetching and embedding on a metered link, and runs
   `increment collect` (merge finished jobs) → `increment prepare` (under `heavy-slot`: fetch
   each master from the drive under `disk-slot --nowait`, or from R2 when the drive is busy or
   unreadable; cut clips) → `increment submit` (one Batch API job) → `increment wait`
   (up to 2 h; anything later is merged the next night) → `gaps --upload`;
3. copies `vectors/tracks.f32` + `tracks-index.jsonl`, `gaps.json` and `nightly-status.json`
   back to `~/.local/state/music-embed/` on the main machine and, when the vectors changed,
   rewrites `mix/music-space.json` with `embed.py export`.

Incremental state is in `incremental/` (`jobs.json`, `prepared.json`, `batches/`, `results/`),
apart from the first run's `jobs.json`. Merges only append rows for ids not yet present, check
that each `.f32` holds exactly one row per index line before and after, keep the first run's
matrices in `vectors/baseline/` and the pair from before the last merge in `vectors/prev/`.
After a nightly merge, `collect` refuses (it rebuilds from the first run's jobs and would drop
the new rows).

**Spend cap.** `$5` a UTC day by default (`--cap-usd` or `$MUSIC_EMBED_CAP_USD`), about 2¢ a song
at the Batch price. `increment submit` takes a lock (`spend.lock`), reads `spend.json`, and
writes the job's estimated cost into today's total before anything is uploaded; it trims the
job to what is left, and refuses when nothing fits. A missing or unreadable ledger submits
nothing (`embed.py spend --init` creates it once). A failed upload keeps its reservation.
`embed.py spend` shows today's total.

**Gap report.** `embed.py gaps --upload` writes `gaps.json` and copies it to the bucket as
`radio/gaps.json`: `inR2NotEmbedded`, `embeddedNotInR2` (vectors with no song in R2, named in
`embeddedNotInR2Songs`; nothing is deleted), `downloadedNotInR2` (from the main machine, or null),
`embedSpendTodayUsd`, `capUsd`. The main machine's morning media check shows it as the `embeddings` row.

After new vectors, the web radio's bundle is rebuilt on the main machine with
`python3 ~/Projects/music-radio/tools/build_space.py`.

The same nightly, after the Gemma slice, runs `increment relate` (10 minute budget,
under `heavy-slot`). That writes the relation map below. It does not embed anything
and it does not spend the Gemini cap.

## Three maps

Two embedding maps, plus a map of how they relate. Coverage is cheap and wide.
Detail is Gemini. The relation says when a coverage score can be read on the
detail scale, and where the two maps agree.

| Map | Role | Files, under `~/.local/state/music-embed/` |
| --- | --- | --- |
| Coverage | EmbeddingGemma 2, 768-d, the whole library, including songs Gemini has not embedded | `vectors-gemma/tracks.f32`, `tracks-index.jsonl` |
| Detail | `gemini-embedding-2`, 3072-d. About 2,314 songs as of 21 Nov 2026; 423 songs were only in the coverage map | `vectors/tracks.f32`, `tracks-index.jsonl` |
| Relation | Ridge, a similarity calibration, and per-song agreement. Not a third embedding of the audio | `vectors/relation/` |

`python3 embed/embed.py increment relate` fits that directory:

- `projection.f32` and `projection.json`. Ridge regression from 768-d to 3072-d, fit on songs present in both maps. A tenth is held out. The JSON records `n`, `heldOutCosine`, `recallAt10`, `fittedAt` and `version`. Recall@10 asks whether the projected vector's true Gemini row is among the 10 nearest Gemini rows, over every shared song (the same definition as `bench_gemma2.py`). The matrix is not written into `vectors/tracks.f32`. A projection is not a Gemini vector: the 300-song bench on 21 Nov scored about 0.90 cosine and only 0.10 recall@10.
- `calibration.json`. A monotone map from a Gemma cosine to a Gemini-equivalent cosine, isotonic on each shared song's top-50 neighbour pairs. Scores are clipped to [-1, 1]. With nothing shared, the map is the identity.
- `agreement.jsonl`. One line per shared song: `{id, overlap_top10, overlap_top20}`, fractions of the neighbour set. `relation.json` holds the mean and a 10-bin distribution from 0 to 1. This is where the cheap map can be trusted. On 21 Nov the mean top-10 overlap on the 2,314 shared songs was 11.8%.

The first real fit should land near those measurements: shared `n` about 2,314, mean `overlap_top10` about 0.12, held-out cosine near 0.90, recall@10 near 0.10. Top-20 overlap was 0.22 on the 300-song bench (different clip windows); the identical-clip figure is whatever `relation.json` records. Recall this low means the projection must not be used as a stand-in for a missing Gemini row.

### Merged neighbours

`embed.py export` still writes Glancebar's `music-space.json` in the same shape. Beside it, the same command writes `music-space-merged.json` (or `<name>-merged.json` when `--out` is not `music-space.json`). Each track carries `source`:

- `gemini` — the song has a Gemini vector. `next` is that song's Gemini neighbour list, the same entries as in `music-space.json`.
- `gemma-calibrated` — the song has no Gemini vector. `next` comes from the Gemma map, and each similarity has been passed through `calibration.json`. A neighbour has to have a Gemma vector. A song that exists only in the Gemini map is not added to a Gemma list.

A Gemma-only track takes `kind` from the library genre. Its `kindScore` is 0, because it was not scored against the Gemini kind centroids.

### Which consumer reads which file

Nothing is switched. The radio stays on Gemini (decision 5, 21 Nov 2026).

- **Glancebar** reloads `~/Library/Application Support/Glancebar/mix/music-space.json` for `kind`, `kindScore` and `next`. The merged file is the one to point it at when Gemma-only songs should have neighbour lists.
- **6IAN Radio** builds `space.json` with `music-radio/tools/build_space.py`. Neighbours there are computed from Gemini `tracks.f32` (16 nearest). `music-space.json` is read only for `kind`. Swapping in the merged JSON does not change radio neighbours. Switching the radio means changing that builder.
- **Onto the TV** loads the radio's `space.json` through `engine/space.ts`. It follows the radio.

`pipeline/m5-sync.sh` still copies only the Gemini vectors, and this repo does not record the new files as a data set. Until `vectors-gemma/` and `vectors/relation/` are on the machine that runs `export`, the merged file is the Gemini list with `source: gemini` and no coverage songs.

## Tests

```bash
python3 -m unittest discover -s tools/music-embed -v
```

No network, no `google-genai`, and no real ffmpeg/ffprobe.

## Flags

Common: `--catalogue`, `--root`, `--ffmpeg-dir` (default `/opt/homebrew/bin`),
`--state`, `--model` (default `gemini-embedding-2`).