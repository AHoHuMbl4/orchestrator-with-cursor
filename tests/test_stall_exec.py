#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STALL dual-timer A3: wait_child / resolve_timers (stdlib, без живого state).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_stall_exec.py
State: только ORCHESTRATION_DIR=/tmp/stall-exec-…; /root/.orchestration не трогаем.

Покрытие:
  (а) тишина лога → STALL 124 + маркер «STALL:»
  (б) рост лога сбрасывает stall-таймер (не stalled)
  (в) --max-wall → WALL 125 + «WALL:», отличим от STALL
  (г) --timeout = stall (compat)
  (д) приоритеты: stall_s > timeout_s > builtin 600 на моке params
"""
from __future__ import print_function

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
LIVE_STATE = "/root/.orchestration"

# ---------------------------------------------------------------------------
# Load bin/run-exec.py as module (hyphenated filename)
# ---------------------------------------------------------------------------

def _load_run_exec():
    path = os.path.join(BIN, "run-exec.py")
    spec = importlib.util.spec_from_file_location("run_exec_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    # bin/ on path for orchlib import inside run-exec
    if BIN not in sys.path:
        sys.path.insert(0, BIN)
    spec.loader.exec_module(mod)
    return mod


rex = _load_run_exec()


def _pass(msg):
    print("PASS: %s" % msg)


def _fail(msg):
    print("FAIL: %s" % msg, file=sys.stderr)
    raise AssertionError(msg)


def _mk_poly(label="stall"):
    """Изолированный state в /tmp; session_UUID с дефисами."""
    root = tempfile.mkdtemp(prefix="stall-exec-", dir="/tmp")
    state = os.path.join(root, "state")
    sid = "session_%s" % uuid.uuid4()
    run_dir = os.path.join(state, "sessions", sid, "runs", "STALL-TEST-%s" % label)
    os.makedirs(run_dir, exist_ok=True)
    os.environ["ORCHESTRATION_DIR"] = state
    return root, state, sid, run_dir


def _assert_not_live(path):
    live = os.path.realpath(LIVE_STATE)
    cur = os.path.realpath(path)
    if cur == live or cur.startswith(live + os.sep):
        _fail("poly touches live state: %s" % path)


def _sleep_proc(seconds):
    """Долгий child в своей session — kill_tree через killpg."""
    return subprocess.Popen(
        [sys.executable, "-c",
         "import time; time.sleep(%d)" % int(seconds)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _open_log(path, initial="boot\n"):
    with open(path, "w", encoding="utf-8") as f:
        f.write(initial)
    return open(path, "a", encoding="utf-8")


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# (д) resolve_timers priorities on mock params
# ---------------------------------------------------------------------------

def test_d_resolve_timers_priorities():
    """stall_s > timeout_s > builtin 600; CLI > params; timeout_s=stall (compat)."""
    # builtin
    s, w = rex.resolve_timers({})
    if s != 600 or w != 86400:
        _fail("builtin: got stall=%s wall=%s want 600/86400" % (s, w))

    # timeout_s alone → stall (документированный переход, НЕ баг)
    s, _ = rex.resolve_timers({"execution": {"timeout_s": 42}})
    if s != 42:
        _fail("timeout_s alone → stall: got %s want 42" % s)

    # stall_s > timeout_s
    s, _ = rex.resolve_timers(
        {"execution": {"stall_s": 11, "timeout_s": 99}})
    if s != 11:
        _fail("stall_s > timeout_s: got %s want 11" % s)

    # --timeout CLI > params.stall_s
    s, _ = rex.resolve_timers(
        {"execution": {"stall_s": 11, "timeout_s": 99}}, timeout=7)
    if s != 7:
        _fail("--timeout > params: got %s want 7" % s)

    # --stall-after > --timeout > params
    s, _ = rex.resolve_timers(
        {"execution": {"stall_s": 11, "timeout_s": 99}},
        stall_after=3, timeout=7)
    if s != 3:
        _fail("--stall-after wins: got %s want 3" % s)

    # max_wall: CLI > params > builtin
    _, w = rex.resolve_timers({"execution": {"max_wall_s": 100}})
    if w != 100:
        _fail("params max_wall_s: got %s want 100" % w)
    _, w = rex.resolve_timers(
        {"execution": {"max_wall_s": 100}}, max_wall=55)
    if w != 55:
        _fail("--max-wall wins: got %s want 55" % w)

    _pass("(д) resolve_timers: stall_s > timeout_s > 600; CLI; max_wall")


# ---------------------------------------------------------------------------
# (а) silence → STALL
# ---------------------------------------------------------------------------

def test_a_stall_on_silence():
    root, state, sid, run_dir = _mk_poly("a")
    _assert_not_live(state)
    log_path = os.path.join(run_dir, "run.log")
    stall_s = 5
    try:
        log_fh = _open_log(log_path)
        # якорь stall = mtime лога; чуть подождём чтобы mtime «устарел» не сейчас
        time.sleep(0.2)
        proc = _sleep_proc(120)
        t0 = time.time()
        code = rex.wait_child(
            proc, log_fh, log_path, stall_s=stall_s, max_wall_s=600,
            yield_after=0)
        elapsed = time.time() - t0
        body = _read(log_path)
        if code != "124":
            _fail("(а) code=%s want 124; log=%r" % (code, body[-200:]))
        if "STALL:" not in body:
            _fail("(а) missing STALL: marker in log")
        if "WALL:" in body:
            _fail("(а) unexpected WALL: in stall path")
        if elapsed < stall_s - 1 or elapsed > stall_s + 8:
            _fail("(а) elapsed=%.1fs outside stall window ~%ss" % (elapsed, stall_s))
        # kill_tree асинхронен — дождёмся смерти child (не критерий приёмки, гигиена)
        for _ in range(20):
            if proc.poll() is not None:
                break
            time.sleep(0.1)
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except Exception:
                proc.kill()
        _pass("(а) silence → STALL EXIT=124 marker STALL: (%.1fs, sid=%s)"
              % (elapsed, sid))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (б) log growth resets stall
# ---------------------------------------------------------------------------

def test_b_growth_resets_stall():
    root, state, sid, run_dir = _mk_poly("b")
    _assert_not_live(state)
    log_path = os.path.join(run_dir, "run.log")
    stall_s = 5
    stop = threading.Event()

    def _grow():
        n = 0
        while not stop.wait(2.0):
            n += 1
            with open(log_path, "a", encoding="utf-8") as f:
                f.write("tick-%d\n" % n)
                f.flush()
                os.fsync(f.fileno())

    try:
        log_fh = _open_log(log_path)
        time.sleep(0.2)
        # child живёт дольше 2×stall — без роста ушёл бы в 124
        proc = _sleep_proc(14)
        grower = threading.Thread(target=_grow, daemon=True)
        grower.start()
        t0 = time.time()
        code = rex.wait_child(
            proc, log_fh, log_path, stall_s=stall_s, max_wall_s=600,
            yield_after=0)
        elapsed = time.time() - t0
        stop.set()
        grower.join(timeout=3)
        body = _read(log_path)
        if code == "124":
            _fail("(б) stalled despite growth; log=%r" % body[-300:])
        if "STALL:" in body:
            _fail("(б) STALL: marker present despite growth")
        if elapsed < 10:
            _fail("(б) finished too early (%.1fs) — expected ~14s sleep" % elapsed)
        _pass("(б) log growth resets stall (code=%s, %.1fs, sid=%s)"
              % (code, elapsed, sid))
    finally:
        stop.set()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (в) max-wall → WALL, distinguishable from STALL
# ---------------------------------------------------------------------------

def test_c_max_wall():
    root, state, sid, run_dir = _mk_poly("c")
    _assert_not_live(state)
    log_path = os.path.join(run_dir, "run.log")
    max_wall_s = 5
    try:
        log_fh = _open_log(log_path)
        time.sleep(0.2)
        proc = _sleep_proc(120)
        # stall огромный — сработает только wall fuse
        t0 = time.time()
        code = rex.wait_child(
            proc, log_fh, log_path, stall_s=600, max_wall_s=max_wall_s,
            yield_after=0)
        elapsed = time.time() - t0
        body = _read(log_path)
        if code != "125":
            _fail("(в) code=%s want 125; log=%r" % (code, body[-200:]))
        if "WALL:" not in body:
            _fail("(в) missing WALL: marker")
        if "STALL:" in body:
            _fail("(в) STALL: must not appear on WALL path")
        if elapsed < max_wall_s - 1 or elapsed > max_wall_s + 8:
            _fail("(в) elapsed=%.1fs outside wall window ~%ss" % (elapsed, max_wall_s))
        _pass("(в) --max-wall → WALL EXIT=125 marker WALL: (%.1fs, sid=%s)"
              % (elapsed, sid))
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (г) --timeout compat = stall semantics
# ---------------------------------------------------------------------------

def test_g_timeout_compat_as_stall():
    """Старый вызов с --timeout → resolve_timers даёт stall_s; wait_child STALL."""
    root, state, sid, run_dir = _mk_poly("g")
    _assert_not_live(state)
    log_path = os.path.join(run_dir, "run.log")
    # как CLI: --timeout 5 без --stall-after
    stall_s, max_wall_s = rex.resolve_timers({}, timeout=5, max_wall=600)
    if stall_s != 5:
        _fail("(г) resolve_timers(timeout=5) stall_s=%s want 5" % stall_s)
    try:
        log_fh = _open_log(log_path)
        time.sleep(0.2)
        proc = _sleep_proc(120)
        code = rex.wait_child(
            proc, log_fh, log_path, stall_s=stall_s, max_wall_s=max_wall_s,
            yield_after=0)
        body = _read(log_path)
        if code != "124":
            _fail("(г) code=%s want 124 (timeout→stall); log=%r" % (code, body[-200:]))
        if "STALL:" not in body:
            _fail("(г) missing STALL: for --timeout compat")
        if "WALL:" in body:
            _fail("(г) unexpected WALL: on timeout-as-stall path")
        _pass("(г) --timeout compat → STALL 124 (sid=%s)" % sid)
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# session id format smoke
# ---------------------------------------------------------------------------

def test_session_uuid_format():
    root, state, sid, run_dir = _mk_poly("fmt")
    try:
        _assert_not_live(state)
        if not sid.startswith("session_"):
            _fail("sid must start with session_: %s" % sid)
        rest = sid[len("session_"):]
        if "_" in rest:
            _fail("uuid part must use hyphens not underscores: %s" % sid)
        if rest.count("-") != 4:
            _fail("uuid must have 4 hyphens: %s" % sid)
        if LIVE_STATE in os.path.realpath(run_dir):
            _fail("run_dir under live state")
        _pass("session format session_<uuid-with-hyphens> ok (%s)" % sid)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [
        test_session_uuid_format,
        test_d_resolve_timers_priorities,
        test_a_stall_on_silence,
        test_b_growth_resets_stall,
        test_c_max_wall,
        test_g_timeout_compat_as_stall,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print("FAIL: %s: %s" % (fn.__name__, e), file=sys.stderr)
    if failed:
        print("RESULT: %d/%d FAILED" % (failed, len(tests)))
        return 1
    print("RESULT: %d/%d PASSED" % (len(tests), len(tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
