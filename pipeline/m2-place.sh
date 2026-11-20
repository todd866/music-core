#!/bin/zsh
# M2 master placer (every 15 min, com.example.music-core-place).
# Runs music-radio/media/place_masters.py locally on the hub via ssh localhost (W24) so downloaded
# lane/request masters land on MediaDrive + R2 continuously even when M5 is asleep or away.
set -u
export PATH="/opt/homebrew/bin:$HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
state="$HOME/.local/state/music-core"
mkdir -p "$state"
log="$state/place.log"
script="$HOME/Projects/music-radio/media/place_masters.py"
[ -f "$script" ] || { print -r -- "$(date -u +%FT%TZ) missing $script" >> "$log"; exit 1; }

ssh -o BatchMode=yes -o ConnectTimeout=15 localhost \
  "PATH=/opt/homebrew/bin:\$HOME/.local/bin:/usr/bin:/bin /opt/homebrew/bin/python3 '$script'" >> "$log" 2>&1
rc=$?
print -r -- "$(date -u +%FT%TZ) place exit $rc" >> "$log"
exit $rc
