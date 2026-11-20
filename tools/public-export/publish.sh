#!/usr/bin/env bash
# Refresh the public music-core repository from this private working checkout.
#   publish.sh          export + leak check + gitleaks + standalone test suite, stage into public checkout
#   publish.sh --push   also commit there and push (plain push; never forced)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/../.." && pwd)"
CONF="$HOME/.config/music-core/public-export.json"
PUBLIC_DIR="$(python3 -c "import json,os;print(os.path.expanduser(json.load(open('$CONF'))['public_dir']))")"
mode="${1:-stage}"
[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=no)" ] || { echo "publish: private tree has uncommitted changes" >&2; exit 1; }
[ -d "$PUBLIC_DIR/.git" ] || { echo "publish: $PUBLIC_DIR is not a git checkout" >&2; exit 1; }
if git -C "$ROOT" remote get-url origin >/dev/null 2>&1 && git -C "$PUBLIC_DIR" remote get-url origin >/dev/null 2>&1; then
  [ "$(git -C "$PUBLIC_DIR" remote get-url origin)" != "$(git -C "$ROOT" remote get-url origin)" ] || { echo "publish: public checkout points at the private remote" >&2; exit 1; }
fi
CAND="$(mktemp -d)/candidate"
trap 'rm -rf "$(dirname "$CAND")"' EXIT
python3 "$HERE/export.py" --out "$CAND"
gitleaks dir "$CAND" --no-banner --exit-code 1 >/dev/null 2>&1 || { echo "publish: gitleaks found a secret" >&2; exit 1; }
# Verify the sanitized public tree passes every unit test standalone (no private mixpool/)
(cd "$CAND" && ./tests.sh >/dev/null)
echo "publish: standalone public tests passed"
git -C "$PUBLIC_DIR" rm -rqf --ignore-unmatch . >/dev/null
cp -R "$CAND"/. "$PUBLIC_DIR"/
git -C "$PUBLIC_DIR" add -A
git -C "$PUBLIC_DIR" status --short | head -60
src="$(git -C "$ROOT" rev-parse --short HEAD)"
[ "$mode" = --push ] || { echo "publish: staged in $PUBLIC_DIR (private $src)"; exit 0; }
git -C "$PUBLIC_DIR" diff --cached --quiet || git -C "$PUBLIC_DIR" commit -qm "Sync from the working repository ($src)"
git -C "$PUBLIC_DIR" push -q origin HEAD:main
echo "publish: pushed (private $src)"
