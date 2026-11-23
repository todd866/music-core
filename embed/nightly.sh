#!/bin/zsh
# Nightly map refresh, on the home worker (started from the main machine by nightly-kick.sh, after the 19:40Z push).
# Embeds catalogue songs that are in R2 but have no vector, through the Batch API, within the
# daily spend cap, and appends them to vectors/tracks.f32 + tracks-index.jsonl. Then writes the
# gap report and copies it to the bucket as radio/gaps.json. Idempotent: every stage resumes.
# zsh, not bash: on the home worker only zsh and python3 hold the privacy grant for external drives (W17).
# State and logs: ~/.local/state/music-embed/. See tools/music-embed/README.md.
set -u
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
state="$HOME/.local/state/music-embed"
tool="${0:A:h}/embed.py"
py="$state/venv/bin/python"
keyfile="$HOME/.config/glancebar/gemini.env"
log="$state/nightly.log"
mkdir -p "$state"
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
say() { print -r -- "$(stamp) $*" >> "$log"; }
steps=() failed=()
run() {  # run NAME CMD...: log its output, remember a failure, carry on
  local name=$1; shift
  say "$name: start"
  "$@" >> "$log" 2>&1
  local rc=$?
  steps+=("$name:$rc")
  (( rc == 0 )) || failed+=("$name exit $rc")
  say "$name: exit $rc"
}
status() {  # status OK NOTE
  local ok=$1 note=$2 at=$(stamp)
  "$py" - "$state/nightly-status.json" "$ok" "$at" "$note" "${(j:,:)steps}" <<'EOF'
import json, sys, os
path, ok, at, note, steps = sys.argv[1:6]
doc = {"ok": ok == "true", "at": at, "note": note, "steps": [s for s in steps.split(",") if s]}
tmp = path + ".part"
open(tmp, "w").write(json.dumps(doc, indent=2) + "\n")
os.replace(tmp, path)
EOF
}

say "nightly start"
[ -x "$py" ] || { say "FAIL no venv at $py"; exit 1; }
[ -f "$tool" ] || { say "FAIL embed.py missing: $tool"; exit 1; }

metered=0
[ -e "$HOME/.local/state/METERED_LINK" ] && metered=1
if (( metered )); then
  say "metered link: no fetching or embedding tonight"
  failed+=("metered link")
else
  # The key is read here and given only to the child processes; never printed or logged.
  if [ -r "$keyfile" ]; then
    GEMINI_API_KEY=$(sed -n 's/^GEMINI_API_KEY=//p' "$keyfile" | head -1 | tr -d '"'"'"' \r')
    export GEMINI_API_KEY
  fi
  # Honour dated per-day cap override (~/.config/glancebar/embed-cap-override.json, Perth date, same as fetch_worker.py),
  # else ask the holistic $100/month allocator (budget.py) so backlogs clear fast while staying <= $96.50/mo.
  if [ -z "${MUSIC_EMBED_CAP_USD:-}" ] && [ -r "$HOME/.config/glancebar/embed-cap-override.json" ]; then
    ov=$("$py" -c 'import json,time,os; o=json.load(open(os.path.expanduser("~/.config/glancebar/embed-cap-override.json"))); print(o["usd"] if o.get("perth_date")==time.strftime("%Y-%m-%d",time.gmtime(time.time()+8*3600)) else "")' 2>/dev/null || true)
    [ -n "$ov" ] && export MUSIC_EMBED_CAP_USD="$ov" && say "cap override active: \$$ov"
  fi
  if [ -z "${MUSIC_EMBED_CAP_USD:-}" ] && [ -f "${0:A:h}/budget.py" ]; then
    hol=$("$py" "${0:A:h}/budget.py" cap --lane music 2>/dev/null || true)
    [ -n "$hol" ] && export MUSIC_EMBED_CAP_USD="$hol" && say "holistic budget cap (music): \$$hol"
  fi
  if [ -z "${GEMINI_API_KEY:-}" ]; then
    say "FAIL no GEMINI_API_KEY in $keyfile"
    failed+=("no key")
  else
    run collect "$py" -W ignore "$tool" increment collect
    run prepare env HEAVY_SLOT_LOCAL=1 heavy-slot -- "$py" -W ignore "$tool" increment prepare
    # Spend hold (the owner, 21 Nov 2026): Gemini bills the same Google Cloud credit, which must last until
    # next month's AI Pro credit. While ~/.config/fleet/gcp-spend-hold exists, songs are prepared but not sent.
    if [ -f "$HOME/.config/fleet/gcp-spend-hold" ]; then
      say "submit: skipped, Google Cloud spend hold is on"
    else
      run submit "$py" -W ignore "$tool" increment submit
    fi
    run wait "$py" -W ignore "$tool" increment wait --timeout 7200 --interval 60
  fi
fi
run spend "$py" "$tool" spend
[ -f "${0:A:h}/budget.py" ] && run budget "$py" "${0:A:h}/budget.py" status
if (( ${#failed} )); then status false "${(j:; :)failed}"; else status true ok; fi
run gaps "$py" "$tool" gaps --upload
# Second map, local and free (decided 21 Nov 2026): EmbeddingGemma 2 on the same clips, kept
# beside the Gemini map in vectors-gemma/. Bounded slice; skipped quietly until its venv exists.
gemma_py="$HOME/.local/state/music-gemma/venv/bin/python"
if [ -x "$gemma_py" ]; then
  run gemma-build env HEAVY_SLOT_LOCAL=1 heavy-slot -- "$gemma_py" "${0:A:h}/gemma_map.py" build --max-minutes 45
  run gemma-compare "$gemma_py" "${0:A:h}/gemma_map.py" compare
fi
# Relation map: ridge 768->3072, similarity calibration, neighbour agreement.
# Written under vectors/relation/. Never touches vectors/tracks.f32. Numpy only.
rel_py=""
for cand in "$gemma_py" "$py"; do
  [ -x "$cand" ] || continue
  if "$cand" -c 'import numpy' >/dev/null 2>&1; then
    rel_py="$cand"
    break
  fi
done
if [ -n "$rel_py" ]; then
  run relate env HEAVY_SLOT_LOCAL=1 heavy-slot -- "$rel_py" -W ignore "$tool" increment relate --max-minutes 10
else
  say "relate: no python with numpy"
  failed+=("relate no numpy")
fi
(( ${#failed} )) && status false "${(j:; :)failed}"
say "nightly end: ${#failed} problems"
(( ${#failed} == 0 ))
