#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""B4 stall matrix for bin/run-cloud.py --wait (tmp poly + mock HTTP API).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_stall_cloud.py
State: только ORCHESTRATION_DIR=/tmp/stall-cloud-…; /root/.orchestration не трогаем.
Мок: 127.0.0.1 bind port 0 (≠8765); CURSOR_API_BASE=http://127.0.0.1:<port>.

Покрытие (а)–(к) — см. STALL-C2 order / C2-TEST prompt.
"""
from __future__ import print_function

import argparse
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import uuid
import io
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
LIVE_STATE = "/root/.orchestration"
FORBIDDEN_PORT = 8765


# ---------------------------------------------------------------------------
# Load bin/run-cloud.py as module
# ---------------------------------------------------------------------------

def _load_run_cloud():
    path = os.path.join(BIN, "run-cloud.py")
    spec = importlib.util.spec_from_file_location("run_cloud_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    if BIN not in sys.path:
        sys.path.insert(0, BIN)
    spec.loader.exec_module(mod)
    return mod


rcl = _load_run_cloud()


def _pass(msg):
    print("PASS: %s" % msg)


def _fail(msg):
    print("FAIL: %s" % msg, file=sys.stderr)
    raise AssertionError(msg)


def _assert_not_live(path):
    live = os.path.realpath(LIVE_STATE)
    cur = os.path.realpath(path)
    if cur == live or cur.startswith(live + os.sep):
        _fail("poly touches live state: %s" % path)


def _read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


# ---------------------------------------------------------------------------
# Mock Cloud Agents API
# ---------------------------------------------------------------------------

class MockAPI(object):
    """Stateful mock for GET run/stream/artifacts + POST cancel."""

    def __init__(self):
        self.lock = threading.Lock()
        self.agent = "bc_stall_agent"
        self.run = "run_stall_1"
        self.status = "RUNNING"
        self.updated_at = "2020-01-01T00:00:00.000Z"
        self.artifacts = [{"path": "out.txt", "updatedAt": "t0"}]
        self.poll_n = 0
        self.cancel_calls = []
        self.cancel_status = 200  # 200 ok / 500 fail
        self.sse_mode = "off"  # off|silent|heartbeat|progress
        self.sse_interval = 1.5
        self.force_429_remaining = 0
        self.retry_after = 2.0
        self.finish_after_polls = None
        self.bump_updated_every = None  # int polls between bumps
        self.bump_artifacts_every = None
        self.stop = threading.Event()
        self.httpd = None
        self.thread = None
        self.port = None
        self.base = None

    def start(self):
        mock = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, fmt, *args):
                return

            def _json(self, code, obj, extra_headers=None):
                body = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if extra_headers:
                    for k, v in extra_headers.items():
                        self.send_header(k, v)
                self.end_headers()
                self.wfile.write(body)

            def _path(self):
                return urlparse(self.path).path

            def do_GET(self):
                p = self._path()
                # /v1/agents/{agent}/runs/{run}/stream
                if p.endswith("/stream"):
                    self._handle_stream()
                    return
                # /v1/agents/{agent}/runs/{run}
                if ("/runs/" in p) and (not p.endswith("/artifacts")):
                    self._handle_run()
                    return
                # /v1/agents/{agent}/artifacts
                if p.endswith("/artifacts"):
                    with mock.lock:
                        arts = {"artifacts": list(mock.artifacts)}
                    self._json(200, arts)
                    return
                self._json(404, {"error": "not found"})

            def do_POST(self):
                p = self._path()
                if p.endswith("/cancel"):
                    with mock.lock:
                        mock.cancel_calls.append(p)
                        st = mock.cancel_status
                    if st >= 400:
                        self._json(st, {"error": "cancel fail"})
                    else:
                        self._json(200, {"status": "CANCELLED"})
                    return
                self._json(404, {"error": "not found"})

            def _handle_run(self):
                with mock.lock:
                    mock.poll_n += 1
                    n = mock.poll_n
                    if mock.force_429_remaining > 0:
                        mock.force_429_remaining -= 1
                        ra = mock.retry_after
                        self._json(
                            429, {"error": "rate"},
                            extra_headers={"Retry-After": str(ra)})
                        return
                    if mock.bump_updated_every and n > 1:
                        if (n % mock.bump_updated_every) == 0:
                            mock.updated_at = "2020-01-01T00:00:%02d.000Z" % (
                                min(59, n),)
                    if mock.bump_artifacts_every and n > 1:
                        if (n % mock.bump_artifacts_every) == 0:
                            mock.artifacts = [{
                                "path": "out.txt",
                                "updatedAt": "t%d" % n,
                            }]
                    if (mock.finish_after_polls is not None
                            and n >= mock.finish_after_polls):
                        mock.status = "FINISHED"
                    payload = {
                        "id": mock.run,
                        "status": mock.status,
                        "updatedAt": mock.updated_at,
                    }
                self._json(200, payload)

            def _handle_stream(self):
                if mock.sse_mode == "off":
                    self._json(404, {"error": "sse unavailable"})
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                # keep connection; emit per sse_mode
                try:
                    while not mock.stop.is_set():
                        if mock.sse_mode == "heartbeat":
                            self.wfile.write(b": keepalive\n\n")
                            self.wfile.flush()
                        elif mock.sse_mode == "progress":
                            self.wfile.write(
                                b"event: assistant\ndata: {\"t\":1}\n\n")
                            self.wfile.flush()
                        elif mock.sse_mode == "silent":
                            pass
                        if mock.stop.wait(mock.sse_interval):
                            break
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        class QuietServer(ThreadingHTTPServer):
            def handle_error(self, request, client_address):
                err = sys.exc_info()[1]
                if isinstance(err, (ConnectionResetError, BrokenPipeError, OSError)):
                    return
                ThreadingHTTPServer.handle_error(self, request, client_address)

        # bind port 0 → OS picks free port; never 8765
        httpd = QuietServer(("127.0.0.1", 0), Handler)
        port = httpd.server_address[1]
        if port == FORBIDDEN_PORT:
            httpd.server_close()
            _fail("mock bound forbidden port %s" % FORBIDDEN_PORT)
        self.httpd = httpd
        self.port = port
        self.base = "http://127.0.0.1:%s" % port
        self.thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def stop_server(self):
        self.stop.set()
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
            except Exception:
                pass
            try:
                self.httpd.server_close()
            except Exception:
                pass
        if self.thread is not None:
            self.thread.join(timeout=3)


def _mk_poly(label="stall"):
    """Изолированный state в /tmp; session_UUID с дефисами; cursor.key stub."""
    root = tempfile.mkdtemp(prefix="stall-cloud-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    sid = "session_%s" % uuid.uuid4()
    run_id = "STALL-C-%s" % label
    run_dir = os.path.join(state, "sessions", sid, "runs", run_id)
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(state, "cursor.key"), "w", encoding="utf-8") as f:
        f.write("test-key-ok\n")
    with open(os.path.join(state, "params.json"), "w", encoding="utf-8") as f:
        f.write("{}\n")
    os.environ["ORCHESTRATION_DIR"] = state
    _assert_not_live(state)
    return root, state, sid, run_id, run_dir


def _args(run_id, sid, stall_after=None, timeout=None, max_wall=600,
          poll=1, http_timeout=30.0):
    return argparse.Namespace(
        id=run_id,
        session=sid,
        stall_after=stall_after,
        timeout=timeout,
        max_wall=max_wall,
        poll=poll,
        http_timeout=http_timeout,
        api_key="test-key-ok",
    )


def _log_path(state, run_id, sid):
    return rcl.cloud_log_path(state, run_id, sid)


def _run_wait(mock, state, sid, run_id, **kw):
    """CURSOR_API_BASE → mock; call wait_and_report; return (rc, log_body)."""
    prev_base = os.environ.get("CURSOR_API_BASE")
    os.environ["CURSOR_API_BASE"] = mock.base
    # sanity: never assert prod URL in path
    if "api.cursor.com" in mock.base:
        _fail("mock base must not be prod URL")
    if rcl.api_base() != mock.base.rstrip("/"):
        _fail("api_base mismatch: %s vs %s" % (rcl.api_base(), mock.base))
    a = _args(run_id, sid, **kw)
    # wait_and_report печатает JSON статуса в stdout — глушим для чистых PASS
    old_out, old_err = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        rc = rcl.wait_and_report(mock.agent, mock.run, "test-key-ok", state, a)
    finally:
        sys.stdout, sys.stderr = old_out, old_err
        mock.stop_server()
        if prev_base is None:
            os.environ.pop("CURSOR_API_BASE", None)
        else:
            os.environ["CURSOR_API_BASE"] = prev_base
    body = _read(_log_path(state, run_id, sid))
    return rc, body


# ---------------------------------------------------------------------------
# (а) 429 + Retry-After does not increment stall
# ---------------------------------------------------------------------------

def test_a_429_retry_after_not_stall():
    """429 Retry-After > stall_s; freeze → continue → FINISHED, not 124."""
    root, state, sid, run_id, _rd = _mk_poly("a")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "off"
        mock.force_429_remaining = 1
        mock.retry_after = 4.0  # > stall_s
        mock.finish_after_polls = 2  # after 429, next poll finishes
        stall_s = 3
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc == 124:
            _fail("(а) STALL 124 despite 429 freeze; log=%r" % body[-400:])
        if "STALL:" in body:
            _fail("(а) unexpected STALL: marker")
        if "429 backoff" not in body:
            _fail("(а) missing 429 backoff log")
        if rc != 0:
            _fail("(а) rc=%s want 0 (FINISHED after backoff); log=%r"
                  % (rc, body[-300:]))
        if elapsed < 3.5:
            _fail("(а) elapsed=%.1fs too short for Retry-After=4" % elapsed)
        _pass("(а) 429 Retry-After∉stall → continue FINISHED "
              "(rc=%s, %.1fs, sid=%s)" % (rc, elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (б) poll-only growth does not reset stall
# ---------------------------------------------------------------------------

def test_b_poll_lines_do_not_reset():
    root, state, sid, run_id, _rd = _mk_poly("b")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "silent"  # open SSE, no applied events
        mock.status = "RUNNING"
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc != 124:
            _fail("(б) rc=%s want 124; log=%r" % (rc, body[-400:]))
        if "STALL:" not in body:
            _fail("(б) missing STALL:")
        if "poll:" not in body:
            _fail("(б) expected poll: lines in cloud log")
        if "WALL:" in body:
            _fail("(б) unexpected WALL:")
        if elapsed < stall_s - 1 or elapsed > stall_s + 10:
            _fail("(б) elapsed=%.1fs outside stall ~%ss" % (elapsed, stall_s))
        _pass("(б) poll-only ≠ reset → STALL 124 (%.1fs, sid=%s)"
              % (elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (в) applied SSE resets stall
# ---------------------------------------------------------------------------

def test_v_sse_progress_resets():
    root, state, sid, run_id, _rd = _mk_poly("v")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "progress"
        mock.sse_interval = 1.5
        mock.finish_after_polls = 8  # ~8s with poll=1 > stall_s if no reset
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc == 124 or "STALL:" in body:
            _fail("(в) stalled despite SSE progress; rc=%s log=%r"
                  % (rc, body[-400:]))
        if rc != 0:
            _fail("(в) rc=%s want 0; log=%r" % (rc, body[-300:]))
        if elapsed < stall_s:
            _fail("(в) finished too early (%.1fs) — need >stall to prove reset"
                  % elapsed)
        _pass("(в) SSE assistant resets stall (rc=%s, %.1fs, sid=%s)"
              % (rc, elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (г) heartbeat/keepalive does NOT reset stall
# ---------------------------------------------------------------------------

def test_g_heartbeat_no_reset():
    root, state, sid, run_id, _rd = _mk_poly("g")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "heartbeat"
        mock.sse_interval = 0.8
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc != 124:
            _fail("(г) rc=%s want 124 (heartbeat≠progress); log=%r"
                  % (rc, body[-400:]))
        if "STALL:" not in body:
            _fail("(г) missing STALL:")
        _pass("(г) heartbeat/keepalive ≠ reset → STALL 124 (%.1fs, sid=%s)"
              % (elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (д) --max-wall → WALL 125
# ---------------------------------------------------------------------------

def test_d_max_wall():
    root, state, sid, run_id, _rd = _mk_poly("d")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "progress"  # progress keeps stall alive; wall fuse wins
        mock.sse_interval = 1.0
        max_wall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=600, max_wall=max_wall_s, poll=1)
        elapsed = time.time() - t0
        if rc != 125:
            _fail("(д) rc=%s want 125; log=%r" % (rc, body[-400:]))
        if "WALL:" not in body:
            _fail("(д) missing WALL:")
        if "STALL:" in body:
            _fail("(д) STALL: must not appear on WALL path")
        if elapsed < max_wall_s - 1 or elapsed > max_wall_s + 8:
            _fail("(д) elapsed=%.1fs outside wall ~%ss" % (elapsed, max_wall_s))
        _pass("(д) --max-wall → WALL EXIT=125 (%.1fs, sid=%s)"
              % (elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (е) SSE off + updatedAt delta resets stall
# ---------------------------------------------------------------------------

def test_e_updated_at_fallback():
    root, state, sid, run_id, _rd = _mk_poly("e")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "off"
        mock.bump_updated_every = 1  # every poll after first bumps updatedAt
        mock.finish_after_polls = 8
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if "sse unavailable" not in body and "fallback" not in body:
            # open_sse 404 → sse unavailable log
            if "sse unavailable" not in body:
                _fail("(е) expected sse unavailable fallback log; got=%r"
                      % body[:400])
        if rc == 124 or "STALL:" in body:
            _fail("(е) stalled despite updatedAt delta; rc=%s log=%r"
                  % (rc, body[-400:]))
        if rc != 0:
            _fail("(е) rc=%s want 0; log=%r" % (rc, body[-300:]))
        if elapsed < stall_s:
            _fail("(е) too early %.1fs" % elapsed)
        _pass("(е) SSE off + updatedAt delta resets (rc=%s, %.1fs, sid=%s)"
              % (rc, elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (ж) artifacts fingerprint delta resets stall
# ---------------------------------------------------------------------------

def test_zh_artifacts_fallback():
    root, state, sid, run_id, _rd = _mk_poly("zh")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "off"
        mock.bump_artifacts_every = 1
        # keep updatedAt static — only artifacts change
        mock.updated_at = "2020-01-01T00:00:00.000Z"
        mock.finish_after_polls = 8
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc == 124 or "STALL:" in body:
            _fail("(ж) stalled despite artifacts delta; rc=%s log=%r"
                  % (rc, body[-400:]))
        if rc != 0:
            _fail("(ж) rc=%s want 0; log=%r" % (rc, body[-300:]))
        if elapsed < stall_s:
            _fail("(ж) too early %.1fs" % elapsed)
        _pass("(ж) artifacts fingerprint delta resets (rc=%s, %.1fs, sid=%s)"
              % (rc, elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (з) no deltas/events → STALL 124
# ---------------------------------------------------------------------------

def test_z_no_deltas_stall():
    root, state, sid, run_id, _rd = _mk_poly("z")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "off"
        mock.status = "RUNNING"
        # static updatedAt + static artifacts
        stall_s = 4
        t0 = time.time()
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc != 124:
            _fail("(з) rc=%s want 124; log=%r" % (rc, body[-400:]))
        if "STALL:" not in body:
            _fail("(з) missing STALL:")
        _pass("(з) без дельт → STALL 124 (%.1fs, sid=%s)" % (elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (и) cancel-on-stall invoked; fail cancel still EXIT 124 + log mark
# ---------------------------------------------------------------------------

def test_i_cancel_on_stall():
    # (и-ok) cancel succeeds
    root, state, sid, run_id, _rd = _mk_poly("iok")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "off"
        mock.cancel_status = 200
        stall_s = 3
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=stall_s, max_wall=60, poll=1)
        if rc != 124:
            _fail("(и) rc=%s want 124; log=%r" % (rc, body[-400:]))
        if not mock.cancel_calls:
            _fail("(и) cancel not called on mock; calls=%r" % mock.cancel_calls)
        # path relative to CURSOR_API_BASE — no prod host in recorded path
        for p in mock.cancel_calls:
            if "api.cursor.com" in p:
                _fail("(и) cancel path hardcoded prod: %s" % p)
        if "cancel ok" not in body:
            _fail("(и) missing cancel ok in log")
        _pass("(и) cancel-on-stall вызван (n=%d) + EXIT 124 (sid=%s)"
              % (len(mock.cancel_calls), sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)

    # (и-fail) cancel HTTP fail → mark in log, still 124
    root2, state2, sid2, run_id2, _rd2 = _mk_poly("ifail")
    mock2 = MockAPI().start()
    try:
        mock2.sse_mode = "off"
        mock2.cancel_status = 500
        rc2, body2 = _run_wait(
            mock2, state2, sid2, run_id2,
            stall_after=3, max_wall=60, poll=1)
        if rc2 != 124:
            _fail("(и-fail) rc=%s want 124; log=%r" % (rc2, body2[-400:]))
        if not mock2.cancel_calls:
            _fail("(и-fail) cancel not attempted")
        if "cancel unavailable" not in body2:
            _fail("(и-fail) missing cancel unavailable mark; log=%r"
                  % body2[-400:])
        _pass("(и) fail cancel → пометка в логе, EXIT 124 (sid=%s)" % sid2)
    finally:
        mock2.stop_server()
        shutil.rmtree(root2, ignore_errors=True)


# ---------------------------------------------------------------------------
# (к) --timeout N without --stall-after ≡ stall; short progress ≠ false STALL
# ---------------------------------------------------------------------------

def test_k_timeout_alias_progress():
    # resolve: timeout alone → stall_s
    s, _w = rcl.resolve_timers({}, timeout=5, max_wall=60)
    if s != 5:
        _fail("(к) resolve_timers(timeout=5) stall=%s want 5" % s)

    root, state, sid, run_id, _rd = _mk_poly("k")
    mock = MockAPI().start()
    try:
        mock.sse_mode = "progress"
        mock.sse_interval = 1.5
        mock.finish_after_polls = 8
        t0 = time.time()
        # --timeout 4 без --stall-after
        rc, body = _run_wait(
            mock, state, sid, run_id,
            stall_after=None, timeout=4, max_wall=60, poll=1)
        elapsed = time.time() - t0
        if rc == 124 or "STALL:" in body:
            _fail("(к) false STALL on short progress; rc=%s log=%r"
                  % (rc, body[-400:]))
        if rc == 125 or "WALL:" in body:
            _fail("(к) false WALL on short progress")
        if rc != 0:
            _fail("(к) rc=%s want 0; log=%r" % (rc, body[-300:]))
        if elapsed < 4:
            _fail("(к) too early %.1fs — need >timeout to prove stall-alias reset"
                  % elapsed)
        _pass("(к) --timeout≡stall; progress ≠ ложный STALL/WALL "
              "(rc=%s, %.1fs, sid=%s)" % (rc, elapsed, sid))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# session format + port sanity
# ---------------------------------------------------------------------------

def test_session_and_port_hygiene():
    root, state, sid, run_id, run_dir = _mk_poly("fmt")
    mock = MockAPI().start()
    try:
        _assert_not_live(state)
        if not sid.startswith("session_"):
            _fail("sid must start with session_: %s" % sid)
        rest = sid[len("session_"):]
        if "_" in rest:
            _fail("uuid part must use hyphens not underscores: %s" % sid)
        if rest.count("-") != 4:
            _fail("uuid must have 4 hyphens: %s" % sid)
        if mock.port == FORBIDDEN_PORT:
            _fail("port must not be %s" % FORBIDDEN_PORT)
        if mock.port < 1:
            _fail("invalid port %s" % mock.port)
        key = _read(os.path.join(state, "cursor.key")).strip()
        if key != "test-key-ok":
            _fail("cursor.key stub mismatch: %r" % key)
        if LIVE_STATE in os.path.realpath(run_dir):
            _fail("run_dir under live state")
        _pass("session/port hygiene ok (sid=%s port=%s)" % (sid, mock.port))
    finally:
        mock.stop_server()
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [
        test_session_and_port_hygiene,
        test_a_429_retry_after_not_stall,
        test_b_poll_lines_do_not_reset,
        test_v_sse_progress_resets,
        test_g_heartbeat_no_reset,
        test_d_max_wall,
        test_e_updated_at_fallback,
        test_zh_artifacts_fallback,
        test_z_no_deltas_stall,
        test_i_cancel_on_stall,
        test_k_timeout_alias_progress,
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
