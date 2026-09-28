#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RCPT-B-W1: run-exec --probe/--oracle автопуть приёмки (группа 2/5).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_receipt_wrap.py
State: только ORCHESTRATION_DIR=/tmp/rcptwrap-…; /root/.orchestration не трогаем.
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
RUN_EXEC = os.path.join(BIN, "run-exec.py")
LIVE_STATE = "/root/.orchestration"

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _assert_not_live(path):
    live = os.path.realpath(LIVE_STATE)
    cur = os.path.realpath(path)
    if cur == live or cur.startswith(live + os.sep):
        raise AssertionError("poly touches live state: %s" % path)


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


def _mk_poly(prefix="rcptwrap-"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "fronts": [{
            "id": "F-POLY",
            "status": "active",
            "title": "poly",
            "owns": ["bin/run-exec.py", "tests/test_receipt_wrap.py"],
        }],
        "goal": "rcpt-wrap",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "execution": {"timeout_s": 1800, "retry_on_fail": 1},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    return root, state


def _mk_wave(state, rid, sid="s1", role="code/coder.md",
             with_probe_block=True, past_mtime=True, oracle_line="exit 0"):
    run_dir = os.path.join(state, "sessions", sid, "runs", rid)
    os.makedirs(run_dir, exist_ok=True)
    art = os.path.join(run_dir, "artifact.md")
    body = "# artifact %s\n" % rid
    if with_probe_block:
        body += (
            "проба: poly-%s\n"
            "оракул: %s\n"
            "полигон: /tmp\n"
            "класс-доказательства: function\n"
        ) % (rid, oracle_line)
    _write_text(art, body)
    if past_mtime:
        past = time.time() - 3600.0
        os.utime(art, (past, past))
    now = time.time()
    _append_journal(state, [
        {"kind": "start", "id": rid, "ts": now - 10,
         "front": "F-POLY", "role": role, "engine": "local",
         "session": sid},
        {"kind": "end", "id": rid, "ts": now, "exit": 0, "gates": []},
    ])
    return run_dir, art


def _run_probe(state, rid, sid, probe_cmd, oracle, front="F-POLY"):
    env = dict(os.environ)
    env["ORCHESTRATION_DIR"] = state
    # не наследовать живой dual-writer parent из внешней сессии
    env.pop("ORCH_RUN_ID", None)
    env.pop("ORCH_FRONT", None)
    argv = [
        sys.executable, RUN_EXEC,
        "--id", rid,
        "--session", sid,
        "--front", front,
        "--probe", probe_cmd,
        "--oracle", str(oracle),
    ]
    return subprocess.run(
        argv, cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )


class TestReceiptWrap(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        self.root, self.state = _mk_poly()
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        shutil.rmtree(self.root, ignore_errors=True)

    def test_probe_exit0_oracle0(self):
        _measure("G2a --probe true oracle 0")
        rid, sid = "WRAP-OK", "s1"
        run_dir, art = _mk_wave(self.state, rid, sid=sid)
        r = _run_probe(self.state, rid, sid, "true", 0)
        self.assertEqual(r.returncode, 0, "stderr=%s stdout=%s" % (
            r.stderr, r.stdout))
        self.assertTrue(r.stdout.strip().startswith("ok "), r.stdout)
        path = os.path.join(run_dir, "probe-receipt.md")
        self.assertTrue(os.path.isfile(path), path)
        text = orchlib._read_text_silent(path)
        self.assertIn("generator: orch-probe-receipt/", text)
        self.assertIn("oracle_match: true", text)
        self.assertIn("exit: 0", text)
        self.assertIn("cmd: true", text)
        pok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id=rid)
        self.assertTrue(pok, reason)
        handmade = orchlib.receipt_handmade(state=self.state)
        self.assertNotIn(rid, handmade)
        chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertNotIn(rid, chips.get("receipt_handmade") or [])

    def test_probe_exit1_oracle1(self):
        _measure("G2b --probe false oracle 1")
        rid, sid = "WRAP-NZ", "s1"
        run_dir, art = _mk_wave(
            self.state, rid, sid=sid, oracle_line="exit 1")
        r = _run_probe(self.state, rid, sid, "false", 1)
        self.assertEqual(r.returncode, 0, "stderr=%s stdout=%s" % (
            r.stderr, r.stdout))
        self.assertTrue(r.stdout.strip().startswith("ok "), r.stdout)
        path = os.path.join(run_dir, "probe-receipt.md")
        self.assertTrue(os.path.isfile(path), path)
        text = orchlib._read_text_silent(path)
        self.assertIn("generator: orch-probe-receipt/", text)
        self.assertIn("oracle_match: true", text)
        self.assertIn("exit: 1", text)
        self.assertIn("cmd: false", text)
        pok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id=rid)
        self.assertTrue(pok, reason)
        handmade = orchlib.receipt_handmade(state=self.state)
        self.assertNotIn(rid, handmade)

    def test_probes_missing_cleared_by_autoprobe(self):
        _measure("G2c probes_missing снят автопутём --probe")
        rid, sid = "WRAP-CLR", "s1"
        run_dir, art = _mk_wave(self.state, rid, sid=sid)
        missing_before = orchlib.probes_missing(state=self.state)
        self.assertIn(rid, missing_before,
                      "ожидали id в probes_missing до квитанции: %s"
                      % missing_before)
        r = _run_probe(self.state, rid, sid, "true", 0)
        self.assertEqual(r.returncode, 0, "stderr=%s stdout=%s" % (
            r.stderr, r.stdout))
        path = os.path.join(run_dir, "probe-receipt.md")
        self.assertTrue(os.path.isfile(path), path)
        text = orchlib._read_text_silent(path)
        pok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id=rid)
        self.assertTrue(pok, reason)
        missing_after = orchlib.probes_missing(state=self.state)
        self.assertNotIn(rid, missing_after,
                         "после --probe id должен быть снят: %s"
                         % missing_after)
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state),
            "снятие должно быть квитанцией, не потерей роли")
        handmade = orchlib.receipt_handmade(state=self.state)
        self.assertNotIn(rid, handmade)
        _measure("MEASURE probes_missing before=%s after=%s" % (
            missing_before, missing_after))

    def test_oracle_mismatch_nonzero_exit(self):
        _measure("G2d oracle mismatch → wrapper ≠0 + receipt false")
        rid, sid = "WRAP-FAIL", "s1"
        run_dir, _art = _mk_wave(self.state, rid, sid=sid)
        r = _run_probe(self.state, rid, sid, "false", 0)
        self.assertNotEqual(r.returncode, 0, "stderr=%s stdout=%s" % (
            r.stderr, r.stdout))
        self.assertTrue(r.stdout.strip().startswith("fail "), r.stdout)
        path = os.path.join(run_dir, "probe-receipt.md")
        self.assertTrue(os.path.isfile(path), path)
        text = orchlib._read_text_silent(path)
        self.assertIn("oracle_match: false", text)
        self.assertIn("exit: 1", text)
        self.assertIn("generator: orch-probe-receipt/", text)


if __name__ == "__main__":
    unittest.main()
