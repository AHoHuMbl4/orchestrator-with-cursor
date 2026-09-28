#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MW2-A: гейт активации owns (orchlib save_fronts + panel /api/fronts/status).

Матрица order п.1: пересечение→отказ; непересекающий OK; пустые warn+OK;
peers=ТОЛЬКО active; legacy running НЕ блокирует; proposed-сосед НЕ блокирует;
exclude-self; panel bypass→HTTP 400; concurrent write JSON/ключ цел.

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_multiwrite_gate.py
State: только ORCHESTRATION_DIR=/tmp/mwgate-… ; /root/.orchestration не трогаем.
"""
from __future__ import print_function

import inspect
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import HTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
PANEL = os.path.join(REPO, "panel")

if BIN not in sys.path:
    sys.path.insert(0, BIN)
if PANEL not in sys.path:
    sys.path.insert(0, PANEL)

import orchlib  # noqa: E402
import server as panel_server  # noqa: E402


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _tmpdir():
    return tempfile.mkdtemp(prefix="mwgate-", dir="/tmp")


def _front(fid, status="proposed", owns=None, **extra):
    fr = {
        "id": fid,
        "title": fid,
        "role": "general",
        "compass": "fronts/%s/compass.md" % fid,
        "deps": [],
        "status": status,
    }
    if owns is not None:
        fr["owns"] = owns
    fr.update(extra)
    return fr


def _write_fronts(state, fronts, goal=""):
    os.makedirs(state, exist_ok=True)
    path = os.path.join(state, "fronts.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"goal": goal, "fronts": fronts, "notes": ""},
            f,
            ensure_ascii=False,
            indent=2,
        )
        f.write("\n")
    return path


def _read_fronts(state):
    with open(os.path.join(state, "fronts.json"), "r", encoding="utf-8") as f:
        return json.load(f)


class _EnvState(object):
    """ORCHESTRATION_DIR + сброс orchlib._fronts_base."""

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
# orchlib save_fronts / check_activation_gate
# ---------------------------------------------------------------------------


class TestOrchlibActivationGate(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir()
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_intersect_reject_with_witness(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["a/b/**"]),
                _front("F2", "proposed", ["a/**"]),
            ],
        )
        with _EnvState(self.state):
            data = orchlib.load_fronts()
            for fr in data["fronts"]:
                if fr["id"] == "F2":
                    fr["status"] = "active"
            with self.assertRaises(ValueError) as cm:
                orchlib.save_fronts(data)
            text = " ".join(str(x) for x in (cm.exception.args[0]
                                             if isinstance(cm.exception.args[0], list)
                                             else [cm.exception]))
            self.assertIn("пересечение", text)
            self.assertIn("F2", text)
            self.assertIn("F1", text)
            # диск не изменился
            disk = _read_fronts(self.state)
            st = {fr["id"]: fr["status"] for fr in disk["fronts"]}
            self.assertEqual(st["F2"], "proposed")
        _measure("MEASURE orchlib intersect reject: %s" % text)

    def test_live_a_globstar_vs_ab(self):
        """Живой замер п.2: owns [a/**] vs active [a/b/**] → отказ со свидетелем."""
        fronts = [
            {"id": "FA", "status": "active", "owns": ["a/b/**"]},
            {"id": "FB", "status": "active", "owns": ["a/**"]},
        ]
        ok, msg = orchlib.check_activation_gate(fronts, "FB")
        self.assertFalse(ok)
        self.assertIsNotNone(msg)
        self.assertIn("пересечение", msg)
        self.assertIn("FB", msg)
        self.assertIn("FA", msg)
        _measure("MEASURE live п.2 witness: %s" % msg)

    def test_non_intersect_ok(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["docs/**"]),
                _front("F2", "proposed", ["bin/**"]),
            ],
        )
        with _EnvState(self.state):
            data = orchlib.load_fronts()
            for fr in data["fronts"]:
                if fr["id"] == "F2":
                    fr["status"] = "active"
            orchlib.save_fronts(data)
            disk = _read_fronts(self.state)
            st = {fr["id"]: fr["status"] for fr in disk["fronts"]}
            self.assertEqual(st["F2"], "active")
        _measure("MEASURE orchlib non-intersect OK")

    def test_empty_owns_warn_ok(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["a/**"]),
                _front("F2", "proposed", []),
            ],
        )
        buf = io.StringIO()
        ok, msg = orchlib.check_activation_gate(
            [
                {"id": "F1", "status": "active", "owns": ["a/**"]},
                {"id": "F2", "status": "active", "owns": []},
            ],
            "F2",
            warn_stream=buf,
        )
        self.assertTrue(ok)
        self.assertIsNone(msg)
        self.assertIn("owns пуст", buf.getvalue())
        with _EnvState(self.state):
            data = orchlib.load_fronts()
            for fr in data["fronts"]:
                if fr["id"] == "F2":
                    fr["status"] = "active"
            # перехватим stderr от gate внутри save_fronts
            old = sys.stderr
            sys.stderr = io.StringIO()
            try:
                orchlib.save_fronts(data)
                err = sys.stderr.getvalue()
            finally:
                sys.stderr = old
            self.assertIn("owns пуст", err)
            disk = _read_fronts(self.state)
            self.assertEqual(
                {fr["id"]: fr["status"] for fr in disk["fronts"]}["F2"], "active"
            )
        _measure("MEASURE empty owns warn+OK")

    def test_missing_owns_warn_ok(self):
        ok, msg = orchlib.check_activation_gate(
            [
                {"id": "F1", "status": "active", "owns": ["a/**"]},
                {"id": "F2", "status": "active"},  # owns отсутствует
            ],
            "F2",
            warn_stream=io.StringIO(),
        )
        self.assertTrue(ok)
        _measure("MEASURE missing owns allow")

    def test_legacy_running_peer_does_not_block(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "running", ["a/**"]),
                _front("F2", "proposed", ["a/b/**"]),
            ],
        )
        with _EnvState(self.state):
            data = orchlib.load_fronts()
            # load_fronts мигрирует running→active in-memory; gate смотрит сырой диск
            for fr in data["fronts"]:
                if fr["id"] == "F2":
                    fr["status"] = "active"
            orchlib.save_fronts(data)
            disk = _read_fronts(self.state)
            st = {fr["id"]: fr["status"] for fr in disk["fronts"]}
            self.assertEqual(st["F2"], "active")
        _measure("MEASURE legacy running peer does not block")

    def test_proposed_peer_does_not_block(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "proposed", ["a/**"]),
                _front("F2", "proposed", ["a/b/**"]),
            ],
        )
        with _EnvState(self.state):
            data = orchlib.load_fronts()
            for fr in data["fronts"]:
                if fr["id"] == "F2":
                    fr["status"] = "active"
            orchlib.save_fronts(data)
            disk = _read_fronts(self.state)
            self.assertEqual(
                {fr["id"]: fr["status"] for fr in disk["fronts"]}["F2"], "active"
            )
            self.assertEqual(
                {fr["id"]: fr["status"] for fr in disk["fronts"]}["F1"], "proposed"
            )
        _measure("MEASURE proposed peer does not block")

    def test_exclude_self(self):
        """Активация фронта не пересекается сама с собой."""
        ok, msg = orchlib.check_activation_gate(
            [{"id": "F1", "status": "active", "owns": ["a/**"]}],
            "F1",
        )
        self.assertTrue(ok)
        self.assertIsNone(msg)
        _measure("MEASURE exclude-self OK")

    def test_peers_only_active(self):
        """stalled/done/cancelled не блокируют."""
        ok, msg = orchlib.check_activation_gate(
            [
                {"id": "F1", "status": "stalled", "owns": ["a/**"]},
                {"id": "F2", "status": "done", "owns": ["a/**"]},
                {"id": "F3", "status": "cancelled", "owns": ["a/**"]},
                {"id": "F4", "status": "active", "owns": ["a/x/**"]},
            ],
            "F4",
        )
        self.assertTrue(ok)
        _measure("MEASURE peers only active")


# ---------------------------------------------------------------------------
# panel /api/fronts/status + _write_key_file + indication
# ---------------------------------------------------------------------------


class TestPanelStatusGate(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir()
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        with open(os.path.join(self.state, "params.json"), "w", encoding="utf-8") as f:
            json.dump(orchlib.DEFAULTS, f)
            f.write("\n")
        self._httpd = None
        self._thread = None
        self._port = None
        self._env = None

    def tearDown(self):
        if self._httpd is not None:
            try:
                self._httpd.shutdown()
            except Exception:
                pass
            try:
                self._httpd.server_close()
            except Exception:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)
        if self._env is not None:
            self._env.__exit__(None, None, None)
            self._env = None
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start(self):
        self._env = _EnvState(self.state)
        self._env.__enter__()
        self._httpd = HTTPServer(("127.0.0.1", 0), panel_server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()
        time.sleep(0.05)

    def _post_status(self, fid, status):
        url = "http://127.0.0.1:%s/api/fronts/status" % self._port
        body = json.dumps({"id": fid, "status": status}).encode("utf-8")
        req = Request(url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=5) as resp:
                return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                obj = json.loads(raw)
            except Exception:
                obj = {"error": raw}
            return e.code, obj

    def _get_fronts(self):
        url = "http://127.0.0.1:%s/api/fronts" % self._port
        with urlopen(url, timeout=5) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))

    def test_panel_intersect_http_400_no_bypass_write(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["a/b/**"]),
                _front("F2", "proposed", ["a/**"]),
            ],
        )
        self._start()
        code, obj = self._post_status("F2", "active")
        self.assertEqual(code, 400, obj)
        self.assertIn("пересечение", obj.get("error", ""))
        disk = _read_fronts(self.state)
        st = {fr["id"]: fr["status"] for fr in disk["fronts"]}
        self.assertEqual(st["F2"], "proposed", "bypass must not write")
        _measure("MEASURE panel status HTTP 400: %s" % obj.get("error"))

    def test_panel_non_intersect_ok(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["docs/**"]),
                _front("F2", "proposed", ["bin/**"]),
            ],
        )
        self._start()
        code, obj = self._post_status("F2", "active")
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"))
        disk = _read_fronts(self.state)
        self.assertEqual(
            {fr["id"]: fr["status"] for fr in disk["fronts"]}["F2"], "active"
        )
        _measure("MEASURE panel non-intersect OK")

    def test_panel_empty_owns_ok(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["a/**"]),
                _front("F2", "proposed", []),
            ],
        )
        self._start()
        code, obj = self._post_status("F2", "active")
        self.assertEqual(code, 200, obj)
        _measure("MEASURE panel empty owns OK")

    def test_panel_running_peer_ok(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "running", ["a/**"]),
                _front("F2", "proposed", ["a/b/**"]),
            ],
        )
        self._start()
        code, obj = self._post_status("F2", "active")
        self.assertEqual(code, 200, obj)
        _measure("MEASURE panel running peer OK")

    def test_panel_owns_indication(self):
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["a/**"]),
                _front("F2", "active", ["a/b/**"]),
                _front("F3", "proposed", ["z/**"]),
            ],
        )
        self._start()
        code, obj = self._get_fronts()
        self.assertEqual(code, 200)
        by_id = {fr["id"]: fr for fr in obj["fronts"]}
        self.assertEqual(by_id["F1"].get("owns"), ["a/**"])
        self.assertEqual(by_id["F2"].get("owns"), ["a/b/**"])
        self.assertTrue(obj.get("owns_conflict"))
        self.assertGreaterEqual(obj.get("owns_overlap_count", 0), 1)
        self.assertGreaterEqual(by_id["F1"].get("owns_overlap", 0), 1)
        _measure(
            "MEASURE owns indication conflict=%s count=%s"
            % (obj.get("owns_conflict"), obj.get("owns_overlap_count"))
        )

    def test_write_key_file_atomic(self):
        src = inspect.getsource(panel_server._write_key_file)
        self.assertIn("tempfile", src)
        self.assertIn("fsync", src)
        self.assertIn("os.replace", src)
        self.assertIn("0o600", src)
        # _persist_fronts_data не тронут — атомарность на месте
        persist_src = inspect.getsource(panel_server._persist_fronts_data)
        self.assertIn("mkstemp", persist_src)
        self.assertIn("os.replace", persist_src)
        with _EnvState(self.state):
            path = panel_server._write_key_file("cursor.key", "secret-key-value-xyz")
            self.assertTrue(os.path.isfile(path))
            mode = os.stat(path).st_mode & 0o777
            self.assertEqual(mode, 0o600)
            with open(path, "r", encoding="utf-8") as f:
                self.assertEqual(f.read(), "secret-key-value-xyz")
        _measure("MEASURE _write_key_file tempfile+fsync+replace+0600 mode=%o" % mode)


# ---------------------------------------------------------------------------
# chips + exit 13 + labels
# ---------------------------------------------------------------------------


class TestChipsAndRefuseExit(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir()
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        _write_fronts(self.state, [])
        open(os.path.join(self.state, "journal.jsonl"), "w").close()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gate_refuse_has_13(self):
        self.assertIn(13, orchlib._GATE_REFUSE_EXITS)
        _measure("MEASURE 13 in _GATE_REFUSE_EXITS")

    def test_health_chips_from_journal(self):
        jpath = os.path.join(self.state, "journal.jsonl")
        with open(jpath, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "kind": "chip", "name": "multi_write_front",
                "front": "F-MULTIWRITE", "id": "run-a", "ts": time.time(),
            }) + "\n")
            f.write(json.dumps({
                "kind": "chip", "name": "commit_no_verify",
                "front": "F-MULTIWRITE", "id": "run-b", "ts": time.time(),
            }) + "\n")
        chips = orchlib.health_red_chips(state=self.state, kit_dir=REPO)
        self.assertIn("multi_write_front", chips)
        self.assertIn("commit_no_verify", chips)
        self.assertTrue(chips["multi_write_front"])
        self.assertTrue(chips["commit_no_verify"])
        _measure(
            "MEASURE chips multi_write_front=%s commit_no_verify=%s"
            % (chips["multi_write_front"], chips["commit_no_verify"])
        )

    def test_index_labels(self):
        idx = os.path.join(PANEL, "index.html")
        with open(idx, "r", encoding="utf-8") as f:
            txt = f.read()
        self.assertIn("multi_write_front:", txt)
        self.assertIn("commit_no_verify:", txt)
        _measure("MEASURE HEALTH_CHIP_LABELS in index.html")


# ---------------------------------------------------------------------------
# concurrent: 2 processes → JSON valid, key intact
# ---------------------------------------------------------------------------


class TestConcurrentWrites(unittest.TestCase):
    def setUp(self):
        self.tmp = _tmpdir()
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state)
        _write_fronts(
            self.state,
            [
                _front("F1", "active", ["x/**"]),
                _front("F2", "proposed", ["y/**"]),
            ],
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_two_processes_fronts_and_key(self):
        worker = """
import json, os, sys, time
sys.path.insert(0, {bin!r})
sys.path.insert(0, {panel!r})
os.environ["ORCHESTRATION_DIR"] = {state!r}
import orchlib
import server as panel_server
orchlib._fronts_base = None
role = sys.argv[1]
if role == "fronts":
    for i in range(20):
        data = orchlib.load_fronts()
        for fr in data.get("fronts") or []:
            if fr.get("id") == "F2":
                fr["notes_n"] = i
                fr["status"] = "active"
        try:
            orchlib.save_fronts(data)
        except Exception:
            pass
        time.sleep(0.005)
elif role == "key":
    for i in range(30):
        panel_server._write_key_file(
            "cursor.key", "KEYBODY-" + ("%04d" % i) + "-END")
        time.sleep(0.003)
print("ok")
""".format(bin=BIN, panel=PANEL, state=self.state)
        script = os.path.join(self.tmp, "worker.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(worker)
        env = os.environ.copy()
        env["ORCHESTRATION_DIR"] = self.state
        p1 = subprocess.Popen(
            [sys.executable, script, "fronts"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        p2 = subprocess.Popen(
            [sys.executable, script, "key"], env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        o1, e1 = p1.communicate(timeout=60)
        o2, e2 = p2.communicate(timeout=60)
        self.assertEqual(p1.returncode, 0, e1.decode("utf-8", "replace"))
        self.assertEqual(p2.returncode, 0, e2.decode("utf-8", "replace"))
        # JSON валиден
        data = _read_fronts(self.state)
        self.assertIsInstance(data, dict)
        self.assertIsInstance(data.get("fronts"), list)
        json.dumps(data)  # round-trip
        # ключ цел (полный токен KEYBODY-NNNN-END)
        key_path = os.path.join(self.state, "cursor.key")
        self.assertTrue(os.path.isfile(key_path))
        with open(key_path, "r", encoding="utf-8") as f:
            key = f.read().strip()
        self.assertTrue(key.startswith("KEYBODY-"), key)
        self.assertTrue(key.endswith("-END"), key)
        self.assertEqual(len(key), len("KEYBODY-0000-END"))
        mode = os.stat(key_path).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        _measure("MEASURE concurrent JSON ok key=%r mode=%o" % (key, mode))


if __name__ == "__main__":
    unittest.main(verbosity=2)
