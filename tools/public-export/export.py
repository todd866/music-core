#!/usr/bin/env python3
"""Build the public music-core tree: copy allowlisted paths, apply site-neutral substitutions from
~/.config/music-core/public-export.json (never committed), add public-only files (README, LICENSE,
CI, examples), and run a fail-closed leak check.
  export.py --out DIR"""
import argparse, fnmatch, json, re, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
CONF = Path.home() / '.config/music-core/public-export.json'
TEXT = {'.py', '.sh', '.json', '.md', '.txt', '.yml', '.yaml', '.plist', '.toml', '.html', '.example', ''}

GENERIC = [
    ('email', re.compile(r'\b[A-Za-z0-9._%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b')),
    ('ipv4', re.compile(r'\b(?!127\.0\.0\.1\b)(?!0\.0\.0\.0\b)(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?:\.(?:25[0-5]|2[0-4]\d|[01]?\d\d?)){3}\b')),
    ('home-path', re.compile(r'/Users/[A-Za-z][A-Za-z0-9_-]{1,}|/home/[a-z][a-z0-9_-]{1,}/')),
    ('tunnel-url', re.compile(r'https?://[a-z0-9]+(-[a-z0-9]+){2,}\.trycloudflare\.com')),
    ('r2-account', re.compile(r'\b[0-9a-f]{32}\.r2\.cloudflarestorage\.com')),
    ('private-key', re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----')),
    ('api-key', re.compile(r'\b(AIza[0-9A-Za-z_-]{30,}|re_[0-9A-Za-z]{20,}|sk-[0-9A-Za-z]{20,})\b')),
]
ALLOWED = {'noreply@example.com', 'you@example.com', 'todd866@users.noreply.github.com'}


def tracked():
    return subprocess.run(['git', '-C', str(ROOT), 'ls-files'], capture_output=True, text=True, check=True).stdout.split('\n')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    a = ap.parse_args()
    conf = json.loads(CONF.read_text())
    subs = [(re.compile(p), r) for p, r in conf.get('substitutions', [])]
    deny = [re.compile(t, re.I) for t in conf.get('deny', [])]
    pats = [l.strip() for l in (HERE / 'allowlist.txt').read_text().splitlines() if l.strip() and not l.startswith('#')]
    out = Path(a.out)
    if out.exists():
        sys.exit(f'{out} exists')

    files = [f for f in tracked() if f and Path(f).name != '.DS_Store' and any(fnmatch.fnmatch(f, p) for p in pats)]
    for f in files:
        dst = out / f
        dst.parent.mkdir(parents=True, exist_ok=True)
        src = ROOT / f
        if src.suffix in TEXT:
            s = src.read_text(encoding='utf-8')
            for p, r in subs:
                s = p.sub(r, s)
            dst.write_text(s, encoding='utf-8')
            shutil.copymode(src, dst)
        else:
            shutil.copy2(src, dst)

    for f in (HERE / 'public').rglob('*'):
        if not f.is_file() or f.name == '.DS_Store':
            continue
        dst = out / f.relative_to(HERE / 'public')
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dst)

    bad = []
    for f in out.rglob('*'):
        rel = str(f.relative_to(out))
        if f.is_file() and f.name == '.DS_Store':
            bad.append(f'{rel}: .DS_Store excluded from public export')
            continue
        if not f.is_file() or (f.suffix not in TEXT and f.name not in ('.gitignore', 'check')):
            continue
        s = f.read_text(errors='ignore')
        for name, p in GENERIC:
            for m in p.finditer(s):
                if m.group(0) not in ALLOWED:
                    bad.append(f'{rel}: {name}: {m.group(0)[:60]}')
        for p in deny:
            for m in p.finditer(s):
                bad.append(f'{rel}: private term: {m.group(0)}')
    if bad:
        print('\n'.join(bad[:100]), file=sys.stderr)
        sys.exit(f'leak check: {len(bad)} finding(s)')
    print(f'export: {len(files)} files, leak check clean -> {out}')


if __name__ == '__main__':
    main()
