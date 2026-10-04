#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C5-K8 + K8-FU: много-блочные квитанции §3 — отравление ретраев красной историей.

Файл валиден ⇔ ≥1 блок §3-валиден и ЗЕЛЁНЫЙ; красные аудит-записи
(oracle_match:false) — история, не яд; только-красный файл и mix без
зелёного блока — невалидны. Фикстуры приказа (а)–(г) + e2e-ретрай
--probe того же id (рана F-C5-K6-REG). Follow-up (критики ×3):
FU-1 покрытие инвариантов только individually зелёными записями;
FU-2 откат лжезелёного fresh-блока при живой зелёной истории;
FU-3 require_generator per-record; FU-4 красный append на зелёный
файл → ok=True, история сохранена. Запуск: cd repo &&
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


def _tool_block(probe, cmd, exit_code, oracle_match, ts=None, gen=None):
    """Блок §3 writer-канона с реальным sha (зелёный/красный по om)."""
    import hashlib
    return (
        "probe: %s\n"
        "cmd: %s\n"
        "exit: %s\n"
        "oracle_match: %s\n"
        "ts: %s\n"
        "critic_id: %s\n"
        "artifact: %s\n"
        "generator: %s\n"
        "cmd_sha256: %s\n"
    ) % (probe, cmd, int(exit_code), oracle_match,
         ts if ts is not None else time.time(), probe, probe,
         gen if gen is not None else
         "orch-probe-receipt/%s" % orchlib.kit_version(),
         hashlib.sha256(str(cmd).encode("utf-8")).hexdigest())


_INV_SECTION_HEADER = (
    "## Инварианты (машиночитаемые — строгий формат "
    "«Инвариант N: <cmd> → <оракул>»; приёмка требует прогона КАЖДОГО)")


def _write_front_order(state, fid, inv_rows):
    """Приказ фронта с машинной секцией инвариантов (полигон /tmp)."""
    lines = ["# Приказ фронту %s — k8fu фикстуры" % fid, "", _INV_SECTION_HEADER, ""]
    lines.extend(inv_rows)
    lines.append("")
    lines.extend(["## Границы", "- полигоны /tmp."])
    path = orchlib.front_order_path(fid, state=state)
    _assert_not_live(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


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

    # --- FU-1 (KR1-1): покрытие инвариантов — только individually
    #     зелёные записи; красный аудит не легализуется чужим зелёным ---
    def test_e_invariants_red_not_covered_by_foreign_green(self):
        _measure("FU-1 red-A + green-B → A остаётся красным")
        cmd_a = "python3 -m pytest tests/test_a.py -q"
        cmd_b = "python3 -m pytest tests/test_b.py -q"
        _write_front_order(self.state, "F-POLY", [
            "- Инвариант 1: %s → exit 0" % cmd_a,
            "- Инвариант 2: %s → exit 0" % cmd_b,
        ])
        rid = "K8-INV"
        run_dir, _art = _mk_wave(self.state, rid)
        # один файл: проваленный прогон A (красный аудит) + зелёный B
        _write_text(
            os.path.join(run_dir, "probe-receipt.md"),
            _audit_block(rid, cmd_a, 1) + "\n"
            + _tool_block(rid, cmd_b, 0, "true"))
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["F-POLY:1"],
            "красная запись A не гасится зелёным блоком B; B погашен своим зелёным")

    def test_f_invariants_green_covers(self):
        _measure("FU-1 green-A → гаснет")
        cmd_a = "python3 -m pytest tests/test_a.py -q"
        cmd_b = "python3 -m pytest tests/test_b.py -q"
        _write_front_order(self.state, "F-POLY", [
            "- Инвариант 1: %s → exit 0" % cmd_a,
            "- Инвариант 2: %s → exit 0" % cmd_b,
        ])
        run_dir_a, _ = _mk_wave(self.state, "K8-INV-A")
        _write_text(
            os.path.join(run_dir_a, "probe-receipt.md"),
            _tool_block("K8-INV-A", cmd_a, 0, "true"))
        run_dir_b, _ = _mk_wave(self.state, "K8-INV-B")
        _write_text(
            os.path.join(run_dir_b, "probe-receipt.md"),
            _tool_block("K8-INV-B", cmd_b, 0, "true"))
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])

    # --- FU-2 (KR2-1): writer откатывает лжезелёный fresh-блок
    #     даже при живой зелёной истории ---
    def test_g_writer_fake_green_fresh_rolled_back(self):
        _measure("FU-2 лжезелёный fresh (exit_ne_oracle/artifact_mismatch) → откат")
        rid = "K8-W"
        run_dir, _art = _mk_wave(self.state, rid, with_probe_block=True)
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, info = orchlib.write_probe_receipt(
            probe=rid, cmd="true", exit_code=0,
            oracle_match=True, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertTrue(ok, info)
        green_text = orchlib._read_text_silent(path)
        # §1-оракул «exit 0», fresh заявляет oracle_match:true при exit 1
        ok2, info2 = orchlib.write_probe_receipt(
            probe=rid, cmd="false", exit_code=1,
            oracle_match=True, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertFalse(ok2)
        self.assertEqual(info2, "validate_failed:fresh_exit_ne_oracle")
        text = orchlib._read_text_silent(path)
        self.assertEqual(text, green_text, "лжезелёный append откачен, зелёная история нетронута")
        # artifact_mismatch: fresh с чужим artifact-референсом
        ok3, info3 = orchlib.write_probe_receipt(
            probe=rid, cmd="true", exit_code=0,
            oracle_match=True, critic_id=rid, artifact="K8-OTHER",
            run_id=rid, path=path, state=self.state)
        self.assertFalse(ok3)
        self.assertEqual(info3, "validate_failed:fresh_artifact_mismatch")
        self.assertEqual(
            orchlib._read_text_silent(path), green_text)

    # --- FU-3 (KR1-2/KR2-2): require_generator per-record — красная
    #     tool-запись не «донирует» generator рукописной зелёной ---
    def test_h_require_generator_per_record(self):
        _measure("FU-3 require_generator: generator обязана нести сама гасящая запись")
        rid = "K8-GEN"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        mtime = float(os.path.getmtime(art))
        handmade_green = (
            "probe: %s\n"
            "cmd: true\n"
            "exit: 0\n"
            "oracle_match: true\n"
            "ts: %s\n"
            "critic_id: %s\n"
            "artifact: %s\n"
        ) % (rid, mtime + 5.0, rid, rid)
        # рукописная зелёная без generator + красная tool-запись С generator
        _write_text(
            path, handmade_green + "\n" + _audit_block(rid, "true", 1))
        _write_json(os.path.join(self.state, "params.json"), {
            "orchestration": {"enabled": True, "hierarchy": "off"},
            "execution": {"timeout_s": 1800},
            "receipt": {"require_generator": True},
        })
        self.assertFalse(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state),
            "generator красной записи не легализует зелёную без generator")
        self.assertIn(rid, orchlib.probes_missing(state=self.state))
        # fail-open / false → рукописная зелёная снова гасит
        _write_json(os.path.join(self.state, "params.json"), {
            "orchestration": {"enabled": True, "hierarchy": "off"},
            "execution": {"timeout_s": 1800},
        })
        self.assertTrue(
            orchlib.wave_has_valid_probe_receipt(rid, state=self.state))
        self.assertNotIn(rid, orchlib.probes_missing(state=self.state))

    # --- FU-4 (KR1-3): красный append на зелёный файл → ok=True,
    #     история сохранена ---
    def test_i_red_append_on_green_file_kept_as_history(self):
        _measure("FU-4 красный append на зелёный файл → ok=True, история жива")
        rid = "K8-H"
        run_dir, art = _mk_wave(self.state, rid)
        path = os.path.join(run_dir, "probe-receipt.md")
        ok, info = orchlib.write_probe_receipt(
            probe=rid, cmd="true", exit_code=0,
            oracle_match=True, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertTrue(ok, info)
        ok2, info2 = orchlib.write_probe_receipt(
            probe=rid, cmd="false", exit_code=1,
            oracle_match=False, critic_id=rid, artifact=rid,
            run_id=rid, path=path, state=self.state)
        self.assertTrue(ok2, info2)
        text = orchlib._read_text_silent(path)
        self.assertEqual(text.count("oracle_match: false"), 1, text)
        self.assertEqual(text.count("oracle_match: true"), 1, text)
        records = orchlib._parse_receipt_records(text)
        self.assertEqual(len(records), 2)
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
