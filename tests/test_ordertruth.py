#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-ORDERTRUTH OT-C1: orders_suspect + order_suspect_facts (/tmp-полигоны).

Группы: (а) маркер+без-советников / чисто / advisor / пометка замера +
прямые проверки предиката; (г) orders_without_basis и advisors_without_scouts
не сломаны. State: только ORCHESTRATION_DIR=/tmp/ordertruth-…;
/root/.orchestration не трогаем.
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


def _mk_poly(prefix="ordertruth-", fid="F-OT", status="active"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "ordertruth-test",
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


# ---------------------------------------------------------------------------
# (а) предикат + orders_suspect
# ---------------------------------------------------------------------------


class TestOrderSuspectFacts(unittest.TestCase):
    def test_marker_and_bez(self):
        _measure("MEASURE predicate: marker+bez")
        bez, markers = orchlib.order_suspect_facts(
            "без советников: выбора нет\nсервер вероятно жив\n")
        self.assertTrue(bez)
        self.assertIn("вероятно", [m.lower() for m in markers])

    def test_no_markers(self):
        _measure("MEASURE predicate: bez without markers")
        bez, markers = orchlib.order_suspect_facts(
            "без советников: выбора нет, механическая\n")
        self.assertTrue(bez)
        self.assertEqual(markers, [])

    def test_no_bez_sovetnikov(self):
        _measure("MEASURE predicate: markers without bez")
        bez, markers = orchlib.order_suspect_facts(
            "сервер вероятно жив, похоже на ок\n")
        self.assertFalse(bez)
        self.assertTrue(len(markers) >= 1)

    def test_case_insensitive(self):
        bez, markers = orchlib.order_suspect_facts(
            "Без Советников: нет\nКАЖЕТСЯ готово\n")
        self.assertTrue(bez)
        self.assertTrue(any(m.lower() == "кажется" for m in markers))


class TestOrdersSuspectChip(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly()
        self.fid = "F-OT"

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_marker_bez_flags_front(self):
        _measure("MEASURE chip: marker+bez → front id")
        _write_text(
            os.path.join(self.state, "fronts", self.fid, "order.md"),
            "без советников: выбора нет\nсервер вероятно жив\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertEqual(ids, [self.fid])
        self.assertIn(self.fid, chips.get("orders_suspect") or [])

    def test_no_markers_clean(self):
        _measure("MEASURE chip: no markers → clean")
        _write_text(
            os.path.join(self.state, "fronts", self.fid, "order.md"),
            "без советников: выбора нет, механическая\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertEqual(ids, [])
        self.assertEqual(chips.get("orders_suspect") or [], [])

    def test_advisor_clears(self):
        _measure("MEASURE chip: advisor journal → cleared")
        _write_text(
            os.path.join(self.state, "fronts", self.fid, "order.md"),
            "без советников: выбора нет\nпохоже на риск\n",
        )
        now = time.time()
        _append_journal(self.state, [{
            "kind": "start",
            "id": "adv-1",
            "ts": now,
            "front": self.fid,
            "role": "meta/opportunity-advisor.md",
            "engine": "local",
        }])
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
        self.assertEqual(ids, [])

    def test_verified_note_clears(self):
        _measure("MEASURE chip: verified note → cleared")
        _write_text(
            os.path.join(self.state, "fronts", self.fid, "order.md"),
            "без советников: выбора нет\nнаверное устарело\n"
            "допущение проверено замером: /tmp/probe-ok.txt\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
        self.assertEqual(ids, [])

    def test_colonel_order_attributes_parent(self):
        _write_text(
            os.path.join(self.state, "fronts", self.fid, "order.md"),
            "подход: fixed (по 2 вариантам советника)\n",
        )
        _write_text(
            os.path.join(
                self.state, "fronts", self.fid, "colonels", "C1", "order.md"),
            "без советников: выбора нет\nскорее всего сломано\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
        self.assertEqual(ids, [self.fid])

    def test_done_status_excluded(self):
        root, state = _mk_poly(fid="F-DONE", status="done")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-DONE", "order.md"),
                "без советников: выбора нет\nвероятно ок\n",
            )
            with _EnvState(state):
                ids = orchlib.orders_suspect(state)
            self.assertEqual(ids, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (г) соседние чипы не сломаны
# ---------------------------------------------------------------------------


class TestNeighborChipsIntact(unittest.TestCase):
    def tearDown(self):
        if getattr(self, "root", None):
            shutil.rmtree(self.root, ignore_errors=True)

    def test_orders_without_basis_still_flags(self):
        _measure("MEASURE regress: orders_without_basis")
        self.root, self.state = _mk_poly(fid="F-OWB")
        _write_text(
            os.path.join(self.state, "fronts", "F-OWB", "order.md"),
            "# приказ без строки подхода и без «без советников»\nцель: x\n",
        )
        with _EnvState(self.state):
            paths = orchlib.orders_without_basis(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(any("F-OWB" in p for p in paths))
        self.assertTrue(any(
            "F-OWB" in p for p in (chips.get("orders_without_basis") or [])))

    def test_orders_without_basis_clean_with_basis(self):
        self.root, self.state = _mk_poly(fid="F-OWB2")
        _write_text(
            os.path.join(self.state, "fronts", "F-OWB2", "order.md"),
            "без советников: выбора нет\n",
        )
        with _EnvState(self.state):
            paths = orchlib.orders_without_basis(self.state)
        self.assertFalse(any("F-OWB2" in p for p in paths))

    def test_advisors_without_scouts_still_flags(self):
        _measure("MEASURE regress: advisors_without_scouts")
        self.root, self.state = _mk_poly(fid="F-AWS")
        now = time.time()
        _append_journal(self.state, [{
            "kind": "start",
            "id": "adv-lonely",
            "ts": now,
            "front": "F-AWS",
            "role": "meta/opportunity-advisor.md",
            "engine": "local",
        }])
        with _EnvState(self.state):
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn(
            "adv-lonely", chips.get("advisors_without_scouts") or [])

    def test_advisors_without_scouts_cleared_by_scout(self):
        self.root, self.state = _mk_poly(fid="F-AWS2")
        now = time.time()
        _append_journal(self.state, [
            {
                "kind": "start",
                "id": "adv-ok",
                "ts": now,
                "front": "F-AWS2",
                "role": "meta/opportunity-advisor.md",
                "engine": "local",
            },
            {
                "kind": "start",
                "id": "scout-ok",
                "ts": now + 1,
                "front": "F-AWS2",
                "role": "meta/web-scout.md",
                "engine": "cloud",
                "parent": "adv-ok",
            },
        ])
        with _EnvState(self.state):
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertNotIn(
            "adv-ok", chips.get("advisors_without_scouts") or [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
