#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""A4-FIX: allowlist + серая зона orders_suspect (advisor-need-check).

Полигоны: ORCHESTRATION_DIR=/tmp/**; живой state не пишем.
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import date
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
ALLOWLIST = os.path.join(REPO, "tests", "adversarial", "allowlist.json")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _mk_poly(prefix="alw-", fid="F-ALW", status="active"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "allowlist-test",
        "fronts": [{
            "id": fid,
            "status": status,
            "title": fid,
        }],
        "notes": "",
    })
    return root, state


class _EnvState(object):
    def __init__(self, state):
        self.state = state
        self._prev = None

    def __enter__(self):
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None
        return self

    def __exit__(self, *args):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        orchlib._fronts_base = None


def _load_allowlist():
    with open(ALLOWLIST, "r", encoding="utf-8") as f:
        return json.load(f)


def _advice(choice, band, action=None):
    if action is None:
        if band == "low":
            action = "defer"
        elif band == "mid":
            action = "confirm"
        elif band == "high":
            action = "auto"
        else:
            action = "defer"
    return {
        "advice": {
            "advisor-need-check": {
                "type": "choice",
                "choice": choice,
                "band": band,
                "action": action,
            }
        }
    }


class TestAllowlistFile(unittest.TestCase):
    def test_allowlist_has_ge5_real_entries(self):
        _measure("ALLOWLIST ≥5 real entries with fixtures")
        data = _load_allowlist()
        entries = data["entries"]
        self.assertGreaterEqual(len(entries), 5)
        ids = []
        for e in entries:
            for k in ("id", "pattern", "fixture", "expires_on", "note"):
                self.assertIn(k, e)
            self.assertTrue(e["pattern"])
            self.assertIn("без советников", e["fixture"].lower())
            ids.append(e["id"])
        self.assertEqual(len(ids), len(set(ids)))
        _measure("ALLOWLIST ids=%s" % ids)


class TestAllowlistHitSilence(unittest.TestCase):
    def test_each_fixture_silences_without_jev(self):
        _measure("ALLOWLIST-HIT → silence (no jev)")
        entries = _load_allowlist()["entries"]
        self.assertGreaterEqual(len(entries), 5)
        for e in entries:
            if not orchlib._orders_allowlist_entry_active(e):
                continue
            root, state = _mk_poly(prefix="alw-hit-", fid="F-HIT")
            try:
                body = (
                    "# Приказ\n%s\n"
                    "python3 bin/run-exec.py --front F-HIT\n"
                    % e["fixture"]
                )
                _write_text(
                    os.path.join(state, "fronts", "F-HIT", "order.md"), body)
                with _EnvState(state):
                    with mock.patch.object(
                        orchlib, "_rules_run_jev_advise",
                        side_effect=AssertionError("jev must not run"),
                    ):
                        ids = orchlib.orders_suspect(state)
                self.assertEqual(
                    ids, [],
                    "allowlist id=%s must silence; got %r" % (e["id"], ids),
                )
            finally:
                shutil.rmtree(root, ignore_errors=True)
        _measure("RESULT allowlist-hit silence OK ×%d" % len(entries))


class TestGreyZoneJev(unittest.TestCase):
    _GREY = (
        "# Приказ\n"
        "Либо A, либо B — развилка.\n"
        "без советников: выбора нет\n"
        "python3 bin/run-exec.py --front F-GZ\n"
    )

    def _run(self, advice_ret, err=None):
        root, state = _mk_poly(prefix="alw-gz-", fid="F-GZ")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-GZ", "order.md"), self._GREY)
            with _EnvState(state):
                with mock.patch.object(
                    orchlib, "_rules_run_jev_advise",
                    return_value=(advice_ret, err),
                ):
                    ids = orchlib.orders_suspect(state)
                    reasons = orchlib.orders_suspect_reasons()
            return ids, reasons
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_fork_mid_suspect(self):
        _measure("GREY fork mid → suspect jev=fork")
        ids, reasons = self._run(_advice("fork", "mid"))
        self.assertEqual(ids, ["F-GZ"])
        self.assertIn("jev=fork", reasons.get("F-GZ") or "")

    def test_fork_high_suspect(self):
        _measure("GREY fork high → suspect jev=fork")
        ids, reasons = self._run(_advice("fork", "high"))
        self.assertEqual(ids, ["F-GZ"])
        self.assertIn("jev=fork", reasons.get("F-GZ") or "")

    def test_mechanical_silence(self):
        _measure("GREY mechanical → silence")
        ids, reasons = self._run(_advice("mechanical", "high"))
        self.assertEqual(ids, [])
        self.assertNotIn("F-GZ", reasons)

    def test_mechanical_low_silence(self):
        _measure("GREY mechanical band=low → silence")
        ids, reasons = self._run(_advice("mechanical", "low"))
        self.assertEqual(ids, [])
        self.assertNotIn("F-GZ", reasons)

    def test_fork_low_suspect(self):
        _measure("GREY fork band=low → suspect jev=low-confidence")
        ids, reasons = self._run(_advice("fork", "low"))
        self.assertEqual(ids, ["F-GZ"])
        self.assertIn("jev=low-confidence", reasons.get("F-GZ") or "")

    def test_defer_unavailable(self):
        _measure("GREY defer → jev=unavailable")
        ids, reasons = self._run(_advice("fork", "absent", action="defer"))
        self.assertEqual(ids, ["F-GZ"])
        reason = reasons.get("F-GZ") or ""
        self.assertIn("jev=unavailable", reason)

    def test_api_fail_unavailable(self):
        _measure("GREY API-fail → jev=unavailable")
        ids, reasons = self._run(None, err="exit 1")
        self.assertEqual(ids, ["F-GZ"])
        self.assertIn("jev=unavailable", reasons.get("F-GZ") or "")


class TestMarkersRegress(unittest.TestCase):
    def test_markers_still_suspect(self):
        _measure("MARKERS regress: bez+вероятно → suspect")
        root, state = _mk_poly(prefix="alw-mk-", fid="F-MK")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-MK", "order.md"),
                "без советников: выбора нет\nсервер вероятно жив\n",
            )
            with _EnvState(state):
                with mock.patch.object(
                    orchlib, "_rules_run_jev_advise",
                    side_effect=AssertionError("markers must not call jev"),
                ):
                    ids = orchlib.orders_suspect(state)
            self.assertEqual(ids, ["F-MK"])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestExpiresOn(unittest.TestCase):
    def test_expired_does_not_silence(self):
        _measure("expires_on expired → NOT silence")
        root = tempfile.mkdtemp(prefix="alw-exp-", dir="/tmp")
        try:
            alw_path = os.path.join(root, "allowlist.json")
            _write_json(alw_path, {
                "version": 1,
                "entries": [{
                    "id": "expired-mech",
                    "pattern": "без советников: выбора нет, механическая",
                    "fixture": "без советников: выбора нет, механическая",
                    "expires_on": "2020-01-01",
                    "note": "просрочено для теста",
                }],
            })
            state_root, state = _mk_poly(prefix="alw-exp-st-", fid="F-EXP")
            try:
                _write_text(
                    os.path.join(state, "fronts", "F-EXP", "order.md"),
                    "без советников: выбора нет, механическая\n"
                    "python3 bin/run-exec.py --front F-EXP\n",
                )
                prev = os.environ.get("ORCH_ORDERS_ALLOWLIST")
                os.environ["ORCH_ORDERS_ALLOWLIST"] = alw_path
                orchlib._ORDERS_ALLOWLIST_CACHE = None
                try:
                    with _EnvState(state):
                        with mock.patch.object(
                            orchlib, "_rules_run_jev_advise",
                            return_value=(_advice("fork", "mid"), None),
                        ):
                            ids = orchlib.orders_suspect(state)
                    self.assertEqual(
                        ids, ["F-EXP"],
                        "expired allowlist must NOT silence; got %r" % ids,
                    )
                    # прямая проверка: hit=False при today после expires
                    self.assertFalse(
                        orchlib.orders_allowlist_hit(
                            "без советников: выбора нет, механическая",
                            today=date(2026, 10, 3),
                        )
                    )
                finally:
                    if prev is None:
                        os.environ.pop("ORCH_ORDERS_ALLOWLIST", None)
                    else:
                        os.environ["ORCH_ORDERS_ALLOWLIST"] = prev
                    orchlib._ORDERS_ALLOWLIST_CACHE = None
            finally:
                shutil.rmtree(state_root, ignore_errors=True)
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestJevPointPresent(unittest.TestCase):
    def test_advisor_need_check_is_19th(self):
        _measure("jev-table advisor-need-check = 19th")
        path = os.path.join(REPO, "routing", "jev-table.json")
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        points = data["points"]
        self.assertEqual(len(points), 19)
        by_id = {p["id"]: p for p in points}
        self.assertIn("advisor-need-check", by_id)
        p = by_id["advisor-need-check"]
        self.assertEqual(p["type"], "Choice")
        self.assertEqual(p["defer_below"], 0.5)
        self.assertEqual(p["confirm_below"], 0.75)
        self.assertEqual(
            set(p["criteria"].keys()), {"mechanical", "fork"})
        self.assertIn("suspect", p["fallback"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
