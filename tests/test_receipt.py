#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""RCPT-A-W1: writer / CLI / receipt_handmade / require_generator (stdlib).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_receipt.py
State: только ORCHESTRATION_DIR=/tmp/rcpt-… (cleanup); /root/.orchestration не трогаем.
Группы: 1 roundtrip; 3 handmade→WARN+valid §3; 4 old снимает probes_missing;
5 ts только writer; +params require_generator true/false. Группа 2 — RCPT-B.
"""
from __future__ import print_function

import hashlib
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
PROBE_RECEIPT_PY = os.path.join(BIN, "probe-receipt.py")

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


def _mk_poly(prefix="rcpt-"):
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
        "goal": "rcpt-test",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "execution": {"timeout_s": 1800},
    })
    return root, state


def _mk_wave(state, rid, sid="s1", role="code/coder.md",
             with_probe_block=True, past_mtime=True):
    """runs/<rid>/artifact.md (+ optional §1 block); journal start+end."""
    run_dir = os.path.join(state, "sessions", sid, "runs", rid)
    os.makedirs(run_dir, exist_ok=True)
    art = os.path.join(run_dir, "artifact.md")
    body = "# artifact %s\n" % rid
    if with_probe_block:
        body += (
            "проба: poly-%s\n"
            "оракул: exit 0\n"
            "полигон: /tmp\n"
            "класс-доказательства: function\n"
        ) % rid
    _write_text(art, body)
    if past_mtime:
        past = time.time() - 3600.0
        os.utime(art, (past, past))
    now = time.time()
    _append_journal(state, [
        {"kind": "start", "id": rid, "ts": now - 10,
         "front": "F-POLY", "role": role, "engine": "local"},
        {"kind": "end", "id": rid, "ts": now, "exit": 0, "gates": []},
    ])
    return run_dir, art


def _handmade_receipt(ts, artifact="artifact.md", rid="WAVE"):
    return (
        "probe: handmade-%s\n"
        "cmd: true\n"
        "exit: 0\n"
        "oracle_match: true\n"
        "ts: %s\n"
        "critic_id: handmade-crit\n"
        "artifact: %s\n"
    ) % (rid, ts, artifact)


class TestReceiptCore(unittest.TestCase):
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

    # --- группа 1: roundtrip write → validate ---
    def test_g1_roundtrip_write_validate(self):
        _measure("G1 roundtrip write→validate")
        run_dir, art = _mk_wave(self.state, "R1")
        path = os.path.join(run_dir, "probe-receipt.md")
        cmd = "python3 -c 'print(1)'"
        ok, info = orchlib.write_probe_receipt(
            probe="g1-roundtrip",
            cmd=cmd,
            exit_code=0,
            oracle_match=True,
            critic_id="R1-crit",
            artifact="R1",
            run_id="R1",
            path=path,
            state=self.state,
        )
        self.assertTrue(ok, info)
        self.assertEqual(info, path)
        text = orchlib._read_text_silent(path)
        self.assertIn("generator: orch-probe-receipt/", text)
        expect_sha = hashlib.sha256(cmd.encode("utf-8")).hexdigest()
        self.assertIn("cmd_sha256: %s" % expect_sha, text)
        pok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id="R1")
        self.assertTrue(pok, reason)
        # CLI validate
        r = subprocess.run(
            [sys.executable, PROBE_RECEIPT_PY, "validate", "--file", path],
            cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- группа 3: handmade без generator → WARN + valid §3 ---
    def test_g3_handmade_warn_but_valid(self):
        _measure("G3 handmade → WARN + valid §3")
        run_dir, art = _mk_wave(self.state, "R3")
        mtime = float(os.path.getmtime(art))
        path = os.path.join(run_dir, "probe-receipt.md")
        _write_text(path, _handmade_receipt(mtime + 10.0, artifact="R3", rid="R3"))
        text = orchlib._read_text_silent(path)
        ok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id="R3")
        self.assertTrue(ok, reason)
        self.assertFalse(orchlib._receipt_records_have_generator(text))
        handmade = orchlib.receipt_handmade(state=self.state)
        self.assertIn("R3", handmade)
        chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn("R3", chips.get("receipt_handmade") or [])
        # probes_missing не должен включать R3 (есть блок + валидная квитанция)
        missing = orchlib.probes_missing(state=self.state)
        self.assertNotIn("R3", missing)

    # --- группа 4: старая без generator снимает probes_missing ---
    def test_g4_old_receipt_clears_probes_missing(self):
        _measure("G4 old receipt clears probes_missing")
        # без квитанции — в probes_missing
        run_dir, art = _mk_wave(self.state, "R4")
        missing_before = orchlib.probes_missing(state=self.state)
        self.assertIn("R4", missing_before)
        mtime = float(os.path.getmtime(art))
        path = os.path.join(run_dir, "probe-receipt.md")
        _write_text(path, _handmade_receipt(mtime + 5.0, artifact="R4", rid="R4"))
        missing_after = orchlib.probes_missing(state=self.state)
        self.assertNotIn("R4", missing_after)
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt("R4", state=self.state))

    # --- группа 5: ts только writer ---
    def test_g5_ts_rejected_from_outside(self):
        _measure("G5 ts rejected from outside")
        run_dir, _art = _mk_wave(self.state, "R5")
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, reason = orchlib.write_probe_receipt(
            probe="g5",
            cmd="true",
            exit_code=0,
            oracle_match=True,
            critic_id="R5-crit",
            artifact="R5",
            run_id="R5",
            path=path,
            state=self.state,
            ts=1234567890.0,
        )
        self.assertFalse(ok)
        self.assertEqual(reason, "ts_rejected")
        self.assertFalse(os.path.isfile(path))
        # успешная запись — ts внутри файла ≈ now
        before = time.time()
        ok2, info = orchlib.write_probe_receipt(
            probe="g5-ok",
            cmd="true",
            exit_code=0,
            oracle_match=True,
            critic_id="R5-crit",
            artifact="R5",
            run_id="R5",
            path=path,
            state=self.state,
        )
        after = time.time()
        self.assertTrue(ok2, info)
        text = orchlib._read_text_silent(path)
        recs = orchlib._parse_receipt_records(text)
        self.assertTrue(recs)
        ts = float(recs[0]["ts"])
        self.assertGreaterEqual(ts, before - 1.0)
        self.assertLessEqual(ts, after + 1.0)

    # --- params-окно require_generator ---
    def test_params_require_generator_true_rejects_nogen(self):
        _measure("params require_generator=true")
        run_dir, art = _mk_wave(self.state, "RP-T")
        mtime = float(os.path.getmtime(art))
        path = os.path.join(run_dir, "probe-receipt.md")
        _write_text(
            path, _handmade_receipt(mtime + 5.0, artifact="RP-T", rid="RP-T"))
        # fail-open / false → принимает
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt("RP-T", state=self.state))
        # ужесточение
        _write_json(os.path.join(self.state, "params.json"), {
            "orchestration": {"enabled": True, "hierarchy": "off"},
            "execution": {"timeout_s": 1800},
            "receipt": {"require_generator": True},
        })
        self.assertFalse(
            orchlib.wave_has_valid_probe_receipt("RP-T", state=self.state))
        # parse_probe_receipt без params — всё ещё ok
        ok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(path),
            artifact_path=art, run_id="RP-T")
        self.assertTrue(ok, reason)
        # writer-квитанция проходит при require_generator=true
        path2 = os.path.join(run_dir, "probe-receipt-writer.md")
        # find_probe_receipts ищет probe-receipt.md — пишем туда через writer
        os.unlink(path)
        okw, infow = orchlib.write_probe_receipt(
            probe="rp-writer",
            cmd="true",
            exit_code=0,
            oracle_match=True,
            critic_id="RP-T-crit",
            artifact="RP-T",
            run_id="RP-T",
            path=path,
            state=self.state,
        )
        self.assertTrue(okw, infow)
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt("RP-T", state=self.state))

    def test_params_require_generator_false_accepts_nogen(self):
        _measure("params require_generator=false / absent")
        run_dir, art = _mk_wave(self.state, "RP-F")
        mtime = float(os.path.getmtime(art))
        path = os.path.join(run_dir, "probe-receipt.md")
        _write_text(
            path, _handmade_receipt(mtime + 5.0, artifact="RP-F", rid="RP-F"))
        _write_json(os.path.join(self.state, "params.json"), {
            "orchestration": {"enabled": True, "hierarchy": "off"},
            "execution": {"timeout_s": 1800},
            "receipt": {"require_generator": False},
        })
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt("RP-F", state=self.state))

    # --- chip: writer не ловится ---
    def test_chip_writer_not_handmade(self):
        _measure("chip: writer receipt not handmade")
        run_dir, _art = _mk_wave(self.state, "RW")
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, info = orchlib.write_probe_receipt(
            probe="writer-chip",
            cmd="true",
            exit_code=0,
            oracle_match=True,
            critic_id="RW-crit",
            artifact="RW",
            run_id="RW",
            path=path,
            state=self.state,
        )
        self.assertTrue(ok, info)
        handmade = orchlib.receipt_handmade(state=self.state)
        self.assertNotIn("RW", handmade)
        chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertNotIn("RW", chips.get("receipt_handmade") or [])

    # --- CLI write delegates to orchlib ---
    def test_cli_write_delegates(self):
        _measure("CLI write → orchlib writer")
        run_dir, _art = _mk_wave(self.state, "CLI1")
        path = os.path.join(run_dir, "probe-receipt.md")
        r = subprocess.run(
            [
                sys.executable, PROBE_RECEIPT_PY, "write",
                "--probe", "cli-p",
                "--cmd", "echo hi",
                "--exit", "0",
                "--oracle-match", "true",
                "--critic-id", "CLI1-crit",
                "--artifact", "CLI1",
                "--run-id", "CLI1",
                "--file", path,
            ],
            cwd=REPO,
            env=dict(os.environ, ORCHESTRATION_DIR=self.state),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        text = orchlib._read_text_silent(path)
        self.assertIn("generator: orch-probe-receipt/", text)
        self.assertIn("cmd_sha256:", text)

    # --- rollback on validate fail ---
    def test_rollback_on_validate_fail(self):
        _measure("rollback on validate fail")
        run_dir, art = _mk_wave(self.state, "RB", past_mtime=False)
        # artifact mtime в будущем относительно любого ts writer'а невозможно
        # проще: oracle_match false → parse fail → откат
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, reason = orchlib.write_probe_receipt(
            probe="rb",
            cmd="true",
            exit_code=0,
            oracle_match=False,
            critic_id="RB-crit",
            artifact="RB",
            run_id="RB",
            path=path,
            state=self.state,
        )
        self.assertFalse(ok)
        self.assertTrue(str(reason).startswith("validate_failed:"))
        self.assertFalse(os.path.isfile(path))
        # append-откат: существующий файл восстанавливается
        _write_text(path, "probe: keep\n")
        ok2, reason2 = orchlib.write_probe_receipt(
            probe="rb2",
            cmd="true",
            exit_code=1,
            oracle_match=False,
            critic_id="RB-crit",
            artifact="RB",
            run_id="RB",
            path=path,
            state=self.state,
        )
        self.assertFalse(ok2)
        self.assertEqual(orchlib._read_text_silent(path), "probe: keep\n")


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestReceiptCore)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
