#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5-K8: много-блочные квитанции §3 — отравление ретраев красной историей.

Файл валиден ⇔ ≥1 блок §3-валиден и ЗЕЛЁНЫЙ; красные аудит-записи
(oracle_match:false) — история, не яд; только-красный файл и mix без
зелёного блока — невалидны. Фикстуры приказа (а)–(г) + e2e-ретрай
--probe того же id (рана F-C5-K6-REG). Запуск: cd repo &&
python3 -m pytest tests/test_receipt_multiblock.py -q.
Полигоны: только ORCHESTRATION_DIR=/tmp/k8mb-…; живой /root/.orchestration
не пишем.
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
PROBE_RECEIPT_PY = os.path.join(BIN, "probe-receipt.py")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _assert_not_live(path):
    live = os.path.realpath("/root/.orchestration")
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


def _mk_poly(prefix="k8mb-"):
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
            "owns": [
                "bin/orchlib.py",
                "tests/test_receipt_multiblock.py",
            ],
        }],
        "goal": "k8-multiblock",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "execution": {"timeout_s": 1800, "retry_on_fail": 1},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    return root, state


def _mk_wave(state, rid, sid="s1", role="code/coder.md",
             with_probe_block=False):
    """Волна кода без §1-блока (форма probe-рана: oracle_exit не задан)."""
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


def _audit_block(rid, cmd, exit_code, ts=None):
    """Красная аудит-запись — форма _write_failed_oracle_audit (writer-канон)."""
    import hashlib
    return (
        "probe: %s\n"
        "cmd: %s\n"
        "exit: %s\n"
        "oracle_match: false\n"
        "ts: %s\n"
        "critic_id: %s\n"
        "artifact: %s\n"
        "generator: orch-probe-receipt/%s\n"
        "cmd_sha256: %s\n"
    ) % (rid, cmd, int(exit_code), ts if ts is not None else time.time(),
         rid, rid, orchlib.kit_version(),
         hashlib.sha256(str(cmd).encode("utf-8")).hexdigest())


def _run_probe(state, rid, sid, probe_cmd, oracle):
    env = dict(os.environ)
    env["ORCHESTRATION_DIR"] = state
    env.pop("ORCH_RUN_ID", None)
    env.pop("ORCH_FRONT", None)
    argv = [
        sys.executable, RUN_EXEC,
        "--id", rid,
        "--session", sid,
        "--front", "F-POLY",
        "--probe", probe_cmd,
        "--oracle", str(oracle),
    ]
    return subprocess.run(
        argv, cwd=REPO, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        encoding="utf-8", errors="replace",
    )


class TestReceiptMultiblock(unittest.TestCase):
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
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        shutil.rmtree(self.root, ignore_errors=True)

    # --- (а) красный-аудит → зелёный ретрай ТОГО ЖЕ id → файл валиден ---
    def test_a_red_audit_then_green_retry_same_id(self):
        _measure("(а) red audit → green retry same id → valid, история жива")
        rid = "K8-A"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        self.assertIn(rid, orchlib.probes_missing(state=self.state))

        # первый прогон провален: writer отвергает красную запись (откат),
        # автопуть --probe оставляет аудит-след (см. e2e ниже)
        ok, info = orchlib.write_probe_receipt(
            probe=rid, cmd="python3 -m pytest tests/ -q", exit_code=1,
            oracle_match=False, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertFalse(ok)
        self.assertTrue(str(info).startswith("validate_failed:"))
        self.assertFalse(os.path.isfile(path), "красная запись откачена")
        _write_text(path, _audit_block(rid, "python3 -m pytest tests/ -q", 1))

        # отравленное состояние: только-красный файл невалиден
        pok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(path), artifact_path=art, run_id=rid)
        self.assertFalse(pok)
        self.assertEqual(reason, "oracle_match_false")
        self.assertIn(rid, orchlib.probes_missing(state=self.state))

        # зелёный ретрай ТОГО ЖЕ id: дописан, не откачен, аудит — история
        ok2, info2 = orchlib.write_probe_receipt(
            probe=rid, cmd="python3 -m pytest tests/ -q", exit_code=0,
            oracle_match=True, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertTrue(ok2, info2)
        text = orchlib._read_text_silent(path)
        self.assertIn("oracle_match: false", text, "аудит-запись сохранилась")
        self.assertIn("oracle_match: true", text, "зелёный блок присутствует")
        records = orchlib._parse_receipt_records(text)
        self.assertEqual(len(records), 2)
        greens = [r for r in records
                  if str(r.get("oracle_match", "")).lower() == "true"]
        self.assertEqual(len(greens), 1)
        pok2, reason2 = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id=rid)
        self.assertTrue(pok2, reason2)
        # потребители: погашение, не отравление историей
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertNotIn(rid, orchlib.probes_missing(state=self.state))
        self.assertTrue(orchlib._receipt_records_have_generator(text))
        # приёмка: CLI-воронка validate
        r = subprocess.run(
            [sys.executable, PROBE_RECEIPT_PY, "validate", "--file", path],
            cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr)

    # --- (а-e2e) ретрай --probe того же id: рана F-C5-K6-REG ---
    def test_a2_probe_wrapper_retry_green_same_id(self):
        _measure("(а-e2e) --probe fail → retry same id → зелёная квитанция")
        rid, sid = "K8-E2E", "s1"
        run_dir, art = _mk_wave(self.state, rid, sid=sid)
        path = os.path.join(run_dir, "probe-receipt.md")
        r1 = _run_probe(self.state, rid, sid, "false", 0)
        self.assertNotEqual(r1.returncode, 0, r1.stdout)
        text1 = orchlib._read_text_silent(path)
        self.assertIn("oracle_match: false", text1)
        self.assertNotIn("oracle_match: true", text1)
        self.assertIn(rid, orchlib.probes_missing(state=self.state))

        r2 = _run_probe(self.state, rid, sid, "true", 0)
        self.assertEqual(r2.returncode, 0, "stderr=%s stdout=%s" % (
            r2.stderr, r2.stdout))
        self.assertTrue(r2.stdout.strip().startswith("ok "), r2.stdout)
        text2 = orchlib._read_text_silent(path)
        # отравленная история остаётся + живой зелёный блок
        self.assertEqual(
            text2.count("oracle_match: false"), 1, text2)
        self.assertEqual(
            text2.count("oracle_match: true"), 1, text2)
        pok, reason = orchlib.parse_probe_receipt(
            text2, artifact_path=art, run_id=rid)
        self.assertTrue(pok, reason)
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertNotIn(rid, orchlib.probes_missing(state=self.state))

    # --- (б) только-красный файл → невалиден ---
    def test_b_only_red_file_invalid(self):
        _measure("(б) only-red file → invalid")
        rid = "K8-B"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        _write_text(path, _audit_block(rid, "python3 -m pytest tests/ -q", 1))
        pok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(path), artifact_path=art, run_id=rid)
        self.assertFalse(pok)
        self.assertEqual(reason, "oracle_match_false")
        self.assertFalse(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertIn(rid, orchlib.probes_missing(state=self.state))

    # --- (в) mix без зелёного → невалиден ---
    def test_c_mix_without_green_invalid(self):
        _measure("(в) red audit + stale green (mix без зелёного) → invalid")
        rid = "K8-C"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        mtime = float(os.path.getmtime(art))
        # структурно полный «зелёный» блок с ts раньше mtime артефакта —
        # сам по себе невалиден; вместе с красным аудитом — mix без зелёного
        stale_green = (
            "probe: %s\n"
            "cmd: python3 -m pytest tests/ -q\n"
            "exit: 0\n"
            "oracle_match: true\n"
            "ts: %s\n"
            "critic_id: %s\n"
            "artifact: %s\n"
            "generator: orch-probe-receipt/%s\n"
        ) % (rid, mtime - 5.0, rid, rid, orchlib.kit_version())
        _write_text(path, _audit_block(rid, "python3 -m pytest tests/ -q", 1)
                    + "\n" + stale_green)
        pok, reason = orchlib.parse_probe_receipt(
            orchlib._read_text_silent(path), artifact_path=art, run_id=rid)
        self.assertFalse(pok)
        self.assertEqual(reason, "oracle_match_false",
                         "reason первой неверной записи")
        self.assertFalse(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertIn(rid, orchlib.probes_missing(state=self.state))

    # --- (г) свежий файл зелёным блоком → валиден (REG2-путь, регрессия) ---
    def test_d_fresh_file_green_valid(self):
        _measure("(г) fresh file + green block → valid (REG2)")
        rid = "K8-D"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, info = orchlib.write_probe_receipt(
            probe=rid, cmd="python3 -m pytest tests/ -q", exit_code=0,
            oracle_match=True, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertTrue(ok, info)
        text = orchlib._read_text_silent(path)
        self.assertEqual(
            text.count("oracle_match: true"), 1, text)
        self.assertNotIn("oracle_match: false", text)
        records = orchlib._parse_receipt_records(text)
        self.assertEqual(len(records), 1)
        pok, reason = orchlib.parse_probe_receipt(
            text, artifact_path=art, run_id=rid)
        self.assertTrue(pok, reason)
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertNotIn(rid, orchlib.probes_missing(state=self.state))


def main():
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(
        TestReceiptMultiblock)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
