#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-ADVERSARIAL ADV-C1: корпус scenarios.json + A2 order_no_mechanics (V2).

Полигоны: только ORCHESTRATION_DIR=/tmp/adv-…; живой /root/.orchestration
не пишем (live-scan — отдельный файл, только чтение state).
"""
from __future__ import print_function

import json
import os
import re
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
SCENARIOS = os.path.join(REPO, "tests", "adversarial", "scenarios.json")
PANEL = os.path.join(REPO, "panel", "index.html")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


ATTACK_ENUM = frozenset(("A1", "A2", "A3", "A4", "A5", "A7"))
CLASS_ENUM = frozenset(("catch", "allow"))
REQUIRED_TOP = ("version", "scenarios")
REQUIRED_SC = ("id", "attack", "class", "input", "expect")
REQUIRED_EXPECT = ("mechanism", "oracle", "detail")
ANCHORS = orchlib.ORDER_MECHANICS_ANCHORS


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


def _mk_poly(prefix="adv-", fid="F-ADV", status="active"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "adv-test",
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


def _load_scenarios():
    with open(SCENARIOS, "r", encoding="utf-8") as f:
        return json.load(f)


def _input_text(sc):
    inp = sc.get("input") or {}
    if "order_md" in inp and inp["order_md"] is not None:
        return inp["order_md"]
    return inp.get("prompt") or ""


# ---------------------------------------------------------------------------
# (1) валидатор схемы + инвентарь
# ---------------------------------------------------------------------------


class TestScenariosSchema(unittest.TestCase):
    def test_schema_and_inventory(self):
        _measure("VALIDATE scenarios.json schema+inventory")
        data = _load_scenarios()
        for k in REQUIRED_TOP:
            self.assertIn(k, data)
        self.assertIsInstance(data["version"], int)
        scenarios = data["scenarios"]
        self.assertIsInstance(scenarios, list)
        self.assertTrue(scenarios)

        by_attack = {a: 0 for a in ATTACK_ENUM}
        allows = []
        allow1 = allow2a = allow2b = None
        for sc in scenarios:
            self.assertIsInstance(sc, dict)
            for k in REQUIRED_SC:
                self.assertIn(k, sc, "missing %s in %s" % (k, sc.get("id")))
            self.assertIn(sc["attack"], ATTACK_ENUM)
            self.assertIn(sc["class"], CLASS_ENUM)
            self.assertIsInstance(sc["input"], dict)
            inp = sc["input"]
            self.assertTrue(
                "order_md" in inp or "prompt" in inp,
                "input needs order_md|prompt: %s" % sc["id"],
            )
            exp = sc["expect"]
            self.assertIsInstance(exp, dict)
            for k in REQUIRED_EXPECT:
                self.assertIn(k, exp, "expect.%s missing in %s" % (k, sc["id"]))
            oracle = exp["oracle"]
            self.assertTrue(
                oracle == "silence"
                or oracle.startswith("chip:")
                or oracle.startswith("exit:")
                or oracle.startswith("live:"),
                "bad oracle %r in %s" % (oracle, sc["id"]),
            )
            by_attack[sc["attack"]] += 1
            if sc["attack"] in ("A3", "A5"):
                self.assertEqual(
                    oracle, "live:W-S",
                    "A3/A5 must be live:W-S (%s)" % sc["id"],
                )
            if sc["class"] == "allow":
                allows.append(sc)
                text = _input_text(sc)
                self.assertTrue(
                    any(a in text for a in ANCHORS),
                    "allow %s must have ≥1 mechanics anchor" % sc["id"],
                )
                sid = sc["id"]
                if sid.startswith("allow-1") or "allow#1" in sid:
                    allow1 = sc
                if "2a" in sid:
                    allow2a = sc
                if "2b" in sid:
                    allow2b = sc

        for a in ATTACK_ENUM:
            self.assertGreaterEqual(
                by_attack[a], 1, "inventory missing attack %s" % a)

        self.assertGreaterEqual(len(allows), 3, "need ≥3 allow scenarios")

        # allow#1 / #2a / #2b — только по id; нет id → честный fail
        self.assertIsNotNone(allow1, "allow#1 missing")
        self.assertIsNotNone(allow2a, "allow#2a missing")
        self.assertIsNotNone(allow2b, "allow#2b missing")
        self.assertEqual(
            _input_text(allow2a), _input_text(allow2b),
            "allow#2a/#2b dual must share identical input",
        )
        self.assertTrue(orchlib._order_has_basis(_input_text(allow1)))
        self.assertFalse(orchlib._order_has_basis(_input_text(allow2a)))

        # A7 stub: sk- + exactly 40 alnum, not from env
        a7 = next(s for s in scenarios if s["attack"] == "A7")
        a7text = _input_text(a7)
        m = re.search(r"sk-([A-Za-z0-9]+)", a7text)
        self.assertIsNotNone(m, "A7 needs sk- stub")
        self.assertEqual(len(m.group(1)), 40, "A7 stub must be sk-+40 alnum")
        env_val = os.environ.get("OPENAI_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
        if env_val:
            self.assertNotIn(env_val, a7text)
        self.assertIsNotNone(orchlib.scan_secrets(a7text))
        _measure("RESULT schema+inventory OK")


# ---------------------------------------------------------------------------
# (2) A2 catch / allow silence
# ---------------------------------------------------------------------------


class TestA2Detector(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly(prefix="adv-a2-", fid="F-A2")
        self.order = os.path.join(self.state, "fronts", "F-A2", "order.md")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a2_catch_basis_no_mechanics(self):
        _measure("PROBE A2 catch basis=true no anchor")
        body = "# order\nподход: сделать без механики\nцель: X\n"
        _write_text(self.order, body)
        self.assertTrue(orchlib._order_has_basis(body))
        self.assertFalse(orchlib._order_has_mechanics(body))
        with _EnvState(self.state):
            paths = orchlib.orders_without_mechanics(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(any("F-A2" in p for p in paths))
        self.assertTrue(chips.get("order_no_mechanics"))
        self.assertTrue(
            any("F-A2" in p for p in chips["order_no_mechanics"]))
        _measure("RESULT A2 catch LOVIT")

    def test_allow1_full_order_silence(self):
        _measure("PROBE allow#1 full order → silence A2")
        body = (
            "# order\nподход: V2 полный\n"
            "запуск: python3 bin/run-exec.py --front F-A2\n"
        )
        _write_text(self.order, body)
        self.assertTrue(orchlib._order_has_basis(body))
        self.assertTrue(orchlib._order_has_mechanics(body))
        with _EnvState(self.state):
            paths = orchlib.orders_without_mechanics(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertEqual(paths, [])
        self.assertEqual(chips.get("order_no_mechanics"), [])
        _measure("RESULT allow#1 silence OK")

    def test_allow2a_no_basis_with_anchor_silence_a2(self):
        _measure("PROBE allow#2a basis=false+anchor → silence A2")
        body = "# order\nцель: X\nмеханика: python3 bin/run-exec.py --front F-A2\n"
        _write_text(self.order, body)
        self.assertFalse(orchlib._order_has_basis(body))
        self.assertTrue(orchlib._order_has_mechanics(body))
        with _EnvState(self.state):
            paths = orchlib.orders_without_mechanics(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
            owb = orchlib.orders_without_basis(self.state)
        self.assertEqual(paths, [])
        self.assertEqual(chips.get("order_no_mechanics"), [])
        self.assertTrue(any("F-A2" in p for p in owb))
        _measure("RESULT allow#2a A2 silence + OWB catch OK")

    def test_colonel_order_also_scanned(self):
        _measure("PROBE colonel order.md walked")
        col = os.path.join(
            self.state, "fronts", "F-A2", "colonels", "C1", "order.md")
        _write_text(
            self.order,
            "# front\nподход: ok\npython3 bin/run-exec.py --front F-A2\n",
        )
        _write_text(col, "# col\nподход: без якоря\nцель: Y\n")
        with _EnvState(self.state):
            paths = orchlib.orders_without_mechanics(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(any("colonels/C1" in p for p in paths))
        self.assertTrue(any("colonels/C1" in p for p in chips["order_no_mechanics"]))


# ---------------------------------------------------------------------------
# (3) wiring «немой чип»
# ---------------------------------------------------------------------------


class TestWiringMuteChip(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly(prefix="adv-wire-", fid="F-W")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_order_no_mechanics_in_empty_and_both_returns(self):
        _measure("WIRING mute-chip: empty + both health returns + panel")
        # empty-dict path: force exception early via broken state? Better:
        # call with valid state and inspect keys; also read source empty literal.
        src_path = os.path.join(BIN, "orchlib.py")
        with open(src_path, "r", encoding="utf-8") as f:
            src = f.read()
        # empty dict must register the key
        self.assertIn('"order_no_mechanics": []', src)
        # panel label
        with open(PANEL, "r", encoding="utf-8") as f:
            panel = f.read()
        self.assertIn("order_no_mechanics:", panel)

        _write_text(
            os.path.join(self.state, "fronts", "F-W", "order.md"),
            "# o\nподход: clean\npython3 bin/run-exec.py --front F-W\n",
        )
        with _EnvState(self.state):
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn(
            "order_no_mechanics", chips,
            "немой чип: ключ в empty, но отсутствует в выходе health",
        )
        self.assertIsInstance(chips["order_no_mechanics"], list)
        # other_chips path also contains the key (same return dict)
        self.assertIn("order_no_mechanics", chips)
        _measure("RESULT wiring OK")


# ---------------------------------------------------------------------------
# (4) регресс соседних чипов
# ---------------------------------------------------------------------------


class TestNeighborRegress(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly(prefix="adv-reg-", fid="F-REG")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_orders_without_basis_regress(self):
        _measure("REGRESS orders_without_basis")
        _write_text(
            os.path.join(self.state, "fronts", "F-REG", "order.md"),
            "# order\nцель: без основы\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_without_basis(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(any("order.md" in x for x in ids))
        self.assertTrue(chips.get("orders_without_basis"))
        # A2 must stay silent (basis=false)
        self.assertEqual(chips.get("order_no_mechanics"), [])

    def test_orders_suspect_regress(self):
        _measure("REGRESS orders_suspect")
        body = (
            "# order\nбез советников: выбора нет\n"
            "вероятно лучше так\n"
            "python3 bin/run-exec.py --front F-REG\n"
        )
        _write_text(
            os.path.join(self.state, "fronts", "F-REG", "order.md"), body)
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn("F-REG", ids)
        self.assertIn("F-REG", chips.get("orders_suspect") or [])
        # basis+anchor → A2 silence
        self.assertEqual(chips.get("order_no_mechanics"), [])

    def test_mustmap_stale_regress(self):
        _measure("REGRESS mustmap_stale")
        kit = tempfile.mkdtemp(prefix="adv-mm-kit-", dir="/tmp")
        try:
            doctrine_rel = "skills/orchestration/SKILL.md"
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
            now = time.time()
            os.utime(mm_path, (now - 100, now - 100))
            os.utime(doctrine, (now - 10, now - 10))  # doctrine newer → stale
            _write_text(
                os.path.join(self.state, "fronts", "F-REG", "order.md"),
                "# o\nподход: x\npython3 bin/run-exec.py --front F-REG\n",
            )
            with _EnvState(self.state):
                stale = orchlib.mustmap_stale(kit_dir=kit)
                chips = orchlib.health_red_chips(
                    state=self.state, kit_dir=kit)
            self.assertTrue(stale)
            self.assertTrue(chips.get("mustmap_stale"))
            self.assertEqual(chips.get("order_no_mechanics"), [])
        finally:
            shutil.rmtree(kit, ignore_errors=True)


# ---------------------------------------------------------------------------
# (5) scenario-driven A2/allow from corpus (local runnable)
# ---------------------------------------------------------------------------


class TestCorpusLocalOracles(unittest.TestCase):
    def test_corpus_a2_and_allows(self):
        _measure("CORPUS local oracles A2/allow/A1 dual")
        data = _load_scenarios()
        for sc in data["scenarios"]:
            oracle = sc["expect"]["oracle"]
            if oracle.startswith("live:") or oracle.startswith("exit:"):
                continue
            text = _input_text(sc)
            if "order_md" not in (sc.get("input") or {}):
                continue
            root, state = _mk_poly(
                prefix="adv-sc-", fid="F-SC")
            try:
                _write_text(
                    os.path.join(state, "fronts", "F-SC", "order.md"), text)
                with _EnvState(state):
                    chips = orchlib.health_red_chips(
                        state=state, kit_dir=REPO)
                if oracle == "silence":
                    self.assertEqual(
                        chips.get("order_no_mechanics"), [],
                        "silence fail: %s" % sc["id"],
                    )
                elif oracle == "chip:order_no_mechanics":
                    self.assertTrue(
                        chips.get("order_no_mechanics"),
                        "catch fail: %s" % sc["id"],
                    )
                elif oracle == "chip:orders_without_basis":
                    self.assertTrue(
                        chips.get("orders_without_basis"),
                        "OWB fail: %s" % sc["id"],
                    )
            finally:
                shutil.rmtree(root, ignore_errors=True)
        _measure("RESULT corpus local oracles OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
