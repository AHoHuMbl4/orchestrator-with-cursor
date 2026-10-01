#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-ORDERTRUTH OT-C2: reground NUDGE «Система работы» + order-suspect pending.

Группы: (б) post→pending→prompt-submit / clean / pre_tool;
(в) блок «Система работы» ≤5 строк; (г) NUDGE фрагменты + схема counters/<sid>.json.
State: только ORCHESTRATION_DIR=/tmp/ordertruth-rg-…; /root/.orchestration не трогаем.
CLI: python3 bin/reground.py <cmd> --engine kimi --format text
"""
from __future__ import print_function

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
REGROUND = os.path.join(BIN, "reground.py")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402
import reground as rg  # noqa: E402

CARD_ID = "dont-kejs-vladelca-30-09-komanduyuschij-dvazh"

ORDER_SUSPECT = (
    "без советников: выбора нет\n"
    "сервер вероятно жив, похоже ок\n"
)
ORDER_CLEAN = (
    "без советников: выбора нет, форма зафиксирована\n"
    "делай Write в A и B\n"
)
ORDER_NO_BASIS = "просто сделай X и Y без обоснования\n"


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


def _mk_poly(prefix="ordertruth-rg-", fid="F-OT"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "ordertruth-rg",
        "fronts": [{"id": fid, "status": "active", "title": fid}],
        "notes": "",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "reground": {"every_min": 60, "every_n_calls": 9999, "compact_reground": True},
        "compass": {"path": "compass.md", "max_chars": 9000},
    })
    _write_text(os.path.join(state, "compass.md"), "# compass\nЦель: test\n")
    # PROJECT.md рядом со state → foreign_state_warning молчит при cwd=root
    _write_text(os.path.join(root, "PROJECT.md"), "ordertruth-rg poly\n")
    return root, state, fid


class _EnvCwd(object):
    """ORCHESTRATION_DIR=state + cwd=root (свой state, не STATE РОДИТЕЛЯ)."""

    def __init__(self, root, state):
        self.root = root
        self.state = state
        self._prev_env = None
        self._prev_cwd = None

    def __enter__(self):
        self._prev_env = os.environ.get("ORCHESTRATION_DIR")
        self._prev_cwd = os.getcwd()
        os.environ["ORCHESTRATION_DIR"] = self.state
        os.chdir(self.root)
        orchlib._fronts_base = None
        return self

    def __exit__(self, *args):
        os.chdir(self._prev_cwd)
        if self._prev_env is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev_env
        orchlib._fronts_base = None


def _run_reground(cmd, event, state, cwd, extra_env=None):
    env = os.environ.copy()
    env["ORCHESTRATION_DIR"] = state
    if extra_env:
        env.update(extra_env)
    raw = json.dumps(event, ensure_ascii=False)
    r = subprocess.run(
        [sys.executable, REGROUND, cmd, "--engine", "kimi", "--format", "text"],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
    )
    return r


def _order_path(state, fid):
    return os.path.join(state, "fronts", fid, "order.md")


def _pending_path(state, sid):
    return os.path.join(state, "sessions", orchlib.safe_name(sid),
                        "pending_order_suspect.json")


def _suspect_counter_path(state, sid):
    return os.path.join(state, "counters",
                        "order-suspect-%s.json" % orchlib.safe_name(sid))


def _nudge_counter_path(state, sid):
    return os.path.join(state, "counters", orchlib.safe_name(sid) + ".json")


def _sistema_block_lines(text):
    """Строки блока «Система работы» inclusive (заголовок + до 4 строк смысла)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "Система работы":
            block = [ln]
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if not s or s == "---":
                    break
                if s.startswith("СВЕРКА ") or s.startswith("Compass:"):
                    break
                if s.startswith("Сессия:") or s.startswith("Kit:"):
                    break
                block.append(lines[j])
                if len(block) >= 5:
                    break
            return block
    return []


# ---------------------------------------------------------------------------
# (в) блок «Система работы»
# ---------------------------------------------------------------------------


class TestSistemaRabotyBlock(unittest.TestCase):
    def test_block_present_le5(self):
        _measure("MEASURE (в): Система работы ≤5 строк в NUDGE_TEXT")
        block = _sistema_block_lines(rg.NUDGE_TEXT)
        self.assertTrue(block, "заголовок «Система работы» отсутствует")
        self.assertLessEqual(len(block), 5, "блок >5 строк: %r" % block)
        self.assertEqual(block[0].strip(), "Система работы")
        joined = "\n".join(block)
        self.assertIn("советник", joined)
        self.assertIn("без советников", joined)
        self.assertIn("критики", joined)
        self.assertIn("фронта", joined)


# ---------------------------------------------------------------------------
# (г) прежние фрагменты NUDGE + схема counters
# ---------------------------------------------------------------------------


class TestNudgeRegression(unittest.TestCase):
    def test_nudge_fragments_intact(self):
        _measure("MEASURE (г): прежние фрагменты NUDGE_TEXT")
        t = rg.NUDGE_TEXT
        self.assertIn("СВЕРКА КУРСА", t)
        self.assertIn("{minutes}", t)
        self.assertIn("{calls}", t)
        self.assertIn("{summary}", t)
        self.assertIn("Compass: {compass}", t)
        self.assertIn("Перечитай compass", t)

    def test_counter_schema_after_events(self):
        _measure("MEASURE (г): counters/<sid>.json схема после событий")
        root, state, fid = _mk_poly()
        sid = "sid-schema"
        try:
            with _EnvCwd(root, state):
                order = _order_path(state, fid)
                _write_text(order, ORDER_SUSPECT)
                r1 = _run_reground("post-tool", {
                    "session_id": sid,
                    "tool_name": "Write",
                    "tool_input": {"file_path": order, "contents": ORDER_SUSPECT},
                }, state, root)
                self.assertEqual(r1.returncode, 0, r1.stderr)
                # nudge-counter появляется после bump в post-tool
                cpath = _nudge_counter_path(state, sid)
                self.assertTrue(os.path.exists(cpath), "nudge counter missing")
                with open(cpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key in ("calls", "task_calls", "last_nudge_ts"):
                    self.assertIn(key, data, "схема нуджа сломана: нет %s" % key)
                # order-suspect — отдельный файл
                sc = _suspect_counter_path(state, sid)
                self.assertTrue(os.path.exists(sc))
                with open(sc, "r", encoding="utf-8") as f:
                    sc_data = json.load(f)
                self.assertIn("count", sc_data)
                self.assertNotIn("calls", sc_data)
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (б) цепочка pending → prompt-submit + pre_tool
# ---------------------------------------------------------------------------


class TestOrderSuspectChain(unittest.TestCase):
    def setUp(self):
        self.root, self.state, self.fid = _mk_poly()
        self.sid = "sid-chain"
        self.order = _order_path(self.state, self.fid)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _baseline_prompt(self):
        """Первый prompt-submit: зафиксировать marks + kit version."""
        r = _run_reground("prompt-submit", {
            "session_id": self.sid,
            "prompt": "ping",
        }, self.state, self.root)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def test_post_pending_prompt_early_return(self):
        _measure("MEASURE (б): post→pending→prompt-submit early-return + card")
        with _EnvCwd(self.root, self.state):
            self._baseline_prompt()
            _write_text(self.order, ORDER_SUSPECT)
            r_post = _run_reground("post-tool", {
                "session_id": self.sid,
                "tool_name": "Write",
                "tool_input": {
                    "file_path": self.order,
                    "contents": ORDER_SUSPECT,
                },
            }, self.state, self.root)
            self.assertEqual(r_post.returncode, 0, r_post.stderr)
            # pending на месте; stdout post-tool без deny (тишина по suspect)
            pending = _pending_path(self.state, self.sid)
            self.assertTrue(os.path.exists(pending), "pending_order_suspect missing")
            sc = _suspect_counter_path(self.state, self.sid)
            with open(sc, "r", encoding="utf-8") as f:
                count1 = int(json.load(f).get("count", 0))
            self.assertGreaterEqual(count1, 1)

            params_p = os.path.join(self.state, "params.json")
            compass_p = os.path.join(
                self.state, "sessions", orchlib.safe_name(self.sid), "compass.md")
            # seed_session_compass мог создать session compass — берём mtime после baseline
            # повторный baseline уже был; фиксируем mtime перед delivery
            mt_params = os.stat(params_p).st_mtime
            mt_compass = None
            if os.path.exists(compass_p):
                mt_compass = os.stat(compass_p).st_mtime
            # небольшой sleep чтобы mtime-дифф был заметен при случайной записи
            time.sleep(0.05)

            r_ps = _run_reground("prompt-submit", {
                "session_id": self.sid,
                "prompt": "next",
            }, self.state, self.root)
            self.assertEqual(r_ps.returncode, 0, r_ps.stderr)
            out = r_ps.stdout or ""
            self.assertIn("ПРЕДПОЛОЖЕНИЕ = ВЫБОР", out)
            self.assertIn("СОВЕТНИК", out)
            self.assertIn(CARD_ID, out)
            self.assertFalse(os.path.exists(pending), "pending not consumed")
            # early-return: params/compass mtime не менялись
            self.assertEqual(os.stat(params_p).st_mtime, mt_params)
            if mt_compass is not None and os.path.exists(compass_p):
                self.assertEqual(os.stat(compass_p).st_mtime, mt_compass)
            # счётчик не вырос от prompt-submit
            with open(sc, "r", encoding="utf-8") as f:
                count2 = int(json.load(f).get("count", 0))
            self.assertEqual(count2, count1)

    def test_clean_no_pending(self):
        _measure("MEASURE (б): без маркеров → нет pending/вклейки")
        with _EnvCwd(self.root, self.state):
            self._baseline_prompt()
            _write_text(self.order, ORDER_CLEAN)
            sc = _suspect_counter_path(self.state, self.sid)
            before = 0
            if os.path.exists(sc):
                with open(sc, "r", encoding="utf-8") as f:
                    before = int(json.load(f).get("count", 0) or 0)
            r_post = _run_reground("post-tool", {
                "session_id": self.sid,
                "tool_name": "Write",
                "tool_input": {
                    "file_path": self.order,
                    "contents": ORDER_CLEAN,
                },
            }, self.state, self.root)
            self.assertEqual(r_post.returncode, 0, r_post.stderr)
            pending = _pending_path(self.state, self.sid)
            self.assertFalse(os.path.exists(pending))
            after = before
            if os.path.exists(sc):
                with open(sc, "r", encoding="utf-8") as f:
                    after = int(json.load(f).get("count", 0) or 0)
            self.assertEqual(after, before)
            r_ps = _run_reground("prompt-submit", {
                "session_id": self.sid,
                "prompt": "next",
            }, self.state, self.root)
            self.assertEqual(r_ps.returncode, 0, r_ps.stderr)
            out = r_ps.stdout or ""
            self.assertNotIn("ПРЕДПОЛОЖЕНИЕ = ВЫБОР", out)
            self.assertNotIn(CARD_ID, out)

    def test_pre_tool_warn_exit0(self):
        _measure("MEASURE (б): pre_tool маркеры → stderr + exit 0")
        with _EnvCwd(self.root, self.state):
            _write_text(self.order, ORDER_CLEAN)  # диск чистый; proposed — suspect
            r = _run_reground("pre-tool", {
                "session_id": self.sid,
                "tool_name": "Write",
                "tool_input": {
                    "file_path": self.order,
                    "contents": ORDER_SUSPECT,
                },
            }, self.state, self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("ПРЕДПОЛОЖЕНИЕ = ВЫБОР", r.stderr or "")
            self.assertIn(CARD_ID, r.stderr or "")

    def test_pre_tool_clean_silent(self):
        _measure("MEASURE (б): pre_tool чисто → тишина exit 0")
        with _EnvCwd(self.root, self.state):
            r = _run_reground("pre-tool", {
                "session_id": self.sid,
                "tool_name": "Write",
                "tool_input": {
                    "file_path": self.order,
                    "contents": ORDER_CLEAN,
                },
            }, self.state, self.root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("ПРЕДПОЛОЖЕНИЕ = ВЫБОР", r.stderr or "")

    def test_pre_tool_no_basis_exit2(self):
        _measure("MEASURE (б): pre_tool без подход/без советников → exit 2")
        with _EnvCwd(self.root, self.state):
            r = _run_reground("pre-tool", {
                "session_id": self.sid,
                "tool_name": "Write",
                "tool_input": {
                    "file_path": self.order,
                    "contents": ORDER_NO_BASIS,
                },
            }, self.state, self.root)
            self.assertEqual(r.returncode, 2, r.stderr)
            self.assertIn("без обоснования", r.stderr or "")


class TestPromptSubmitFormat(unittest.TestCase):
    def test_kit_line_present(self):
        _measure("MEASURE (г): формат prompt-submit не сломан (Kit:)")
        root, state, fid = _mk_poly(prefix="ordertruth-rg-fmt-")
        sid = "sid-fmt"
        try:
            with _EnvCwd(root, state):
                r = _run_reground("prompt-submit", {
                    "session_id": sid,
                    "prompt": "hello",
                }, state, root)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(
                    re.search(r"Kit:\s*\S+", r.stdout or ""),
                    "нет строки Kit: в выводе: %r" % (r.stdout[:200],),
                )
        finally:
            shutil.rmtree(root, ignore_errors=True)


class TestNudgePasteHasSistema(unittest.TestCase):
    def test_pending_nudge_delivers_sistema(self):
        _measure("MEASURE (в): pending_nudge→prompt-submit несёт блок Система работы")
        root, state, fid = _mk_poly(prefix="ordertruth-rg-nudge-")
        sid = "sid-nudge"
        try:
            with _EnvCwd(root, state):
                # baseline marks
                _run_reground("prompt-submit", {
                    "session_id": sid, "prompt": "ping",
                }, state, root)
                flag = os.path.join(
                    state, "sessions", orchlib.safe_name(sid), "pending_nudge.json")
                os.makedirs(os.path.dirname(flag), exist_ok=True)
                _write_json(flag, {"minutes": 7})
                r = _run_reground("prompt-submit", {
                    "session_id": sid, "prompt": "next",
                }, state, root)
                self.assertEqual(r.returncode, 0, r.stderr)
                block = _sistema_block_lines(r.stdout or "")
                self.assertTrue(block, "блок не в доставленном nudge")
                self.assertLessEqual(len(block), 5, block)
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
