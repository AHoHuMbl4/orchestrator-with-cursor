#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Регресс ложного probes_missing на валидных квитанциях §3 (A4FIX2).

Полигоны: только ORCHESTRATION_DIR=/tmp/adv-prb-…; живой
/root/.orchestration не пишем. Форма (г) — как run-exec --probe --oracle 0.
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


def _mk_poly(prefix="adv-prb-"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "fronts": [{
            "id": "F-POLY",
            "status": "active",
            "title": "poly",
        }],
        "goal": "adv-probe-receipt",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "execution": {"timeout_s": 1800},
    })
    return root, state


def _mk_code_end(state, rid, sid="s1"):
    now = time.time()
    _append_journal(state, [
        {"kind": "start", "id": rid, "ts": now - 10,
         "front": "F-POLY", "role": "code/coder.md", "engine": "local",
         "prompt_file": None},
        {"kind": "end", "id": rid, "ts": now, "exit": 0, "gates": []},
    ])


def _mk_wave_with_block(state, rid, sid="s1"):
    run_dir = os.path.join(state, "sessions", sid, "runs", rid)
    os.makedirs(run_dir, exist_ok=True)
    art = os.path.join(run_dir, "artifact.md")
    _write_text(art, (
        "# artifact %s\n"
        "проба: poly-%s\n"
        "оракул: exit 0\n"
        "полигон: /tmp\n"
        "класс-доказательства: function\n"
    ) % (rid, rid))
    past = time.time() - 3600.0
    os.utime(art, (past, past))
    _mk_code_end(state, rid, sid=sid)
    return run_dir, art


def _section3_receipt(rid, ts, oracle_match="true", artifact="artifact.md"):
    return (
        "probe: poly-%s\n"
        "cmd: true\n"
        "exit: 0\n"
        "oracle_match: %s\n"
        "ts: %s\n"
        "critic_id: handmade-crit\n"
        "artifact: %s\n"
    ) % (rid, oracle_match, ts, artifact)


def _a4fix2_form_receipt(rid, ts, oracle_match="true"):
    """Форма writer run-exec --probe --oracle 0 (образец ADV-PROBE-A4FIX2)."""
    return (
        "probe: %s\n"
        "cmd: python3 /tmp/adv-wl/probe-a4fix.py\n"
        "exit: 0\n"
        "oracle_match: %s\n"
        "ts: %s\n"
        "critic_id: %s\n"
        "artifact: %s\n"
        "generator: orch-probe-receipt/ec88243007\n"
        "cmd_sha256: fdd49c7ddf4177b429e5b8861d841138ccb29a285525b85a77f0c7115fd35ed3\n"
    ) % (rid, oracle_match, ts, rid, rid)


class TestAdversarialProbesReceipt(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        self.root, self.state = _mk_poly()
        os.environ["ORCHESTRATION_DIR"] = self.state

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_valid_section3_not_in_chip(self):
        _measure("A valid §3 receipt → not in probes_missing")
        run_dir, art = _mk_wave_with_block(self.state, "WAVE-A")
        ts = float(os.path.getmtime(art)) + 10.0
        _write_text(os.path.join(run_dir, "probe-receipt.md"),
                    _section3_receipt("WAVE-A", ts, artifact="WAVE-A"))
        pok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(os.path.join(run_dir, "probe-receipt.md")),
            artifact_path=art, run_id="WAVE-A")
        self.assertTrue(pok, reason)
        missing = orchlib.probes_missing(state=self.state)
        self.assertNotIn("WAVE-A", missing)

    def test_b_missing_receipt_in_chip(self):
        _measure("B no receipt → in probes_missing")
        _mk_wave_with_block(self.state, "WAVE-B")
        missing = orchlib.probes_missing(state=self.state)
        self.assertIn("WAVE-B", missing)

    def test_c_oracle_match_false_in_chip(self):
        _measure("C oracle_match false → in probes_missing")
        run_dir, art = _mk_wave_with_block(self.state, "WAVE-C")
        ts = float(os.path.getmtime(art)) + 10.0
        _write_text(os.path.join(run_dir, "probe-receipt.md"),
                    _section3_receipt("WAVE-C", ts, oracle_match="false",
                                     artifact="WAVE-C"))
        pok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(os.path.join(run_dir, "probe-receipt.md")),
            artifact_path=art, run_id="WAVE-C")
        self.assertFalse(pok)
        self.assertEqual(reason, "oracle_match_false")
        missing = orchlib.probes_missing(state=self.state)
        self.assertIn("WAVE-C", missing)

    def test_d_a4fix2_probe_form_not_in_chip(self):
        _measure("D A4FIX2 form (run-exec --probe --oracle 0) → not in chip")
        rid = "ADV-PROBE-FORM"
        run_dir = os.path.join(self.state, "sessions", "s1", "runs", rid)
        os.makedirs(run_dir, exist_ok=True)
        _write_text(os.path.join(run_dir, "run.log"),
                    "PROBE_CMD=python3 /tmp/adv-wl/probe-a4fix.py\nEXIT=0\n")
        _mk_code_end(self.state, rid)
        ts = time.time()
        rec = _a4fix2_form_receipt(rid, ts)
        _write_text(os.path.join(run_dir, "probe-receipt.md"), rec)
        pok, reason = orchlib.parse_probe_receipt(rec, run_id=rid)
        self.assertTrue(pok, reason)
        self.assertFalse(orchlib.wave_has_probe_block(rid, state=self.state))
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        missing = orchlib.probes_missing(state=self.state)
        self.assertNotIn(rid, missing)


if __name__ == "__main__":
    unittest.main()
