#!/usr/bin/env python3
"""Unified music masters pool (local SSD or 2 TB cloud drive stand-in).

Configured via ~/.config/glancebar/music-pool.json (MUSIC_POOL_CONFIG overrides):
  {
    "mode": "cloud",                          # "cloud" | "local"
    "cloud_remote": "gdrive:MusicPool",       # 2 TB cloud stand-in for the SanDisk SSD
    "r2_remote": "music-bucket:music-bucket",
    "local_root": "/Volumes/MediaDrive/Music/radio-masters",
    "quota_gb": 2000,
    "hires_free_gb": 300,
    "min_free_gb": 100
  }

In cloud mode, every placed master is uploaded directly to R2 (serving/catalogue truth) AND to
cloud_remote (unified 2 TB master pool, zero USB disk contention). Free space is computed from
min(quota_gb - used_gb, remote_free_gb) and cached in ~/.local/state/music-core/cloud-pool-usage.json.
Switching to a physical 2 TB SSD later is a one-line config flip ("mode": "local") after `rclone copy`.
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any

HOME = Path.home()
CONFIG_PATH = Path(os.environ.get("MUSIC_POOL_CONFIG", HOME / ".config/glancebar/music-pool.json"))
USAGE_PATH = Path(os.environ.get("MUSIC_POOL_USAGE", HOME / ".local/state/music-core/cloud-pool-usage.json"))
RCLONE = os.environ.get("RCLONE_BIN", "/opt/homebrew/bin/rclone")
USAGE_TTL_S = 3600


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or CONFIG_PATH
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(doc, dict):
            return doc
    except (OSError, ValueError):
        pass
    return {"mode": "local", "local_root": "/Volumes/MediaDrive/Music/radio-masters", "quota_gb": 0}


def is_cloud_mode(cfg: dict[str, Any] | None = None) -> bool:
    c = cfg if cfg is not None else load_config()
    return c.get("mode") == "cloud" and bool(c.get("cloud_remote"))


def _rclone_json(args: list[str], timeout: int = 120, runner=None) -> dict[str, Any] | list[Any] | None:
    run = runner or subprocess.run
    try:
        r = run([RCLONE, *args], capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0 and (r.stdout or "").strip():
            return json.loads(r.stdout)
    except Exception:
        pass
    return None


def refresh_cloud_usage(cfg: dict[str, Any] | None = None, *, usage_path: Path | None = None, runner=None) -> dict[str, Any]:
    """Measure cloud_remote used bytes + backend free bytes and cache them atomically."""
    c = cfg if cfg is not None else load_config()
    up = usage_path or USAGE_PATH
    remote = str(c["cloud_remote"]).rstrip("/")
    base_remote = remote.split(":", 1)[0] + ":"
    size_doc = _rclone_json(["size", "--json", remote], timeout=120, runner=runner)
    about_doc = _rclone_json(["about", "--json", base_remote], timeout=60, runner=runner)
    used_bytes = int(size_doc.get("bytes", 0)) if isinstance(size_doc, dict) else None
    count = int(size_doc.get("count", 0)) if isinstance(size_doc, dict) else None
    remote_free = int(about_doc.get("free", 0)) if isinstance(about_doc, dict) and about_doc.get("free") is not None else None

    prev = {}
    try:
        prev = json.loads(up.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass

    doc = {
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "epoch": int(time.time()),
        "remote": remote,
        "quota_gb": float(c.get("quota_gb", 2000)),
        "used_bytes": used_bytes if used_bytes is not None else int(prev.get("used_bytes", 0)),
        "count": count if count is not None else int(prev.get("count", 0)),
        "remote_free_bytes": remote_free if remote_free is not None else prev.get("remote_free_bytes"),
        "ok": used_bytes is not None or remote_free is not None,
    }
    up.parent.mkdir(parents=True, exist_ok=True)
    tmp = up.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, up)
    return doc


def record_placed_bytes(added_bytes: int, *, usage_path: Path | None = None) -> None:
    up = usage_path or USAGE_PATH
    try:
        doc = json.loads(up.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    doc["used_bytes"] = int(doc.get("used_bytes", 0)) + int(added_bytes)
    doc["count"] = int(doc.get("count", 0)) + 1
    if isinstance(doc.get("remote_free_bytes"), (int, float)):
        doc["remote_free_bytes"] = max(0, int(doc["remote_free_bytes"]) - int(added_bytes))
    tmp = up.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, up)


def pool_free_gb(cfg: dict[str, Any] | None = None, *, usage_path: Path | None = None, runner=None) -> float:
    """Free space in decimal GB for the active music masters pool (local SSD or 2 TB cloud drive)."""
    c = cfg if cfg is not None else load_config()
    if is_cloud_mode(c):
        up = usage_path or USAGE_PATH
        doc = None
        try:
            doc = json.loads(up.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            doc = None
        if not doc or time.time() - float(doc.get("epoch", 0)) > USAGE_TTL_S:
            doc = refresh_cloud_usage(c, usage_path=up, runner=runner)
        quota_gb = float(c.get("quota_gb", 2000))
        used_gb = float(doc.get("used_bytes", 0)) / 1e9
        logical_free = max(0.0, quota_gb - used_gb)
        rf = doc.get("remote_free_bytes")
        if isinstance(rf, (int, float)) and rf >= 0:
            return min(logical_free, float(rf) / 1e9)
        return logical_free if doc.get("ok") else -1.0

    local_root = Path(os.path.expanduser(str(c.get("local_root", "/Volumes/MediaDrive/Music/radio-masters"))))
    target = local_root if local_root.exists() else local_root.parent
    try:
        st = os.statvfs(target)
        return st.f_bavail * st.f_frsize / 1e9
    except OSError:
        return -1.0


def verify_remote_size(remote_obj: str, expected_size: int, *, runner=None) -> bool:
    info = _rclone_json(["lsjson", remote_obj, "--s3-no-check-bucket"], timeout=60, runner=runner)
    if isinstance(info, list) and len(info) == 1 and int(info[0].get("Size", -1)) == expected_size:
        return True
    return False


def _cleanup_staging_parent(src: Path) -> None:
    """After a verified staged master is unlinked, tidy leftover cover.jpg & empty album dirs inside dl/."""
    parts = src.parts
    if "dl" not in parts:
        return
    dl_idx = len(parts) - 1 - parts[::-1].index("dl")
    dl_root = Path(*parts[: dl_idx + 1])
    parent = src.parent
    try:
        parent.relative_to(dl_root)
    except ValueError:
        return
    if parent == dl_root:
        return
    audio_exts = {".m4a", ".mp4", ".flac", ".wav", ".aiff", ".alac", ".mp3"}
    if parent.is_dir() and not any(p.is_file() and p.suffix.lower() in audio_exts for p in parent.iterdir()):
        cover = parent / "cover.jpg"
        if cover.is_file() and not cover.is_symlink():
            cover.unlink(missing_ok=True)
    cur = parent
    while cur != dl_root and cur != dl_root / "AM-Lossless":
        try:
            cur.rmdir()
        except OSError:
            break
        cur = cur.parent


def place_to_cloud(src: Path, rel: str, cfg: dict[str, Any] | None = None, *, usage_path: Path | None = None, runner=None) -> tuple[bool, str]:
    """Upload one staged master to R2 AND the 2 TB cloud pool; verify size before deleting staging."""
    c = cfg if cfg is not None else load_config()
    run = runner or subprocess.run
    if not src.is_file():
        return True, "already-gone"
    size = src.stat().st_size
    r2_remote = str(c.get("r2_remote", "music-bucket:music-bucket")).rstrip("/")
    cloud_remote = str(c["cloud_remote"]).rstrip("/")
    r2_dst = f"{r2_remote}/{rel}"
    cloud_dst = f"{cloud_remote}/{rel}"

    # 1. Upload to R2 (serving + catalogue truth) and verify size
    r1 = run([RCLONE, "copyto", str(src), r2_dst, "--s3-no-check-bucket"], capture_output=True, text=True, timeout=600)
    if r1.returncode != 0:
        return False, f"r2 upload failed: {(r1.stderr or '')[-120:].strip()}"
    if not verify_remote_size(r2_dst, size, runner=run):
        return False, "r2 size verification failed"

    # 2. Upload to the 2 TB cloud pool (gdrive:MusicPool)
    r2 = run([RCLONE, "copyto", str(src), cloud_dst, "--drive-stop-on-upload-limit"], capture_output=True, text=True, timeout=600)
    if r2.returncode != 0:
        return False, f"cloud pool upload failed: {(r2.stderr or '')[-120:].strip()}"

    # 3. Both verified -> remove temporary internal staging file and update cached pool usage
    src.unlink(missing_ok=True)
    _cleanup_staging_parent(src)
    record_placed_bytes(size, usage_path=usage_path)
    return True, "placed"


def main(argv: list[str] | None = None) -> int:
    import sys

    args = argv if argv is not None else sys.argv[1:]
    cmd = args[0] if args else "status"
    cfg = load_config()
    if cmd == "mode":
        print("cloud" if is_cloud_mode(cfg) else "local")
        return 0
    if cmd == "cloud-remote":
        print(str(cfg.get("cloud_remote", "")).rstrip("/"))
        return 0
    if cmd == "refresh":
        if not is_cloud_mode(cfg):
            print(json.dumps({"mode": "local", "free_gb": round(pool_free_gb(cfg), 1)}))
            return 0
        doc = refresh_cloud_usage(cfg)
        print(json.dumps({**doc, "mode": "cloud", "free_gb": round(pool_free_gb(cfg), 1)}))
        return 0 if doc.get("ok") else 1
    if cmd == "status":
        free = pool_free_gb(cfg)
        print(json.dumps({
            "mode": "cloud" if is_cloud_mode(cfg) else "local",
            "cloud_remote": cfg.get("cloud_remote") if is_cloud_mode(cfg) else None,
            "local_root": cfg.get("local_root"),
            "quota_gb": cfg.get("quota_gb"),
            "free_gb": round(free, 1),
            "hires_active": free >= float(cfg.get("hires_free_gb", 300)),
        }))
        return 0
    print(f"usage: pool.py [status|mode|cloud-remote|refresh]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
