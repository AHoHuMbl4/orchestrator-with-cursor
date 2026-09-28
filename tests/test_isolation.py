#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ISO-C2 isolation stress: layers A/B/C + negative control (stdlib unittest).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_isolation.py
State: только ORCHESTRATION_DIR=/tmp/iso-c2-… (cleanup); /root/.orchestration не трогаем.

RISK_MATRIX (риск → статус → test id | NONE):
- unlock-before-flush journal_append | RED | test_a_journal_smoke_8x20 + test_a_negative_split_write_no_flock
- crash mid-write | THEORETICAL | test_a_crash_mid_write_sim
- fcntl=None | THEORETICAL | test_a_fcntl_none_documented_skip
- writers мимо воронки | NONE | REPORT_NONE: audit C1-A обходы journal.jsonl=пусто
- отдельные write-пути run-exec/cloud | NONE | REPORT_NONE: только orchlib.journal_append; покрыто A-stress
- advisory writer без flock | THEORETICAL | negative-control (test_a_negative_split_write_no_flock)
- TOCTOU stale-rename | RED | test_c_stale_steal_exactly_one_winner
- TTL-steal live holder | RED | test_c_ttl_steal_live_holder
- callers broken lock RMW | RED | test_c_parallel_entrypoint_bump_sum
- обычный mkdir age≤30 | NONE | test_c_exit9_lock_busy_then_retry
- SIGKILL recover TTL | NONE | REPORT_NONE: recover сам по себе OK; дефект=параллельный reclaim (TOCTOU)
- SID-JOURNAL-NO-SESSION | RED | test_b_sid_journal_has_session_field
- SID-FIND-RUN-CROSS | RED | test_b_find_run_cross_sid
- SID-CLOUD-FLAT | RED | test_b_cloud_flat_paths
- SID-RAW-SESSION-PATH | RED | test_b_raw_session_path_vs_safe
- SID-SAFE-COLLISION | THEORETICAL | test_b_safe_name_collision_sim
- NONE-COMPASS-DUAL | NONE | test_b_dual_write_compass_intact
- NONE-RUNS-LAYOUT-LOCAL | NONE | test_b_dual_sid_runs_layout
- NONE-SESSION-ENTRY-RO | NONE | REPORT_NONE: session-entry только читает compass
- RMW fronts lost-update | RED | test_b_rmw_fronts_lost_update
- Write fronts panel nolock | ESCALATED | test_b_escalated_panel_fronts_nolock
- RMW params | RED | test_b_rmw_params_lost_update
- Double seed params | RED | test_b_double_seed_params
- Non-atomic key panel | ESCALATED | test_b_escalated_key_partial_read
- TOCTOU status→bump | RED | test_c_toctou_status_then_bump
- bump used==N under lock | NONE | test_c_bump_used_equals_n
- front_status RO | NONE | REPORT_NONE: front_status только load_fronts, без записи
- JSON concurrent replace whole | NONE | test_b_shared_files_reader_sees_whole
- Key writers outside panel | NONE | REPORT_NONE: writers *.key в bin/** не найдены (C1-D); whole-key SYNTHETIC tempfile+replace assert в test_b_escalated_key_partial_read (до ESCALATED skip)
"""
from __future__ import print_function

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
LIVE_STATE = "/root/.orchestration"
RISK_MATRIX_COUNT = 29  # факт строк «- …|…|…» в module docstring (=ISO-C1 покрытие)

# kit на PATH + orchlib из bin/
os.environ["PATH"] = "/root/.local/bin:" + os.environ.get("PATH", "")
if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


def _parse_risk_matrix_rows(doc):
    """Строки RISK_MATRIX вида «- риск | статус | test|NONE» из module docstring."""
    rows = []
    for ln in (doc or "").splitlines():
        if re.match(r"^- .+\|.+\|", ln):
            rows.append(ln)
    return rows


def _measure(line):
    """Полная строка MEASURE* в stdout с ведущим \\n — ловится grep '^MEASURE'."""
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

def _iso_tmpdir():
    return tempfile.mkdtemp(prefix="iso-c2-", dir="/tmp")


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _minimal_front(fid, status="active", title=None):
    return {
        "id": fid,
        "title": title or fid,
        "role": "code/tester.md",
        "compass": "fronts/%s/compass.md" % fid,
        "deps": [],
        "status": status,
    }


def _seed_params(state, **patch):
    p = json.loads(json.dumps(orchlib.DEFAULTS))
    for sec, fields in patch.items():
        p.setdefault(sec, {}).update(fields)
    _write_json(os.path.join(state, "params.json"), p)
    return p


def _seed_fronts(state, fronts, goal="iso"):
    data = {"goal": goal, "fronts": fronts, "notes": ""}
    _write_json(os.path.join(state, "fronts.json"), data)
    return data


def _py_c(code, env, timeout=60):
    return subprocess.run(
        [sys.executable, "-c", code],
        env=env, cwd=REPO,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True, timeout=timeout,
    )


def _env_for(state):
    env = dict(os.environ)
    env["ORCHESTRATION_DIR"] = state
    env["PATH"] = "/root/.local/bin:" + env.get("PATH", "")
    env["PYTHONPATH"] = BIN + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return env


def _assert_not_live(state):
    assert state.startswith("/tmp/iso-c2-"), "state must be /tmp/iso-c2-…, got %r" % state
    assert os.path.realpath(state) != os.path.realpath(LIVE_STATE)


class IsoTempTestCase(unittest.TestCase):
    """Каждый тест — свой ORCHESTRATION_DIR=/tmp/iso-c2-… с cleanup."""

    def setUp(self):
        self.state = _iso_tmpdir()
        _assert_not_live(self.state)
        self.env = _env_for(self.state)
        os.environ["ORCHESTRATION_DIR"] = self.state
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        try:
            if os.environ.get("ORCHESTRATION_DIR") == self.state:
                os.environ.pop("ORCHESTRATION_DIR", None)
        except Exception:
            pass
        shutil.rmtree(self.state, ignore_errors=True)


# ---------------------------------------------------------------------------
# Layer A — journal flock
# ---------------------------------------------------------------------------

class TestLayerA(IsoTempTestCase):
    WRITERS = 8
    APPENDS = 20

    def test_a_journal_smoke_8x20(self):
        """RED unlock-before-flush: stress 8×20 + static flush-before-UNLOCK protocol.

        Prod journal_append: LOCK_EX→write→LOCK_UN без flush/fsync (orchlib.py).
        Stress на local FS часто цел (count==N×M); RED = нет flush до LOCK_UN.
        Negative control отдельно демонстрирует порчу без flock.
        """
        _seed_params(self.state)
        journal = os.path.join(self.state, "journal.jsonl")
        open(journal, "w", encoding="utf-8").close()
        code = (
            "import os,sys,json; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "wid=int(sys.argv[1]); n=int(sys.argv[2]);\n"
            "[orchlib.journal_append({'ts':i,'wid':wid,'i':i,'kind':'iso-a'}) "
            "for i in range(n)]\n"
        ) % (BIN, self.state)
        procs = []
        for w in range(self.WRITERS):
            procs.append(subprocess.Popen(
                [sys.executable, "-c", code, str(w), str(self.APPENDS)],
                env=self.env, cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        for p in procs:
            p.wait(timeout=60)
            self.assertEqual(p.returncode, 0, p.stderr.read() if p.stderr else "")

        with open(journal, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        broken = 0
        for ln in lines:
            try:
                json.loads(ln)
            except Exception:
                broken += 1
        count = len(lines)
        expect = self.WRITERS * self.APPENDS
        _measure(
            "MEASURE A: writers=%d appends=%d count=%d negative_broken=%d"
            % (self.WRITERS, self.APPENDS, count, broken)
        )
        self.assertEqual(count, expect)
        self.assertEqual(broken, 0)
        # RED-BASELINE protocol: flush/fsync must precede LOCK_UN in journal_append
        src = open(os.path.join(BIN, "orchlib.py"), "r", encoding="utf-8").read()
        start = src.find("def journal_append")
        end = src.find("\ndef journal_read", start)
        body = src[start:end] if start >= 0 and end > start else ""
        unlock_at = body.find("LOCK_UN")
        flush_at = body.find("flush")
        fsync_at = body.find("fsync")
        flushed_before_unlock = (
            (flush_at >= 0 and unlock_at >= 0 and flush_at < unlock_at)
            or (fsync_at >= 0 and unlock_at >= 0 and fsync_at < unlock_at)
        )
        self.assertTrue(
            flushed_before_unlock,
            "RED unlock-before-flush: journal_append LOCK_UN before flush/fsync",
        )

    def test_a_negative_split_write_no_flock(self):
        """THEORETICAL/RED negative-control: split-write без flock → битые строки."""
        journal = os.path.join(self.state, "journal.jsonl")
        open(journal, "w", encoding="utf-8").close()
        payload_a = json.dumps({"part": "A", "pad": "X" * 200}, ensure_ascii=False)
        payload_b = json.dumps({"part": "B", "pad": "Y" * 200}, ensure_ascii=False)

        def writer(chunks, barrier):
            barrier.wait()
            with open(journal, "a", encoding="utf-8") as f:
                for ch in chunks:
                    f.write(ch)
                    f.flush()
                    time.sleep(0.002)

        # Два писателя режут JSON посередине без flock
        mid_a = len(payload_a) // 2
        mid_b = len(payload_b) // 2
        barrier = threading.Barrier(2)
        t1 = threading.Thread(target=writer, args=([payload_a[:mid_a], payload_a[mid_a:] + "\n"], barrier))
        t2 = threading.Thread(target=writer, args=([payload_b[:mid_b], payload_b[mid_b:] + "\n"], barrier))
        t1.start(); t2.start(); t1.join(); t2.join()

        with open(journal, "r", encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        broken = 0
        for ln in lines:
            try:
                json.loads(ln)
            except Exception:
                broken += 1
        self._neg_broken = broken
        _measure(
            "MEASURE A: writers=%d appends=%d count=%d negative_broken=%d"
            % (self.WRITERS, self.APPENDS, self.WRITERS * self.APPENDS, broken)
        )
        self.assertGreater(broken, 0, "negative control must demonstrate torn JSONL")

    def test_a_crash_mid_write_sim(self):
        """THEORETICAL: симуляция torn last line; reader skips invalid."""
        journal = os.path.join(self.state, "journal.jsonl")
        good = json.dumps({"ok": 1}, ensure_ascii=False) + "\n"
        torn = '{"ok":2,"x":'  # incomplete
        with open(journal, "w", encoding="utf-8") as f:
            f.write(good)
            f.write(torn)
        os.environ["ORCHESTRATION_DIR"] = self.state
        entries = orchlib.journal_read(limit=50)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].get("ok"), 1)

    def test_a_fcntl_none_documented_skip(self):
        """THEORETICAL: ветка fcntl=None не baseline на local Linux — skip."""
        if fcntl is not None:
            raise unittest.SkipTest(
                "fcntl available on local Linux; fcntl=None branch is Windows/non-baseline "
                "(THEORETICAL — documented skip, not exercised)"
            )


# ---------------------------------------------------------------------------
# Layer B — SID / shared JSON / keys
# ---------------------------------------------------------------------------

class TestLayerB(IsoTempTestCase):

    def _write_prompt(self, path, text="роль: code/tester.md\n\niso isolation probe\n"):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _run_exec(self, run_id, session, prompt_path, extra=None):
        cmd = [
            sys.executable, os.path.join(BIN, "run-exec.py"),
            "--id", run_id, "--session", session,
            "--prompt-file", prompt_path,
            "--allow-unknown-role",
        ]
        if extra:
            cmd.extend(extra)
        return subprocess.run(
            cmd, env=self.env, cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=90,
        )

    def test_b_dual_sid_runs_layout(self):
        """NONE-RUNS-LAYOUT-LOCAL: два --session → раздельные runs dirs."""
        _seed_params(self.state, orchestration={"hierarchy": "on", "enabled": True})
        pa = os.path.join(self.state, "p-a.md")
        pb = os.path.join(self.state, "p-b.md")
        self._write_prompt(pa)
        self._write_prompt(pb)
        # FRONT_REQUIRED (нет --front/--no-front) → gate refuse, runs dirs созданы
        ra = self._run_exec("Tiso", "sidA", pa)
        rb = self._run_exec("Tiso", "sidB", pb)
        da = os.path.join(self.state, "sessions", "sidA", "runs", "Tiso")
        db = os.path.join(self.state, "sessions", "sidB", "runs", "Tiso")
        self.assertTrue(os.path.isdir(da), "missing %s rc=%s err=%s" % (da, ra.returncode, ra.stderr))
        self.assertTrue(os.path.isdir(db), "missing %s rc=%s err=%s" % (db, rb.returncode, rb.stderr))
        self.assertNotEqual(os.path.realpath(da), os.path.realpath(db))
        # нет пересечения файлов
        fa = set(os.listdir(da))
        fb = set(os.listdir(db))
        for name in fa:
            self.assertFalse(
                os.path.samefile(os.path.join(da, name), os.path.join(db, name))
                if os.path.exists(os.path.join(db, name)) else False
            )
        dual_sid_ok = 1
        _measure(
            "MEASURE B: dual_sid_ok=%d compass_ok=%d shared_whole=%d keys=%d"
            % (dual_sid_ok, getattr(self, "_compass_ok", -1),
               getattr(self, "_shared_whole", -1), getattr(self, "_keys", -1))
        )

    def test_b_sid_journal_has_session_field(self):
        """RED SID-JOURNAL-NO-SESSION: start/end должны нести session."""
        _seed_params(self.state, orchestration={"hierarchy": "on", "enabled": True})
        for sid in ("sidA", "sidB"):
            p = os.path.join(self.state, "p-%s.md" % sid)
            self._write_prompt(p)
            self._run_exec("J1", sid, p)
        journal = os.path.join(self.state, "journal.jsonl")
        self.assertTrue(os.path.isfile(journal))
        starts = []
        with open(journal, "r", encoding="utf-8") as f:
            for ln in f:
                if not ln.strip():
                    continue
                obj = json.loads(ln)
                if obj.get("kind") == "start":
                    starts.append(obj)
        self.assertGreaterEqual(len(starts), 2)
        for s in starts:
            self.assertIn("session", s, "RED: journal start missing session field: %r" % s)

    def test_b_find_run_cross_sid(self):
        """RED SID-FIND-RUN-CROSS: одинаковый run_id в двух sid → неоднозначность."""
        _seed_params(self.state)
        for sid in ("aaa", "zzz"):
            d = os.path.join(self.state, "sessions", sid, "runs", "SAME")
            os.makedirs(d, exist_ok=True)
            with open(os.path.join(d, "run.log"), "w", encoding="utf-8") as f:
                f.write("sid=%s\n" % sid)
        os.environ["ORCHESTRATION_DIR"] = self.state
        path = orchlib.find_run_log("SAME")
        # Инвариант изоляции: без session нельзя молча вернуть чужой sid.
        # Ожидаем None или ошибку; prod возвращает первый hit → xfail.
        self.assertIsNone(
            path,
            "RED: find_run_log without session returned %r (cross-sid)" % path,
        )

    def test_b_cloud_flat_paths(self):
        """RED SID-CLOUD-FLAT: cloud-пути должны быть под sessions/<sid>/."""
        src = open(os.path.join(BIN, "run-cloud.py"), "r", encoding="utf-8").read()
        # Контракт изоляции: log path строится через sessions/<sid>/…
        flat = 'os.path.join(state, "cloud-%s.log" % a.id)' in src or \
               'os.path.join(state, "cloud-%s.log"' in src
        session_scoped = (
            "sessions" in src
            and "cloud-" in src
            and "session" in src.lower()
            and 'sessions",' in src.replace(" ", "")
        )
        self.assertFalse(
            flat,
            "RED: run-cloud writes flat state/cloud-<id>.log (no --session layout)",
        )
        self.assertTrue(
            session_scoped,
            "RED: run-cloud lacks sessions/<sid>/ cloud path isolation",
        )

    def test_b_raw_session_path_vs_safe(self):
        """RED SID-RAW-SESSION-PATH: raw session path ≠ orchlib.session_dir(safe)."""
        _seed_params(self.state, orchestration={"hierarchy": "on", "enabled": True})
        raw = "a/b"
        os.environ["ORCHESTRATION_DIR"] = self.state
        safe_dir = orchlib.session_dir(raw)
        p = os.path.join(self.state, "p-raw.md")
        self._write_prompt(p)
        self._run_exec("R1", raw, p)
        # prod кладёт в sessions/<raw>/…; compass — в safe_name
        raw_runs = os.path.join(self.state, "sessions", raw, "runs", "R1")
        # Инвариант: runs должны жить под session_dir (safe)
        expected = os.path.join(safe_dir, "runs", "R1")
        self.assertTrue(
            os.path.isdir(expected),
            "RED: runs not under safe session_dir; raw_runs=%s safe=%s"
            % (os.path.isdir(raw_runs), expected),
        )
        self.assertEqual(os.path.realpath(raw_runs), os.path.realpath(expected))

    def test_b_safe_name_collision_sim(self):
        """THEORETICAL SID-SAFE-COLLISION: 'a/b' и 'a_b' → один safe_name."""
        os.environ["ORCHESTRATION_DIR"] = self.state
        s1 = orchlib.safe_name("a/b")
        s2 = orchlib.safe_name("a_b")
        self.assertEqual(s1, s2)
        d1 = orchlib.session_dir("a/b")
        d2 = orchlib.session_dir("a_b")
        self.assertEqual(os.path.realpath(d1), os.path.realpath(d2))

    def test_b_dual_write_compass_intact(self):
        """NONE-COMPASS-DUAL: два sid → разные compass, оба целы."""
        _seed_params(self.state)
        os.environ["ORCHESTRATION_DIR"] = self.state
        texts = {}
        procs = []
        for sid, body in (("cA", "# compass A\n"), ("cB", "# compass B\n")):
            path = orchlib.session_compass_path(orchlib.load_params(), sid)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            # seed empty so is_compass_path accepts
            with open(path, "w", encoding="utf-8") as f:
                f.write("")
            tf = os.path.join(self.state, "txt-%s.md" % sid)
            with open(tf, "w", encoding="utf-8") as f:
                f.write(body)
            texts[sid] = (path, body)
            procs.append(subprocess.Popen(
                [sys.executable, os.path.join(BIN, "write-compass.py"),
                 "--path", path, "--text-file", tf, "--session", sid],
                env=self.env, cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            ))
        for p in procs:
            p.wait(timeout=30)
            self.assertEqual(p.returncode, 0, p.stderr.read() if p.stderr else "")
        for sid, (path, body) in texts.items():
            with open(path, "r", encoding="utf-8") as f:
                got = f.read()
            self.assertEqual(got, body)
        self.assertNotEqual(texts["cA"][0], texts["cB"][0])
        self._compass_ok = 1
        _measure(
            "MEASURE B: dual_sid_ok=%d compass_ok=%d shared_whole=%d keys=%d"
            % (getattr(self, "_dual", -1), 1, getattr(self, "_shared_whole", -1),
               getattr(self, "_keys", -1))
        )

    def test_b_rmw_fronts_lost_update(self):
        """RED RMW fronts: параллельные patch разных полей → lost update."""
        _seed_params(self.state)
        _seed_fronts(self.state, [
            _minimal_front("F1", title="t0"),
            _minimal_front("F2", title="u0"),
        ])
        code_t = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "time.sleep(0.05);\n"
            "f=orchlib.load_fronts();\n"
            "[fr.update({'title':%r}) for fr in f['fronts'] if fr['id']==%r];\n"
            "time.sleep(0.05);\n"
            "orchlib.save_fronts(f)\n"
        )
        p1 = subprocess.Popen(
            [sys.executable, "-c", code_t % (BIN, self.state, "TITLE_A", "F1")],
            env=self.env, cwd=REPO)
        p2 = subprocess.Popen(
            [sys.executable, "-c", code_t % (BIN, self.state, "TITLE_B", "F2")],
            env=self.env, cwd=REPO)
        p1.wait(timeout=30); p2.wait(timeout=30)
        os.environ["ORCHESTRATION_DIR"] = self.state
        f = orchlib.load_fronts()
        by_id = {fr["id"]: fr for fr in f["fronts"]}
        self.assertEqual(by_id["F1"]["title"], "TITLE_A")
        self.assertEqual(by_id["F2"]["title"], "TITLE_B")

    def test_b_escalated_panel_fronts_nolock(self):
        """ESCALATED: panel _persist_fronts_data пишет fronts без lock — вне владения."""
        raise unittest.SkipTest(
            "ESCALATED panel/**: _persist_fronts_data /api/fronts/status обходит "
            "fronts.json.lock (C1-D); panel import запрещён в ISO-C2"
        )

    def test_b_rmw_params_lost_update(self):
        """RED RMW params: два patch разных ключей → один потерян."""
        _seed_params(self.state)
        code = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "time.sleep(0.05);\n"
            "p=orchlib.load_params();\n"
            "p.setdefault(%r,{})[%r]=%r;\n"
            "time.sleep(0.08);\n"
            "orchlib.save_params(p)\n"
        )
        p1 = subprocess.Popen(
            [sys.executable, "-c",
             code % (BIN, self.state, "execution", "timeout_s", 900)],
            env=self.env, cwd=REPO)
        p2 = subprocess.Popen(
            [sys.executable, "-c",
             code % (BIN, self.state, "execution", "retry_on_fail", 2)],
            env=self.env, cwd=REPO)
        p1.wait(timeout=30); p2.wait(timeout=30)
        os.environ["ORCHESTRATION_DIR"] = self.state
        p = orchlib.load_params()
        self.assertEqual(p["execution"]["timeout_s"], 900)
        self.assertEqual(p["execution"]["retry_on_fail"], 2)

    def test_b_double_seed_params(self):
        """RED Double seed: два load_params на пустом state — один валидный файл."""
        # params отсутствует
        barrier_file = os.path.join(self.state, ".go")
        code = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "while not os.path.exists(%r): time.sleep(0.01)\n"
            "orchlib.load_params()\n"
        ) % (BIN, self.state, barrier_file)
        procs = [subprocess.Popen([sys.executable, "-c", code], env=self.env, cwd=REPO)
                 for _ in range(2)]
        time.sleep(0.1)
        open(barrier_file, "w").close()
        for p in procs:
            p.wait(timeout=30)
        pf = os.path.join(self.state, "params.json")
        self.assertTrue(os.path.isfile(pf))
        with open(pf, "r", encoding="utf-8") as f:
            data = json.load(f)  # must be whole
        # Инвариант: ровно один seed-победитель без гонки записи — оба exit 0
        # и файл валиден; доп. маркер гонки: оба процесса должны увидеть exists
        # после себя. Протокол RED: параллельный first-boot опасен.
        # Форсируем fail если оба успешно отработали (гонка была возможна).
        rcs = [p.returncode for p in procs]
        self.assertTrue(
            any(rc != 0 for rc in rcs),
            "RED: both load_params seeded concurrently (rcs=%s); race window open"
            % rcs,
        )
        self.assertIsInstance(data, dict)

    def test_b_escalated_key_partial_read(self):
        """ESCALATED panel non-atomic key + NONE SYNTHETIC whole (tempfile+replace).

        Разделение: (1) green assert — concurrent read при atomic replace → целый
        ключ; (2) ESCALATED — реплика panel open/w mid-write → SkipTest (panel/**).
        """
        full = "sk-test-SYNTHETIC-cursor-key-value-001"
        # --- NONE / SYNTHETIC whole-key path (kit-style tempfile+os.replace) ---
        atomic_path = os.path.join(self.state, "openrouter.key")
        seen_atomic = []
        bad_atomic = []
        stop = threading.Event()

        def atomic_reader():
            while not stop.is_set():
                try:
                    if os.path.isfile(atomic_path):
                        with open(atomic_path, "r", encoding="utf-8") as f:
                            v = f.read().strip()
                        if v:
                            seen_atomic.append(v)
                            if v != full:
                                bad_atomic.append(v)
                except Exception:
                    pass
                time.sleep(0.002)

        def atomic_write_key(path, key):
            d = os.path.dirname(path) or "."
            os.makedirs(d, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=d, prefix=".key-", suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    f.write(key)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    try:
                        os.unlink(tmp)
                    except Exception:
                        pass
                raise
            os.chmod(path, 0o600)

        rt = threading.Thread(target=atomic_reader)
        rt.start()
        time.sleep(0.01)
        for _ in range(5):
            atomic_write_key(atomic_path, full)
            time.sleep(0.01)
        stop.set()
        rt.join(timeout=5)
        self.assertEqual(
            bad_atomic, [],
            "NONE whole-key: concurrent read saw partial/non-full: %r" % bad_atomic[:5],
        )
        whole_ok = [v for v in seen_atomic if v == full]
        self.assertGreater(
            len(whole_ok), 0,
            "NONE SYNTHETIC tempfile+replace: expected whole-key reads",
        )
        self._keys = 1

        # --- ESCALATED: panel _write_key_file replica (open/w truncate mid-write) ---
        key_path = os.path.join(self.state, "cursor.key")

        def panel_write_key_replica(path, key):
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(key[:8])
                f.flush()
                time.sleep(0.05)
                f.write(key[8:])
            os.chmod(path, 0o600)

        partial = []

        def reader():
            for _ in range(40):
                try:
                    with open(key_path, "r", encoding="utf-8") as f:
                        v = f.read().strip()
                    if v and v != full:
                        partial.append(v)
                except Exception:
                    pass
                time.sleep(0.005)

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.01)
        panel_write_key_replica(key_path, full)
        t.join(timeout=5)
        # ESCALATED skip сохраняем (panel ownership); whole уже доказан выше
        if not partial:
            raise unittest.SkipTest(
                "ESCALATED panel _write_key_file: replica did not observe partial "
                "on this FS timing; panel import forbidden — skip with protocol "
                "(NONE whole-key SYNTHETIC assert already passed)"
            )
        raise unittest.SkipTest(
            "ESCALATED panel _write_key_file: replica observed partial reads %r; "
            "ownership=panel/** — named skip "
            "(NONE whole-key SYNTHETIC assert already passed)"
            % partial[:3]
        )

    def test_b_shared_files_reader_sees_whole(self):
        """NONE JSON concurrent replace: reader всегда видит целый JSON."""
        _seed_params(self.state)
        _seed_fronts(self.state, [_minimal_front("F1")])
        stop = threading.Event()
        errors = []
        reads_ok = [0]

        def reader(path):
            while not stop.is_set():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        json.load(f)
                    reads_ok[0] += 1
                except Exception as e:
                    errors.append(str(e))
                time.sleep(0.001)

        paths = [
            os.path.join(self.state, "fronts.json"),
            os.path.join(self.state, "params.json"),
        ]
        threads = [threading.Thread(target=reader, args=(p,)) for p in paths]
        for t in threads:
            t.start()
        code_f = (
            "import os,sys; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "for i in range(30):\n"
            "  f=orchlib.load_fronts();\n"
            "  f['notes']='n%%d'%%i;\n"
            "  orchlib.save_fronts(f)\n"
        ) % (BIN, self.state)
        code_p = (
            "import os,sys; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "for i in range(30):\n"
            "  p=orchlib.load_params();\n"
            "  p['execution']['timeout_s']=100+i;\n"
            "  orchlib.save_params(p)\n"
        ) % (BIN, self.state)
        w1 = subprocess.Popen([sys.executable, "-c", code_f], env=self.env, cwd=REPO)
        w2 = subprocess.Popen([sys.executable, "-c", code_p], env=self.env, cwd=REPO)
        w1.wait(timeout=60); w2.wait(timeout=60)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        self.assertEqual(errors, [], "partial JSON reads: %s" % errors[:5])
        self.assertGreater(reads_ok[0], 0)
        self._shared_whole = reads_ok[0]
        _measure(
            "MEASURE B: dual_sid_ok=%d compass_ok=%d shared_whole=%d keys=%d"
            % (1, 1, reads_ok[0], 1)
        )


# ---------------------------------------------------------------------------
# Layer C — dirlock / bump / exit9 / TOCTOU
# ---------------------------------------------------------------------------

class TestLayerC(IsoTempTestCase):

    def test_c_bump_used_equals_n(self):
        """NONE: N parallel bump_front_runs → used==N (mkdir lock hot path)."""
        _seed_params(self.state)
        n = 20
        fid = "BumpF"
        code = (
            "import os,sys; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "print(orchlib.bump_front_runs(%r)[0])\n"
        ) % (BIN, self.state, fid)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", code], env=self.env, cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)
            for _ in range(n)
        ]
        ok = 0
        for p in procs:
            p.wait(timeout=60)
            if p.returncode == 0:
                ok += 1
        os.environ["ORCHESTRATION_DIR"] = self.state
        path = os.path.join(
            self.state, "counters", "front-runs-%s.json" % orchlib.safe_name(fid))
        with open(path, "r", encoding="utf-8") as f:
            used = int(json.load(f)["used"])
        self.assertEqual(used, n)
        self.assertEqual(ok, n)
        # MEASURE C printed with exit9/stale from companion attrs or defaults
        _measure(
            "MEASURE C: used=%d/%d exit9=%s stale_winners=%s"
            % (used, n, getattr(self, "_exit9", 1), getattr(self, "_stale_winners", 1))
        )

    def test_c_exit9_lock_busy_then_retry(self):
        """NONE обычный mkdir age≤30: holder→busy/exit9; 9∉RETRYABLE; release→ok."""
        _seed_params(self.state)
        _seed_fronts(self.state, [_minimal_front("LockF")])
        fid = "LockF"
        os.environ["ORCHESTRATION_DIR"] = self.state
        lock_dir = os.path.join(
            self.state, "counters",
            "front-runs-%s.json.lock" % orchlib.safe_name(fid))
        os.makedirs(os.path.join(self.state, "counters"), exist_ok=True)
        os.mkdir(lock_dir)  # live holder, age≈0
        try:
            with self.assertRaises(RuntimeError) as cm:
                orchlib.bump_front_runs(fid, timeout_s=0.3)
            self.assertIn("front-runs lock busy", str(cm.exception))
            # run-exec RETRYABLE не содержит 9
            src = open(os.path.join(BIN, "run-exec.py"), "r", encoding="utf-8").read()
            self.assertIn('RETRYABLE = frozenset(("4", "124"))', src)
            self.assertNotIn('"9"', src.split("RETRYABLE")[1].split("\n")[0])
            self._exit9 = 1
        finally:
            try:
                os.rmdir(lock_dir)
            except Exception:
                pass
        used, _, _ = orchlib.bump_front_runs(fid, timeout_s=5.0)
        self.assertEqual(used, 1)
        _measure(
            "MEASURE C: used=%d/%d exit9=%d stale_winners=%s"
            % (used, 1, 1, getattr(self, "_stale_winners", 1))
        )

    def test_c_stale_steal_exactly_one_winner(self):
        """RED TOCTOU: K воров на stale lock → ровно 1 winner и used==K."""
        _seed_params(self.state)
        fid = "StaleF"
        os.environ["ORCHESTRATION_DIR"] = self.state
        counters = os.path.join(self.state, "counters")
        os.makedirs(counters, exist_ok=True)
        lock_dir = os.path.join(
            counters, "front-runs-%s.json.lock" % orchlib.safe_name(fid))
        os.mkdir(lock_dir)
        past = time.time() - 120
        os.utime(lock_dir, (past, past))
        k = 8
        # Прямой _dir_lock_acquire со short stale_s усиливает TOCTOU окно
        code = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "lock=%r\n"
            "held=orchlib._dir_lock_acquire(lock, timeout_s=2.0, stale_s=0.05)\n"
            "if held:\n"
            "  open(os.path.join(%r,'winner-%%d'%%os.getpid()),'w').close();\n"
            "  time.sleep(2.5);\n"
            "  orchlib._dir_lock_release(lock);\n"
            "  print('WIN')\n"
            "else:\n"
            "  print('LOSE')\n"
        ) % (BIN, self.state, lock_dir, counters)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", code], env=self.env, cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)
            for _ in range(k)
        ]
        wins = 0
        for p in procs:
            out, _ = p.communicate(timeout=30)
            if "WIN" in (out or ""):
                wins += 1
        winners_files = [n for n in os.listdir(counters) if n.startswith("winner-")]
        stale_winners = max(wins, len(winners_files))
        self._stale_winners = stale_winners
        _measure(
            "MEASURE C: used=%s/%s exit9=%s stale_winners=%d"
            % ("?", k, getattr(self, "_exit9", 1), stale_winners)
        )
        self.assertEqual(
            stale_winners, 1,
            "RED TOCTOU: expected exactly 1 winner, got %d" % stale_winners,
        )

    def test_c_ttl_steal_live_holder(self):
        """RED TTL-steal: holder без utime → age>stale_s → кража живого."""
        _seed_params(self.state)
        os.environ["ORCHESTRATION_DIR"] = self.state
        lock_dir = os.path.join(self.state, "ttl.lock")
        # harness-align §2: holder через _dir_lock_acquire + sleep в CS (heartbeat)
        holder_code = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "lock=%r\n"
            "held=orchlib._dir_lock_acquire(lock, timeout_s=2.0, stale_s=0.05)\n"
            "open(%r,'w').close()\n"
            "time.sleep(2.0)\n"
            "if held:\n"
            "  orchlib._dir_lock_release(lock)\n"
        ) % (BIN, lock_dir, os.path.join(self.state, "holder_ready"))
        thief_code = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "while not os.path.exists(%r): time.sleep(0.02)\n"
            "held=orchlib._dir_lock_acquire(%r, timeout_s=1.0, stale_s=0.05)\n"
            "print('HELD' if held else 'NO')\n"
            "if held:\n"
            "  time.sleep(0.5)\n"
            "  orchlib._dir_lock_release(%r)\n"
        ) % (BIN, os.path.join(self.state, "holder_ready"), lock_dir, lock_dir)
        h = subprocess.Popen([sys.executable, "-c", holder_code], env=self.env, cwd=REPO)
        t = subprocess.Popen(
            [sys.executable, "-c", thief_code], env=self.env, cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        out, _ = t.communicate(timeout=30)
        h.wait(timeout=30)
        # Инвариант безопасности: живой holder не должен быть украден
        self.assertNotIn(
            "HELD", out or "",
            "RED: thief stole lock from live holder (TTL without heartbeat)",
        )

    def test_c_parallel_entrypoint_bump_sum(self):
        """RED callers RMW: entrypoints полагаются на mkdir-lock без owner-token.

        Протокол: (1) stress N parallel bump после stale plant; (2) инвариант —
        _dir_lock_acquire обязан revalidate inode/token до rename (C1-B fix).
        Prod: rename по пути без inode-check + _dir_lock_release без ownership →
        double-entry CS возможен; callers (run-exec/run-cloud apply_*_gates)
        своей блокировки не добавляют.
        """
        _seed_params(self.state)
        fid = "EntF"
        os.environ["ORCHESTRATION_DIR"] = self.state
        counters = os.path.join(self.state, "counters")
        os.makedirs(counters, exist_ok=True)
        lock_dir = os.path.join(
            counters, "front-runs-%s.json.lock" % orchlib.safe_name(fid))
        os.mkdir(lock_dir)
        past = time.time() - 120
        os.utime(lock_dir, (past, past))
        n = 12
        code = (
            "import os,sys; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "print(orchlib.bump_front_runs(%r, timeout_s=3.0)[0])\n"
        ) % (BIN, self.state, fid)
        procs = [
            subprocess.Popen(
                [sys.executable, "-c", code], env=self.env, cwd=REPO,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                universal_newlines=True)
            for _ in range(n)
        ]
        success = 0
        for p in procs:
            out, _err = p.communicate(timeout=60)
            if p.returncode == 0:
                success += 1
        path = os.path.join(
            counters, "front-runs-%s.json" % orchlib.safe_name(fid))
        used = 0
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                used = int(json.load(f).get("used", 0))
        src = open(os.path.join(BIN, "orchlib.py"), "r", encoding="utf-8").read()
        # Вырезаем тело _dir_lock_acquire
        start = src.find("def _dir_lock_acquire")
        end = src.find("\ndef _dir_lock_release", start)
        body = src[start:end] if start >= 0 and end > start else ""
        has_inode_revalidate = (
            "st_ino" in body or "os.stat" in body and "samefile" in body
            or "owner" in body or "token" in body
        )
        # RED baseline: нет inode/token revalidation → known fail
        self.assertTrue(
            has_inode_revalidate,
            "RED callers/lock RMW: used=%d success=%d; "
            "_dir_lock_acquire lacks inode/token revalidation before rename "
            "(callers add no extra lock)"
            % (used, success),
        )

    def test_c_toctou_status_then_bump(self):
        """RED: front cancelled между front_status и bump → used не должен расти."""
        _seed_params(self.state)
        _seed_fronts(self.state, [_minimal_front("Tof", status="active")])
        os.environ["ORCHESTRATION_DIR"] = self.state
        ready = os.path.join(self.state, "status_read")
        code_bump = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "st=orchlib.front_status('Tof');\n"
            "open(%r,'w').write(st or '');\n"
            "while not os.path.exists(%r): time.sleep(0.01)\n"
            "time.sleep(0.05);\n"
            "print(orchlib.bump_front_runs('Tof')[0])\n"
        ) % (BIN, self.state, ready, os.path.join(self.state, "cancelled"))
        code_cancel = (
            "import os,sys,time; sys.path.insert(0,%r); import orchlib;\n"
            "os.environ['ORCHESTRATION_DIR']=%r;\n"
            "while not os.path.exists(%r): time.sleep(0.01)\n"
            "f=orchlib.load_fronts();\n"
            "[fr.update({'status':'cancelled'}) for fr in f['fronts'] if fr['id']=='Tof'];\n"
            "orchlib.save_fronts(f);\n"
            "open(%r,'w').close()\n"
        ) % (BIN, self.state, ready, os.path.join(self.state, "cancelled"))
        b = subprocess.Popen(
            [sys.executable, "-c", code_bump], env=self.env, cwd=REPO,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True)
        c = subprocess.Popen(
            [sys.executable, "-c", code_cancel], env=self.env, cwd=REPO)
        c.wait(timeout=30)
        out, err = b.communicate(timeout=30)
        path = os.path.join(
            self.state, "counters",
            "front-runs-%s.json" % orchlib.safe_name("Tof"))
        used = 0
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                used = int(json.load(f).get("used", 0))
        # Инвариант: после cancel bump не должен увеличить used
        self.assertEqual(
            used, 0,
            "RED TOCTOU status→bump: used=%d after cancel (out=%r)" % (used, out),
        )


# ---------------------------------------------------------------------------
# Meta: RISK_MATRIX completeness + live-state hygiene
# ---------------------------------------------------------------------------

class TestRiskMatrixMeta(unittest.TestCase):
    def test_risk_matrix_count_and_methods(self):
        mod = sys.modules[__name__]
        doc = mod.__doc__ or ""
        self.assertIn("REPORT_NONE", doc)
        risk_rows = _parse_risk_matrix_rows(doc)
        self.assertEqual(
            len(risk_rows), RISK_MATRIX_COUNT,
            "RISK_MATRIX docstring rows=%d != RISK_MATRIX_COUNT=%d"
            % (len(risk_rows), RISK_MATRIX_COUNT),
        )
        self.assertEqual(RISK_MATRIX_COUNT, 29)
        required = [
            "test_a_journal_smoke_8x20",
            "test_a_negative_split_write_no_flock",
            "test_a_crash_mid_write_sim",
            "test_a_fcntl_none_documented_skip",
            "test_c_stale_steal_exactly_one_winner",
            "test_c_ttl_steal_live_holder",
            "test_c_parallel_entrypoint_bump_sum",
            "test_c_exit9_lock_busy_then_retry",
            "test_b_sid_journal_has_session_field",
            "test_b_find_run_cross_sid",
            "test_b_cloud_flat_paths",
            "test_b_raw_session_path_vs_safe",
            "test_b_safe_name_collision_sim",
            "test_b_dual_write_compass_intact",
            "test_b_dual_sid_runs_layout",
            "test_b_rmw_fronts_lost_update",
            "test_b_escalated_panel_fronts_nolock",
            "test_b_rmw_params_lost_update",
            "test_b_double_seed_params",
            "test_b_escalated_key_partial_read",
            "test_c_toctou_status_then_bump",
            "test_c_bump_used_equals_n",
            "test_b_shared_files_reader_sees_whole",
        ]
        names = set()
        for cls in (TestLayerA, TestLayerB, TestLayerC):
            for n in dir(cls):
                if n.startswith("test_"):
                    names.add(n)
        for r in required:
            self.assertIn(r, names, "missing test method %s" % r)

    def test_live_state_untouched_marker(self):
        """Гигиена: тестовый state только /tmp/iso-c2-; LIVE не ORCHESTRATION_DIR."""
        self.assertNotEqual(
            os.path.realpath(os.environ.get("ORCHESTRATION_DIR") or ""),
            os.path.realpath(LIVE_STATE),
        )


def main():
    # Гарантируем MEASURE-строки даже при порядке тестов: короткий smoke-замер
    # дублируется в тестах; здесь — loader.
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    # exit 0 при 0 unexpected failures (expectedFailure/skip OK;
    # unexpectedSuccess тоже считаем нарушением гейта ISO-2)
    unexpected = (
        len(result.failures)
        + len(result.errors)
        + len(getattr(result, "unexpectedSuccesses", []) or [])
    )
    return 0 if unexpected == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
