#!/usr/bin/env python3
"""Unified Fleet health check for the music-core + 6IAN + Glancebar + TV music stack.

Writes ~/.local/state/music-core/check.json in Fleet's `.fleet/check` v1 JSON schema so
`fleet morning` sees the entire music pipeline (hub + laptop + 6IAN worker) in one row,
with zero SSH calls during collection (reads local state + Syncthing mac-sync/music-core/).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
STATE = Path(os.environ.get("MUSIC_CORE_STATE", HOME / ".local/state/music-core"))
SYNC_DIR = Path(os.environ.get("MUSIC_CORE_SYNC", HOME / ".local/state/mac-sync/music-core"))


def _host_id() -> str:
    cfg = HOME / ".config/glancebar/music-store.json"
    if cfg.is_file():
        try:
            h = json.loads(cfg.read_text()).get("host")
            if h:
                return str(h).lower()
        except Exception:
            pass
    try:
        r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, timeout=5)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip().lower()
    except Exception:
        pass
    return os.uname().nodename.split(".")[0].lower()


def _load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _age_h(path: Path, doc: dict | None = None) -> float | None:
    now = time.time()
    if isinstance(doc, dict):
        ep = doc.get("epoch") or doc.get("generated")
        if isinstance(ep, (int, float)) and ep > 1e9:
            return max(0.0, (now - ep) / 3600.0)
        at = doc.get("at")
        if isinstance(at, str):
            for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%d %H:%M:%S"):
                try:
                    t = time.mktime(time.strptime(at[:19], fmt))
                    # strptime in local time if no Z; for Z adjust by timezone
                    if at.endswith("Z"):
                        import calendar
                        t = calendar.timegm(time.strptime(at[:19], "%Y-%m-%dT%H:%M:%S"))
                    return max(0.0, (now - t) / 3600.0)
                except ValueError:
                    pass
    if path.is_file():
        return max(0.0, (now - path.stat().st_mtime) / 3600.0)
    return None


def _row(key: str, status: str, name: str, value: str, datum: str = "", detail: str = "") -> dict:
    return {
        "key": key,
        "status": status,
        "name": name,
        "value": value,
        "datum": datum,
        "detail": detail or f"{name}: {value}",
    }


def _check_fresh_status(key: str, name: str, path: Path, warn_h: float, fail_h: float, *, ok_key: str = "ok") -> dict:
    doc = _load_json(path)
    if doc is None:
        return _row(key, "warn", name, "missing", "no status", f"{path} missing")
    age = _age_h(path, doc)
    age_s = f"{age:.0f}h" if age is not None else "?"
    ok = doc.get(ok_key, True)
    warn = doc.get("warning") or ""
    err = doc.get("detail") or doc.get("note") or ""
    if not ok:
        return _row(key, "fail", name, "failed", age_s, f"{err or 'ok=false'} ({age_s} ago)")
    if age is not None and age > fail_h:
        return _row(key, "fail", name, f"stale {age_s}", f"max {fail_h:.0f}h", f"{name} last ok {age_s} ago")
    if warn:
        return _row(key, "warn", name, "warning", age_s, f"{warn} ({age_s} ago)")
    if age is not None and age > warn_h:
        return _row(key, "warn", name, f"stale {age_s}", f"warn {warn_h:.0f}h", f"{name} last ok {age_s} ago")
    return _row(key, "pass", name, "ok", age_s, f"{name}: ok ({age_s} ago)")


def collect_m2_rows() -> list[dict]:
    rows: list[dict] = []
    # 1. Nightly push/embed/facts
    push_p = HOME / ".local/state/music-library/push-status.json"
    rows.append(_check_fresh_status("mc:worker:push", "m2 push", push_p, 30, 54))

    emb_p = HOME / ".local/state/music-embed/nightly-status.json"
    rows.append(_check_fresh_status("mc:worker:embed", "m2 embed", emb_p, 30, 54))

    # 2. Music store sync (10 min cadence)
    st_p = HOME / ".local/state/music-store/sync-status.json"
    st_doc = _load_json(st_p)
    if st_doc is None:
        rows.append(_row("mc:worker:store", "warn", "m2 store-sync", "missing", "no status"))
    else:
        age = _age_h(st_p, st_doc) or 0.0
        failed = (st_doc.get("flush") or {}).get("failed", 0)
        if failed:
            rows.append(_row("mc:worker:store", "fail", "m2 store-sync", f"{failed} flush fail", f"{age*60:.0f}m"))
        elif age > 2:
            rows.append(_row("mc:worker:store", "fail", "m2 store-sync", f"stale {age:.1f}h", "10m job"))
        else:
            rows.append(_row("mc:worker:store", "pass", "m2 store-sync", "ok", f"{age*60:.0f}m"))

    # 3. 6IAN fetch worker
    r = subprocess.run(["pgrep", "-f", "media/fetch_worker.py"], capture_output=True)
    lane_p = HOME / ".local/state/music-radio-fetch/lane.json"
    lane_n = 0
    if lane_p.is_file():
        try:
            ld = json.loads(lane_p.read_text())
            lane_n = len(ld) if isinstance(ld, list) else len(ld.get("items", []))
        except Exception:
            pass
    if r.returncode == 0:
        rows.append(_row("mc:worker:fetch", "pass", "6ian fetch-worker", "running", f"lane {lane_n}"))
    else:
        rows.append(_row("mc:worker:fetch", "fail", "6ian fetch-worker", "not running", f"lane {lane_n}",
                         "com.example.music-radio-fetch process not found"))

    # 4. Facts freshness
    facts_p = HOME / ".local/state/music-store/facts.json"
    if facts_p.is_file():
        age = _age_h(facts_p) or 0.0
        st = "pass" if age <= 36 else ("warn" if age <= 72 else "fail")
        rows.append(_row("mc:worker:facts", st, "m2 facts", "ok" if st == "pass" else f"stale {age:.0f}h", f"{age:.0f}h"))
    else:
        rows.append(_row("mc:worker:facts", "warn", "m2 facts", "missing"))

    return rows


def collect_m5_rows() -> list[dict]:
    rows: list[dict] = []
    # 1. M5 sync (claims + vectors + hot-set)
    m5_p = STATE / "m5-sync-status.json"
    if m5_p.is_file():
        rows.append(_check_fresh_status("mc:primary:sync", "m5 sync", m5_p, 12, 36))

    # 2. Hot-set freshness
    lib_p = HOME / ".local/state/music-library/last-status.json"
    if lib_p.is_file():
        age = _age_h(lib_p) or 0.0
        st = "pass" if age <= 24 else ("warn" if age <= 72 else "fail")
        rows.append(_row("mc:primary:hotset", st, "m5 hot-set", "ok" if st == "pass" else f"stale {age:.0f}h", f"{age:.0f}h"))

    # 3. Radio sync (10 min cadence)
    rs_p = HOME / ".local/state/glancebar-radio/status.json"
    rows.append(_check_fresh_status("mc:primary:radio-sync", "radio-sync", rs_p, 2, 12))

    # 4. Scout last run
    sc_p = HOME / ".local/state/music-scout/last-run.json"
    sc_doc = _load_json(sc_p)
    if sc_doc is None:
        rows.append(_row("mc:primary:scout", "warn", "scout", "missing"))
    else:
        age = _age_h(sc_p, sc_doc) or 0.0
        errs = sc_doc.get("errors") or []
        if sc_doc.get("ok"):
            st = "pass" if age <= 36 else "warn"
            rows.append(_row("mc:primary:scout", st, "scout", "ok" if st == "pass" else f"stale {age:.0f}h", f"{age:.0f}h"))
        else:
            rows.append(_row("mc:primary:scout", "warn", "scout", f"{len(errs)} err", f"{age:.0f}h",
                             "; ".join(str(e)[:120] for e in errs[:2])))

    # 5. 6IAN nightly rebuild
    rb_log = HOME / ".local/state/music-radio/rebuild.log"
    if rb_log.is_file():
        age = _age_h(rb_log) or 0.0
        st = "pass" if age <= 36 else ("warn" if age <= 72 else "fail")
        rows.append(_row("mc:primary:rebuild", st, "6ian rebuild", "ok" if st == "pass" else f"stale {age:.0f}h", f"{age:.0f}h"))

    return rows


def main() -> int:
    STATE.mkdir(parents=True, exist_ok=True)
    SYNC_DIR.mkdir(parents=True, exist_ok=True)
    host = _host_id()
    now = int(time.time())

    local_rows = collect_m2_rows() if host == "m2" else collect_m5_rows()
    # Publish this host's rows into Syncthing share so the peer reads them with zero SSH
    share_file = SYNC_DIR / f"{host}-rows.json"
    tmp = share_file.with_suffix(".tmp")
    tmp.write_text(json.dumps({"host": host, "generated": now, "rows": local_rows}, indent=2) + "\n")
    os.replace(tmp, share_file)

    all_rows = list(local_rows)
    peer = "m5" if host == "m2" else "m2"
    peer_file = SYNC_DIR / f"{peer}-rows.json"
    peer_doc = _load_json(peer_file)
    if peer_doc and isinstance(peer_doc.get("rows"), list):
        peer_age_h = max(0.0, (now - int(peer_doc.get("generated", 0))) / 3600.0)
        if peer_age_h > 48:
            all_rows.append(_row(f"mc:{peer}:stale", "warn", f"{peer} peer", f"stale {peer_age_h:.0f}h"))
        all_rows.extend(peer_doc["rows"])

    fails = [r for r in all_rows if r["status"] == "fail"]
    warns = [r for r in all_rows if r["status"] == "warn"]
    status = "fail" if fails else ("warn" if warns else "pass")
    value = "ok" if status == "pass" else f"{len(fails)} fail {len(warns)} warn"
    datum = (fails[0]["name"] if fails else (warns[0]["name"] if warns else f"{len(all_rows)} checks"))
    detail = "; ".join(r["detail"] for r in (fails + warns)[:4])

    out = {
        "version": 1,
        "generated": now,
        "status": status,
        "value": value,
        "datum": datum,
        "detail": detail,
        "rows": all_rows,
    }
    check_path = STATE / "check.json"
    ctmp = check_path.with_suffix(".tmp")
    ctmp.write_text(json.dumps(out, indent=2) + "\n")
    os.replace(ctmp, check_path)

    for r in all_rows:
        print(f"{r['status'].upper():4s} {r['name']}: {r['value']} ({r['datum']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
