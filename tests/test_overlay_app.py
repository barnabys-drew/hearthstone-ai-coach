from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker import overlayapp  # noqa: E402


def repo(tmp: Path) -> Path:
    r = tmp / "repo"
    for rel, text in {"main.js": "main v2", "preload.js": "pre", "package.json": '{"v": 2}',
                      "config.example.json": '{"opacity": 0.9}', "renderer/app.js": "app v2",
                      "renderer/fonts/F.ttf": "font", "README.md": "not an app file"}.items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text, encoding="utf-8")
    return r


class OverlayAppTests(unittest.TestCase):
    def test_stale_then_sync_keeps_config_and_ignores_non_app_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, app = repo(Path(tmp)), Path(tmp) / "app"
            (app / "renderer").mkdir(parents=True)
            (app / "main.js").write_text("main v1", encoding="utf-8")
            (app / "preload.js").write_text("pre", encoding="utf-8")
            (app / "config.json").write_text('{"mine": true}', encoding="utf-8")
            stale = overlayapp.stale_files(app, r)
            self.assertIn("main.js", stale)
            self.assertIn("renderer/fonts/F.ttf", stale)
            self.assertNotIn("preload.js", stale)
            self.assertNotIn("README.md", stale)
            result = overlayapp.sync(app, r)
            self.assertEqual(set(result["copied"]), set(stale))
            self.assertEqual(result["seeded"], [])
            self.assertEqual((app / "config.json").read_text(encoding="utf-8"), '{"mine": true}')
            self.assertEqual(overlayapp.stale_files(app, r), [])

    def test_missing_config_is_seeded_from_the_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            r, app = repo(Path(tmp)), Path(tmp) / "app"
            app.mkdir()
            self.assertEqual(overlayapp.sync(app, r)["seeded"], ["config.json"])
            self.assertEqual((app / "config.json").read_text(encoding="utf-8"), '{"opacity": 0.9}')

    def test_app_dir_override(self):
        with mock.patch.dict("os.environ", {"HS_OVERLAY_APP_DIR": "/x/app"}):
            self.assertEqual(overlayapp.windows_app_dir(), Path("/x/app"))
        with mock.patch.dict("os.environ", {"HS_OVERLAY_DIR": "/c/u/hs-overlay"}, clear=True):
            self.assertEqual(overlayapp.windows_app_dir(), Path("/c/u/hearthstone-overlay"))

    def test_real_repo_file_list_covers_the_app(self):
        files = overlayapp.app_files()
        for rel in ("main.js", "preload.js", "package.json", "renderer/app.js",
                    "renderer/logic.js", "renderer/index.html", "renderer/fonts/Grenze.ttf"):
            self.assertIn(rel, files)
        self.assertNotIn("config.json", files)


if __name__ == "__main__":
    unittest.main()
