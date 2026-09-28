#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-JEVFAST C3: panel badge /api/jev-last (офлайн, без сети).

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_jevfast_panel.py -q
"""
from __future__ import print_function

import importlib.util
import json
import os
import re
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADVISE_PATH = os.path.join(REPO, "bin", "jev-advise.py")
SERVER_PATH = os.path.join(REPO, "panel", "server.py")
INDEX_PATH = os.path.join(REPO, "panel", "index.html")


def _load_advise():
    spec = importlib.util.spec_from_file_location("jev_advise_c3panel", ADVISE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_server():
    # panel/server.py inserts kit/bin on sys.path; load as module for helpers
    spec = importlib.util.spec_from_file_location("orch_panel_server_c3", SERVER_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestBadgeMapping(unittest.TestCase):
    """Unit: journal record → advisory|defer|fail|None (C2 pure function)."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_band_yes_advisory(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "yes"}}),
            "advisory")

    def test_band_low_defer(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "low"}}),
            "defer")

    def test_band_absent_defer(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "absent"}}),
            "defer")

    def test_band_empty_dict_defer(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {}}),
            "defer")

    def test_status_error_fail(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "error", "band": {"q": "yes"}}),
            "fail")


class TestJevLastEndpoint(unittest.TestCase):
    """Smoke: jev_last_payload / read_last_jev_call on fixture journal."""

    @classmethod
    def setUpClass(cls):
        cls.server = _load_server()

    def test_missing_file_badge_null(self):
        d = tempfile.mkdtemp(prefix="jevfast-panel-nofile-")
        payload = self.server.jev_last_payload(d)
        self.assertIsNone(payload.get("badge"))
        self.assertIsNone(self.server.read_last_jev_call(d))

    def test_empty_file_badge_null(self):
        d = tempfile.mkdtemp(prefix="jevfast-panel-empty-")
        path = os.path.join(d, "jev-calls.jsonl")
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        payload = self.server.jev_last_payload(d)
        self.assertIsNone(payload.get("badge"))
        self.assertIsNone(self.server.read_last_jev_call(d))

    def test_last_record_fields(self):
        d = tempfile.mkdtemp(prefix="jevfast-panel-last-")
        path = os.path.join(d, "jev-calls.jsonl")
        rows = [
            {
                "ts": "2026-09-28T10:00:00Z",
                "point": "scout-need",
                "band": {"scout-need": "below"},
                "status": "ok",
            },
            {
                "ts": "2026-09-28T11:00:00Z",
                "point": "focus-hint-diff",
                "band": {"focus-hint-diff": "mid"},
                "status": "ok",
            },
        ]
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        payload = self.server.jev_last_payload(d)
        self.assertEqual(payload["ts"], "2026-09-28T11:00:00Z")
        self.assertEqual(payload["point"], "focus-hint-diff")
        self.assertEqual(payload["badge"], "advisory")


class TestIndexBadgeMarker(unittest.TestCase):
    """index.html: badge marker present; no jev action buttons."""

    def test_badge_marker_and_no_actions(self):
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("data-jev-badge", html)
        self.assertIn("/api/jev-last", html)
        self.assertIn("Jev:", html)
        # no shrink / skip-observer UI
        self.assertIsNone(re.search(r"1 критик", html))
        self.assertIsNone(re.search(r"пропустить", html, re.IGNORECASE))
        # no onclick/action wired to jev decisions
        self.assertIsNone(re.search(r"onclick\s*=\s*[\"'][^\"']*jev", html,
                                    re.IGNORECASE))
        self.assertIsNone(re.search(r"action\s*=\s*[\"'][^\"']*jev", html,
                                    re.IGNORECASE))

class TestServerRouteMarker(unittest.TestCase):
    def test_api_jev_last_in_server(self):
        with open(SERVER_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('/api/jev-last', src)
        self.assertIn("jev_last_payload", src)


if __name__ == "__main__":
    unittest.main()
