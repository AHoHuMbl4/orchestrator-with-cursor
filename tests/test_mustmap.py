#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-MUSTMAP MM-C2: перекласс существующих механик + чипы handoff/project.

Группы: (1) переклассификация — живые пробы write-compass/dual-writer/
probes_missing/reground/orch-lint; (2) чипы handoff_oversize /
project_md_missing — нарушение→ловит, чисто→тишина; (3) регресс соседей
orders_suspect/orders_without_basis/advisors_without_scouts.
State: только ORCHESTRATION_DIR=/tmp/mustmap-…; /root/.orchestration не трогаем.
"""
from __future__ import print_function

import importlib.util
import json
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
RUN_EXEC = os.path.join(BIN, "run-exec.py")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402
import reground as rg  # noqa: E402


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rex = _load_mod("run_exec_mustmap", RUN_EXEC)


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


def _mk_poly(prefix="mustmap-", fid="F-MM", status="active", with_project=True):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "mustmap-test",
        "fronts": [{
            "id": fid,
            "status": status,
            "title": fid,
        }],
        "notes": "",
    })
    if with_project:
        _write_text(os.path.join(root, "PROJECT.md"), "mustmap poly\n")
    return root, state


def _sleep_child(sec):
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(%d)" % sec],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


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
# (1) переклассификация — пробы существующих механик
# ---------------------------------------------------------------------------


class TestReclassWriteCompass(unittest.TestCase):
    """MM-073 / MM-117 → write-compass лимит 4000."""

    def setUp(self):
        self.root, self.state = _mk_poly(prefix="mm-compass-")
        self.front_compass = os.path.join(
            self.state, "fronts", "F-MM", "compass.md")
        os.makedirs(os.path.dirname(self.front_compass), exist_ok=True)
        _write_text(self.front_compass, "")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mm073_117_overflow_refused(self):
        _measure("PROBE MM-073/MM-117 write-compass >4000 → refuse")
        p = {"compass": {"max_front_chars": 4000, "max_session_chars": 8500}}
        big = "x" * 4001
        with _EnvState(self.state):
            ok, info = orchlib.write_compass_checked(p, self.front_compass, big)
        self.assertFalse(ok)
        self.assertIn("error", info)
        with open(self.front_compass, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), "")
        _measure("RESULT MM-073/MM-117 → write-compass LOVIT")

    def test_mm073_117_under_limit_ok(self):
        _measure("PROBE MM-073/MM-117 write-compass ≤4000 → ok")
        p = {"compass": {"max_front_chars": 4000, "max_session_chars": 8500}}
        body = "ok\n" * 10
        with _EnvState(self.state):
            ok, info = orchlib.write_compass_checked(
                p, self.front_compass, body)
        self.assertTrue(ok, info)
        with open(self.front_compass, "r", encoding="utf-8") as f:
            self.assertEqual(f.read(), body)


class TestReclassDualWriter(unittest.TestCase):
    """MM-045 / MM-087 → FRONT_DUAL_WRITER exit 13."""

    def setUp(self):
        self.root, self.state = _mk_poly(prefix="mm-dual-", fid="F-DUAL")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_mm045_087_second_writer_exit_13(self):
        _measure("PROBE MM-045/MM-087 FRONT_DUAL_WRITER → 13")
        proc = _sleep_child(30)
        try:
            with _EnvState(self.state):
                rid_a = "W-A"
                pid_path = os.path.join(
                    self.state, "cursor-run-%s.pid" % rid_a)
                rex.write_pid_file(pid_path, proc.pid)
                _append_journal(self.state, [{
                    "ts": time.time(), "kind": "start", "id": rid_a,
                    "engine": "local", "front": "F-DUAL", "role": None,
                }])
                rc, msg = rex.check_dual_writer_guard(
                    "F-DUAL", "W-B", self.state, readonly=False)
            self.assertEqual(rc, 13)
            self.assertIn("F-DUAL", msg or "")
            _measure("RESULT MM-045/MM-087 → FRONT_DUAL_WRITER LOVIT")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_mm045_087_clean_no_dual(self):
        _measure("PROBE MM-045/MM-087 no live writer → silence")
        with _EnvState(self.state):
            rc, _ = rex.check_dual_writer_guard(
                "F-DUAL", "W-ONLY", self.state, readonly=False)
        self.assertIsNone(rc)


class TestReclassProbesMissing(unittest.TestCase):
    """MM-036 / MM-098 → probes_missing."""

    def setUp(self):
        self.root, self.state = _mk_poly(prefix="mm-probe-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _mk_code_wave(self, rid, with_probe_block=True):
        run_dir = os.path.join(self.state, "sessions", "s1", "runs", rid)
        os.makedirs(run_dir, exist_ok=True)
        art = os.path.join(run_dir, "artifact.md")
        body = "# art %s\n" % rid
        if with_probe_block:
            body += (
                "проба: poly-%s\nоракул: exit 0\nполигон: /tmp\n"
                "класс-доказательства: function\n"
            ) % rid
        _write_text(art, body)
        past = time.time() - 3600.0
        os.utime(art, (past, past))
        now = time.time()
        _append_journal(self.state, [
            {"kind": "start", "id": rid, "ts": now - 10,
             "front": "F-MM", "role": "code/coder.md", "engine": "local"},
            {"kind": "end", "id": rid, "ts": now, "exit": 0, "gates": []},
        ])
        return art

    def test_mm036_098_missing_receipt_flagged(self):
        _measure("PROBE MM-036/MM-098 probes_missing → ловит")
        with _EnvState(self.state):
            self._mk_code_wave("PM1")
            missing = orchlib.probes_missing(state=self.state)
        self.assertIn("PM1", missing)
        _measure("RESULT MM-036/MM-098 → probes_missing LOVIT")

    def test_mm036_098_no_code_wave_silence(self):
        _measure("PROBE MM-036/MM-098 no code wave → silence")
        with _EnvState(self.state):
            missing = orchlib.probes_missing(state=self.state)
        self.assertEqual(missing, [])


class TestReclassReground(unittest.TestCase):
    """MM-003 → reground foreign_state_warning (STATE БЕЗ ПРОЕКТА)."""

    def test_mm003_no_project_warns(self):
        _measure("PROBE MM-003 STATE БЕЗ ПРОЕКТА → ловит")
        root, state = _mk_poly(prefix="mm-rg-", with_project=False)
        prev_cwd = os.getcwd()
        try:
            with _EnvState(state):
                os.chdir(root)  # state под cwd → не foreign
                msg = rg.foreign_state_warning()
            self.assertIn("STATE БЕЗ ПРОЕКТА", msg)
            _measure("RESULT MM-003 → reground LOVIT")
        finally:
            os.chdir(prev_cwd)
            shutil.rmtree(root, ignore_errors=True)

    def test_mm003_with_project_silence(self):
        _measure("PROBE MM-003 +PROJECT.md → silence")
        root, state = _mk_poly(prefix="mm-rgok-", with_project=True)
        prev_cwd = os.getcwd()
        try:
            with _EnvState(state):
                os.chdir(root)
                msg = rg.foreign_state_warning()
            self.assertEqual(msg, "")
        finally:
            os.chdir(prev_cwd)
            shutil.rmtree(root, ignore_errors=True)


class TestReclassOrchLint(unittest.TestCase):
    """MM-008 → orch-lint (роль на диске ↔ _index)."""

    def test_mm008_orphan_role_flagged(self):
        _measure("PROBE MM-008 orch-lint orphan role → ловит")
        kit = tempfile.mkdtemp(prefix="mm-lint-", dir="/tmp")
        try:
            roles = os.path.join(
                kit, "skills", "orchestration", "references", "roles")
            os.makedirs(os.path.join(roles, "code"), exist_ok=True)
            _write_text(
                os.path.join(roles, "_index.md"),
                "| Роль | Файл | Когда |\n|---|---|---|\n"
                "| Coder | code/coder.md | code |\n",
            )
            _write_text(os.path.join(roles, "code", "coder.md"), "# coder\n")
            _write_text(
                os.path.join(roles, "code", "orphan-role.md"), "# orphan\n")
            rules = os.path.join(kit, "rules")
            os.makedirs(rules, exist_ok=True)
            _write_json(os.path.join(rules, "manifest.json"), {"cards": []})
            viols = orchlib.orch_lint_violations(kit_dir=kit, deep=False)
            hit = [v for v in viols if "orphan-role.md" in v
                   and "not listed in _index" in v]
            self.assertTrue(hit, viols)
            _measure("RESULT MM-008 → orch-lint LOVIT")
        finally:
            shutil.rmtree(kit, ignore_errors=True)

    def test_mm008_index_aligned_silence(self):
        kit = tempfile.mkdtemp(prefix="mm-lintok-", dir="/tmp")
        try:
            roles = os.path.join(
                kit, "skills", "orchestration", "references", "roles")
            os.makedirs(os.path.join(roles, "code"), exist_ok=True)
            _write_text(
                os.path.join(roles, "_index.md"),
                "| Роль | Файл | Когда |\n|---|---|---|\n"
                "| Coder | code/coder.md | code |\n",
            )
            _write_text(os.path.join(roles, "code", "coder.md"), "# coder\n")
            rules = os.path.join(kit, "rules")
            os.makedirs(rules, exist_ok=True)
            _write_json(os.path.join(rules, "manifest.json"), {"cards": []})
            viols = orchlib.orch_lint_violations(kit_dir=kit, deep=False)
            orphan = [v for v in viols if "not listed in _index" in v]
            self.assertEqual(orphan, [])
        finally:
            shutil.rmtree(kit, ignore_errors=True)


# ---------------------------------------------------------------------------
# (2) новые чипы
# ---------------------------------------------------------------------------


class TestHandoffOversize(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly(prefix="mm-ho-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_oversize_flags(self):
        _measure("CHIP handoff_oversize: >2000 → ловит")
        _write_text(
            os.path.join(self.state, "handoff.md"), "H" * 2001)
        with _EnvState(self.state):
            ids = orchlib.handoff_oversize(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(ids)
        self.assertTrue(ids[0].startswith("handoff.md:"))
        self.assertEqual(chips.get("handoff_oversize"), ids)

    def test_under_limit_silence(self):
        _measure("CHIP handoff_oversize: ≤2000 → тишина")
        _write_text(
            os.path.join(self.state, "handoff.md"), "H" * 100)
        with _EnvState(self.state):
            ids = orchlib.handoff_oversize(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertEqual(ids, [])
        self.assertEqual(chips.get("handoff_oversize") or [], [])

    def test_no_hierarchy_silence(self):
        root = tempfile.mkdtemp(prefix="mm-ho-empty-", dir="/tmp")
        state = os.path.join(root, "state")
        os.makedirs(state, exist_ok=True)
        open(os.path.join(state, "journal.jsonl"), "w").close()
        _write_json(os.path.join(state, "fronts.json"), {
            "goal": "", "fronts": [], "notes": "",
        })
        _write_text(os.path.join(state, "handoff.md"), "H" * 3000)
        try:
            with _EnvState(state):
                ids = orchlib.handoff_oversize(state)
            self.assertEqual(ids, [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestProjectMdMissing(unittest.TestCase):
    def test_missing_flags(self):
        _measure("CHIP project_md_missing: нет файла → ловит")
        root, state = _mk_poly(prefix="mm-pm-", with_project=False)
        try:
            with _EnvState(state):
                ids = orchlib.project_md_missing(state)
                chips = orchlib.health_red_chips(state=state, kit_dir=REPO)
            self.assertEqual(ids, ["PROJECT.md"])
            self.assertEqual(chips.get("project_md_missing"), ["PROJECT.md"])
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_present_silence(self):
        _measure("CHIP project_md_missing: есть файл → тишина")
        root, state = _mk_poly(prefix="mm-pmok-", with_project=True)
        try:
            with _EnvState(state):
                ids = orchlib.project_md_missing(state)
                chips = orchlib.health_red_chips(state=state, kit_dir=REPO)
            self.assertEqual(ids, [])
            self.assertEqual(chips.get("project_md_missing") or [], [])
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (3) регресс соседей
# ---------------------------------------------------------------------------


class TestNeighborRegression(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_poly(prefix="mm-nb-", fid="F-NB")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_orders_suspect_still_works(self):
        _measure("REGRESS orders_suspect")
        _write_text(
            os.path.join(self.state, "fronts", "F-NB", "order.md"),
            "без советников: выбора нет\nсервер вероятно жив\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_suspect(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertEqual(ids, ["F-NB"])
        self.assertIn("F-NB", chips.get("orders_suspect") or [])

    def test_orders_without_basis_still_works(self):
        _measure("REGRESS orders_without_basis")
        _write_text(
            os.path.join(self.state, "fronts", "F-NB", "order.md"),
            "# order\nцель: сделать\nбез строки подхода/обоснования\n",
        )
        with _EnvState(self.state):
            ids = orchlib.orders_without_basis(self.state)
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertTrue(any("order.md" in x for x in ids))
        self.assertTrue(chips.get("orders_without_basis"))

    def test_advisors_without_scouts_still_works(self):
        _measure("REGRESS advisors_without_scouts")
        now = time.time()
        _append_journal(self.state, [{
            "kind": "start", "id": "adv-mm", "ts": now,
            "front": "F-NB", "role": "meta/opportunity-advisor.md",
            "engine": "local",
        }])
        with _EnvState(self.state):
            chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn("adv-mm", chips.get("advisors_without_scouts") or [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
