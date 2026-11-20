#!/bin/zsh
# M5 periodic sync (every 4 hours + at login, com.example.music-core-m5-sync).
# Replaces M5->M2 SSH kick wrappers and installs the previously missing Glancebar hot-set sync:
#  1. Exports M5 alias claims (Apple IDs, persistent IDs) to the hub alias-inputs/ (best-effort when M2 reachable).
#  2. Pulls fresh vectors + gaps.json from the hub (or R2 music/vectors/ fallback when away from home) and
#     re-exports Glancebar mix/music-space.json when vectors changed.
#  3. Syncs Glancebar's lossless hot set (musiclib.py sync) so the laptop always has current tracks.
#  4. Refreshes ~/.local/state/music-core/check.json.
set -u
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
here="${0:A:h}"
root="${here:h}"
state="$HOME/.local/state/music-core"
emb="$HOME/.local/state/music-embed"
log="$state/m5-sync.log"
mkdir -p "$state" "$emb/vectors/incoming"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { print -r -- "$(stamp) $*" >> "$log"; }
ssh_opts=(-o BatchMode=yes -o ConnectTimeout=10 -o ServerAliveInterval=20 -o ServerAliveCountMax=3)
peer="${MUSIC_CORE_HUB:-m2}"
problems=0
notes=()

say "m5-sync start"

# 1. Alias claims -> M2 (best-effort; skips cleanly if off-LAN/unreachable)
tmp=$(mktemp -d -t music-core-m5) || exit 1
trap 'rm -rf "$tmp"' EXIT
host_id=$(scutil --get LocalHostName 2>/dev/null || hostname -s)
claims="$tmp/${host_id:l}.jsonl"
if /opt/homebrew/bin/python3 "$root/store/musicstore.py" claims \
     --apple-ledger "$HOME/.local/state/music-scout/ledger.jsonl" \
     --liked-pulled "$HOME/.local/state/glancebar-mix/liked-pulled.json" \
     --library "$HOME/Library/Application Support/Glancebar/mix/library.json" \
     --out "$claims" >> "$log" 2>&1; then
  if ssh "${ssh_opts[@]}" "$peer" "mkdir -p .local/state/music-library/alias-inputs" 2>/dev/null \
     && scp -q "${ssh_opts[@]}" "$claims" "$peer:.local/state/music-library/alias-inputs/"; then
    say "claims: pushed to $peer"
  else
    say "claims: $peer unreachable (will retry next run)"
  fi
fi

# 2. Pull vectors + gaps.json (LAN rsync from the hub, else R2 fallback if unmetered)
before=$(cksum < "$emb/vectors/tracks-index.jsonl" 2>/dev/null || echo none)
inc="$emb/vectors/incoming"
got_vec=0
if rsync -t -e "ssh ${ssh_opts[*]}" \
     "$peer:.local/state/music-embed/vectors/tracks.f32" \
     "$peer:.local/state/music-embed/vectors/tracks-index.jsonl" \
     "$inc/" >> "$log" 2>&1; then
  got_vec=1
  rsync -t -e "ssh ${ssh_opts[*]}" \
    "$peer:.local/state/music-embed/gaps.json" \
    "$peer:.local/state/music-embed/nightly-status.json" \
    "$emb/" >> "$log" 2>&1 || true
  say "vectors: pulled from $peer"
elif [ ! -e "$HOME/.local/state/METERED_LINK" ]; then
  remote=$(/opt/homebrew/bin/python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.config/glancebar/music-library.json")))["remote"].rstrip("/"))' 2>/dev/null || echo "")
  if [ -n "$remote" ] \
     && rclone copyto "$remote/music/vectors/tracks.f32" "$inc/tracks.f32" --s3-no-check-bucket >> "$log" 2>&1 \
     && rclone copyto "$remote/music/vectors/tracks-index.jsonl" "$inc/tracks-index.jsonl" --s3-no-check-bucket >> "$log" 2>&1; then
    got_vec=1
    rclone copyto "$remote/music/vectors/gaps.json" "$emb/gaps.json" --s3-no-check-bucket >> "$log" 2>&1 || true
    rclone copyto "$remote/music/vectors/nightly-status.json" "$emb/nightly-status.json" --s3-no-check-bucket >> "$log" 2>&1 || true
    say "vectors: pulled from R2"
  fi
fi

if (( got_vec )); then
  rows=$(( $(stat -f %z "$inc/tracks.f32") / 12288 ))
  lines=$(grep -c . "$inc/tracks-index.jsonl")
  if [ "$rows" = "$lines" ] && (( rows > 0 )); then
    cp -p "$inc/tracks.f32" "$emb/vectors/tracks.f32.part" \
      && cp -p "$inc/tracks-index.jsonl" "$emb/vectors/tracks-index.jsonl.part" \
      && mv -f "$emb/vectors/tracks.f32.part" "$emb/vectors/tracks.f32" \
      && mv -f "$emb/vectors/tracks-index.jsonl.part" "$emb/vectors/tracks-index.jsonl"
    after=$(cksum < "$emb/vectors/tracks-index.jsonl" 2>/dev/null || echo none)
    library="$HOME/Library/Application Support/Glancebar/mix/library.json"
    if [ "$before" != "$after" ] && [ -f "$library" ]; then
      HEAVY_SLOT_LOCAL=1 heavy-slot -- /opt/homebrew/bin/python3 "$root/embed/embed.py" export --vectors "$emb/vectors" >> "$log" 2>&1 \
        && say "music-space.json rewritten" || { say "FAIL export"; problems=1; notes+=("embed export failed"); }
    fi
  else
    say "FAIL vectors disagree: $rows rows vs $lines lines"
    problems=1; notes+=("vectors disagree")
  fi
fi

# 3. Sync Glancebar lossless hot set + pull shared ids/facts/taste snapshot for offline engine
if [ ! -e "$HOME/.local/state/METERED_LINK" ]; then
  /opt/homebrew/bin/python3 "$root/store/musicstore.py" pull >> "$log" 2>&1 || say "store pull: warn"
  /opt/homebrew/bin/python3 "$root/store/musicstore.py" taste-pull >> "$log" 2>&1 || say "taste-pull: warn"
  /opt/homebrew/bin/python3 "$root/library/musiclib.py" sync >> "$log" 2>&1
  rc=$?
  if (( rc == 0 )); then
    say "hot-set: synced"
  elif (( rc == 75 )); then
    say "hot-set: busy (deferred)"
  else
    say "hot-set: exit $rc"
    problems=1; notes+=("hot-set exit $rc")
  fi
else
  say "hot-set: skipped (metered link)"
fi

# Write M5 sync status
/opt/homebrew/bin/python3 - "$state/m5-sync-status.json" "$problems" "${(j:; :)notes}" <<'PY'
import json, os, sys, time
path, problems, detail = sys.argv[1:4]
doc = {
    "ok": int(problems) == 0,
    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "epoch": int(time.time()),
    "detail": detail or "ok",
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as f:
    f.write(json.dumps(doc, indent=2) + "\n")
os.replace(tmp, path)
PY

# 4. Refresh unified health check + media-sync check
/opt/homebrew/bin/python3 "$here/check.py" >> "$log" 2>&1 || true
say "m5-sync end: problems=$problems"
(( problems == 0 ))
