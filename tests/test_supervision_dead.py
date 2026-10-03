#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-C5 K1 (а): живой надзор — чип supervision_dead, Инвариант 1.

Смерть/нестарт надзорного рана (prosecutor/observer) → видимый сигнал ≤60 с
в journal (без HTTP). Фикстуры 1–10 приказа полковника C5-K1.

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_supervision_dead.py -q
State: только ORCHESTRATION_DIR=/tmp/supd-…; живой /root/.orchestration не пишем.
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
import threading
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
RUN_EXEC = os.path.join(BIN, "run-exec.py")
PANEL_INDEX = os.path.join(REPO, "panel", "index.html")
LIVE_STATE = "/root/.orchestration"
FRONT = "F-K1T"
ROLE_PROS = "meta/front-prosecutor.md"
ROLE_OBS = "meta/front-observer.md"

if BIN not in sys.path:
    sys.path.insert(0, BIN)


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


rex = _load_mod("run_exec_supd", RUN_EXEC)
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
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


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


def _sup_chips(state):
    return [e for e in _read_journal(state)
            if e.get("kind") == "chip"
            and e.get("name") == "supervision_dead"]


def _mk_state(prefix="supd"):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "k1 supervision_dead",
        "fronts": [{
            "id": FRONT,
            "title": "k1",
            "status": "active",
            "owns": ["bin/run-exec.py", "bin/orchlib.py",
                     "panel/index.html", "panel/server.py",
                     "tests/test_supervision_dead.py"],
        }],
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"hierarchy": "on", "enabled": True},
        "execution": {"retry_on_fail": 1, "timeout_s": 30},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8").close()
    return root, state


def _fake_agent_dir(state):
    """PATH-stub cursor-agent: пишет маркер в лог и спит (никакого result)."""
    bindir = os.path.join(state, "fake-bin")
    os.makedirs(bindir, exist_ok=True)
    agent = os.path.join(bindir, "cursor-agent")
    with open(agent, "w", encoding="utf-8") as f:
        f.write("#!/bin/bash\n")
        f.write("echo FAKE_AGENT_START\n")
        f.write("sleep 120\n")
    os.chmod(agent, 0o755)
    return bindir


def _sleep_child(seconds=120):
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(%d)" % int(seconds)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True)


def _dead_pid():
    """pid заведомо мёртвого процесса (spawn+kill+wait)."""
    p = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    p.wait(timeout=10)
    return p.pid


def _kill_polygon_procs(state):
    """Убить сторожа/watcher полигона (по /proc cmdline с путём state)."""
    me = os.getpid()
    for pid_s in os.listdir("/proc"):
        if not pid_s.isdigit() or int(pid_s) == me:
            continue
        try:
            with open("/proc/%s/cmdline" % pid_s, "rb") as f:
                cmd = f.read().decode("utf-8", "replace")
        except Exception:
            continue
        if RUN_EXEC not in cmd or state not in cmd:
            continue
        if "--__supervise" not in cmd and "--__watch" not in cmd:
            continue
        try:
            os.kill(int(pid_s), signal.SIGKILL)
        except Exception:
            pass


# Живой state: снимок до/после всего модуля — оракул «живой не писался».
_LIVE_SNAPSHOT = {}


def _live_snapshot():
    """Рекурсивный снимок живого state (mtime_ns, size) — вкл. подкаталоги."""
    out = {}
    for root, _dirs, files in os.walk(LIVE_STATE):
        for name in files:
            p = os.path.join(root, name)
            rel = os.path.relpath(p, LIVE_STATE)
            try:
                st = os.stat(p)
                out[rel] = (st.st_mtime_ns, st.st_size)
            except Exception:
                pass
    return out


def setUpModule():
    _LIVE_SNAPSHOT.clear()
    _LIVE_SNAPSHOT.update(_live_snapshot())


def _live_housekeeping(rel):
    """Housekeeping живой сессии + квитанционного харнесса — не волна.

    counters/: всё, кроме front-runs-* (их пишет run-exec волн — утечка
    обязана краснеть; lock-каталоги проходят по basename owner).
    sessions/**: pending_*.json, null_series.json, enabled.json, last-seen
    (хуки/нуджи живой сессии) + sessions/*/runs/** — квитанционный
    харнесс §3: run.log, prompt*, probe-receipt.md там штатно пишет
    writer и обёртка проб/ранов живой сессии. Корень state — ТОЛЬКО
    корень (len(parts)==1): cursor-run-* (log/pid/TOMBSTONE) — обёртка
    ранов без --session (auto-prosecutor харнесса; зуб на запуск ранов
    живьём держит counters/front-runs-*). Глубже корня cursor-run-* —
    красный (fronts/**/cursor-run-*, audit/cursor-run-* — утечка).
    Зубы: fronts.json, fronts/** (compass, prosecutor/, colonels/),
    params.json и прочие пути — красные.

    Решение (fix2, KR1+KR3 за, KR2 таймингами журнала против расширения):
    prompt-prosecutor-auto-*.md, prompt-<id>.run.md и корневые runs/<id>/**
    НЕ housekeeping — харнесс пишет их синхронно в journal_end предыдущего
    рана (maybe_auto_prosecutor_after_end), строго ДО окна оракула; их
    появление В окне — аномалия → красный.
    """
    parts = rel.split(os.sep)
    if parts and parts[0] == "counters":
        return not os.path.basename(rel).startswith("front-runs-")
    if parts and parts[0] == "sessions":
        if len(parts) >= 3 and parts[2] == "runs":
            return True
        name = os.path.basename(rel)
        return (name == "last-seen" or name == "null_series.json"
                or name == "enabled.json" or name.startswith("pending_"))
    name = os.path.basename(rel)
    return len(parts) == 1 and name.startswith("cursor-run-")


def tearDownModule():
    # Оракул «живой state не писался»: всё неизменно, НО journal.jsonl —
    # файл новых писателей волны — разрешён только в прирост строками БЕЗ
    # чипов supervision_dead (живая чужая запись допускается). Исключения —
    # housekeeping-маркеры живости сессии + территория квитанционного
    # харнесса (sessions/*/runs/**, cursor-run-* только в корне state;
    # см. _live_housekeeping) — волновые писатели их не трогают в принципе.
    after = _live_snapshot()
    for rel in after:
        assert rel in _LIVE_SNAPSHOT or _live_housekeeping(rel), (
            "live state touched (new file): %s" % rel)
    for rel, before in _LIVE_SNAPSHOT.items():
        now_stat = after.get(rel)
        if rel == "journal.jsonl":
            assert now_stat is not None, "live journal исчез"
            assert now_stat[1] >= before[1], "live journal усох"
            with open(os.path.join(LIVE_STATE, rel), "rb") as f:
                f.seek(before[1])
                grown = f.read()
            for line in grown.splitlines():
                if b"supervision_dead" in line and b'"chip"' in line:
                    raise AssertionError(
                        "live journal получил чип supervision_dead: %r"
                        % line[:200])
            continue
        if _live_housekeeping(rel):
            continue  # маркеры живости живой сессии — не запись волны
        assert now_stat == before, "live state touched: %s" % rel


class SupTemp(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_state()
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        self._prev_poll = os.environ.get("ORCH_SUPERVISION_POLL_S")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        os.environ["ORCH_SUPERVISION_POLL_S"] = "0.3"

    def tearDown(self):
        _kill_polygon_procs(self.state)
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        if self._prev_poll is None:
            os.environ.pop("ORCH_SUPERVISION_POLL_S", None)
        else:
            os.environ["ORCH_SUPERVISION_POLL_S"] = self._prev_poll
        shutil.rmtree(self.root, ignore_errors=True)

    def _prompt(self, name, text=None, role=ROLE_PROS):
        p = os.path.join(self.state, name)
        with open(p, "w", encoding="utf-8") as f:
            f.write(text or ("роль: %s\n\nsupervision fixture\n" % role))
        return p

    def _env(self, fake_bin=None):
        e = os.environ.copy()
        e["ORCHESTRATION_DIR"] = self.state
        if fake_bin:
            e["PATH"] = fake_bin + os.pathsep + e.get("PATH", "")
        e.pop("ORCH_RUN_ID", None)
        e.pop("ORCH_FRONT", None)
        return e

    def _run_wrapper(self, argv, fake_bin=None, timeout=60):
        return subprocess.run(
            [sys.executable, RUN_EXEC] + argv,
            cwd=REPO, env=self._env(fake_bin),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            encoding="utf-8", errors="replace", timeout=timeout)

    def _main_argv(self, argv):
        prev = sys.argv
        sys.argv = ["run-exec.py"] + argv
        try:
            return rex.main()
        finally:
            sys.argv = prev


# ---------------------------------------------------------------------------
# (1) kill рана без end: SIGKILL мимо обёртки, сторож выжил → чип ≤60 с
# ---------------------------------------------------------------------------


class TestRuntimeKillNoEnd(SupTemp):
    def test_sigkill_past_wrapper_chip_within_60s(self):
        fake = _fake_agent_dir(self.state)
        rid = "RT1"
        prompt = self._prompt("p-rt1.md")
        wrapper = subprocess.Popen(
            [sys.executable, RUN_EXEC, "--id", rid, "--front", FRONT,
             "--role", ROLE_PROS, "--prompt-file", prompt,
             "--no-reground-line", "--yield-after", "0"],
            cwd=REPO, env=self._env(fake),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
        try:
            deadline = time.time() + 20
            pid = None
            while time.time() < deadline:
                if os.path.isfile(pid_path):
                    pid = rex.read_pid_file(pid_path)
                    if pid and rex.pid_alive(pid):
                        break
                time.sleep(0.2)
            self.assertTrue(pid and rex.pid_alive(pid), "агент не стартовал")
            t_kill = time.time()
            # SIGKILL обёртки (end не будет), затем агента — мимо обёртки
            wrapper.send_signal(signal.SIGKILL)
            wrapper.wait(timeout=10)
            os.kill(pid, signal.SIGKILL)
            # сторож — отдельная сессия: выжил, пишет чип ≤60 с
            chip = None
            deadline = time.time() + 60
            while time.time() < deadline:
                chips = [c for c in _sup_chips(self.state)
                         if c.get("id") == rid]
                if chips:
                    chip = chips[0]
                    break
                time.sleep(0.3)
            self.assertIsNotNone(chip, "чип supervision_dead не появился ≤60с")
            self.assertLessEqual(time.time() - t_kill, 60.0)
            self.assertEqual(chip.get("cause"), "runtime_pid_dead")
            self.assertEqual(chip.get("front"), FRONT)
            ends = [e for e in _read_journal(self.state)
                    if e.get("kind") == "end" and e.get("id") == rid]
            self.assertEqual(ends, [], "kill мимо обёртки — end не пишется")
            _measure("MEASURE (1) SIGKILL мимо обёртки → сторож пишет chip "
                     "supervision_dead ≤60с (без HTTP)")
        finally:
            try:
                wrapper.send_signal(signal.SIGKILL)
                wrapper.wait(timeout=5)
            except Exception:
                pass


# ---------------------------------------------------------------------------
# (2) --kill/TOMBSTONE при итоговом end 4 → чип
# ---------------------------------------------------------------------------


class TestKillTombstoneEnd4(SupTemp):
    def test_kill_tombstone_end4_chip(self):
        fake = _fake_agent_dir(self.state)
        rid = "KL1"
        prompt = self._prompt("p-kl1.md")
        r1 = self._run_wrapper(
            ["--id", rid, "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt, "--no-reground-line", "--detach"],
            fake_bin=fake)
        self.assertEqual(r1.returncode, 0, r1.stderr)
        pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
        deadline = time.time() + 20
        while time.time() < deadline and not os.path.isfile(pid_path):
            time.sleep(0.2)
        self.assertTrue(os.path.isfile(pid_path), "pid-файл не появился")
        rk = self._run_wrapper(["--kill", rid], timeout=30)
        self.assertEqual(rk.returncode, 0, rk.stderr)
        self.assertTrue(os.path.isfile(
            os.path.join(self.state, "cursor-run-%s.TOMBSTONE" % rid)))
        end = None
        deadline = time.time() + 30
        while time.time() < deadline:
            ends = [e for e in _read_journal(self.state)
                    if e.get("kind") == "end" and e.get("id") == rid]
            if ends:
                end = ends[-1]
                break
            time.sleep(0.3)
        self.assertIsNotNone(end, "watcher не дописал end после --kill")
        self.assertEqual(str(end.get("exit")), "4")
        self.assertEqual(_sup_chips(self.state), [])
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == rid]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "death_exit_4")
        _measure("MEASURE (2) --kill/TOMBSTONE → итоговый end 4 → чип "
                 "death_exit_4 (писатель — скан)")


# ---------------------------------------------------------------------------
# (3) смерть СО end (exit 4/124/125) → чип сканом
# ---------------------------------------------------------------------------


class TestDeathWithEnd(SupTemp):
    def test_oversight_end_death_set_chip_by_scan(self):
        now = time.time()
        for i, code in enumerate(("4", "124", "125")):
            rid = "DW%d" % i
            _seed_journal(self.state, [
                {"ts": now - 300, "kind": "start", "id": rid,
                 "engine": "local", "front": FRONT, "role": ROLE_PROS},
                {"ts": now - 200, "kind": "end", "id": rid, "exit": int(code)},
            ])
            self.assertEqual(
                [c for c in _sup_chips(self.state) if c.get("id") == rid], [])
        orchlib.supervision_dead_scan(state=self.state)
        chips = {c.get("id"): c for c in _sup_chips(self.state)}
        for i, code in enumerate(("4", "124", "125")):
            rid = "DW%d" % i
            self.assertIn(rid, chips)
            self.assertEqual(chips[rid].get("cause"),
                             "death_exit_%s" % code)
        _measure("MEASURE (3) смерть СО end 4/124/125 → чип (писатель — скан)")


# ---------------------------------------------------------------------------
# (4) нестарты 2/3/10/11/12 → end-пара + чип (писатель — обёртка)
# ---------------------------------------------------------------------------


class TestNonstartPairAndChip(SupTemp):
    def test_nonstart_exit2_pair_and_chip(self):
        missing = os.path.join(self.state, "nope.md")
        r = self._run_wrapper(
            ["--id", "NS2", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", missing])
        self.assertEqual(r.returncode, 2)
        j = _read_journal(self.state)
        starts = [e for e in j if e.get("kind") == "start"
                  and e.get("id") == "NS2"]
        ends = [e for e in j if e.get("kind") == "end"
                and e.get("id") == "NS2"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].get("front"), FRONT)
        self.assertEqual(starts[0].get("role"), ROLE_PROS)
        self.assertEqual(len(ends), 1)
        self.assertEqual(str(ends[0].get("exit")), "2")
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "NS2"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "nonstart_exit_2")
        self.assertEqual(chips[0].get("front"), FRONT)
        _measure("MEASURE (4a) нестарт exit 2 → end-пара + чип (обёртка)")

    def test_nonstart_exit3_mocked_binary(self):
        prompt = self._prompt("p-ns3.md")
        real = rex.find_cursor_agent
        rex.find_cursor_agent = lambda: None
        try:
            rc = self._main_argv(
                ["--id", "NS3", "--front", FRONT, "--role", ROLE_PROS,
                 "--prompt-file", prompt])
        finally:
            rex.find_cursor_agent = real
        self.assertEqual(rc, 3)
        j = _read_journal(self.state)
        self.assertEqual(len([e for e in j if e.get("kind") == "start"
                              and e.get("id") == "NS3"]), 1)
        self.assertEqual(len([e for e in j if e.get("kind") == "end"
                              and e.get("id") == "NS3"]), 1)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "NS3"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "nonstart_exit_3")
        _measure("MEASURE (4b) нестарт exit 3 (мок find_cursor_agent) → "
                 "пара + чип")

    def test_nonstart_exit10_broken_params(self):
        prompt = self._prompt("p-ns10.md")
        with open(os.path.join(self.state, "params.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ битый json\n")
        rc = self._main_argv(
            ["--id", "NS10", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt])
        self.assertEqual(rc, 10)
        j = _read_journal(self.state)
        self.assertEqual(len([e for e in j if e.get("kind") == "start"
                              and e.get("id") == "NS10"]), 1)
        ends = [e for e in j if e.get("kind") == "end"
                and e.get("id") == "NS10"]
        self.assertEqual(len(ends), 1)
        self.assertEqual(str(ends[0].get("exit")), "10")
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "NS10"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "nonstart_exit_10")
        _measure("MEASURE (4c) нестарт exit 10 (битый params) → пара + чип")

    def test_nonstart_exit11_dup_id(self):
        child = _sleep_child(60)
        try:
            pid_path = os.path.join(self.state, "cursor-run-NS11.pid")
            rex.write_pid_file(pid_path, child.pid)
            prompt = self._prompt("p-ns11.md")
            rc = self._main_argv(
                ["--id", "NS11", "--front", FRONT, "--role", ROLE_PROS,
                 "--prompt-file", prompt])
            self.assertEqual(rc, 11)
            j = _read_journal(self.state)
            self.assertEqual(len([e for e in j if e.get("kind") == "start"
                                  and e.get("id") == "NS11"]), 1)
            ends = [e for e in j if e.get("kind") == "end"
                    and e.get("id") == "NS11"]
            self.assertEqual(len(ends), 1)
            self.assertEqual(str(ends[0].get("exit")), "11")
            chips = [c for c in _sup_chips(self.state)
                     if c.get("id") == "NS11"]
            self.assertEqual(len(chips), 1)
            self.assertEqual(chips[0].get("cause"), "nonstart_exit_11")
            _measure("MEASURE (4d) нестарт exit 11 (дубль-id) → пара + чип")
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)

    def test_nonstart_exit12_unknown_oversight_role(self):
        # oversight по хвосту, но файла нет в каталоге → exit 12 + чип
        ghost = "x/meta/front-prosecutor.md"
        prompt = self._prompt("p-ns12.md")
        r = self._run_wrapper(
            ["--id", "NS12", "--front", FRONT, "--role", ghost,
             "--prompt-file", prompt])
        self.assertEqual(r.returncode, 12)
        j = _read_journal(self.state)
        starts = [e for e in j if e.get("kind") == "start"
                  and e.get("id") == "NS12"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0].get("role"), ghost)
        self.assertEqual(len([e for e in j if e.get("kind") == "end"
                              and e.get("id") == "NS12"]), 1)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "NS12"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "nonstart_exit_12")
        _measure("MEASURE (4e) нестарт exit 12 (роль не найдена) → пара + чип")


# ---------------------------------------------------------------------------
# (5) гейт-отказы 5–9,13 → пара без чипа; НЕ-надзорные роли неизменны
# ---------------------------------------------------------------------------


class TestGateRefusesNoChip(SupTemp):
    def _assert_pair_no_chip(self, rid, rc):
        self.assertEqual(rc[0], rc[1], "unexpected exit")
        j = _read_journal(self.state)
        self.assertEqual(len([e for e in j if e.get("kind") == "start"
                              and e.get("id") == rid]), 1,
                         "gate-refuse пара пишется (как раньше)")
        ends = [e for e in j if e.get("kind") == "end" and e.get("id") == rid]
        self.assertEqual(len(ends), 1)
        self.assertEqual(str(ends[0].get("exit")), str(rc[1]))
        self.assertEqual(
            [c for c in _sup_chips(self.state) if c.get("id") == rid],
            [], "легитимный гейт-отказ — БЕЗ чипа supervision_dead")

    def test_exit5_secret_pair_no_chip(self):
        prompt = self._prompt(
            "p-g5.md",
            text="роль: %s\n\nключ sk-abcdefghijklmnopqrstuvwxyz12 в промте\n"
                 % ROLE_PROS)
        rc = self._main_argv(
            ["--id", "G5", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt])
        self._assert_pair_no_chip("G5", (rc, 5))
        _measure("MEASURE (5a) гейт-отказ 5 (секрет) → пара без чипа")

    def test_exit6_front_closed_pair_no_chip(self):
        _write_json(os.path.join(self.state, "fronts.json"), {
            "goal": "k1", "fronts": [
                {"id": FRONT, "title": "k1", "status": "cancelled"}],
        })
        prompt = self._prompt("p-g6.md")
        rc = self._main_argv(
            ["--id", "G6", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt])
        self._assert_pair_no_chip("G6", (rc, 6))
        _measure("MEASURE (5b) гейт-отказ 6 (фронт закрыт) → пара без чипа")

    def test_exit7_budget_hard_pair_no_chip(self):
        _write_json(os.path.join(self.state, "params.json"), {
            "orchestration": {"hierarchy": "on", "enabled": True},
            "execution": {"retry_on_fail": 1, "timeout_s": 30},
            "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 1},
        })
        _write_json(os.path.join(
            self.state, "counters", "front-runs-%s.json" % FRONT),
            {"used": 1, "ts": time.time()})
        prompt = self._prompt("p-g7.md")
        rc = self._main_argv(
            ["--id", "G7", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt])
        self._assert_pair_no_chip("G7", (rc, 7))
        _measure("MEASURE (5c) гейт-отказ 7 (hard budget) → пара без чипа")

    def test_exit8_front_required_pair_no_chip(self):
        prompt = self._prompt("p-g8.md")
        rc = self._main_argv(
            ["--id", "G8", "--role", ROLE_PROS, "--prompt-file", prompt])
        self._assert_pair_no_chip("G8", (rc, 8))
        _measure("MEASURE (5d) гейт-отказ 8 (FRONT_REQUIRED) → пара без чипа")

    def test_exit9_lock_busy_pair_no_chip(self):
        counters = os.path.join(self.state, "counters")
        os.makedirs(counters, exist_ok=True)
        lock = os.path.join(
            counters, "front-runs-%s.json.lock" % FRONT)
        held = orchlib._dir_lock_acquire(lock, timeout_s=1.0)
        self.assertTrue(held)
        try:
            prompt = self._prompt("p-g9.md")
            rc = self._main_argv(
                ["--id", "G9", "--front", FRONT, "--role", ROLE_PROS,
                 "--prompt-file", prompt])
            self._assert_pair_no_chip("G9", (rc, 9))
        finally:
            orchlib._dir_lock_release(lock)
        _measure("MEASURE (5e) гейт-отказ 9 (front-runs lock busy) → пара "
                 "без чипа")

    def test_exit13_unreachable_for_oversight(self):
        # надзор не берёт write-lock: dual-writer-отказ недостижим (как раньше)
        child = _sleep_child(60)
        try:
            rex.write_pid_file(
                os.path.join(self.state, "cursor-run-W1.pid"), child.pid)
            _seed_journal(self.state, [{
                "ts": time.time(), "kind": "start", "id": "W1",
                "engine": "local", "front": FRONT,
                "role": "code/coder.md",
            }])
            rc, msg = rex.check_dual_writer_guard(
                FRONT, "G13", self.state, role=ROLE_PROS)
            self.assertIsNone(rc)
            self.assertEqual(_sup_chips(self.state), [])
            _measure("MEASURE (5f) oversight не dual-writer → exit 13 "
                     "недостижим, поведение неизменно")
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)

    def test_non_oversight_roles_journal_snapshot_unchanged(self):
        # снимок до/после: НЕ-надзорные 2/3 остаются БЕЗ journal (как раньше)
        before = _read_journal(self.state)
        rc2 = self._run_wrapper(
            ["--id", "PLAIN2", "--front", FRONT, "--role", "code/coder.md",
             "--prompt-file", os.path.join(self.state, "missing.md")])
        self.assertEqual(rc2.returncode, 2)
        prompt = self._prompt("p-plain3.md", role="code/coder.md")
        real = rex.find_cursor_agent
        rex.find_cursor_agent = lambda: None
        try:
            rc3 = self._main_argv(
                ["--id", "PLAIN3", "--front", FRONT,
                 "--prompt-file", prompt])
        finally:
            rex.find_cursor_agent = real
        self.assertEqual(rc3, 3)
        self.assertEqual(_read_journal(self.state), before,
                         "НЕ-надзорные роли: journal не изменился")
        self.assertEqual(_sup_chips(self.state), [])
        _measure("MEASURE (5g) НЕ-надзорные роли 2/3 — снимок до/после "
                 "идентичен (поведение неизменно)")


# ---------------------------------------------------------------------------
# (6) ложные окна: retry в полёте / pid удалён в grace / свежий start ≤60 с
#     без pid / end 0 → тишина
# ---------------------------------------------------------------------------


class TestFalseWindowsSilence(SupTemp):
    def _seed_start(self, rid, ts, session=None, role=ROLE_PROS):
        entry = {"ts": ts, "kind": "start", "id": rid, "engine": "local",
                 "front": FRONT, "role": role}
        if session:
            entry["session"] = session
        _seed_journal(self.state, [entry])

    def test_alive_pid_silence(self):
        child = _sleep_child(60)
        try:
            self._seed_start("FW1", time.time() - 300)
            rex.write_pid_file(
                os.path.join(self.state, "cursor-run-FW1.pid"), child.pid)
            self.assertEqual(orchlib.supervision_dead_events(
                state=self.state), [])
            _measure("MEASURE (6a) живой pid → тишина")
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)

    def test_retry_in_flight_silence(self):
        now = time.time()
        self._seed_start("FW2", now - 30)
        with open(os.path.join(self.state, "cursor-run-FW2.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        with open(os.path.join(self.state, "cursor-run-FW2.log"), "w",
                  encoding="utf-8") as f:
            f.write("RETRY=1/1 (prev EXIT=4)\n")
        self.assertEqual(orchlib.supervision_dead_events(
            state=self.state), [])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(_sup_chips(self.state), [])
        _measure("MEASURE (6b) retry в полёте (мёртвый pid + RETRY-маркер) "
                 "→ тишина")

    def test_fresh_start_without_pid_silence(self):
        self._seed_start("FW3", time.time() - 10)
        self.assertEqual(orchlib.supervision_dead_events(
            state=self.state), [])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(_sup_chips(self.state), [])
        _measure("MEASURE (6c) свежий start ≤60 с без pid → тишина (grace)")

    def test_pid_removed_in_grace_exit_marker_silence(self):
        now = time.time()
        self._seed_start("FW4", now - 300)
        with open(os.path.join(self.state, "cursor-run-FW4.log"), "w",
                  encoding="utf-8") as f:
            f.write("FAKE_AGENT_START\n\nEXIT=0\n")
        self.assertEqual(orchlib.supervision_dead_events(
            state=self.state), [])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(_sup_chips(self.state), [])
        _measure("MEASURE (6d) pid-файл удалён в grace, EXIT-маркер дописан "
                 "→ тишина (ждать end ≤60 с)")

    def test_end0_silence(self):
        now = time.time()
        self._seed_start("FW5", now - 300)
        _seed_journal(self.state, [
            {"ts": now - 100, "kind": "end", "id": "FW5", "exit": 0}])
        self.assertEqual(orchlib.supervision_dead_events(
            state=self.state), [])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(_sup_chips(self.state), [])
        _measure("MEASURE (6e) end 0 надзора → тишина")


# ---------------------------------------------------------------------------
# (7) сторож мёртв/не спавнился → чип всё равно появляется сканом
# ---------------------------------------------------------------------------


class TestWatcherDeadScanCovers(SupTemp):
    def test_no_watchdog_scan_writes_chip(self):
        now = time.time()
        _seed_journal(self.state, [
            {"ts": now - 300, "kind": "start", "id": "WD1",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
        ])
        with open(os.path.join(self.state, "cursor-run-WD1.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        with open(os.path.join(self.state, "cursor-run-WD1.log"), "w",
                  encoding="utf-8") as f:
            f.write("FAKE_AGENT_START\n")
        self.assertEqual(_sup_chips(self.state), [])
        events = orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual([e.get("id") for e in events], ["WD1"])
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "WD1"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "runtime_pid_dead")
        _measure("MEASURE (7a) сторож не спавнился → чип появляется сканом")

    def test_sigkill_watchdog_scan_writes_chip(self):
        fake = _fake_agent_dir(self.state)
        rid = "WD2"
        prompt = self._prompt("p-wd2.md")
        wrapper = subprocess.Popen(
            [sys.executable, RUN_EXEC, "--id", rid, "--front", FRONT,
             "--role", ROLE_PROS, "--prompt-file", prompt,
             "--no-reground-line", "--yield-after", "0"],
            cwd=REPO, env=self._env(fake),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
        try:
            deadline = time.time() + 20
            pid = None
            while time.time() < deadline:
                if os.path.isfile(pid_path):
                    pid = rex.read_pid_file(pid_path)
                    if pid and rex.pid_alive(pid):
                        break
                time.sleep(0.2)
            self.assertTrue(pid and rex.pid_alive(pid))
            supervise_pids = self._find_supervise_pids(rid)
            self.assertTrue(supervise_pids, "сторож не найден")
            wrapper.send_signal(signal.SIGKILL)
            wrapper.wait(timeout=10)
            for spid in supervise_pids:
                os.kill(spid, signal.SIGKILL)
            os.kill(pid, signal.SIGKILL)
            time.sleep(1.0)
            self.assertEqual(_sup_chips(self.state), [],
                             "сторож мёртв — чипа нет до скана")
            orchlib.supervision_dead_scan(state=self.state)
            chips = [c for c in _sup_chips(self.state)
                     if c.get("id") == rid]
            self.assertEqual(len(chips), 1)
            _measure("MEASURE (7b) сторож SIGKILL → чип всё равно сканом")
        finally:
            try:
                wrapper.send_signal(signal.SIGKILL)
                wrapper.wait(timeout=5)
            except Exception:
                pass

    def _find_supervise_pids(self, rid):
        out = []
        me = os.getpid()
        for pid_s in os.listdir("/proc"):
            if not pid_s.isdigit() or int(pid_s) == me:
                continue
            try:
                with open("/proc/%s/cmdline" % pid_s, "rb") as f:
                    parts = f.read().decode("utf-8", "replace").split("\0")
            except Exception:
                continue
            if "--__supervise" in parts and rid in parts:
                out.append(int(pid_s))
        return out


# ---------------------------------------------------------------------------
# (8) повторный скан не добавляет второй чип; повторный нестарт — без дубля
# ---------------------------------------------------------------------------


class TestDedup(SupTemp):
    def test_rescan_no_duplicate_chip(self):
        now = time.time()
        _seed_journal(self.state, [
            {"ts": now - 300, "kind": "start", "id": "DD1",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
            {"ts": now - 200, "kind": "end", "id": "DD1", "exit": 4},
        ])
        orchlib.supervision_dead_scan(state=self.state)
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "DD1"]
        self.assertEqual(len(chips), 1, "повторный скан — без второго чипа")
        # дедуп emit напрямую
        self.assertFalse(orchlib.emit_supervision_dead_chip(
            FRONT, "DD1", cause="death_exit_4", role=ROLE_PROS,
            state=self.state))
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "DD1"]), 1)
        _measure("MEASURE (8a) повторный скан/emit → один чип (дедуп)")

    def test_repeat_wrapper_nonstart_no_duplicate(self):
        missing = os.path.join(self.state, "nope.md")
        for _ in range(2):
            r = self._run_wrapper(
                ["--id", "DD2", "--front", FRONT, "--role", ROLE_PROS,
                 "--prompt-file", missing])
            self.assertEqual(r.returncode, 2)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "DD2"]
        self.assertEqual(len(chips), 1, "повторный нестарт того же id — "
                                        "без дубля чипа")
        _measure("MEASURE (8b) повторная обёртка-нестарт того же id → один "
                 "чип")


# ---------------------------------------------------------------------------
# (9) снятие в health: end 0 ТОГО ЖЕ класса со start новее смерти
# ---------------------------------------------------------------------------


class TestHealthClearing(SupTemp):
    def test_same_class_end0_clears_health(self):
        now = time.time()
        _seed_journal(self.state, [
            {"ts": now - 300, "kind": "start", "id": "HC1",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
        ])
        with open(os.path.join(self.state, "cursor-run-HC1.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "HC1"]
        self.assertEqual(len(chips), 1)
        ids = orchlib.supervision_dead_ids(state=self.state)
        self.assertIn("HC1", ids)
        # возрождение: успешный прокурор, старт новее смерти (чипа)
        t2 = time.time()
        _seed_journal(self.state, [
            {"ts": t2, "kind": "start", "id": "HC2",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
            {"ts": t2 + 1, "kind": "end", "id": "HC2", "exit": 0},
        ])
        ids2 = orchlib.supervision_dead_ids(state=self.state)
        self.assertNotIn("HC1", ids2, "end 0 того же класса новее смерти — "
                                      "чип снят в health")
        self.assertNotIn("HC2", ids2)
        # journal-чип при этом остаётся (снятие — только health-семантика)
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "HC1"]), 1)
        _measure("MEASURE (9a) смерть прокурора + end 0 прокурора новее → "
                 "чипа нет в health (journal-чип остаётся)")

    def test_other_class_end0_does_not_clear(self):
        now = time.time()
        _seed_journal(self.state, [
            {"ts": now - 300, "kind": "start", "id": "HC3",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
        ])
        with open(os.path.join(self.state, "cursor-run-HC3.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        orchlib.supervision_dead_scan(state=self.state)
        t2 = time.time()
        _seed_journal(self.state, [
            {"ts": t2, "kind": "start", "id": "HC4",
             "engine": "local", "front": FRONT, "role": ROLE_OBS},
            {"ts": t2 + 1, "kind": "end", "id": "HC4", "exit": 0},
        ])
        ids = orchlib.supervision_dead_ids(state=self.state)
        self.assertIn("HC3", ids, "end 0 ДРУГОГО класса (observer) не гасит "
                                  "смерть прокурора")
        _measure("MEASURE (9b) observer end 0 не гасит смерть prosecutor")


# ---------------------------------------------------------------------------
# (10) label в HEALTH_CHIP_LABELS; журнал >5000 строк — поведение стабильно
# ---------------------------------------------------------------------------


class TestPanelLabelAndBigJournal(SupTemp):
    def test_label_in_health_chip_labels(self):
        with open(PANEL_INDEX, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("HEALTH_CHIP_LABELS", html)
        self.assertIn("supervision_dead:", html)
        # ключ пасс-тру в /api/health (panel/server.py — counts/ids)
        server = _load_mod("panel_server_supd",
                           os.path.join(REPO, "panel", "server.py"))
        payload = server._health_payload()
        self.assertIn("supervision_dead", payload["counts"])
        self.assertIn("supervision_dead", payload["ids"])
        _measure("MEASURE (10a) label supervision_dead в "
                 "HEALTH_CHIP_LABELS + ключ в /api/health")

    def test_big_journal_beyond_window_stable(self):
        now = time.time()
        # смерть за окном 5000 строк: событие первым, затем 5100 филлеров
        entries = [
            {"ts": now - 500, "kind": "start", "id": "BJ1",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
            {"ts": now - 400, "kind": "end", "id": "BJ1", "exit": 124},
        ]
        for i in range(5100):
            entries.append({"ts": now - 399 + i * 0.001, "kind": "note",
                            "id": "filler-%d" % i})
        _seed_journal(self.state, entries)
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "BJ1"]
        self.assertEqual(len(chips), 1,
                         "полный журнал: событие за окном 5000 видно")
        health = orchlib.health_red_chips(state=self.state)
        self.assertIn("BJ1", health.get("supervision_dead") or [])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "BJ1"]), 1, "повторный скан — стабильно")
        _measure("MEASURE (10b) журнал >5000 строк → детектор по полному "
                 "журналу стабилен")


# ---------------------------------------------------------------------------
# FOLLOW-UP (круг ревью ×3): дубль-id, свежесть маркера, единое окно,
# диспетчер, session-кодировка, дедуп, readonly
# ---------------------------------------------------------------------------


class TestFollowUpDupIdDoesNotMaskDeath(SupTemp):
    """(follow-up 1) дубль-id не гасит смерть живого оригинала."""

    def test_dup_rejected_then_original_killed_chip(self):
        fake = _fake_agent_dir(self.state)
        rid = "DUPF"
        prompt = self._prompt("p-dupf.md")
        wrapper = subprocess.Popen(
            [sys.executable, RUN_EXEC, "--id", rid, "--front", FRONT,
             "--role", ROLE_PROS, "--prompt-file", prompt,
             "--no-reground-line", "--yield-after", "0"],
            cwd=REPO, env=self._env(fake),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        pid_path = os.path.join(self.state, "cursor-run-%s.pid" % rid)
        try:
            deadline = time.time() + 20
            pid = None
            while time.time() < deadline:
                if os.path.isfile(pid_path):
                    pid = rex.read_pid_file(pid_path)
                    if pid and rex.pid_alive(pid):
                        break
                time.sleep(0.2)
            self.assertTrue(pid and rex.pid_alive(pid), "оригинал не жив")
            # дубль-id отвергнут: пара + чип nonstart_exit_11
            r2 = self._run_wrapper(
                ["--id", rid, "--front", FRONT, "--role", ROLE_PROS,
                 "--prompt-file", prompt])
            self.assertEqual(r2.returncode, 11)
            chips = [c for c in _sup_chips(self.state)
                     if c.get("id") == rid]
            self.assertEqual(len(chips), 1)
            self.assertEqual(chips[0].get("cause"), "nonstart_exit_11")
            # сторож оригинала НЕ вышел по чужому end: kill оригинала мимо
            # обёртки → чип смерти ≤60 с (дубль-id не гасит чужую смерть)
            t_kill = time.time()
            wrapper.send_signal(signal.SIGKILL)
            wrapper.wait(timeout=10)
            os.kill(pid, signal.SIGKILL)
            death_chip = None
            deadline = time.time() + 60
            while time.time() < deadline:
                death_chips = [c for c in _sup_chips(self.state)
                               if c.get("id") == rid
                               and c.get("cause") == "runtime_pid_dead"]
                if death_chips:
                    death_chip = death_chips[0]
                    break
                time.sleep(0.3)
            self.assertIsNotNone(death_chip,
                                 "смерть оригинала не детектирована ≤60с")
            self.assertLessEqual(time.time() - t_kill, 60.0)
            # скан тоже видит смерть оригинала (инкарнация не погашена)
            events = orchlib.supervision_dead_events(state=self.state)
            self.assertIn(
                rid, [e.get("id") for e in events
                      if e.get("cause") == "runtime_pid_dead"])
            ids = orchlib.supervision_dead_ids(state=self.state)
            self.assertIn(rid, ids)
            _measure("MEASURE (FU1) живой надзор + дубль-id отвергнут → "
                     "kill оригинала → чип runtime_pid_dead ≤60 с")
        finally:
            try:
                wrapper.send_signal(signal.SIGKILL)
                wrapper.wait(timeout=5)
            except Exception:
                pass


class TestFollowUpMarkerFreshness(SupTemp):
    """(follow-up 2) маркер/TOMBSTONE — тишина только ≤60 с, не бессрочно."""

    def _seed_open_run(self, rid, ts=None):
        _seed_journal(self.state, [{
            "ts": ts if ts is not None else time.time() - 300,
            "kind": "start", "id": rid, "engine": "local",
            "front": FRONT, "role": ROLE_PROS,
        }])

    def test_stale_retry_marker_chip_by_scan(self):
        self._seed_open_run("MF1")
        log = os.path.join(self.state, "cursor-run-MF1.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("RETRY=1/1 (prev EXIT=4)\n")
        stale = time.time() - 120
        os.utime(log, (stale, stale))
        with open(os.path.join(self.state, "cursor-run-MF1.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "MF1"]
        self.assertEqual(len(chips), 1,
                         "замороженный RETRY-маркер (>60с) — не индульгенция")
        self.assertEqual(chips[0].get("cause"), "runtime_pid_dead")
        _measure("MEASURE (FU2a) RETRY-маркер + мёртвый pid + лог заморожен "
                 ">60с → чип сканом")

    def test_stale_exit_marker_chip_by_scan(self):
        # гибель обёртки в зазоре EXIT=→journal_end
        self._seed_open_run("MF2")
        log = os.path.join(self.state, "cursor-run-MF2.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("FAKE_AGENT_START\n\nEXIT=4\n")
        stale = time.time() - 120
        os.utime(log, (stale, stale))
        with open(os.path.join(self.state, "cursor-run-MF2.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        orchlib.supervision_dead_scan(state=self.state)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "MF2"]
        self.assertEqual(len(chips), 1,
                         "замороженный EXIT= без end (>60с) → чип")
        self.assertEqual(chips[0].get("cause"), "runtime_pid_dead")
        _measure("MEASURE (FU2b) зазор EXIT=→end при гибели обёртки → чип")

    def test_stale_marker_chip_by_watchdog(self):
        self._seed_open_run("MF3")
        log = os.path.join(self.state, "cursor-run-MF3.log")
        with open(log, "w", encoding="utf-8") as f:
            f.write("RETRY=1/1 (prev EXIT=4)\n")
        stale = time.time() - 120
        os.utime(log, (stale, stale))
        pid_path = os.path.join(self.state, "cursor-run-MF3.pid")
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        t0 = time.time()
        rex.supervision_watch(
            "MF3", self.state, None, FRONT, ROLE_PROS, pid_path, log,
            time.time() - 300, poll_s=0.3)
        chips = [c for c in _sup_chips(self.state)
                 if c.get("id") == "MF3"
                 and c.get("cause") == "runtime_pid_dead"]
        self.assertEqual(len(chips), 1, "сторож чипует по протухшему маркеру")
        self.assertLess(time.time() - t0, 60.0)
        _measure("MEASURE (FU2c) сторож: мёртвый pid + протухший маркер → "
                 "чип ≤60 с (не выходит в вечную тишину)")

    def test_fresh_tombstone_silence_stale_tombstone_chip(self):
        # свежий TOMBSTONE (--kill в обработке) — тишина; протухший — чип
        self._seed_open_run("MF4")
        log = os.path.join(self.state, "cursor-run-MF4.log")
        open(log, "w", encoding="utf-8").close()
        pid_path = os.path.join(self.state, "cursor-run-MF4.pid")
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        ts_path = os.path.join(self.state, "cursor-run-MF4.TOMBSTONE")
        open(ts_path, "w", encoding="utf-8").close()
        self.assertEqual(orchlib.supervision_dead_events(
            state=self.state), [], "свежий TOMBSTONE — тишина")
        stale = time.time() - 120
        os.utime(ts_path, (stale, stale))
        events = orchlib.supervision_dead_events(state=self.state)
        self.assertEqual([e.get("cause") for e in events],
                         ["runtime_pid_dead"],
                         "протухший TOMBSTONE без end — чип")
        _measure("MEASURE (FU2d) TOMBSTONE свеж → тишина; протухший (>60с) "
                 "без end → событие смерти")


class TestFollowUpNoPidSingleWindow(SupTemp):
    """(follow-up 3) pid никогда не появлялся — ЕДИНОЕ окно ≤60 с."""

    def test_never_pid_chip_within_single_window(self):
        start_ts = time.time() - 59.0
        _seed_journal(self.state, [{
            "ts": start_ts, "kind": "start", "id": "NP1",
            "engine": "local", "front": FRONT, "role": ROLE_PROS,
        }])
        # ни pid-файла, ни лога, ни маркера: сторож обязан дать чип по
        # истечении единственного 60-с окна от journal_start (не ~120 с)
        t0 = time.time()
        rex.supervision_watch(
            "NP1", self.state, None, FRONT, ROLE_PROS,
            os.path.join(self.state, "cursor-run-NP1.pid"),
            os.path.join(self.state, "cursor-run-NP1.log"),
            start_ts, poll_s=0.3)
        elapsed_call = time.time() - t0
        self.assertLess(elapsed_call, 10.0,
                        "чип в первом окне, без второго 60-с ожидания")
        chips = [c for c in _sup_chips(self.state)
                 if c.get("id") == "NP1"
                 and c.get("cause") == "runtime_no_pid"]
        self.assertEqual(len(chips), 1)
        self.assertLess(
            chips[0].get("ts", 0) - start_ts, 65.0,
            "сигнал ≤60 с от journal_start (+поллинг)")
        _measure("MEASURE (FU3) pid не появился → чип runtime_no_pid в "
                 "единственном окне ≤60 с от journal_start")


class TestFollowUpExit10Dispatcher(SupTemp):
    """(follow-up 4) exit-10 хук — только путь запуска рана."""

    def _break_params(self):
        with open(os.path.join(self.state, "params.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ битый json\n")

    def test_status_and_kill_no_false_chip(self):
        self._break_params()
        rc = self._main_argv(["--status", "--id", "ST10", "--role", ROLE_PROS])
        self.assertEqual(rc, 10)
        rc2 = self._main_argv(["--kill", "K10"])
        self.assertEqual(rc2, 10)
        rc3 = self._main_argv(["--list"])
        self.assertEqual(rc3, 10)
        self.assertEqual(_read_journal(self.state), [],
                         "управляющие команды при битом params — без пар/чипов")
        _measure("MEASURE (FU4a) --status/--kill/--list при битом params → "
                 "exit 10 без ложного чипа supervision_dead")

    def test_launch_path_still_pairs_and_chips(self):
        self._break_params()
        prompt = self._prompt("p-fu4.md")
        rc = self._main_argv(
            ["--id", "NS10F", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", prompt])
        self.assertEqual(rc, 10)
        j = _read_journal(self.state)
        self.assertEqual(len([e for e in j if e.get("kind") == "start"
                              and e.get("id") == "NS10F"]), 1)
        chips = [c for c in _sup_chips(self.state)
                 if c.get("id") == "NS10F"]
        self.assertEqual(len(chips), 1,
                         "путь запуска рана — пара + чип как раньше")
        _measure("MEASURE (FU4b) запуск рана при битом params → пара + чип "
                 "(поведение сохранено)")


class TestFollowUpSessionEncoding(SupTemp):
    """(follow-up 5) session id с кодировкой/усечением — без ложного no_pid."""

    def _seed_session_run(self, rid, sid, pid, ts=None):
        sdir = orchlib.session_dir(sid)  # кодировка/усечение как в проде
        run_dir = os.path.join(sdir, "runs", rid)
        os.makedirs(run_dir, exist_ok=True)
        if pid is not None:
            rex.write_pid_file(os.path.join(run_dir, "run.pid"), pid)
        _seed_journal(self.state, [{
            "ts": ts if ts is not None else time.time() - 300,
            "kind": "start", "id": rid, "engine": "local",
            "front": FRONT, "role": ROLE_PROS, "session": sid,
        }])

    def test_encodable_session_live_pid_silence(self):
        child = _sleep_child(60)
        try:
            sid = "séssion ид + 2026/окт"
            self._seed_session_run("SE1", sid, child.pid)
            self.assertEqual(orchlib.supervision_dead_events(
                state=self.state), [],
                "кодируемый session id: живой pid найден — тишина")
            _measure("MEASURE (FU5a) session id с percent-кодировкой + "
                     "живой pid → тишина (пути совпадают с session_dir)")
        finally:
            child.send_signal(signal.SIGKILL)
            child.wait(timeout=5)

    def test_long_session_dead_pid_detected(self):
        sid = "session-" + "x" * 80  # длиннее SESSION_ID_MAX → усечение
        self._seed_session_run("SE2", sid, _dead_pid())
        events = orchlib.supervision_dead_events(state=self.state)
        self.assertEqual(
            [(e.get("id"), e.get("cause")) for e in events],
            [("SE2", "runtime_pid_dead")],
            "усечённый session id: мёртвый pid детектирован в верном пути")
        _measure("MEASURE (FU5b) длинный session id (>64) + мёртвый pid → "
                 "смерть детектирована (без ложного runtime_no_pid)")


class TestFollowUpDedupSemantics(SupTemp):
    """(follow-up 7) дедуп: ключ (name, front/id) + непогашенность + класс."""

    def test_front_in_dedup_key(self):
        self.assertTrue(orchlib.emit_supervision_dead_chip(
            "F-A", "DD7", cause="death_exit_4", role=ROLE_PROS,
            state=self.state))
        # тот же id на ДРУГОМ фронте — отдельное событие, чип пишется
        self.assertTrue(orchlib.emit_supervision_dead_chip(
            "F-B", "DD7", cause="death_exit_4", role=ROLE_PROS,
            state=self.state))
        # тот же (front, id) — дедуп
        self.assertFalse(orchlib.emit_supervision_dead_chip(
            "F-A", "DD7", cause="death_exit_4", role=ROLE_PROS,
            state=self.state))
        chips = [c for c in _sup_chips(self.state)
                 if c.get("id") == "DD7"]
        self.assertEqual(len(chips), 2)
        _measure("MEASURE (FU7a) дедуп-ключ включает front: чужой фронт — "
                 "второй легитимный чип")

    def test_event_class_split(self):
        # нестарт и смерть — разные события: оба чипа живут
        self.assertTrue(orchlib.emit_supervision_dead_chip(
            FRONT, "DD8", cause="nonstart_exit_11", role=ROLE_PROS,
            state=self.state))
        self.assertTrue(orchlib.emit_supervision_dead_chip(
            FRONT, "DD8", cause="runtime_pid_dead", role=ROLE_PROS,
            state=self.state))
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "DD8"]), 2)
        # но второй чип того же класса — дедуп (сторож vs скан одной смерти)
        self.assertFalse(orchlib.emit_supervision_dead_chip(
            FRONT, "DD8", cause="death_exit_4", role=ROLE_PROS,
            state=self.state))
        _measure("MEASURE (FU7b) класс события в ключе: nonstart ≠ death; "
                 "внутри класса — один чип на событие")

    def test_cleared_chip_may_be_rewritten(self):
        now = time.time()
        # смерть без end → чип
        with open(os.path.join(self.state, "cursor-run-DD9.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        _seed_journal(self.state, [{
            "ts": now - 300, "kind": "start", "id": "DD9",
            "engine": "local", "front": FRONT, "role": ROLE_PROS,
        }])
        orchlib.supervision_dead_scan(state=self.state)
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "DD9"]), 1)
        # возрождение: end 0 того же класса со start новее чипа
        time.sleep(0.05)
        t2 = time.time()
        _seed_journal(self.state, [
            {"ts": t2, "kind": "start", "id": "DD9R",
             "engine": "local", "front": FRONT, "role": ROLE_PROS},
            {"ts": t2 + 0.1, "kind": "end", "id": "DD9R", "exit": 0},
        ])
        self.assertNotIn("DD9", orchlib.supervision_dead_ids(
            state=self.state))
        # снятый (погашенный) чип не глушит новую смерть того же id
        time.sleep(0.05)
        with open(os.path.join(self.state, "cursor-run-DD9.pid"), "w",
                  encoding="utf-8") as f:
            f.write("%d\n" % _dead_pid())
        _seed_journal(self.state, [{
            "ts": time.time(), "kind": "start", "id": "DD9",
            "engine": "local", "front": FRONT, "role": ROLE_PROS,
        }])
        # у DD9 две инкарнации: старая (открытая) — единственный кандидат;
        # её смерть всё ещё событие: чип переписывается заново (новый ts)
        before = [c.get("ts") for c in _sup_chips(self.state)
                  if c.get("id") == "DD9"]
        wrote = orchlib.emit_supervision_dead_chip(
            FRONT, "DD9", cause="runtime_pid_dead", role=ROLE_PROS,
            state=self.state)
        self.assertTrue(wrote, "погашенный чип не бессрочный — пишется заново")
        after = [c.get("ts") for c in _sup_chips(self.state)
                 if c.get("id") == "DD9"]
        self.assertEqual(len(after), len(before) + 1)
        self.assertIn("DD9", orchlib.supervision_dead_ids(state=self.state))
        _measure("MEASURE (FU7c) снятый чип (end 0 новее) не глушит новую "
                 "смерть того же id — чип пишется заново")

    def test_concurrent_emit_single_chip(self):
        wrote = []
        barrier = threading.Barrier(8)

        def _emit():
            barrier.wait()
            wrote.append(orchlib.emit_supervision_dead_chip(
                FRONT, "DD10", cause="death_exit_4", role=ROLE_PROS,
                state=self.state))

        threads = [threading.Thread(target=_emit) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
        self.assertEqual(wrote.count(True), 1,
                         "атомарный дедуп: ровно один писатель успевает")
        self.assertEqual(
            len([c for c in _sup_chips(self.state)
                 if c.get("id") == "DD10"]), 1)
        _measure("MEASURE (FU7d) гонка 8 эмиттеров → один чип (flock)")


class TestFollowUpReadonlyPair(SupTemp):
    """(follow-up 8) readonly-флаг в нестарт-паре надзора."""

    def test_readonly_flag_in_nonstart_pair(self):
        missing = os.path.join(self.state, "nope.md")
        r = self._run_wrapper(
            ["--id", "RO1", "--front", FRONT, "--role", ROLE_PROS,
             "--prompt-file", missing, "--readonly"])
        self.assertEqual(r.returncode, 2)
        starts = [e for e in _read_journal(self.state)
                  if e.get("kind") == "start" and e.get("id") == "RO1"]
        self.assertEqual(len(starts), 1)
        self.assertIs(starts[0].get("readonly"), True,
                      "readonly-маркер в нестарт-паре (как journal_gate_refuse)")
        _measure("MEASURE (FU8) нестарт-пара надзора несёт readonly: true")


class TestFix2NonstartSessionEncoding(SupTemp):
    """(fix-r2, KR1) нестарт-цепочка exit 10/11 до дефолта prompt_file."""

    def test_exotic_session_nonstart_pair_and_chip(self):
        sid = "séssion ид + 2026/окт"  # требует percent-кодировки
        # промт ЛЕЖИТ по кодированному пути (как его создал бы прод),
        # шапка несёт роль надзора; --role/--prompt-file НЕ переданы
        run_dir = os.path.join(
            orchlib._supervision_session_root(self.state, sid),
            "runs", "FX1")
        os.makedirs(run_dir, exist_ok=True)
        with open(os.path.join(run_dir, "prompt.md"), "w",
                  encoding="utf-8") as f:
            f.write("роль: %s\n\nнестарт по кодированному пути\n" % ROLE_PROS)
        with open(os.path.join(self.state, "params.json"), "w",
                  encoding="utf-8") as f:
            f.write("{ битый json\n")
        rc = self._main_argv(
            ["--id", "FX1", "--front", FRONT, "--session", sid])
        self.assertEqual(rc, 10)
        j = _read_journal(self.state)
        starts = [e for e in j if e.get("kind") == "start"
                  and e.get("id") == "FX1"]
        self.assertEqual(len(starts), 1,
                         "нестарт-пара пишется и для кодируемого sid")
        self.assertEqual(starts[0].get("role"), ROLE_PROS)
        self.assertEqual(starts[0].get("session"), sid)
        chips = [c for c in _sup_chips(self.state) if c.get("id") == "FX1"]
        self.assertEqual(len(chips), 1)
        self.assertEqual(chips[0].get("cause"), "nonstart_exit_10")
        _measure("MEASURE (FX-1) экзотичный sid без --role/--prompt-file → "
                 "нестарт-пара + чип (fallback через session-кодировку)")


class TestFix2JournalStepRobustness(SupTemp):
    """(fix-r2, KR2+KR3) инкрементальный читатель журнала сторожа."""

    def _journal(self):
        return os.path.join(self.state, "journal.jsonl")

    def test_partial_line_completed_next_tick(self):
        # обрыв строки в момент снапшота: offset не должен перепрыгнуть её
        start_ts = time.time() - 100
        with open(self._journal(), "w", encoding="utf-8") as f:
            f.write(json.dumps({"ts": start_ts - 1, "kind": "start",
                                "id": "FX2"}) + "\n")
            f.write('{"ts": %f, "kind": "end", "id": "FX2"' % (start_ts + 1))
        offset, open_new, closed = rex._supervise_journal_step(
            self.state, "FX2", start_ts, None, 0)
        self.assertFalse(closed)
        with open(self._journal(), "a", encoding="utf-8") as f:
            f.write(', "exit": 0}\n')  # строка дописана в следующем тике
        offset2, open_new2, closed2 = rex._supervise_journal_step(
            self.state, "FX2", start_ts, offset, open_new)
        self.assertTrue(closed2, "дописанная строка дочитана — не потеряна")
        _measure("MEASURE (FX-2a) offset только до последнего \\n — обрыв "
                 "строки дочитывается следующим тиком")

    def test_offset_inside_utf8_char_not_stuck(self):
        start_ts = time.time() - 100
        line1 = json.dumps({"ts": start_ts - 1, "kind": "start",
                            "id": "FX3", "note": "кириллица-заметка"},
                           ensure_ascii=False)
        line2 = json.dumps({"ts": start_ts + 1, "kind": "end",
                            "id": "FX3", "exit": 0}, ensure_ascii=False)
        with open(self._journal(), "w", encoding="utf-8") as f:
            f.write(line1 + "\n")
            f.write(line2 + "\n")
        # offset внутрь многобайтового символа первой строки
        raw = line1.encode("utf-8")
        mid = raw.find("кириллица".encode("utf-8")) + 3
        offset, open_new, closed = rex._supervise_journal_step(
            self.state, "FX3", start_ts, mid, 0)
        self.assertTrue(closed, "seek посреди UTF-8 символа не клинит "
                                "читатель — end распарсен")
        _measure("MEASURE (FX-2b) чтение байтами + decode(replace): offset "
                 "внутри многобайтового символа не клинит")

    def test_truncation_recompute_resets_open_new(self):
        start_ts = time.time() - 100
        now = time.time()
        _seed_journal(self.state, [
            # наша инкарнация (ts ≤ start_ts — фильтруется) …
            {"ts": start_ts - 1, "kind": "start", "id": "FX4"},
            # … чужая более новая того же id (кладётся и снимается) …
            {"ts": now, "kind": "start", "id": "FX4"},
            {"ts": now + 1, "kind": "end", "id": "FX4", "exit": 11},
            # … и end НАШЕЙ инкарнации
            {"ts": now + 2, "kind": "end", "id": "FX4", "exit": 0},
        ])
        # ротация: offset за пределами усечённого файла, очередь «заедена»
        size = os.path.getsize(self._journal())
        offset, open_new, closed = rex._supervise_journal_step(
            self.state, "FX4", start_ts, size + 1000, 5)
        self.assertTrue(closed,
                        "пересчёт с нуля: очередь сброшена, наш end виден")
        self.assertEqual(open_new, 0)
        _measure("MEASURE (FX-2c) усечение journal → полный пересчёт с "
                 "open_new=0 (синтетика KR3: 5+1-1 больше не съедает end)")


if __name__ == "__main__":
    _assert_not_live("/tmp")
    unittest.main(verbosity=2)
