#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-JEVFAST C2: CLI-обёртки 8 advisory-точек (офлайн-шов, без сети).

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_jevfast_cli.py -q
"""
from __future__ import print_function

import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADVISE_PATH = os.path.join(REPO, "bin", "jev-advise.py")
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

SHRINK_RE = re.compile(
    r"1 критик|один критик|пропустить наблюдателя|skip observer|"
    r"сократить критик|prefilter|auto-approve",
    re.IGNORECASE,
)
PATH_OR_SEARCH_RE = re.compile(
    r"(?:\./)|(?:^/)|(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)|"
    r"(?:\.(?:py|json|md|html|sh|ts)\b)|"
    r"смотри в|ищи в|проверь файл|где искать|look in|see file",
    re.IGNORECASE,
)


def _load_advise():
    spec = importlib.util.spec_from_file_location("jev_advise_c2", ADVISE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _run_main(mod, argv, decisions_fixture, state_dir=None):
    """Вызвать main с подменой jev_decisions; вернуть (exit, parsed JSON)."""
    if state_dir is None:
        state_dir = tempfile.mkdtemp(prefix="jevfast-cli-")

    def _fake_decisions(state, questions, **kwargs):
        if callable(decisions_fixture):
            return decisions_fixture(state, questions, **kwargs)
        return decisions_fixture

    buf = io.StringIO()
    with mock.patch.object(mod, "jev_decisions", side_effect=_fake_decisions):
        with mock.patch.object(mod.orchlib, "find_state_dir",
                               return_value=state_dir):
            with mock.patch.object(mod, "load_openrouter_key",
                                   return_value="test-key-not-real"):
                with redirect_stdout(buf):
                    code = mod.main(argv)
    out = buf.getvalue().strip()
    data = json.loads(out) if out else {}
    return code, data


def _fixture_noul(noul, qid):
    return {
        "model": "typesafe/jev-1.13",
        "answers": {qid: {"type": "noul", "noul": noul}},
        "usage": {"cost": 0},
    }


def _fixture_choice(choice, confidence, qid):
    ans = {"type": "choice", "choice": choice}
    if confidence is not None:
        ans["confidence"] = confidence
    return {
        "model": "typesafe/jev-1.13",
        "answers": {qid: ans},
        "usage": {"cost": 0},
    }


def _fixture_scores(score_map, conf_map=None):
    """score_map: id → score; conf_map: id → confidence (default 0.8)."""
    answers = {}
    for rid, sc in score_map.items():
        conf = 0.8 if conf_map is None else conf_map.get(rid, 0.8)
        ans = {"type": "score", "score": sc}
        if conf is not None:
            ans["confidence"] = conf
        answers[rid] = ans
    return {
        "model": "typesafe/jev-1.13",
        "answers": answers,
        "usage": {"cost": 0},
    }


class TestNormalizeAndBadge(unittest.TestCase):
    """Чистые функции для C3 reuse."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_normalize_chip_list_objects(self):
        ids = self.mod.normalize_chip_input(
            [{"id": "a"}, {"id": "b"}, {"id": "c"}])
        self.assertEqual(ids, ["a", "b", "c"])

    def test_normalize_chip_dict(self):
        ids = self.mod.normalize_chip_input(
            {"red": ["x", "y"], "stale": ["z"]})
        self.assertEqual(ids, ["red#0", "red#1", "stale#0"])

    def test_normalize_chip_health_envelope(self):
        ids = self.mod.normalize_chip_input({
            "counts": {"red": 2},
            "ids": {"red": ["r1", "r2"], "fail": ["f1"]},
            "ts": 1,
        })
        self.assertEqual(ids, ["red#0", "red#1", "fail#0"])

    def test_badge_mapping(self):
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "error", "band": {}}),
            "fail")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "mid"}}),
            "advisory")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "yes"}}),
            "advisory")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"q": "high"}}),
            "advisory")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"a": "low", "b": "absent"}}),
            "defer")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {}}),
            "defer")
        self.assertEqual(
            self.mod.badge_from_journal_record(
                {"status": "ok", "band": {"a": "below"}}),
            "defer")


class TestBandsEightPoints(unittest.TestCase):
    """(а) bands для каждой из 8 точек."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()
        with open(TABLE_PATH, "r", encoding="utf-8") as f:
            cls.table = json.load(f)
        cls.points = {p["id"]: p for p in cls.table["points"]}

    def _base_argv(self, point_id, state_text="state"):
        return [
            "--point", point_id,
            "--caller", "F-JEVFAST-C2/test",
            "--state-text", state_text,
            "--table-path", TABLE_PATH,
        ]

    def _assert_advisory(self, data, point_id):
        self.assertIn("advisory_text", data, point_id)
        at = data["advisory_text"]
        self.assertIn("advisory", at, point_id)
        self.assertIn("решение за командиром", at, point_id)
        for qid, adv in (data.get("advice") or {}).items():
            if isinstance(adv, dict):
                self.assertEqual(adv.get("action"), "advisory",
                                 "%s/%s" % (point_id, qid))
                self.assertNotEqual(adv.get("action"), "auto")

    def test_scout_need_bands(self):
        pid = "scout-need"
        # yes (≥0.6)
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            _fixture_noul(0.9, pid))
        self.assertEqual(code, 0)
        self.assertTrue(data["ok"])
        self.assertEqual(data["advice"][pid]["band"], "yes")
        self._assert_advisory(data, pid)
        self.assertIn("no-scout", data["advisory_text"])
        # below
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            _fixture_noul(0.1, pid))
        self.assertEqual(code, 0)
        self.assertEqual(data["advice"][pid]["band"], "below")
        self.assertIn("scout", data["advisory_text"].lower())
        self._assert_advisory(data, pid)
        # absent
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            {"answers": {pid: {"type": "noul"}}, "usage": {}})
        self.assertEqual(code, 0)
        self.assertEqual(data["advice"][pid]["band"], "absent")
        self._assert_advisory(data, pid)

    def test_mechanic_vs_fork_bands(self):
        pid = "mechanic-vs-fork"
        code, data = _run_main(
            self.mod, self._base_argv(pid), _fixture_noul(0.8, pid))
        self.assertEqual(data["advice"][pid]["band"], "yes")
        self.assertIn("механик", data["advisory_text"])
        self._assert_advisory(data, pid)
        code, data = _run_main(
            self.mod, self._base_argv(pid), _fixture_noul(0.2, pid))
        self.assertEqual(data["advice"][pid]["band"], "below")
        self.assertIn("развилк", data["advisory_text"])
        self._assert_advisory(data, pid)

    def test_repair_followup_bands(self):
        pid = "repair-followup-or-fresh"
        code, data = _run_main(
            self.mod, self._base_argv(pid), _fixture_noul(0.9, pid))
        self.assertIn("follow-up", data["advisory_text"])
        self._assert_advisory(data, pid)
        code, data = _run_main(
            self.mod, self._base_argv(pid), _fixture_noul(0.1, pid))
        self.assertIn("follow-up", data["advisory_text"])
        self.assertIn("fresh", data["advisory_text"])
        self._assert_advisory(data, pid)

    def test_focus_hint_bands(self):
        pid = "focus-hint-diff"
        crit = self.points[pid]["criteria"]
        self.assertIsInstance(crit, dict)
        keys = list(crit.keys())
        vals = list(crit.values())
        # high + valid choice as list of keys
        code, data = _run_main(
            self.mod, self._base_argv(pid, "diff text"),
            _fixture_choice([keys[0], keys[1]], 0.9, pid))
        self.assertEqual(code, 0)
        self.assertNotEqual(data.get("hint_block"), "без подсветки")
        self.assertIn("N критиков и круги НЕ меняются", data["hint_block"])
        self.assertIn(vals[0], data["hint_block"])
        self.assertIn(vals[1], data["hint_block"])
        self._assert_advisory(data, pid)
        # high + choice as single key-string
        code, data = _run_main(
            self.mod, self._base_argv(pid, "diff text"),
            _fixture_choice(keys[0], 0.9, pid))
        self.assertEqual(code, 0)
        self.assertIn(vals[0], data["hint_block"])
        self._assert_advisory(data, pid)
        # low → без подсветки
        code, data = _run_main(
            self.mod, self._base_argv(pid, "diff"),
            _fixture_choice([keys[0]], 0.1, pid))
        self.assertEqual(data["hint_block"], "без подсветки")
        self._assert_advisory(data, pid)
        # invalid choice → без подсветки
        code, data = _run_main(
            self.mod, self._base_argv(pid, "diff"),
            _fixture_choice(["не-из-списка"], 0.9, pid))
        self.assertEqual(data["hint_block"], "без подсветки")
        # absent confidence
        code, data = _run_main(
            self.mod, self._base_argv(pid, "diff"),
            _fixture_choice([keys[0]], None, pid))
        self.assertEqual(data["hint_block"], "без подсветки")

    def test_observer_bands(self):
        pid = "observer-journal-brief"
        crit = self.points[pid]["criteria"]
        self.assertIsInstance(crit, dict)
        keys = list(crit.keys())
        tail = "journal-line-1\njournal-line-2"
        # mid: conf between 0.35 and 0.6
        code, data = _run_main(
            self.mod, self._base_argv(pid, tail),
            _fixture_choice(keys[:4], 0.5, pid))
        self.assertEqual(code, 0)
        self.assertEqual(data["raw_tail"], tail)
        self.assertIn("brief_theses", data)
        self.assertGreaterEqual(len(data["brief_theses"]), 3)
        self.assertEqual(data["brief_theses"], keys[:4])
        self._assert_advisory(data, pid)
        # low → no brief
        code, data = _run_main(
            self.mod, self._base_argv(pid, tail),
            _fixture_choice(keys[:4], 0.1, pid))
        self.assertEqual(data["raw_tail"], tail)
        self.assertNotIn("brief_theses", data)
        self._assert_advisory(data, pid)

    def test_chip_triage_bands(self):
        pid = "chip-triage"
        state = json.dumps([{"id": "c1"}, {"id": "c2"}, {"id": "c3"}])
        # valid mid
        code, data = _run_main(
            self.mod, self._base_argv(pid, state),
            _fixture_choice("c2", 0.8, pid))
        self.assertEqual(data["recommended_order"][0], "c2")
        self.assertEqual(
            sorted(data["recommended_order"]),
            sorted(data["original_order"]))
        self._assert_advisory(data, pid)
        # invalid choice → FIFO
        code, data = _run_main(
            self.mod, self._base_argv(pid, state),
            _fixture_choice("nope", 0.9, pid))
        self.assertEqual(data["recommended_order"], data["original_order"])
        # low → FIFO
        code, data = _run_main(
            self.mod, self._base_argv(pid, state),
            _fixture_choice("c2", 0.1, pid))
        self.assertEqual(data["recommended_order"], data["original_order"])

    def test_retro_bands(self):
        pid = "retro-card-hint"
        crit = self.points[pid]["criteria"]
        self.assertIsInstance(crit, dict)
        keys = list(crit.keys())
        # choice as key-string
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            _fixture_choice(keys[0], 0.9, pid))
        self.assertEqual(data.get("card_category"), keys[0])
        self._assert_advisory(data, pid)
        # choice as list of keys
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            _fixture_choice([keys[1]], 0.9, pid))
        self.assertEqual(data.get("card_category"), keys[1])
        self._assert_advisory(data, pid)
        code, data = _run_main(
            self.mod, self._base_argv(pid),
            _fixture_choice(keys[0], 0.1, pid))
        self.assertNotIn("card_category", data)
        self._assert_advisory(data, pid)

    def test_run_queue_bands(self):
        pid = "run-queue-prio"
        runs = [{"id": "r1"}, {"id": "r2"}, {"id": "r3"}]
        state = json.dumps(runs)
        # high scores reorder
        code, data = _run_main(
            self.mod, self._base_argv(pid, state),
            _fixture_scores({"r1": 1, "r2": 3, "r3": 2}))
        self.assertEqual(data["recommended_order"], ["r2", "r3", "r1"])
        self.assertEqual(
            sorted(data["recommended_order"]),
            sorted(data["original_order"]))
        self._assert_advisory(data, pid)
        # all-low → original
        code, data = _run_main(
            self.mod, self._base_argv(pid, state),
            _fixture_scores(
                {"r1": 3, "r2": 2, "r3": 1},
                {"r1": 0.1, "r2": 0.1, "r3": 0.1}))
        self.assertEqual(data["recommended_order"], data["original_order"])
        self._assert_advisory(data, pid)


class TestGoldenFocusHint(unittest.TestCase):
    """(б) golden спорный дифф; (в) слепота criteria."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()
        with open(TABLE_PATH, "r", encoding="utf-8") as f:
            cls.table = json.load(f)
        cls.points = {p["id"]: p for p in cls.table["points"]}

    def test_golden_hint_block(self):
        pid = "focus-hint-diff"
        crit = self.points[pid]["criteria"]
        self.assertIsInstance(crit, dict)
        keys = list(crit.keys())
        vals = list(crit.values())
        diff = (
            "--- a/x\n+++ b/x\n@@\n-old\n+new contested threshold\n"
            "fail-open path changed\n"
        )
        code, data = _run_main(
            self.mod,
            ["--point", pid, "--caller", "C2/golden",
             "--state-text", diff, "--table-path", TABLE_PATH],
            _fixture_choice(
                [keys[0], keys[2], keys[3]], 0.85, pid))
        self.assertEqual(code, 0)
        hb = data.get("hint_block") or ""
        self.assertTrue(hb and hb != "без подсветки")
        self.assertIn("N критиков и круги НЕ меняются", hb)
        # hint_block содержит ОПИСАНИЯ (values)
        self.assertIn(vals[0], hb)
        self.assertIn(vals[2], hb)
        self.assertIn(vals[3], hb)
        blob = "\n".join([
            hb,
            self.points[pid].get("instructions") or "",
            "\n".join(keys),
            "\n".join(vals),
        ])
        self.assertIsNone(SHRINK_RE.search(blob), blob)

    def test_blindness_criteria_no_paths(self):
        for pid in ("focus-hint-diff", "observer-journal-brief",
                    "retro-card-hint"):
            crit = self.points[pid].get("criteria") or {}
            self.assertIsInstance(crit, dict, pid)
            for k, v in crit.items():
                self.assertIsNone(
                    PATH_OR_SEARCH_RE.search(k),
                    "%s criteria key path/search: %r" % (pid, k))
                self.assertIsNone(
                    PATH_OR_SEARCH_RE.search(v),
                    "%s criteria value path/search: %r" % (pid, v))
            instr = self.points[pid].get("instructions") or ""
            self.assertIsNone(
                PATH_OR_SEARCH_RE.search(instr),
                "%s instructions: %r" % (pid, instr))


class TestChipThreeFormsAndQueue(unittest.TestCase):
    """(д) chip 3 формы + run-queue permutation."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_chip_three_forms(self):
        pid = "chip-triage"
        forms = [
            [{"id": "a"}, {"id": "b"}, {"id": "c"}],
            {"red": ["x", "y"], "stale": ["z"]},
            {"counts": {"red": 2}, "ids": {"red": ["1", "2"], "fail": ["3"]}},
        ]
        expected_first_choice = [
            "b",       # from list
            "red#1",   # second in normalized
            "fail#0",  # pick fail#0
        ]
        for form, want_first in zip(forms, expected_first_choice):
            state = json.dumps(form)
            original = self.mod.normalize_chip_input(
                json.loads(state) if isinstance(form, (dict, list)) else form)
            # ensure want_first in original
            if want_first not in original:
                want_first = original[0]
            code, data = _run_main(
                self.mod,
                ["--point", pid, "--caller", "C2/chip",
                 "--state-text", state, "--table-path", TABLE_PATH],
                _fixture_choice(want_first, 0.9, pid))
            self.assertEqual(code, 0)
            self.assertEqual(data["recommended_order"][0], want_first)
            self.assertEqual(
                sorted(data["recommended_order"]),
                sorted(data["original_order"]))
            # invalid → FIFO
            code, data = _run_main(
                self.mod,
                ["--point", pid, "--caller", "C2/chip",
                 "--state-text", state, "--table-path", TABLE_PATH],
                _fixture_choice("___invalid___", 0.9, pid))
            self.assertEqual(
                data["recommended_order"], data["original_order"])

    def test_run_queue_permutation_and_missing_score(self):
        pid = "run-queue-prio"
        runs = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        state = json.dumps(runs)
        # b highest, c missing score → lowest but stable
        fix = {
            "answers": {
                "a": {"type": "score", "score": 1, "confidence": 0.9},
                "b": {"type": "score", "score": 3, "confidence": 0.9},
                "c": {"type": "score", "confidence": 0.9},  # no score
            },
            "usage": {},
        }
        code, data = _run_main(
            self.mod,
            ["--point", pid, "--caller", "C2/q",
             "--state-text", state, "--table-path", TABLE_PATH],
            fix)
        self.assertEqual(data["recommended_order"][0], "b")
        self.assertEqual(data["recommended_order"][-1], "c")
        self.assertEqual(
            sorted(data["recommended_order"]),
            sorted(["a", "b", "c"]))


class TestLegacyOverrideObserverFailopen(unittest.TestCase):
    """(е) legacy; (ж) observer raw_tail; (з) override; (г) subprocess fail-open."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_legacy_need_advisor_empty_questions(self):
        buf = io.StringIO()
        state_dir = tempfile.mkdtemp(prefix="jev-legacy-")
        with mock.patch.object(self.mod.orchlib, "find_state_dir",
                               return_value=state_dir):
            with redirect_stdout(buf):
                code = self.mod.main([
                    "--point", "need-advisor",
                    "--caller", "C2/legacy",
                    "--state-text", "x",
                    "--table-path", TABLE_PATH,
                ])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertIn("пустой questions", data.get("error") or "")

    def test_override_question_wins(self):
        pid = "scout-need"
        seen = {}

        def capture(state, questions, **kwargs):
            seen["questions"] = questions
            return _fixture_noul(0.9, "custom-qid")

        code, data = _run_main(
            self.mod,
            [
                "--point", pid,
                "--caller", "C2/override",
                "--state-text", "s",
                "--table-path", TABLE_PATH,
                "--question",
                'custom-qid:noul:explicit override instructions',
            ],
            capture)
        self.assertEqual(code, 0)
        self.assertIn("custom-qid", seen["questions"])
        self.assertEqual(
            seen["questions"]["custom-qid"]["instructions"],
            "explicit override instructions")
        # wrapper still forces advisory action
        for adv in (data.get("advice") or {}).values():
            if isinstance(adv, dict):
                self.assertEqual(adv.get("action"), "advisory")

    def test_empty_questions_file_blocks_auto(self):
        """Явный --questions-file={} побеждает автосборку → пустой questions."""
        pid = "scout-need"
        with tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False, encoding="utf-8") as f:
            f.write("{}")
            qpath = f.name
        buf = io.StringIO()
        state_dir = tempfile.mkdtemp(prefix="jev-empty-qf-")
        called = []

        def boom(*a, **k):
            called.append(True)
            raise AssertionError("jev_decisions must not be called")

        with mock.patch.object(self.mod, "jev_decisions", side_effect=boom):
            with mock.patch.object(self.mod.orchlib, "find_state_dir",
                                   return_value=state_dir):
                with mock.patch.object(self.mod, "load_openrouter_key",
                                       return_value="k"):
                    with redirect_stdout(buf):
                        code = self.mod.main([
                            "--point", pid,
                            "--caller", "C2/empty-qf",
                            "--state-text", "s",
                            "--table-path", TABLE_PATH,
                            "--questions-file", qpath,
                        ])
        self.assertEqual(code, 0)
        self.assertFalse(called)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertIn("пустой questions", data.get("error") or "")

    def test_observer_failopen_keeps_raw_tail(self):
        pid = "observer-journal-brief"
        tail = "RAW_TAIL_KEEP_ME"
        state_dir = tempfile.mkdtemp(prefix="jev-obs-")
        buf = io.StringIO()

        def boom(*a, **k):
            raise RuntimeError("simulated API down")

        with mock.patch.object(self.mod, "jev_decisions", side_effect=boom):
            with mock.patch.object(self.mod.orchlib, "find_state_dir",
                                   return_value=state_dir):
                with mock.patch.object(self.mod, "load_openrouter_key",
                                       return_value="k"):
                    with redirect_stdout(buf):
                        code = self.mod.main([
                            "--point", pid,
                            "--caller", "C2/obs-fo",
                            "--state-text", tail,
                            "--table-path", TABLE_PATH,
                        ])
        self.assertEqual(code, 0)
        data = json.loads(buf.getvalue())
        self.assertFalse(data["ok"])
        self.assertEqual(data.get("raw_tail"), tail)
        self.assertEqual(data.get("advice"), {})
        self.assertTrue(data.get("fallback"))

    def test_subprocess_failopen_api_down(self):
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "openrouter.key")
            with open(key_path, "w", encoding="utf-8") as f:
                f.write("fixture-key-not-used-on-network\n")
            # isolated state so journal doesn't pollute
            env = dict(os.environ)
            env["ORCHESTRATION_DIR"] = td
            proc = subprocess.run(
                [
                    sys.executable, ADVISE_PATH,
                    "--point", "scout-need",
                    "--caller", "C2/subprocess-fo",
                    "--state-text", "hello",
                    "--table-path", TABLE_PATH,
                    "--api-url", "http://127.0.0.1:1/",
                    "--key-path", key_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=60,
                universal_newlines=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip())
        self.assertFalse(data["ok"])
        self.assertEqual(data.get("advice"), {})
        self.assertTrue(data.get("fallback"))
        self.assertTrue(data.get("error"))

    def test_legacy_failopen_api_down_no_fallback_field(self):
        """(а) legacy need-advisor + --question + API-down → ok:false, без fallback."""
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "openrouter.key")
            with open(key_path, "w", encoding="utf-8") as f:
                f.write("fixture-key-not-used-on-network\n")
            env = dict(os.environ)
            env["ORCHESTRATION_DIR"] = td
            proc = subprocess.run(
                [
                    sys.executable, ADVISE_PATH,
                    "--point", "need-advisor",
                    "--caller", "C-FIX2/legacy-fo",
                    "--state-text", "есть ли выбор подхода",
                    "--table-path", TABLE_PATH,
                    "--question",
                    "need-advisor:noul:нужен ли советник перед выдачей",
                    "--api-url", "http://127.0.0.1:1/",
                    "--key-path", key_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=60,
                universal_newlines=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip())
        self.assertFalse(data["ok"])
        self.assertEqual(data.get("advice"), {})
        self.assertTrue(data.get("error"))
        self.assertNotIn("fallback", data)

    def test_focus_hint_failopen_api_down_has_fallback(self):
        """(б) focus-hint-diff + API-down → ok:false и поле fallback есть."""
        with tempfile.TemporaryDirectory() as td:
            key_path = os.path.join(td, "openrouter.key")
            with open(key_path, "w", encoding="utf-8") as f:
                f.write("fixture-key-not-used-on-network\n")
            env = dict(os.environ)
            env["ORCHESTRATION_DIR"] = td
            crit = json.dumps({
                "thresholds": "границы/пороги",
                "format": "формат данных и швы",
            }, ensure_ascii=False)
            proc = subprocess.run(
                [
                    sys.executable, ADVISE_PATH,
                    "--point", "focus-hint-diff",
                    "--caller", "C-FIX2/focus-fo",
                    "--state-text", "diff summary",
                    "--table-path", TABLE_PATH,
                    "--question",
                    "focus-hint-diff:choice:выбрать темы::%s" % crit,
                    "--api-url", "http://127.0.0.1:1/",
                    "--key-path", key_path,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                timeout=60,
                universal_newlines=True,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout.strip())
        self.assertFalse(data["ok"])
        self.assertEqual(data.get("advice"), {})
        self.assertTrue(data.get("error"))
        self.assertIn("fallback", data)
        self.assertTrue(data.get("fallback"))


class TestNoAutoInActions(unittest.TestCase):
    """(и) advice.*.action новых точек ≠ auto."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_high_band_action_is_advisory_not_auto(self):
        pid = "focus-hint-diff"
        with open(TABLE_PATH, "r", encoding="utf-8") as f:
            points = {p["id"]: p for p in json.load(f)["points"]}
        crit = points[pid]["criteria"]
        self.assertIsInstance(crit, dict)
        keys = list(crit.keys())
        code, data = _run_main(
            self.mod,
            ["--point", pid, "--caller", "C2/auto",
             "--state-text", "d", "--table-path", TABLE_PATH],
            _fixture_choice(keys[:2], 0.99, pid))
        self.assertEqual(code, 0)
        for adv in data["advice"].values():
            self.assertEqual(adv["action"], "advisory")
            self.assertNotIn(adv["action"], ("auto",))
        self.assertIn("advisory", data["advisory_text"])
        self.assertIn("решение за командиром", data["advisory_text"])


class TestChoiceCriteriaRecordSeam(unittest.TestCase):
    """Регрессия шва: Choice auto_question criteria — dict; Score — list."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()
        with open(TABLE_PATH, "r", encoding="utf-8") as f:
            cls.table = json.load(f)
        cls.points = {p["id"]: p for p in cls.table["points"]}

    def test_auto_questions_choice_criteria_is_dict_score_is_list(self):
        choice_ids = (
            "focus-hint-diff",
            "observer-journal-brief",
            "retro-card-hint",
            "chip-triage",
        )
        for pid in choice_ids:
            point = self.points[pid]
            if pid == "chip-triage":
                state = [{"id": "c1"}, {"id": "c2"}, {"id": "c3"}]
            else:
                state = "state"
            payload = self.mod.build_auto_questions(point, state)
            self.assertIn(pid, payload, pid)
            crit = payload[pid].get("criteria")
            self.assertIsInstance(crit, dict, pid)
            self.assertTrue(len(crit) > 0, pid)
            if pid == "chip-triage":
                self.assertEqual(set(crit.keys()), {"c1", "c2", "c3"})

        # Score: run-queue-prio — list
        point = self.points["run-queue-prio"]
        payload = self.mod.build_auto_questions(point, [])
        self.assertIn("run-queue-prio", payload)
        crit = payload["run-queue-prio"].get("criteria")
        self.assertIsInstance(crit, list)
        self.assertTrue(len(crit) > 0)


if __name__ == "__main__":
    unittest.main()
