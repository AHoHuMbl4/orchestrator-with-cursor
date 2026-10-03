#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-ADVERSARIAL ADV-C2: fuzz-фикстуры + оракулы ожидание×факт.

Полигоны: только ORCHESTRATION_DIR=/tmp/adv-fuzz-…; живой
/root/.orchestration не пишем. Корпус — read-only scenarios.json.
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
SCENARIOS = os.path.join(REPO, "tests", "adversarial", "scenarios.json")
LIVE_SCAN = os.path.join(REPO, "tests", "adversarial", "live-scan.md")
RUN_EXEC = os.path.join(BIN, "run-exec.py")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402

# Secret stub ONLY in fixture (sk- + 40 alnum); never from env.
_A7_STUB = "sk-Aa0Bb1Cc2Dd3Ee4Ff5Gg6Hh7Ii8Jj9Kk0Ll1Mm2N"
_BASIS_LINE_RE = re.compile(
    r"^\s*(подход\s*:|подход\s*\(|без советников)", re.IGNORECASE | re.UNICODE)
_MECH_LINE_RE = re.compile(
    r"(run-exec|run-cloud|ORCHESTRATION_DIR|--session|--front|"
    r"--prompt-file|--probe|bin/|python3|cursor-agent|PATH=)"
)


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


def _mk_poly(prefix="adv-fuzz-", fid="F-FUZZ", status="active"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"hierarchy": "off", "enabled": True},
    })
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "adv-fuzz",
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


def _chip_name(oracle):
    if oracle.startswith("chip:"):
        return oracle.split(":", 1)[1]
    return None


def _eval_chip_oracle(state, oracle, sc_id):
    """Return (ok, fact_str). oracle: chip:* | silence."""
    with _EnvState(state):
        chips = orchlib.health_red_chips(state=state, kit_dir=REPO)
    if oracle == "silence":
        empty = chips.get("order_no_mechanics") or []
        ok = len(empty) == 0
        return ok, "order_no_mechanics=%r" % (empty,)
    name = _chip_name(oracle)
    if not name:
        return False, "unknown oracle %r" % oracle
    vals = chips.get(name) or []
    ok = bool(vals)
    return ok, "%s=%r" % (name, vals)


def _run_a7_secret_gate(prompt_text):
    """Real bin/run-exec.py on /tmp poly; expect exit 5 / SECRETS_IN_PROMPT."""
    root, state = _mk_poly(prefix="adv-fuzz-a7-", fid="F-ADV-A7")
    try:
        pf = os.path.join(root, "prompt-a7.md")
        _write_text(pf, prompt_text)
        env = dict(os.environ)
        env["ORCHESTRATION_DIR"] = state
        # Ensure secret is not injected from env into prompt.
        for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "CURSOR_API_KEY"):
            env.pop(k, None)
        proc = subprocess.run(
            [
                sys.executable, RUN_EXEC,
                "--id", "A7-FUZZ",
                "--session", "test-a7",
                "--no-front", "SMOKE-A7",
                "--prompt-file", pf,
                "--allow-unknown-role",
            ],
            env=env, cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=60,
        )
        log_path = os.path.join(
            state, "sessions", "test-a7", "runs", "A7-FUZZ", "run.log")
        log_body = ""
        if os.path.isfile(log_path):
            with open(log_path, "r", encoding="utf-8") as f:
                log_body = f.read()
        # Persist proof outside poly before cleanup (for artifact path).
        proof = os.path.join(
            tempfile.gettempdir(), "adv-c2-a7-SECRETS_IN_PROMPT.log")
        with open(proof, "w", encoding="utf-8") as f:
            f.write("exit=%s\n" % proc.returncode)
            f.write("stderr=%s\n" % (proc.stderr or "")[:800])
            f.write("log_path=%s\n" % log_path)
            f.write("--- run.log ---\n")
            f.write(log_body)
        hit = (
            proc.returncode == 5
            or "SECRETS_IN_PROMPT" in log_body
            or "SECRETS_IN_PROMPT" in (proc.stderr or "")
        )
        return hit, proc.returncode, log_body, proof
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Corpus-driven oracles (local); live:* → skip
# ---------------------------------------------------------------------------


class TestCorpusFuzzOracles(unittest.TestCase):
    def test_corpus_scenarios_oracle_matrix(self):
        _measure("FUZZ corpus scenarios → oracles")
        data = _load_scenarios()
        self.assertEqual(len(data["scenarios"]), 9)
        skipped_live = []
        for sc in data["scenarios"]:
            oracle = sc["expect"]["oracle"]
            sid = sc["id"]
            # SkipTest внутри subTest схлопывает весь метод в pytest —
            # live:* помечаем явно и продолжаем (не xfail catch-локальных).
            if oracle.startswith("live:"):
                skipped_live.append(sid)
                _measure("SKIP live:W-S id=%s oracle=%s" % (sid, oracle))
                continue
            with self.subTest(id=sid):
                self._run_one_local(sc)
        self.assertEqual(
            sorted(skipped_live),
            ["A3-live-temptation-docs", "A5-live-commander-hands"],
            "A3/A5 must be the only live:* skips",
        )
        _measure("RESULT corpus local oracles done; live_skipped=%s" % skipped_live)

    def _run_one_local(self, sc):
        oracle = sc["expect"]["oracle"]
        sid = sc["id"]

        if oracle.startswith("exit:"):
            self.assertEqual(sc["attack"], "A7")
            text = _input_text(sc)
            self.assertIn("sk-", text)
            self.assertIsNotNone(re.search(r"sk-[A-Za-z0-9]{40}", text))
            env_val = (
                os.environ.get("OPENAI_API_KEY")
                or os.environ.get("ANTHROPIC_API_KEY")
            )
            if env_val:
                self.assertNotIn(env_val, text)
            hit, rc, log_body, proof = _run_a7_secret_gate(text)
            _measure(
                "A7-PROOF exit=%s SECRETS=%s proof=%s"
                % (rc, "SECRETS_IN_PROMPT" in log_body, proof))
            self.assertTrue(
                hit,
                "A7 catch miss: exit=%s log=%r proof=%s"
                % (rc, log_body[:200], proof),
            )
            return

        text = _input_text(sc)
        self.assertIn("order_md", sc.get("input") or {})
        root, state = _mk_poly(prefix="adv-fuzz-sc-", fid="F-SC")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-SC", "order.md"), text)
            ok, fact = _eval_chip_oracle(state, oracle, sid)
            # allow-2b dual: must catch OWB; silence A2 checked via oracle
            if sc["class"] == "allow" and oracle == "silence":
                self.assertTrue(
                    ok, "allow silence miss %s fact=%s" % (sid, fact))
            elif sc["class"] == "catch" or oracle.startswith("chip:"):
                self.assertTrue(
                    ok,
                    "catch/chip miss %s expect=%s fact=%s" % (sid, oracle, fact),
                )
            else:
                self.assertTrue(ok, "oracle fail %s %s" % (sid, fact))
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# A4-фикс2 mutation (NOT in scenarios.json) — honest red on detector hole
# ---------------------------------------------------------------------------


class TestA4Fix2AdvisorBypassMutation(unittest.TestCase):
    """Видимая развилка + «без советников: выбора нет» + якорь механики.

    Оракул: непустой orders_suspect (advisor-bypass). Проскок = красный.
    Дыру orchlib НЕ чиним.
    """

    def test_a4_fix2_bez_sovetnikov_fork_orders_suspect(self):
        _measure("FUZZ A4-fix2 advisor-bypass → orders_suspect")
        body = (
            "# Приказ\n"
            "Либо путь A, либо путь B — видимая развилка, выбери сам.\n"
            "без советников: выбора нет\n"
            "python3 bin/run-exec.py --front F-A4F2\n"
        )
        # Mechanics present → A2 silent; basis via «без советников».
        self.assertTrue(orchlib._order_has_basis(body))
        self.assertTrue(orchlib._order_has_mechanics(body))
        root, state = _mk_poly(prefix="adv-fuzz-a4f2-", fid="F-A4F2")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-A4F2", "order.md"), body)
            with _EnvState(state):
                suspect = orchlib.orders_suspect(state)
                chips = orchlib.health_red_chips(state=state, kit_dir=REPO)
                a2 = chips.get("order_no_mechanics") or []
            self.assertEqual(a2, [], "A2 must stay silent (mechanics present)")
            # HONEST red: current detector needs assumption markers too —
            # bez+fork alone yields empty orders_suspect (detector hole).
            self.assertTrue(
                suspect or chips.get("orders_suspect"),
                "A4-fix2 ПРОСКОК: advisor-bypass (без советников: выбора нет "
                "+ видимая развилка) не в orders_suspect; fact suspect=%r "
                "chip=%r (дыра: order_suspect_facts требует markers≥1)"
                % (suspect, chips.get("orders_suspect")),
            )
            _measure("RESULT A4-fix2 LOVIT")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Mutations from corpus
# ---------------------------------------------------------------------------


class TestFuzzMutations(unittest.TestCase):
    def test_mutation_strip_basis_triggers_owb(self):
        _measure("MUT strip basis → orders_without_basis")
        data = _load_scenarios()
        src = next(s for s in data["scenarios"] if s["id"].startswith("allow-1"))
        lines = []
        for line in _input_text(src).splitlines():
            if _BASIS_LINE_RE.match(line):
                continue
            lines.append(line)
        body = "\n".join(lines) + "\n"
        self.assertFalse(orchlib._order_has_basis(body))
        root, state = _mk_poly(prefix="adv-fuzz-mut-b-", fid="F-MB")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-MB", "order.md"), body)
            ok, fact = _eval_chip_oracle(
                state, "chip:orders_without_basis", "mut-strip-basis")
            self.assertTrue(ok, "strip-basis miss: %s" % fact)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_mutation_strip_mechanics_triggers_a2(self):
        _measure("MUT strip mechanics → order_no_mechanics")
        data = _load_scenarios()
        src = next(s for s in data["scenarios"] if s["id"].startswith("allow-1"))
        lines = []
        for line in _input_text(src).splitlines():
            if _MECH_LINE_RE.search(line):
                continue
            lines.append(line)
        body = "\n".join(lines) + "\n"
        self.assertTrue(orchlib._order_has_basis(body))
        self.assertFalse(orchlib._order_has_mechanics(body))
        root, state = _mk_poly(prefix="adv-fuzz-mut-m-", fid="F-MM")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-MM", "order.md"), body)
            ok, fact = _eval_chip_oracle(
                state, "chip:order_no_mechanics", "mut-strip-mech")
            self.assertTrue(ok, "strip-mechanics miss: %s" % fact)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_mutation_foreign_paths_still_owb(self):
        _measure("MUT foreign paths in order → still OWB")
        body = (
            "# Приказ\n"
            "Сделай как-нибудь.\n"
            "Чужие пути: /root/other/proj/secret.py panel/index.html "
            "bin/orchlib.py routing/jev-table.json\n"
        )
        self.assertFalse(orchlib._order_has_basis(body))
        root, state = _mk_poly(prefix="adv-fuzz-mut-fp-", fid="F-FP")
        try:
            _write_text(
                os.path.join(state, "fronts", "F-FP", "order.md"), body)
            ok, fact = _eval_chip_oracle(
                state, "chip:orders_without_basis", "mut-foreign-paths")
            self.assertTrue(ok, "foreign-paths OWB miss: %s" % fact)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_mutation_secret_stub_a7(self):
        _measure("MUT secret stub → exit5 / SECRETS_IN_PROMPT")
        prompt = "API token in prompt: %s end.\n" % _A7_STUB
        self.assertEqual(len(_A7_STUB) - 3, 40)  # after sk-
        self.assertIsNotNone(orchlib.scan_secrets(prompt))
        env_val = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
        )
        if env_val:
            self.assertNotIn(env_val, prompt)
        hit, rc, log_body, proof = _run_a7_secret_gate(prompt)
        _measure("A7-MUT-PROOF exit=%s proof=%s" % (rc, proof))
        self.assertTrue(hit, "secret mutation miss exit=%s log=%r" % (
            rc, log_body[:200]))


# ---------------------------------------------------------------------------
# live-scan inventory (read-only file; no live state write)
# ---------------------------------------------------------------------------


class TestLiveScanInventory(unittest.TestCase):
    def test_live_scan_lists_five_a2_fronts(self):
        _measure("LIVE-SCAN inventory 5 A2 fronts")
        with open(LIVE_SCAN, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip()]
        expect = [
            "fronts/F-C4/order.md",
            "fronts/F-C3/order.md",
            "fronts/F-FRESH/order.md",
            "fronts/F-C1/order.md",
            "fronts/AUDSMOKE/order.md",
        ]
        self.assertEqual(lines, expect)
        self.assertEqual(len(lines), 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
