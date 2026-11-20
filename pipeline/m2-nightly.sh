#!/bin/zsh
# M2 nightly music-core pipeline (19:40Z daily via utc-daily, com.example.music-core-nightly).
# Replaces three separate jobs (M5->M2 music-push, M5->M2 music-embed,
# and M2 standalone music-facts) with one sequenced hub pipeline using ssh localhost (W24)
# for external-drive TCC access so it runs reliably even when the main machine is asleep or travelling.
set -u
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
here="${0:A:h}"
root="${here:h}"
state="$HOME/.local/state/music-core"
sync_dir="$HOME/.local/state/mac-sync/music-core"
log="$state/m2-nightly.log"
mkdir -p "$state" "$sync_dir"

stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { print -r -- "$(stamp) $*" >> "$log"; }
ssh_local=(ssh -o BatchMode=yes -o ConnectTimeout=15 localhost)

say "m2-nightly start"
push_rc=0; embed_rc=0; mirror_rc=0; facts_rc=0
notes=()

# 1. Push readable master roots to R2 and rebuild catalogue.json + music/ids.json (needs drive access -> ssh localhost)
say "step 1: push"
"${ssh_local[@]}" "/bin/zsh '$root/library/push.sh'" >> "$log" 2>&1
push_rc=$?
say "step 1: push exit $push_rc"
(( push_rc == 0 )) || notes+=("push exit $push_rc")

# 2. Incremental Gemini Batch embeddings (uses R2 catalogue + local venv + Gemini key)
say "step 2: embed"
"${ssh_local[@]}" "MUSIC_EMBED_CAP_USD='${MUSIC_EMBED_CAP_USD:-}' /bin/zsh '$root/embed/nightly.sh'" >> "$log" 2>&1
embed_rc=$?
say "step 2: embed exit $embed_rc"
(( embed_rc == 0 )) || notes+=("embed exit $embed_rc")

# 3. Mirror vectors + gaps + nightly-status to R2 music/vectors/ so M5 can pull from LAN or R2
say "step 3: mirror-vectors"
if [ ! -e "$HOME/.local/state/METERED_LINK" ]; then
  remote=$(/opt/homebrew/bin/python3 -c 'import json,os; print(json.load(open(os.path.expanduser("~/.config/glancebar/music-library.json")))["remote"].rstrip("/"))' 2>/dev/null || echo "")
  emb="$HOME/.local/state/music-embed"
  if [ -n "$remote" ] && [ -f "$emb/vectors/tracks.f32" ] && [ -f "$emb/vectors/tracks-index.jsonl" ]; then
    rclone copyto "$emb/vectors/tracks.f32" "$remote/music/vectors/tracks.f32" --s3-no-check-bucket >> "$log" 2>&1 \
      && rclone copyto "$emb/vectors/tracks-index.jsonl" "$remote/music/vectors/tracks-index.jsonl" --s3-no-check-bucket >> "$log" 2>&1 \
      && rclone copyto "$emb/gaps.json" "$remote/music/vectors/gaps.json" --s3-no-check-bucket >> "$log" 2>&1 \
      && rclone copyto "$emb/nightly-status.json" "$remote/music/vectors/nightly-status.json" --s3-no-check-bucket >> "$log" 2>&1
    mirror_rc=$?
  fi
fi
say "step 3: mirror-vectors exit $mirror_rc"
(( mirror_rc == 0 )) || notes+=("mirror-vectors exit $mirror_rc")

# 4. Sourced song facts (MusicBrainz year, covers, LRCLIB lyrics id, Wikipedia bio -> R2 music/facts.json)
say "step 4: facts"
/opt/homebrew/bin/python3 "$root/store/facts.py" >> "$HOME/.local/state/music-store/facts.out.log" 2>> "$HOME/.local/state/music-store/facts.err.log"
facts_rc=$?
say "step 4: facts exit $facts_rc"
(( facts_rc == 0 )) || notes+=("facts exit $facts_rc")

# Write atomic status locally and into mac-sync so M5 rebuild + fleet check see it without SSH
ok="true"
(( push_rc == 0 && embed_rc == 0 && mirror_rc == 0 && facts_rc == 0 )) || ok="false"
/opt/homebrew/bin/python3 - "$state/nightly-status.json" "$sync_dir/m2-nightly.json" "$ok" "$push_rc" "$embed_rc" "$mirror_rc" "$facts_rc" "${(j:; :)notes}" <<'PY'
import json, os, sys, time
out1, out2, ok, push_rc, embed_rc, mirror_rc, facts_rc, detail = sys.argv[1:9]
doc = {
    "ok": ok == "true",
    "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "epoch": int(time.time()),
    "steps": {
        "push": int(push_rc),
        "embed": int(embed_rc),
        "mirror_vectors": int(mirror_rc),
        "facts": int(facts_rc),
    },
    "detail": detail or "ok",
}
payload = json.dumps(doc, indent=2) + "\n"
for p in (out1, out2):
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, p)
PY

# 5. Refresh unified music-core health check
/opt/homebrew/bin/python3 "$here/check.py" >> "$log" 2>&1 || true
say "m2-nightly end: ok=$ok"
[[ "$ok" == "true" ]]
