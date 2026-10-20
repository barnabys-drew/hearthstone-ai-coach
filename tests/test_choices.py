from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "hearthstone-tracker"))

from hstracker import choices  # noqa: E402


class ChoicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_default_follows_hs_jev_when_nothing_picked(self):
        with mock.patch.dict("os.environ", {"HS_JEV": "1"}, clear=True):
            self.assertEqual(choices.read_mode(self.dir), "jev")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(choices.read_mode(self.dir), "coach")
            self.assertEqual(choices.read_mode(None), "coach")

    def test_write_then_read_keeps_other_keys(self):
        (self.dir / "settings.json").write_text(json.dumps({"other": 1}), encoding="utf-8")
        choices.write_mode(self.dir, "both")
        data = json.loads((self.dir / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual((data["choices"], data["other"]), ("both", 1))
        self.assertEqual(choices.read_mode(self.dir), "both")

    def test_invalid_values_are_rejected_or_ignored(self):
        with self.assertRaises(ValueError):
            choices.write_mode(self.dir, "sonnet")
        (self.dir / "settings.json").write_text('{"choices": "nonsense"}', encoding="utf-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(choices.read_mode(self.dir), "coach")
        (self.dir / "settings.json").write_text("not json", encoding="utf-8")
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertEqual(choices.read_mode(self.dir), "coach")

    def test_jev_modes_fall_back_without_a_key(self):
        self.assertEqual(choices.effective_mode("jev", False), "coach")
        self.assertEqual(choices.effective_mode("both", False), "coach")
        self.assertEqual(choices.effective_mode("off", False), "off")
        self.assertEqual(choices.effective_mode("both", True), "both")
        self.assertIn("TYPESAFE_API_KEY is not set", choices.status_line("jev", False))
        self.assertTrue(choices.status_line("off", True).startswith("== CHOICES MODE: off"))

    def test_watcher_reports_a_change_once(self):
        with mock.patch.dict("os.environ", {}, clear=True):
            watcher = choices.ModeWatcher(self.dir)
            self.assertEqual(watcher.poll(), ("coach", False))
            choices.write_mode(self.dir, "off")
            path = self.dir / "settings.json"
            os.utime(path, (time.time() + 5, time.time() + 5))  # coarse-mtime filesystems
            self.assertEqual(watcher.poll(), ("off", True))
            self.assertEqual(watcher.poll(), ("off", False))



class ServeSettingsTests(unittest.TestCase):
    """serve.py's one write endpoint: the browser-mode choices picker."""

    @classmethod
    def setUpClass(cls):
        import importlib.util
        import threading
        from http.server import ThreadingHTTPServer

        spec = importlib.util.spec_from_file_location("overlay_serve", ROOT / "hearthstone-overlay" / "serve.py")
        cls.serve = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.serve)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.serve.OverlayHandler.overlay_dir = Path(cls.tmp.name)
        cls.serve.OverlayHandler.poll_ms = 250
        cls.serve.OverlayHandler.stale_advice_seconds = 75
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), cls.serve.OverlayHandler)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def post(self, body, *, ctype="application/json", origin=None, path="/settings"):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        headers = {"Content-Type": ctype, "Host": f"localhost:{self.port}"}
        if origin:
            headers["Origin"] = origin
        conn.request("POST", path, body=body.encode(), headers=headers)
        resp = conn.getresponse()
        status = resp.status
        resp.read()
        conn.close()
        return status

    def test_valid_pick_is_written(self):
        self.assertEqual(self.post('{"choices": "both"}', origin=f"http://localhost:{self.port}"), 200)
        self.assertEqual(choices.read_mode(self.tmp.name), "both")

    def test_guards(self):
        self.assertEqual(self.post('{"choices": "jev"}', ctype="text/plain"), 415)
        self.assertEqual(self.post('{"choices": "jev"}', origin="https://evil.example"), 403)
        self.assertEqual(self.post('{"choices": "jev"}', origin="null"), 403)
        self.assertEqual(self.post('{"choices": "gpt"}'), 400)
        self.assertEqual(self.post("not json"), 400)
        self.assertEqual(self.post('{"choices": "jev"}', path="/data/live.json"), 404)
        self.assertEqual(self.post("x" * 2000), 413)

    def test_ipv6_loopback_and_empty_origins(self):
        # [::1] is loopback and must work; an Origin with no host must not.
        self.assertEqual(self.post('{"choices": "coach"}', origin=f"http://[::1]:{self.port}"), 200)
        self.assertEqual(self.post('{"choices": "jev"}', origin="file://"), 403)
        self.assertEqual(self.serve._hostname("[::1]:8420"), "::1")
        self.assertEqual(self.serve._hostname("LOCALHOST:8420"), "localhost")
        self.assertIsNone(self.serve._hostname("file://"))


class AtomicWriteTests(unittest.TestCase):
    def test_concurrent_writers_never_collide(self):
        """Jev answers are written from background threads; they used to share
        one temp file per process."""
        import threading

        from hstracker.overlay import atomic_write_json

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "jev.json"
            errors = []

            def write(i):
                try:
                    for _ in range(40):
                        atomic_write_json(target, {"writer": i, "pad": "x" * 2000})
                except Exception as e:  # noqa: BLE001 - any failure is the finding
                    errors.append(e)

            threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            self.assertEqual(errors, [])
            self.assertIn(json.loads(target.read_text(encoding="utf-8"))["writer"], range(6))
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])


class CurrentDeckTests(unittest.TestCase):
    def test_one_decks_log_pass_gives_counts_name_and_format(self):
        from datetime import datetime

        from hstracker import cli
        from hstracker.decks import DeckEvent

        deck = "AAECAaKaBgL8rweqyQcOhqAEjtQEkNQE4+YG6IcHhZUH1aYH7awHj7EH0LIH1rIHlcIHm8IHnMIHAAA="
        calls = []

        def fake_parse(path):
            calls.append(path)
            return [DeckEvent(datetime(2026, 7, 4), "Burn Warrior", deck)]

        class Resolver:
            def card_by_dbf(self, dbf):
                return {"id": f"C{dbf}"}

        with mock.patch.object(cli, "deck_logs", return_value=["Decks.log"]), \
                mock.patch.object(cli, "parse_decks_log", side_effect=fake_parse):
            counts, name, fmt = cli._current_deck("S", Resolver())
        self.assertEqual(len(calls), 1)
        self.assertEqual((name, fmt), ("Burn Warrior", 2))
        self.assertEqual(sum(counts.values()), 30)


if __name__ == "__main__":
    unittest.main()
