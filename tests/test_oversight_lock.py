#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Надзор (prosecutor/observer) не берёт dual-writer write-lock.

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_oversight_lock.py
State: только ORCHESTRATION_DIR=/tmp/ovlock-…; /root/.orchestration не трогаем.
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
LIVE_STATE = "/root/.orchestration"

if BIN not in sys.path:
    sys.path.insert(0, BIN)


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rex = _load_mod("run_exec_ovlock", RUN_EXEC)
import orchlib  # noqa: E402

PROSECUTOR = "meta/front-prosecutor.md"
OBSERVER = "meta/front-observer.md"
CODER = "code/coder.md"


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


def _mk_state(prefix="ovlock"):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "oversight-lock",
        "fronts": [{
            "id": "F-MULTIWRITE",
            "title": "mw",
            "status": "active",
            "owns": ["bin/run-exec.py", "bin/orchlib.py",
                     "tests/test_oversight_lock.py"],
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


def _sleep_child(seconds=30):
    return subprocess.Popen(
        [sys.executable, "-c",
         "import time; time.sleep(%d)" % int(seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _fake_oversight_agent_dir(state):
    """PATH-stub: пишет артефакты надзора по роли из stdin; result success."""
    bindir = os.path.join(state, "fake-bin")
    os.makedirs(bindir, exist_ok=True)
    agent = os.path.join(bindir, "cursor-agent")
    with open(agent, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("set -e\n")
        f.write("prompt=$(cat)\n")
        f.write("head3=$(printf '%s\\n' \"$prompt\" | head -n 3)\n")
        f.write("STATE=${ORCHESTRATION_DIR}\n")
        f.write("SID=${ORCH_SESSION:-}\n")
        f.write("FRONT=${ORCH_FRONT:-}\n")
        f.write("if printf '%s\\n' \"$head3\" | grep -q 'meta/front-prosecutor.md'; then\n")
        f.write("  mkdir -p \"$STATE/sessions/$SID/prosecutor\"\n")
        f.write("  echo prosecutor-ok > \"$STATE/sessions/$SID/prosecutor/report.md\"\n")
        f.write("elif printf '%s\\n' \"$head3\" | grep -q 'meta/front-observer.md'; then\n")
        f.write("  mkdir -p \"$STATE/fronts/$FRONT\"\n")
        f.write("  echo hb > \"$STATE/fronts/$FRONT/observer-heartbeat.txt\"\n")
        f.write("fi\n")
        f.write("echo '{\"type\":\"result\",\"subtype\":\"success\"}'\n")
        f.write("exit 0\n")
    os.chmod(agent, 0o755)
    return bindir


def _fake_coder_sleep_dir(state):
    bindir = os.path.join(state, "fake-bin-sleep")
    os.makedirs(bindir, exist_ok=True)
    agent = os.path.join(bindir, "cursor-agent")
    with open(agent, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("echo FAKE_AGENT_START\n")
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


class TestRoleIsOversight(WrapTemp):
    def test_exact_and_short_negative(self):
        self.assertTrue(orchlib.role_is_oversight(PROSECUTOR))
        self.assertTrue(orchlib.role_is_oversight(OBSERVER))
        self.assertTrue(orchlib.role_is_oversight(
            "/kit/skills/orchestration/references/roles/" + PROSECUTOR))
        self.assertFalse(orchlib.role_is_oversight("prosecutor"))
        self.assertFalse(orchlib.role_is_oversight("observer"))
        self.assertFalse(orchlib.role_is_oversight(CODER))
        self.assertFalse(orchlib.role_is_oversight(None))
        _measure("MEASURE role_is_oversight exact CLI paths; short names False")


class TestGuardSkip(WrapTemp):
    def test_guard_none_for_oversight_roles_with_live_coder(self):
        proc = _sleep_child(30)
        try:
            rid_a = "W-CODER"
            pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid_a)
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": rid_a,
                "engine": "local", "front": "F-MULTIWRITE", "role": CODER,
            }])
            for role in (PROSECUTOR, OBSERVER):
                rc, msg = rex.check_dual_writer_guard(
                    "F-MULTIWRITE", "OV-1", self.state, readonly=False,
                    role=role)
                self.assertIsNone(rc, msg)
                self.assertIsNone(msg)
            rc13, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "W-B", self.state, readonly=False,
                role=CODER)
            self.assertEqual(rc13, 13)
            writers = rex._journal_open_writers("F-MULTIWRITE")
            self.assertIn(rid_a, writers)
            _measure("MEASURE guard(role=oversight) None; coder still 13")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_oversight_start_not_open_writer(self):
        proc = _sleep_child(30)
        try:
            pid_path = os.path.join(self.state, "cursor-run-OV-P.pid")
            rex.write_pid_file(pid_path, proc.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": "OV-P",
                "engine": "local", "front": "F-MULTIWRITE",
                "role": PROSECUTOR,
            }])
            writers = rex._journal_open_writers("F-MULTIWRITE")
            self.assertNotIn("OV-P", writers)
            rc, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "W-NEW", self.state, readonly=False,
                role=CODER)
            self.assertIsNone(rc)
            _measure("MEASURE oversight journal start skipped as writer")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)


class TestLiveOversightWrap(WrapTemp):
    def _env(self, fake_bin, sid):
        e = os.environ.copy()
        e["ORCHESTRATION_DIR"] = self.state
        e["PATH"] = fake_bin + os.pathsep + e.get("PATH", "")
        e["ORCH_SESSION"] = sid
        e.pop("ORCH_FRONT", None)
        return e

    def _prompt(self, name, role):
        p = os.path.join(self.state, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write("роль: %s\n\nsmoke oversight\n" % role)
        return p

    def _seed_live_coder(self):
        proc = _sleep_child(40)
        rid = "W-CODER"
        pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
        rex.write_pid_file(pid_path, proc.pid)
        _seed_journal(self.state, [{
            "ts": time.time(), "kind": "start", "id": rid,
            "engine": "local", "front": "F-MULTIWRITE", "role": CODER,
        }])
        return proc, rid, pid_path

    def _run_ov(self, env, sid, run_id, role, extra=None):
        pf = self._prompt("p-%s.md" % run_id, role)
        cmd = [sys.executable, RUN_EXEC, "--id", run_id, "--session", sid,
               "--role", role, "--prompt-file", pf,
               "--no-reground-line", "--yield-after", "0"]
        if extra is None:
            cmd.extend(["--front", "F-MULTIWRITE"])
        else:
            cmd.extend(extra)
        return subprocess.run(
            cmd, cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=45)

    def _assert_writer_untouched(self, proc, rid, pid_path):
        self.assertTrue(rex.pid_alive(proc.pid))
        self.assertTrue(rex.run_pid_is_alive(pid_path))
        ts = rex.resolve_tombstone_path(self.state, rid, None)
        self.assertFalse(os.path.isfile(ts), ts)

    def test_a_prosecutor_live_writer_exit_0_and_report(self):
        fake = _fake_oversight_agent_dir(self.state)
        sid = "session_%s" % uuid.uuid4()
        env = self._env(fake, sid)
        proc, rid, pid_path = self._seed_live_coder()
        try:
            rc_g, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "OV-P", self.state, readonly=False,
                role=PROSECUTOR)
            self.assertIsNone(rc_g)
            r = self._run_ov(env, sid, "OV-P", PROSECUTOR)
            self.assertNotIn(r.returncode, (9, 13),
                             "stderr=%s stdout=%s" % (r.stderr, r.stdout))
            self.assertEqual(r.returncode, 0,
                             "stderr=%s stdout=%s" % (r.stderr, r.stdout))
            report = os.path.join(self.state, "sessions", sid, "prosecutor",
                                  "report.md")
            self.assertTrue(os.path.isfile(report), report)
            fronts_dir = os.path.join(self.state, "fronts", "F-MULTIWRITE")
            if os.path.isdir(fronts_dir):
                self.assertEqual(os.listdir(fronts_dir), [])
            self._assert_writer_untouched(proc, rid, pid_path)
            _measure("MEASURE (а)(б-прок) prosecutor exit 0 + report, no fronts/")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_a_observer_live_writer_exit_0_and_heartbeat(self):
        fake = _fake_oversight_agent_dir(self.state)
        sid = "session_%s" % uuid.uuid4()
        env = self._env(fake, sid)
        proc, rid, pid_path = self._seed_live_coder()
        try:
            rc_g, _ = rex.check_dual_writer_guard(
                "F-MULTIWRITE", "OV-O", self.state, readonly=False,
                role=OBSERVER)
            self.assertIsNone(rc_g)
            r = self._run_ov(env, sid, "OV-O", OBSERVER)
            self.assertNotIn(r.returncode, (9, 13),
                             "stderr=%s stdout=%s" % (r.stderr, r.stdout))
            self.assertEqual(r.returncode, 0,
                             "stderr=%s stdout=%s" % (r.stderr, r.stdout))
            hb = os.path.join(self.state, "fronts", "F-MULTIWRITE",
                              "observer-heartbeat.txt")
            self.assertTrue(os.path.isfile(hb), hb)
            self._assert_writer_untouched(proc, rid, pid_path)
            _measure("MEASURE (а)(б-набл) observer exit 0 + heartbeat")
        finally:
            proc.send_signal(signal.SIGKILL)
            proc.wait(timeout=5)

    def test_v_second_coder_writer_exit_13(self):
        fake = _fake_coder_sleep_dir(self.state)
        sid = "session_%s" % uuid.uuid4()
        env = self._env(fake, sid)
        p1 = self._prompt("p1.md", CODER)
        r1 = subprocess.run(
            [sys.executable, RUN_EXEC, "--id", "LIVE-A", "--session", sid,
             "--front", "F-MULTIWRITE", "--prompt-file", p1,
             "--role", CODER, "--detach", "--no-reground-line",
             "--yield-after", "0"],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30)
        self.assertEqual(r1.returncode, 0, "r1=%s %s" % (r1.stdout, r1.stderr))
        time.sleep(0.4)
        p2 = self._prompt("p2.md", CODER)
        r2 = subprocess.run(
            [sys.executable, RUN_EXEC, "--id", "LIVE-B", "--session", sid,
             "--front", "F-MULTIWRITE", "--prompt-file", p2,
             "--role", CODER, "--detach", "--no-reground-line",
             "--yield-after", "0"],
            cwd=REPO, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=30)
        subprocess.run(
            [sys.executable, RUN_EXEC, "--kill", "LIVE-A", "--session", sid],
            cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", timeout=15)
        self.assertEqual(r2.returncode, 13, "stderr=%s stdout=%s" % (
            r2.stderr, r2.stdout))
        _measure("MEASURE (в) second coder writer → 13")

    def test_g_front_required_no_exemption(self):
        fake = _fake_oversight_agent_dir(self.state)
        sid = "session_%s" % uuid.uuid4()
        env = self._env(fake, sid)
        for role in (PROSECUTOR, OBSERVER):
            r_miss = self._run_ov(env, sid, "NF1-%s" % role.split("/")[-1],
                                  role, extra=[])
            self.assertEqual(
                r_miss.returncode, 8,
                "missing front role=%s stderr=%s" % (role, r_miss.stderr))
            r_nf = self._run_ov(env, sid, "NF2-%s" % role.split("/")[-1],
                                role, extra=["--no-front", "x"])
            self.assertEqual(
                r_nf.returncode, 8,
                "no-front role=%s stderr=%s" % (role, r_nf.stderr))
        _measure("MEASURE (г) oversight without --front and --no-front x → 8")


if __name__ == "__main__":
    _assert_not_live("/tmp")
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
