#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-MUSTMAP MM-C3: MUST-блок реестра в NUDGE + «Система работы» п.6 + fail-open.

Группы: MUST-блок ≤5; числа = mustmap.json; fail-open нет/битый;
Система работы ≤5 после замены; соседние NUDGE фрагменты; counters schema;
_order_has_basis exit-2; heartbeat→pending→prompt-submit → MUST вклейка.
State: только ORCHESTRATION_DIR=/tmp/mustmap-rg-…; живой стейт не трогаем.
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
REGROUND = os.path.join(BIN, "reground.py")
MUSTMAP = os.path.join(REPO, "audit", "mustmap", "mustmap.json")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402
import reground as rg  # noqa: E402

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


def _mk_poly(prefix="mustmap-rg-"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "mustmap-rg",
        "fronts": [{"id": "F-MM", "status": "active", "title": "F-MM"}],
        "notes": "",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"enabled": True, "hierarchy": "off"},
        "reground": {"every_min": 60, "every_n_calls": 9999, "compact_reground": True},
        "compass": {"path": "compass.md", "max_chars": 9000},
    })
    _write_text(os.path.join(state, "compass.md"), "# compass\nЦель: test\n")
    _write_text(os.path.join(root, "PROJECT.md"), "mustmap-rg poly\n")
    return root, state


class _EnvCwd(object):
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


def _run_reground(cmd, event, state, cwd):
    env = os.environ.copy()
    env["ORCHESTRATION_DIR"] = state
    raw = json.dumps(event, ensure_ascii=False)
    return subprocess.run(
        [sys.executable, REGROUND, cmd, "--engine", "kimi", "--format", "text"],
        input=raw,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
    )


def _mustmap_counts():
    with open(MUSTMAP, "r", encoding="utf-8") as f:
        data = json.load(f)
    cmd = sum(1 for i in data["imperatives"] if i.get("to") == "commander")
    gen = sum(1 for i in data["imperatives"] if i.get("to") == "general")
    return cmd, gen


def _must_block_lines(text):
    """Строки MUST-блока (заголовок MUST … до «Система работы» / конца)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip().startswith("MUST ") or ln.strip() == "MUST":
            block = [ln]
            for j in range(i + 1, len(lines)):
                s = lines[j].strip()
                if not s or s == "---":
                    break
                if s == "Система работы" or s.startswith("СВЕРКА "):
                    break
                if s.startswith("Сессия:") or s.startswith("Kit:"):
                    break
                block.append(lines[j])
            return block
    return []


def _sistema_block_lines(text):
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
                if s.startswith("MUST "):
                    break
                block.append(lines[j])
            return block
    return []


def _nudge_rendered():
    return rg.NUDGE_TEXT.format(
        minutes=7, calls=0, summary="s", compass="/c",
        mustmap=rg._mustmap_nudge_block(),
    )


# ---------------------------------------------------------------------------
# MUST-блок
# ---------------------------------------------------------------------------


class TestMustBlock(unittest.TestCase):
    def test_must_block_present_le5_path_counts(self):
        _measure("MEASURE: MUST-блок ≤5 + путь + числа = mustmap.json")
        block = _must_block_lines(_nudge_rendered())
        self.assertTrue(block, "MUST-блок отсутствует")
        self.assertLessEqual(len(block), 5, "MUST-блок >5: %r" % block)
        joined = "\n".join(block)
        self.assertIn("audit/mustmap/mustmap.json", joined)
        cmd, gen = _mustmap_counts()
        self.assertIn("commander=%d" % cmd, joined)
        self.assertIn("general=%d" % gen, joined)
        self.assertEqual(rg._MUSTMAP_CMD_N, cmd)
        self.assertEqual(rg._MUSTMAP_GEN_N, gen)
        self.assertIn("status=prompt", joined)

    def test_must_fail_open_missing(self):
        _measure("MEASURE: нет mustmap → fail-open без traceback")
        root, state = _mk_poly(prefix="mustmap-rg-miss-")
        fake_kit = os.path.join(root, "kit")
        os.makedirs(os.path.join(fake_kit, "audit", "mustmap"), exist_ok=True)
        # пустой kit без mustmap.json
        prev = orchlib.KIT_DIR
        try:
            orchlib.KIT_DIR = fake_kit
            out = rg._mustmap_nudge_block()
            self.assertEqual(out, "")
            # format не падает
            text = rg.NUDGE_TEXT.format(
                minutes=1, calls=0, summary="s", compass="/c", mustmap=out)
            self.assertIn("Система работы", text)
            self.assertNotIn("commander=", text)
        finally:
            orchlib.KIT_DIR = prev
            shutil.rmtree(root, ignore_errors=True)

    def test_must_fail_open_broken(self):
        _measure("MEASURE: битый mustmap → fail-open без traceback")
        root, state = _mk_poly(prefix="mustmap-rg-brk-")
        fake_kit = os.path.join(root, "kit")
        mm = os.path.join(fake_kit, "audit", "mustmap", "mustmap.json")
        os.makedirs(os.path.dirname(mm), exist_ok=True)
        _write_text(mm, "{not-json")
        prev = orchlib.KIT_DIR
        try:
            orchlib.KIT_DIR = fake_kit
            out = rg._mustmap_nudge_block()
            self.assertEqual(out, "")
            text = rg.NUDGE_TEXT.format(
                minutes=1, calls=0, summary="s", compass="/c", mustmap=out)
            self.assertNotIn("Traceback", text)
            self.assertNotIn("commander=", text)
        finally:
            orchlib.KIT_DIR = prev
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# Система работы + соседние NUDGE
# ---------------------------------------------------------------------------


class TestSistemaAndNeighbors(unittest.TestCase):
    def test_sistema_le5_after_replace(self):
        _measure("MEASURE: Система работы ≤5 после замены п.6")
        block = _sistema_block_lines(rg.NUDGE_TEXT)
        self.assertTrue(block)
        self.assertLessEqual(len(block), 5, block)
        self.assertEqual(block[0].strip(), "Система работы")
        joined = "\n".join(block)
        self.assertIn("planning.md", joined)
        self.assertIn("MM-001", joined)
        self.assertNotIn("слова-допущения", joined)
        self.assertIn("без советников", joined)  # регресс F-ORDERTRUTH assert
        self.assertIn("советник", joined)
        self.assertIn("критики", joined)
        self.assertIn("фронта", joined)

    def test_nudge_neighbors_intact(self):
        _measure("MEASURE: соседние фрагменты NUDGE_TEXT целы")
        t = rg.NUDGE_TEXT
        self.assertIn("СВЕРКА КУРСА", t)
        self.assertIn("{minutes}", t)
        self.assertIn("{calls}", t)
        self.assertIn("{summary}", t)
        self.assertIn("Compass: {compass}", t)
        self.assertIn("{mustmap}", t)
        self.assertIn("Перечитай compass", t)
        self.assertIn("Система работы", t)


# ---------------------------------------------------------------------------
# counters schema + _order_has_basis exit-2
# ---------------------------------------------------------------------------


class TestSchemaAndGate(unittest.TestCase):
    def test_counter_schema_untouched(self):
        _measure("MEASURE: counters/<sid>.json схема нетронута")
        root, state = _mk_poly(prefix="mustmap-rg-ctr-")
        sid = "sid-ctr"
        try:
            with _EnvCwd(root, state):
                r = _run_reground("post-tool", {
                    "session_id": sid,
                    "tool_name": "Bash",
                    "tool_input": {"command": "echo ok"},
                }, state, root)
                self.assertEqual(r.returncode, 0, r.stderr)
                cpath = os.path.join(
                    state, "counters", orchlib.safe_name(sid) + ".json")
                self.assertTrue(os.path.exists(cpath))
                with open(cpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for key in ("calls", "task_calls", "last_nudge_ts"):
                    self.assertIn(key, data, "схема нуджа сломана: нет %s" % key)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_order_has_basis_exit2(self):
        _measure("MEASURE: _order_has_basis exit-2 сохранён")
        root, state = _mk_poly(prefix="mustmap-rg-basis-")
        sid = "sid-basis"
        order = os.path.join(state, "fronts", "F-MM", "order.md")
        try:
            with _EnvCwd(root, state):
                r = _run_reground("pre-tool", {
                    "session_id": sid,
                    "tool_name": "Write",
                    "tool_input": {
                        "file_path": order,
                        "contents": ORDER_NO_BASIS,
                    },
                }, state, root)
                self.assertEqual(r.returncode, 2, r.stderr)
                self.assertIn("без обоснования", r.stderr or "")
        finally:
            shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# heartbeat → pending → prompt-submit → MUST
# ---------------------------------------------------------------------------


class TestHeartbeatMustDelivery(unittest.TestCase):
    def test_heartbeat_pending_prompt_has_must(self):
        _measure("MEASURE: heartbeat→pending→prompt-submit → MUST-блок")
        root, state = _mk_poly(prefix="mustmap-rg-hb-")
        sid = "sid-hb"
        try:
            with _EnvCwd(root, state):
                # baseline marks
                r0 = _run_reground("prompt-submit", {
                    "session_id": sid, "prompt": "ping",
                }, state, root)
                self.assertEqual(r0.returncode, 0, r0.stderr)
                # heartbeat: first tick init; second past every_min
                r1 = _run_reground("heartbeat", {
                    "session_id": sid, "uptime_ms": 1000,
                }, state, root)
                self.assertEqual(r1.returncode, 0, r1.stderr)
                r2 = _run_reground("heartbeat", {
                    "session_id": sid, "uptime_ms": 1000 + 60 * 60 * 1000,
                }, state, root)
                self.assertEqual(r2.returncode, 0, r2.stderr)
                flag = os.path.join(
                    state, "sessions", orchlib.safe_name(sid), "pending_nudge.json")
                self.assertTrue(os.path.exists(flag), "pending_nudge missing")
                r3 = _run_reground("prompt-submit", {
                    "session_id": sid, "prompt": "next",
                }, state, root)
                self.assertEqual(r3.returncode, 0, r3.stderr)
                out = r3.stdout or ""
                block = _must_block_lines(out)
                self.assertTrue(block, "MUST-блок не в доставке: %r" % out[:500])
                self.assertLessEqual(len(block), 5, block)
                self.assertIn("audit/mustmap/mustmap.json", "\n".join(block))
                self.assertFalse(os.path.exists(flag), "pending not consumed")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
