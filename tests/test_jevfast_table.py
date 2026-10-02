#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-JEVFAST C1 этап A: схема routing/jev-table.json (8 новых advisory-точек).

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_jevfast_table.py -q
Без сети.
"""
from __future__ import print_function

import json
import os
import re
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TABLE_PATH = os.path.join(REPO, "routing", "jev-table.json")

NEW_IDS = [
    "scout-need",
    "mechanic-vs-fork",
    "repair-followup-or-fresh",
    "focus-hint-diff",
    "observer-journal-brief",
    "chip-triage",
    "retro-card-hint",
    "run-queue-prio",
]

NEW_EXPECT = {
    "scout-need": {"type": "Noul", "yes_at": 0.6},
    "mechanic-vs-fork": {"type": "Noul", "yes_at": 0.6},
    "repair-followup-or-fresh": {"type": "Noul", "yes_at": 0.6},
    "focus-hint-diff": {
        "type": "Choice",
        "defer_below": 0.35,
        "confirm_below": 0.6,
    },
    "observer-journal-brief": {
        "type": "Choice",
        "defer_below": 0.35,
        "confirm_below": 0.6,
    },
    "chip-triage": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "retro-card-hint": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "run-queue-prio": {
        "type": "Score",
        "defer_below": 0.35,
        "confirm_below": 0.6,
    },
}

LEGACY_EXPECT = {
    "approach-after-advisor": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "role-shortlist": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "best-synthesis-report": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "need-advisor": {"type": "Noul", "yes_at": 0.6},
    "need-split": {"type": "Noul", "yes_at": 0.55},
    "split-quality": {
        "type": "Score",
        "defer_below": 0.35,
        "confirm_below": 0.6,
    },
    "rules-apply": {
        "type": "Choice",
        "defer_below": 0.5,
        "confirm_below": 0.75,
    },
    "tried-before": {"type": "Noul", "yes_at": 0.6},
    "probe-sufficiency": {
        "type": "Score",
        "defer_below": 0.35,
        "confirm_below": 0.6,
        "advisory": True,
    },
}

FORBIDDEN_ID_EXACT = ("critic-prefilter", "kt-prefilter")
FORBIDDEN_ID_SUBSTR = ("prefilter", "auto-approve", "auto_approve", "hitl")
SHRINK_RE = re.compile(
    r"1 критик|один критик|пропустить наблюдателя|skip observer|сократить критик",
    re.IGNORECASE,
)


def _load_table():
    with open(TABLE_PATH, encoding="utf-8") as f:
        return json.load(f)


def _by_id(points):
    out = {}
    for p in points:
        pid = p["id"]
        out.setdefault(pid, []).append(p)
    return out


def _text_fields(point):
    texts = []
    for key in ("id", "instructions", "fallback", "_comment"):
        val = point.get(key)
        if isinstance(val, str):
            texts.append(val)
    crit = point.get("criteria")
    if isinstance(crit, list):
        for item in crit:
            if isinstance(item, str):
                texts.append(item)
    elif isinstance(crit, dict):
        for k, v in crit.items():
            if isinstance(k, str):
                texts.append(k)
            if isinstance(v, str):
                texts.append(v)
    elif isinstance(crit, str):
        texts.append(crit)
    return texts


class TestJevfastTable(unittest.TestCase):
    def setUp(self):
        self.data = _load_table()
        self.points = self.data["points"]
        self.by_id = _by_id(self.points)

    def test_schema_valid(self):
        self.assertEqual(self.data["schemaVersion"], 1)
        self.assertIsInstance(self.points, list)
        self.assertEqual(len(self.points), 18)

    def test_new_points_once_with_thresholds(self):
        for pid in NEW_IDS:
            matches = self.by_id.get(pid, [])
            self.assertEqual(len(matches), 1, "id %s count=%s" % (pid, len(matches)))
            p = matches[0]
            exp = NEW_EXPECT[pid]
            self.assertEqual(p["type"], exp["type"], pid)
            if "yes_at" in exp:
                self.assertEqual(p["yes_at"], exp["yes_at"], pid)
            if "defer_below" in exp:
                self.assertEqual(p["defer_below"], exp["defer_below"], pid)
            if "confirm_below" in exp:
                self.assertEqual(p["confirm_below"], exp["confirm_below"], pid)
            self.assertIs(p.get("advisory"), True, pid)
            self.assertIs(p.get("auto_question"), True, pid)

    def test_forbidden_ids_absent(self):
        for p in self.points:
            pid = p["id"]
            low = pid.lower()
            self.assertNotIn(low, FORBIDDEN_ID_EXACT)
            for frag in FORBIDDEN_ID_SUBSTR:
                self.assertNotIn(frag, low, "id=%s contains %s" % (pid, frag))

    def test_legacy_unchanged(self):
        for pid, exp in LEGACY_EXPECT.items():
            matches = self.by_id.get(pid, [])
            self.assertEqual(len(matches), 1, pid)
            p = matches[0]
            self.assertEqual(p["type"], exp["type"], pid)
            if "yes_at" in exp:
                self.assertEqual(p["yes_at"], exp["yes_at"], pid)
            if "defer_below" in exp:
                self.assertEqual(p["defer_below"], exp["defer_below"], pid)
            if "confirm_below" in exp:
                self.assertEqual(p["confirm_below"], exp["confirm_below"], pid)
            if exp.get("advisory") is True:
                self.assertIs(p.get("advisory"), True, pid)
            self.assertNotIn("auto_question", p, pid)
            self.assertFalse(p.get("auto_question"), pid)

    def test_new_choice_score_have_criteria_all_have_fallback(self):
        for p in self.points:
            self.assertTrue(
                isinstance(p.get("fallback"), str) and p["fallback"].strip(),
                "fallback missing: %s" % p.get("id"),
            )
        choice_ids = (
            "focus-hint-diff",
            "observer-journal-brief",
            "chip-triage",
            "retro-card-hint",
        )
        score_ids = ("run-queue-prio",)
        for pid in choice_ids:
            p = self.by_id[pid][0]
            crit = p.get("criteria")
            self.assertIsInstance(crit, dict, pid)
            self.assertTrue(len(crit) > 0, pid)
            self.assertTrue(
                all(
                    isinstance(k, str) and k.strip()
                    and isinstance(v, str) and v.strip()
                    for k, v in crit.items()
                ),
                pid,
            )
        for pid in score_ids:
            p = self.by_id[pid][0]
            crit = p.get("criteria")
            self.assertIsInstance(crit, list, pid)
            self.assertTrue(len(crit) > 0, pid)
            self.assertTrue(
                all(isinstance(c, str) and c.strip() for c in crit), pid
            )
        # legacy Score points remain list
        for pid in ("split-quality", "probe-sufficiency"):
            p = self.by_id[pid][0]
            crit = p.get("criteria")
            self.assertIsInstance(crit, list, pid)
            self.assertTrue(len(crit) > 0, pid)

    def test_no_shrink_patterns(self):
        for p in self.points:
            for text in _text_fields(p):
                self.assertIsNone(
                    SHRINK_RE.search(text),
                    "shrink pattern in %s: %r" % (p.get("id"), text),
                )


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestJevfastTable)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
