import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import pool


class PoolTests(unittest.TestCase):
    def test_cloud_free_gb_respects_quota_and_remote_free(self):
        with tempfile.TemporaryDirectory() as td:
            up = Path(td) / "usage.json"
            cfg = {"mode": "cloud", "cloud_remote": "gdrive:MusicPool", "quota_gb": 2000}

            def fake_run(argv, **kw):
                if "size" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"count": 100, "bytes": int(150e9)}), stderr="")
                if "about" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"free": int(4500e9)}), stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="err")

            free = pool.pool_free_gb(cfg, usage_path=up, runner=fake_run)
            self.assertAlmostEqual(free, 1850.0, places=2)

            # Caps at real remote free space when smaller than quota headroom
            def tight_run(argv, **kw):
                if "size" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"count": 10, "bytes": int(10e9)}), stderr="")
                if "about" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps({"free": int(500e9)}), stderr="")
                return SimpleNamespace(returncode=1, stdout="", stderr="err")

            up.unlink()
            free2 = pool.pool_free_gb(cfg, usage_path=up, runner=tight_run)
            self.assertAlmostEqual(free2, 500.0, places=2)

    def test_place_to_cloud_verifies_r2_and_deletes_staging(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "track.m4a"
            src.write_bytes(b"0" * 4096)
            up = Path(td) / "usage.json"
            up.write_text(json.dumps({"used_bytes": 0, "count": 0, "remote_free_bytes": int(4000e9)}))
            cfg = {"mode": "cloud", "cloud_remote": "gdrive:MusicPool", "r2_remote": "r2:bucket", "quota_gb": 2000}
            calls = []

            def fake_run(argv, **kw):
                calls.append(argv)
                if "lsjson" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps([{"Size": 4096}]), stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            ok, msg = pool.place_to_cloud(src, "AM-Lossless/A/B/01.m4a", cfg, usage_path=up, runner=fake_run)
            self.assertTrue(ok, msg)
            self.assertFalse(src.exists())
            usage = json.loads(up.read_text())
            self.assertEqual(usage["used_bytes"], 4096)
            self.assertEqual(usage["count"], 1)
            self.assertEqual(len(calls), 3)

    def test_place_to_cloud_keeps_staging_on_upload_failure(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "track.m4a"
            src.write_bytes(b"1" * 1024)
            up = Path(td) / "usage.json"
            cfg = {"mode": "cloud", "cloud_remote": "gdrive:MusicPool", "r2_remote": "r2:bucket", "quota_gb": 2000}

            def fail_cloud(argv, **kw):
                if "lsjson" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps([{"Size": 1024}]), stderr="")
                if any("gdrive:MusicPool" in a for a in argv):
                    return SimpleNamespace(returncode=1, stdout="", stderr="quota exceeded")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            ok, _ = pool.place_to_cloud(src, "AM-Lossless/A/B/01.m4a", cfg, usage_path=up, runner=fail_cloud)
            self.assertFalse(ok)
            self.assertTrue(src.exists())

    def test_place_to_cloud_cleans_dl_cover_and_empty_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            album_dir = Path(td) / "dl" / "AM-Lossless" / "Artist" / "Album"
            album_dir.mkdir(parents=True)
            t1 = album_dir / "01.m4a"
            t2 = album_dir / "02.m4a"
            cover = album_dir / "cover.jpg"
            t1.write_bytes(b"a" * 512)
            t2.write_bytes(b"b" * 512)
            cover.write_bytes(b"jpg")
            up = Path(td) / "usage.json"
            up.write_text(json.dumps({"used_bytes": 0, "count": 0, "remote_free_bytes": int(4000e9)}))
            cfg = {"mode": "cloud", "cloud_remote": "gdrive:MusicPool", "r2_remote": "r2:bucket", "quota_gb": 2000}

            def fake_run(argv, **kw):
                if "lsjson" in argv:
                    return SimpleNamespace(returncode=0, stdout=json.dumps([{"Size": 512}]), stderr="")
                return SimpleNamespace(returncode=0, stdout="", stderr="")

            ok1, _ = pool.place_to_cloud(t1, "AM-Lossless/Artist/Album/01.m4a", cfg, usage_path=up, runner=fake_run)
            self.assertTrue(ok1)
            self.assertTrue(cover.exists(), "cover kept while sibling audio remains")
            ok2, _ = pool.place_to_cloud(t2, "AM-Lossless/Artist/Album/02.m4a", cfg, usage_path=up, runner=fake_run)
            self.assertTrue(ok2)
            self.assertFalse(cover.exists(), "cover removed when last staged track placed")
            self.assertFalse(album_dir.exists(), "empty album dir removed")
            self.assertTrue((Path(td) / "dl" / "AM-Lossless").is_dir())


if __name__ == "__main__":
    unittest.main()
