#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-MUSTMAP MM-C2: mustmap_stale chip (/tmp-полигоны).

Кейсы: touch doctrine → красный; touch mustmap.json → погас; файла нет →
тишина; битый/без doctrine_files → invalid; регресс соседей.
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")

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


def _append_journal(state, entries):
    path = os.path.join(state, "journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _mk_kit_with_mustmap(doctrine_rel="skills/orchestration/SKILL.md"):
    """tmp-kit: audit/mustmap/mustmap.json + doctrine file."""
    kit = tempfile.mkdtemp(prefix="mm-stale-kit-", dir="/tmp")
    doctrine = os.path.join(kit, doctrine_rel)
    os.makedirs(os.path.dirname(doctrine), exist_ok=True)
    _write_text(doctrine, "# doctrine\n")
    mm_dir = os.path.join(kit, "audit", "mustmap")
    os.makedirs(mm_dir, exist_ok=True)
    mm_path = os.path.join(mm_dir, "mustmap.json")
    _write_json(mm_path, {
        "version": 1,
        "doctrine_files": [doctrine_rel],
        "imperatives": [],
    })
    # ensure mustmap newer than doctrine initially
    now = time.time()
    os.utime(doctrine, (now - 100, now - 100))
    os.utime(mm_path, (now - 10, now - 10))
    return kit, mm_path, doctrine, doctrine_rel


def _mk_state(prefix="mm-stale-st-"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "stale-test",
        "fronts": [{"id": "F-ST", "status": "active", "title": "F-ST"}],
        "notes": "",
    })
    _write_text(os.path.join(root, "PROJECT.md"), "stale poly\n")
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


class TestMustmapStale(unittest.TestCase):
    def test_touch_skill_red(self):
        _measure("mustmap_stale: touch SKILL.md → красный")
        kit, mm_path, doctrine, rel = _mk_kit_with_mustmap()
        try:
            # doctrine newer than mustmap
            now = time.time()
            os.utime(mm_path, (now - 50, now - 50))
            os.utime(doctrine, (now, now))
            ids = orchlib.mustmap_stale(kit_dir=kit)
            self.assertIn(rel, ids)
            root, state = _mk_state()
            try:
                with _EnvState(state):
                    chips = orchlib.health_red_chips(
                        state=state, kit_dir=kit)
                self.assertIn(rel, chips.get("mustmap_stale") or [])
            finally:
                shutil.rmtree(root, ignore_errors=True)
        finally:
            shutil.rmtree(kit, ignore_errors=True)

    def test_touch_mustmap_clears(self):
        _measure("mustmap_stale: touch mustmap.json → погас")
        kit, mm_path, doctrine, rel = _mk_kit_with_mustmap()
        try:
            now = time.time()
            os.utime(doctrine, (now - 5, now - 5))
            os.utime(mm_path, (now, now))  # mustmap newer
            ids = orchlib.mustmap_stale(kit_dir=kit)
            self.assertEqual(ids, [])
        finally:
            shutil.rmtree(kit, ignore_errors=True)

    def test_no_file_silence(self):
        _measure("mustmap_stale: файла нет → тишина")
        kit = tempfile.mkdtemp(prefix="mm-stale-nofile-", dir="/tmp")
        try:
            ids = orchlib.mustmap_stale(kit_dir=kit)
            self.assertEqual(ids, [])
        finally:
            shutil.rmtree(kit, ignore_errors=True)

    def test_broken_invalid(self):
        _measure("mustmap_stale: битый JSON → invalid")
        kit = tempfile.mkdtemp(prefix="mm-stale-bad-", dir="/tmp")
        try:
            mm = os.path.join(kit, "audit", "mustmap", "mustmap.json")
            os.makedirs(os.path.dirname(mm), exist_ok=True)
            _write_text(mm, "{not-json")
            ids = orchlib.mustmap_stale(kit_dir=kit)
            self.assertEqual(ids, ["invalid"])
        finally:
            shutil.rmtree(kit, ignore_errors=True)

    def test_no_doctrine_files_invalid(self):
        _measure("mustmap_stale: без doctrine_files → invalid")
        kit = tempfile.mkdtemp(prefix="mm-stale-ndf-", dir="/tmp")
        try:
            mm = os.path.join(kit, "audit", "mustmap", "mustmap.json")
            _write_json(mm, {"version": 1, "imperatives": []})
            ids = orchlib.mustmap_stale(kit_dir=kit)
            self.assertEqual(ids, ["invalid"])
            _write_json(mm, {"version": 1, "doctrine_files": [], "imperatives": []})
            ids2 = orchlib.mustmap_stale(kit_dir=kit)
            self.assertEqual(ids2, ["invalid"])
        finally:
            shutil.rmtree(kit, ignore_errors=True)


class TestMustmapStaleNeighbors(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_state(prefix="mm-stale-nb-")
        self.kit, self.mm_path, self.doctrine, self.rel = _mk_kit_with_mustmap()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.kit, ignore_errors=True)

    def test_orders_suspect_regress(self):
        _measure("REGRESS orders_suspect (mustmap_stale suite)")
        _write_text(
            os.path.join(self.state, "fronts", "F-ST", "order.md"),
            "без советников: выбора нет\nпохоже сломано\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
            chips = orchlib.health_red_chips(
                state=self.state, kit_dir=self.kit)
        self.assertEqual(ids, ["F-ST"])
        self.assertIn("F-ST", chips.get("orders_suspect") or [])
        # mustmap not stale → empty
        self.assertEqual(chips.get("mustmap_stale") or [], [])

    def test_orders_without_basis_regress(self):
        _measure("REGRESS orders_without_basis (mustmap_stale suite)")
        _write_text(
            os.path.join(self.state, "fronts", "F-ST", "order.md"),
            "# bare order\nцель без подхода\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_without_basis(self.state)
        self.assertTrue(any("order.md" in x for x in ids))

    def test_advisors_without_scouts_regress(self):
        _measure("REGRESS advisors_without_scouts (mustmap_stale suite)")
        now = time.time()
        _append_journal(self.state, [{
            "kind": "start", "id": "adv-st", "ts": now,
            "front": "F-ST", "role": "meta/opportunity-advisor.md",
            "engine": "local",
        }])
        with _EnvState(self.state):
            chips = orchlib.health_red_chips(
                state=self.state, kit_dir=self.kit)
        self.assertIn("adv-st", chips.get("advisors_without_scouts") or [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
