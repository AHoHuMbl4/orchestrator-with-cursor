#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-MULTIWRITE MW2-B: обёртки run-exec/run-cloud (группа е).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_multiwrite_wrap.py
State: только ORCHESTRATION_DIR=/tmp/mwwrap-…; /root/.orchestration не трогаем.
"""
from __future__ import print_function

import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
RUN_EXEC = os.path.join(BIN, "run-exec.py")
RUN_CLOUD = os.path.join(BIN, "run-cloud.py")
LIVE_STATE = "/root/.orchestration"

if BIN not in sys.path:
    sys.path.insert(0, BIN)


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rex = _load_mod("run_exec_mwwrap", RUN_EXEC)
rcl = _load_mod("run_cloud_mwwrap", RUN_CLOUD)
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


def _mk_state(prefix="mwwrap"):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "mw2b-wrap",
        "fronts": [{
            "id": "F-MULTIWRITE",
            "title": "mw",
            "status": "active",
            "owns": ["bin/run-exec.py", "bin/run-cloud.py",
                     "tests/test_multiwrite_wrap.py"],
        }],
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"hierarchy": "on", "enabled": True},
        "execution": {"retry_on_fail": 1, "timeout_s": 30},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8").close()
    return root, state


def _seed_journal(state, entries):
    path = os.path.join(state, "journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _read_journal(state):
    path = os.path.join(state, "journal.jsonl")
    out = []
    if not os.path.isfile(path):
        return out
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _sleep_child(seconds=30):
    return subprocess.Popen(
        [sys.executable, "-c",
         "import time; time.sleep(%d)" % int(seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _fake_agent_dir(state):
    """PATH-stub cursor-agent: пишет env ORCH_* в лог-файл и спит."""
    bindir = os.path.join(state, "fake-bin")
    os.makedirs(bindir, exist_ok=True)
    agent = os.path.join(bindir, "cursor-agent")
    with open(agent, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("echo FAKE_AGENT_START\n")
        f.write("echo ORCH_RUN_ID=${ORCH_RUN_ID:-}\n")
        f.write("echo ORCH_FRONT=${ORCH_FRONT:-}\n")
        f.write("sleep 60\n")
    os.chmod(agent, 0o755)
    return bindir


class WrapTemp(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_state()
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Unit matrix (order п.7)
# ---------------------------------------------------------------------------


class TestTombstoneRetry(WrapTemp):
    def test_tombstone_cancels_retry(self):
        log = os.path.join(self.state, "r.log")
        pid = os.path.join(self.state, "r.pid")
        open(log, "w").close()
        open(pid, "w").write("1\n")
        rex.write_tombstone(self.state, "RID", prev_pid=1, session=None)
        calls = []

        def fake_retry(*a, **k):
            calls.append(1)
            return "0"

        real = rex.run_retry_child
        rex.run_retry_child = fake_retry
        try:
            code = rex.apply_retries(
                "4", log, pid, 10, time.time() + 100,
                "/dev/null", 2, "auto", [],
                state=self.state, run_id="RID", session=None)
        finally:
            rex.run_retry_child = real
        self.assertEqual(str(code), "4")
        self.assertEqual(calls, [])
        with open(log, "r", encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("TOMBSTONE: retry отменён (kill вручную)", txt)
        _measure("MEASURE tombstone cancels retry EXIT=4 no-respawn")


class TestDupIdGuard(WrapTemp):
    def test_live_pid_exit_11(self):
        proc = _sleep_child(20)
        try:
            pid_path = os.path.join(self.state, "cursor-run-DUP.pid")
            rex.write_pid_file(pid_path, proc.pid)
            rc = rex.check_duplicate_id_guard(self.state, "DUP", force=False)
            self.assertEqual(rc, 11)
            _measure("MEASURE dup live pid → exit 11")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_eperm_is_alive(self):
        # Simulate PermissionError from os.kill
        real_kill = os.kill

        def boom(pid, sig):
            raise PermissionError("EPERM")

        os.kill = boom
        try:
            self.assertTrue(rex.pid_alive(12345))
            _measure("MEASURE EPERM → alive")
        finally:
            os.kill = real_kill

    def test_starttime_mismatch_dead(self):
        proc = _sleep_child(20)
        try:
            pid_path = os.path.join(self.state, "cursor-run-ST.pid")
            with open(pid_path, "w", encoding="utf-8") as f:
                f.write("%d\n0\n" % proc.pid)  # wrong starttime
            self.assertFalse(rex.run_pid_is_alive(pid_path))
            rc = rex.check_duplicate_id_guard(self.state, "ST", force=False)
            self.assertIsNone(rc)  # мёртв → дубль разрешён
            _measure("MEASURE starttime mismatch → dead (dup allowed)")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


class TestDualWriter(WrapTemp):
    def test_second_writer_exit_13_chip(self):
        proc = _sleep_child(30)
        try:
            rid_a = "W-A"
            pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid_a)
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": rid_a,
                "engine": "local", "front": "F-MULTIWRITE", "role": None,
            }])
            rc, msg = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "W-B", self.state, readonly=False)
            self.assertEqual(rc, 13)
            self.assertIn("F-MULTIWRITE", msg)
            self.assertIn("W-A", msg)
            # chip path
            rex.journal_chip("multi_write_front", front="F-MULTIWRITE",
                             run_id="W-B")
            chips = [e for e in _read_journal(self.state)
                     if e.get("kind") == "chip"
                     and e.get("name") == "multi_write_front"]
            self.assertTrue(chips)
            _measure("MEASURE dual-writer → 13 + chip multi_write_front")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_same_id_not_13(self):
        # same id → dual guard returns None (exit 11 is separate)
        proc = _sleep_child(20)
        try:
            pid_path = os.path.join(self.state, "cursor-run-SAME.pid")
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": "SAME",
                "engine": "local", "front": "F-MULTIWRITE",
            }])
            rc, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "SAME", self.state, readonly=False)
            self.assertIsNone(rc)
            _measure("MEASURE same id → dual guard None (exit 11 path)")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


class TestCloudWritable(WrapTemp):
    def test_writable_marks_writer(self):
        _seed_journal(self.state, [{
            "ts": time.time(), "kind": "start", "id": "C-W",
            "engine": "cloud", "front": "F-MULTIWRITE", "writable": True,
        }])
        writers = rex._journal_open_writers("F-MULTIWRITE")
        self.assertIn("C-W", writers)
        _measure("MEASURE cloud --writable marks writer")

    def test_cloud_without_writable_not_writer(self):
        _seed_journal(self.state, [{
            "ts": time.time(), "kind": "start", "id": "C-RO",
            "engine": "cloud", "front": "F-MULTIWRITE",
        }])
        writers = rex._journal_open_writers("F-MULTIWRITE")
        self.assertNotIn("C-RO", writers)
        rc, _ = rex.check_dual_writer_guard(
            "F-MULTIWRITE", "NEW", self.state, readonly=False)
        self.assertIsNone(rc)
        _measure("MEASURE cloud without --writable NOT writer")


class TestA2(WrapTemp):
    def test_a2_with_front(self):
        prompt = "роль: code/coder.md\n\nзадача\n"
        out = rex.inject_a2_ownership(prompt, "F-MULTIWRITE",
                                       state_dir=self.state)
        self.assertIn("## Владение (A2)", out)
        self.assertIn("F-MULTIWRITE", out)
        self.assertTrue(out.index("роль:") < out.index("## Владение (A2)"))
        _measure("MEASURE A2 section present with --front")

    def test_a2_without_front(self):
        prompt = "роль: code/coder.md\n\nзадача\n"
        out = rex.inject_a2_ownership(prompt, None, state_dir=self.state)
        self.assertNotIn("## Владение (A2)", out)
        out2 = rex.inject_a2_ownership(prompt, "F-MISSING",
                                        state_dir=self.state)
        self.assertNotIn("## Владение (A2)", out2)
        _measure("MEASURE A2 absent without front/owns")


class TestNoVerify(WrapTemp):
    def test_no_verify_chip(self):
        log = os.path.join(self.state, "nv.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("running git commit -m x --no-verify\n")
        self.assertTrue(rex.detect_commit_no_verify(log))
        # journal_end emits chip
        prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        try:
            rex.journal_end("NV1", log, 0, front="F-MULTIWRITE",
                            readonly=False)
        finally:
            if prev is None:
                os.environ.pop("ORCHESTRATION_DIR", None)
            else:
                os.environ["ORCHESTRATION_DIR"] = prev
        chips = [e for e in _read_journal(self.state)
                 if e.get("kind") == "chip"
                 and e.get("name") == "commit_no_verify"]
        ends = [e for e in _read_journal(self.state)
                if e.get("kind") == "end" and e.get("id") == "NV1"]
        self.assertTrue(chips)
        self.assertTrue(ends and ends[-1].get("commit_no_verify") is True)
        _measure("MEASURE --no-verify → chip commit_no_verify")


class TestKillNoPid(WrapTemp):
    def test_kill_without_pid_tombstone_warn(self):
        # capture stderr
        import io
        buf = io.StringIO()
        real_err = sys.stderr
        sys.stderr = buf
        try:
            rc = rex.cmd_kill(self.state, "NOPE", session=None)
        finally:
            sys.stderr = real_err
        self.assertEqual(rc, 0)
        ts_path = rex.resolve_tombstone_path(self.state, "NOPE", None)
        self.assertTrue(os.path.isfile(ts_path))
        err = buf.getvalue()
        self.assertIn("warn", err.lower())
        killed = [e for e in _read_journal(self.state)
                  if e.get("kind") == "killed" and e.get("id") == "NOPE"]
        self.assertTrue(killed)
        _measure("MEASURE kill no pid → tombstone+warn")

    def test_kill_starttime_mismatch_no_sigkill(self):
        """PID жив, но starttime в pid-файле чужой → tombstone+warn, без kill."""
        import io
        proc = _sleep_child(30)
        try:
            pid_path = os.path.join(self.state, "cursor-run-KM.pid")
            with open(pid_path, "w", encoding="utf-8") as f:
                f.write("%d\n0\n" % proc.pid)  # wrong starttime
            self.assertFalse(rex.run_pid_is_alive(pid_path))
            killed_pids = []
            real_kill = rex.kill_pid

            def spy_kill(pid):
                killed_pids.append(pid)
                return real_kill(pid)

            rex.kill_pid = spy_kill
            buf = io.StringIO()
            real_err = sys.stderr
            sys.stderr = buf
            try:
                rc = rex.cmd_kill(self.state, "KM", session=None)
            finally:
                sys.stderr = real_err
                rex.kill_pid = real_kill
            self.assertEqual(rc, 0)
            self.assertEqual(killed_pids, [])
            self.assertTrue(rex.pid_alive(proc.pid))  # чужой процесс жив
            self.assertTrue(os.path.isfile(
                rex.resolve_tombstone_path(self.state, "KM", None)))
            self.assertIn("warn", buf.getvalue().lower())
            _measure("MEASURE kill starttime mismatch → tombstone+warn no SIGKILL")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


class TestToctouYounger(WrapTemp):
    def test_toctou_older_proceeds_younger_refuses(self):
        """После journal_start: младший (больший ts) → 13; старший → None."""
        proc_a = _sleep_child(30)
        proc_b = _sleep_child(30)
        try:
            for rid, proc in (("OLD", proc_a), ("YOUNG", proc_b)):
                pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
                rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [
                {"ts": 100.0, "kind": "start", "id": "OLD",
                 "engine": "local", "front": "F-MULTIWRITE"},
                {"ts": 200.0, "kind": "start", "id": "YOUNG",
                 "engine": "local", "front": "F-MULTIWRITE"},
            ])
            # старший на TOCTOU видит младшего, но other.ts > self.ts → идёт
            rc_old, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "OLD", self.state, readonly=False, toctou=True)
            self.assertIsNone(rc_old)
            # младший: other.ts <= self.ts → отказ
            rc_y, msg = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "YOUNG", self.state, readonly=False, toctou=True)
            self.assertEqual(rc_y, 13)
            self.assertIn("OLD", msg)
            # без toctou оба бы отказались — pre-start по-прежнему 13
            rc_pre, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "YOUNG", self.state, readonly=False)
            self.assertEqual(rc_pre, 13)
            _measure("MEASURE TOCTOU younger refuses, older proceeds")
        finally:
            for p in (proc_a, proc_b):
                p.send_signal(signal.SIGKILL)
                p.wait(timeout=5)

    def test_toctou_equal_ts_refuses(self):
        proc = _sleep_child(20)
        try:
            pid_path = os.path.join(self.state, "cursor-run-EQ-A.pid")
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [
                {"ts": 50.0, "kind": "start", "id": "EQ-A",
                 "engine": "local", "front": "F-MULTIWRITE"},
                {"ts": 50.0, "kind": "start", "id": "EQ-B",
                 "engine": "local", "front": "F-MULTIWRITE"},
            ])
            # in-flight без pid у EQ-B (≤60с) — жив
            rc, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "EQ-B", self.state, readonly=False, toctou=True)
            self.assertEqual(rc, 13)
            _measure("MEASURE TOCTOU equal ts → refuse self")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


class TestParentSkip(WrapTemp):
    def test_parent_does_not_block_child(self):
        proc = _sleep_child(30)
        try:
            pid_path = os.path.join(self.state, "cursor-run-COL.pid")
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": "COL",
                "engine": "local", "front": "F-MULTIWRITE",
            }])
            prev = os.environ.get("ORCH_RUN_ID")
            os.environ["ORCH_RUN_ID"] = "COL"
            try:
                # env parent
                other = rex.find_live_dual_writer(
                    "F-MULTIWRITE", "CHILD", self.state)
                self.assertIsNone(other)
                rc, _ = rex.check_dual_writer_guard(
                    "F-MULTIWRITE", "CHILD", self.state, readonly=False)
                self.assertIsNone(rc)
            finally:
                if prev is None:
                    os.environ.pop("ORCH_RUN_ID", None)
                else:
                    os.environ["ORCH_RUN_ID"] = prev
            # journal start.parent (после journal_start child)
            _seed_journal(self.state, [{
                "ts": time.time() + 1, "kind": "start", "id": "CHILD",
                "engine": "local", "front": "F-MULTIWRITE", "parent": "COL",
            }])
            other2 = rex.find_live_dual_writer(
                "F-MULTIWRITE", "CHILD", self.state)
            self.assertIsNone(other2)
            _measure("MEASURE parent colonel does not block child")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_sibling_still_exit_13(self):
        proc = _sleep_child(30)
        try:
            pid_path = os.path.join(self.state, "cursor-run-SIB-A.pid")
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [
                {"ts": time.time(), "kind": "start", "id": "COL",
                 "engine": "local", "front": "F-MULTIWRITE"},
                {"ts": time.time(), "kind": "start", "id": "SIB-A",
                 "engine": "local", "front": "F-MULTIWRITE", "parent": "COL"},
            ])
            prev = os.environ.get("ORCH_RUN_ID")
            os.environ["ORCH_RUN_ID"] = "COL"
            try:
                rc, msg = rex.check_dual_writer_guard(
                    "F-MULTIWRITE", "SIB-B", self.state, readonly=False)
                self.assertEqual(rc, 13)
                self.assertIn("SIB-A", msg)
            finally:
                if prev is None:
                    os.environ.pop("ORCH_RUN_ID", None)
                else:
                    os.environ["ORCH_RUN_ID"] = prev
            _measure("MEASURE sibling → exit 13 (parent skipped)")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


# ---------------------------------------------------------------------------
# Live probes on ORCHESTRATION_DIR=tmp
# ---------------------------------------------------------------------------


class TestLiveProbes(WrapTemp):
    def _env(self, fake_bin):
        e = os.environ.copy()
        e["ORCHESTRATION_DIR"] = self.state
        e["PATH"] = fake_bin + os.pathsep + e.get("PATH", "")
        e.pop("ORCH_FRONT", None)
        return e

    def _prompt(self, name, text=None):
        p = os.path.join(self.state, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text or "роль: code/coder.md\n\nsmoke live\n")
        return p

    def test_live_dual_start_exit_13(self):
        fake = _fake_agent_dir(self.state)
        env = self._env(fake)
        sid = "session_%s" % uuid.uuid4()
        p1 = self._prompt("p1.md")
        # first detach writer
        r1 = subprocess.run(
            [sys.executable, RUN_EXEC, "--id", "LIVE-A", "--session", sid,
             "--front", "F-MULTIWRITE", "--prompt-file", p1,
             "--allow-unknown-role", "--detach", "--no-reground-line",
             "--yield-after", "0"],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(r1.returncode, 0, "r1=%s %s" % (r1.stdout, r1.stderr))
        time.sleep(0.4)
        p2 = self._prompt("p2.md")
        r2 = subprocess.run(
            [sys.executable, RUN_EXEC, "--id", "LIVE-B", "--session", sid,
             "--front", "F-MULTIWRITE", "--prompt-file", p2,
             "--allow-unknown-role", "--detach", "--no-reground-line",
             "--yield-after", "0"],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30)
        # cleanup A
        subprocess.run(
            [sys.executable, RUN_EXEC, "--kill", "LIVE-A", "--session", sid],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", timeout=15)
        self.assertEqual(r2.returncode, 13, "stderr=%s stdout=%s" % (
            r2.stderr, r2.stdout))
        self.assertIn("пишущий ран", r2.stderr)
        chips = [e for e in _read_journal(self.state)
                 if e.get("kind") == "chip"
                 and e.get("name") == "multi_write_front"]
        self.assertTrue(chips)
        # A2 in first prompt.run.md
        pr = os.path.join(self.state, "sessions", sid, "runs", "LIVE-A",
                          "prompt.run.md")
        if os.path.isfile(pr):
            with open(pr, "r", encoding="utf-8") as f:
                body = f.read()
            self.assertIn("## Владение (A2)", body)
        # ORCH_FRONT in child log
        log = os.path.join(self.state, "sessions", sid, "runs", "LIVE-A",
                           "run.log")
        if os.path.isfile(log):
            with open(log, "r", encoding="utf-8", errors="replace") as f:
                lt = f.read()
            self.assertIn("ORCH_FRONT=F-MULTIWRITE", lt)
        _measure("MEASURE live dual-start EXIT=13 + chip + A2 + ORCH_FRONT")

    def test_live_kill_detach(self):
        fake = _fake_agent_dir(self.state)
        env = self._env(fake)
        sid = "session_%s" % uuid.uuid4()
        p = self._prompt("pk.md")
        r1 = subprocess.run(
            [sys.executable, RUN_EXEC, "--id", "KILL-ME", "--session", sid,
             "--front", "F-MULTIWRITE", "--prompt-file", p,
             "--allow-unknown-role", "--detach", "--no-reground-line"],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        time.sleep(0.3)
        pid_path = os.path.join(self.state, "sessions", sid, "runs", "KILL-ME",
                                "run.pid")
        self.assertTrue(os.path.isfile(pid_path))
        pid = rex.read_pid_file(pid_path)
        self.assertTrue(pid and rex.pid_alive(pid))
        rk = subprocess.run(
            [sys.executable, RUN_EXEC, "--kill", "KILL-ME", "--session", sid],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=15)
        self.assertEqual(rk.returncode, 0, rk.stderr)
        time.sleep(0.3)
        self.assertFalse(rex.pid_alive(pid))
        ts = os.path.join(self.state, "sessions", sid, "runs", "KILL-ME",
                          "TOMBSTONE")
        self.assertTrue(os.path.isfile(ts))
        # tombstone blocks retry
        log = os.path.join(self.state, "sessions", sid, "runs", "KILL-ME",
                           "run.log")
        code = rex.apply_retries(
            "4", log, pid_path, 5, time.time() + 30,
            "/dev/null", 1, "auto", [],
            state=self.state, run_id="KILL-ME", session=sid)
        self.assertEqual(str(code), "4")
        with open(log, "r", encoding="utf-8", errors="replace") as f:
            self.assertIn("TOMBSTONE: retry отменён", f.read())
        _measure("MEASURE live --kill detach → dead + tombstone blocks retry")


class TestCloudA2Helper(WrapTemp):
    def test_cloud_inject_delegates(self):
        prompt = "роль: code/coder.md\n\nx\n"
        out = rcl.inject_a2_ownership(prompt, "F-MULTIWRITE",
                                       state_dir=self.state)
        self.assertIn("## Владение (A2)", out)
        _measure("MEASURE run-cloud A2 helper delegates")


if __name__ == "__main__":
    # Ensure we never touch live state
    _assert_not_live("/tmp")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
