#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-C5 K5: лаунчер-журнализатор движковых спавн-ранов + честный backfill.

Регистратор spawn-start / spawn-finish / spawn-backfill (bin/orchlib.py):
движковый спавн-ран становится journal-парой start/end с "spawn": true и
сводкой kind=backfill — запись лишь отражает существующий артефакт
(source_artifact + verdict из вердикт-строки + artifact_mtime-факт);
ts = момент регистрации, никакого ретро-творчества. probes_missing
уважает receipts у spawn-end код-ранов (все перечисленные квитанции §3
валидны → рана прикрыта; без поля — красный, как раньше). Фикстуры
(а)–(ж) приказа полковника C5-K5.

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_spawn_journal.py -q
State: только ORCHESTRATION_DIR=/tmp/k5-…; живой /root/.orchestration не пишем.
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
ORCHLIB_CLI = os.path.join(REPO, "bin", "orchlib.py")
LIVE_STATE = "/root/.orchestration"
FRONT = "F-K5A"
FRONT_E = "F-K5E"

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402

# (ж): сьюты K1–K3 + поведение стандартных обёрточных ранов (run-exec);
# полный tests/ ×2 — приёмочный замер волны (отчёт кодера), не сьют.
REGRESS_SUITES = (
    "tests/test_supervision_dead.py",
    "tests/test_invariants_gate.py",
    "tests/test_close_gate.py",
    "tests/test_receipt_wrap.py",
    "tests/test_stall_exec.py",
)


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
    _assert_not_live(path)
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _seed_journal(state, entries):
    _assert_not_live(os.path.join(state, "journal.jsonl"))
    with open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _front(fid, status="active", owns=None):
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
    return fr


def _mk_state(prefix="k5", fronts=None):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "k5 spawn journal",
        "fronts": fronts if fronts is not None else [
            _front(FRONT, "active", ["bin/orchlib.py",
                                     "tests/test_spawn_journal.py",
                                     "skills/**"]),
            _front(FRONT_E, "active", ["tests/**"]),
        ],
        "notes": "",
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"hierarchy": "on", "enabled": True},
        "execution": {"retry_on_fail": 1, "timeout_s": 30},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8").close()
    return root, state


def _seed_run(state, rid, front, role, ts_start, ts_end=None, extra=None,
              spawn=False):
    """start(+end)-пара рана role на фронте front (journal; spawn — движковый)."""
    entries = [{"ts": ts_start, "kind": "start", "id": rid,
                "engine": "local", "front": front, "role": role}]
    if spawn:
        entries[0]["spawn"] = True
    if ts_end is not None:
        end = {"ts": ts_end, "kind": "end", "id": rid, "exit": 0}
        if spawn:
            end["spawn"] = True
        if extra:
            end.update(extra)
        entries.append(end)
    _seed_journal(state, entries)


def _entries(state):
    return orchlib._journal_entries_at(state)


def _raw_journal(state):
    with open(os.path.join(state, "journal.jsonl"), "rb") as f:
        return f.read()


def _cli_spawn(state, *args):
    """CLI-вызов регистратора на полигоне → (rc, stdout, stderr)."""
    env = dict(os.environ)
    env["ORCHESTRATION_DIR"] = state
    p = subprocess.run(
        [sys.executable, ORCHLIB_CLI] + list(args),
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True)
    return p.returncode, p.stdout, p.stderr


def _cli_check_close(state, fid):
    env = dict(os.environ)
    env["ORCHESTRATION_DIR"] = state
    p = subprocess.run(
        [sys.executable, ORCHLIB_CLI, "--check-close", fid],
        cwd=REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        universal_newlines=True)
    return p.returncode, p.stdout, p.stderr


def _artifact(root, name, text):
    path = os.path.join(root, "art", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _mk_receipt(state, rid):
    """Валидная квитанция §3 рана rid на полигоне (writer кита)."""
    d = os.path.join(state, "runs", rid)
    os.makedirs(d, exist_ok=True)
    ok, info = orchlib.write_probe_receipt(
        probe=rid, cmd="true", exit_code=0, oracle_match=True,
        critic_id=rid, artifact=rid, run_id=rid,
        path=os.path.join(d, "probe-receipt.md"), state=state)
    assert ok, info
    return rid


def _prosecutor(state, fid, ts):
    _seed_run(state, "prosecutor-auto-%s-1" % fid, fid,
              "meta/front-prosecutor.md", ts - 5, ts - 1)


# Живой state: снимок до/после всего модуля — оракул «живой не писался».
_LIVE_SNAPSHOT = {}


def _live_snapshot():
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
    """Housekeeping живой сессии + квитанционного харнесса — не волна
    (паттерн tests/test_supervision_dead.py)."""
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


def _live_journal_growth_violation(grown):
    """Строка spawn-записи в приросте живого journal или None.

    Прирост чужими записями живой сессии — штатно; K5 пишет spawn-пары
    ТОЛЬКО в полигоны (живой state — замеры read-only).
    """
    for line in grown.splitlines():
        if b'"spawn": true' in line or b'"kind": "backfill"' in line:
            return line
    return None


def tearDownModule():
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
            bad = _live_journal_growth_violation(grown)
            if bad is not None:
                raise AssertionError(
                    "live journal получил spawn-запись: %r" % bad[:200])
            continue
        if _live_housekeeping(rel):
            continue
        assert now_stat == before, "live state touched: %s" % rel


class SpawnTemp(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_state()
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        orchlib._CLOSE_BLOCKERS_CACHE.clear()
        self.now = time.time()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        orchlib._CLOSE_BLOCKERS_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (а) spawn-start + spawn-finish → пара в журнале; детекторы видят раны:
#     волна кода с зарегистрированными критиками/warden →
#     waves_no_critic / code_waves_no_gitwarden молчат
# ---------------------------------------------------------------------------


class TestSpawnPairVisible(SpawnTemp):
    def test_start_finish_pair_and_detectors_silent(self):
        _prosecutor(self.state, FRONT, self.now)
        r1 = _mk_receipt(self.state, "R-INV1")
        r2 = _mk_receipt(self.state, "R-REG1")
        art = _artifact(self.root, "coder.md",
                        "# Отчёт\n\nтекст\n\nВердикт: OK\n")
        rc, out, err = _cli_spawn(
            self.state, "spawn-start", "--id", "COD1",
            "--role", "code/coder.md", "--front", FRONT)
        self.assertEqual(rc, 0, "spawn-start ok: %s" % err)
        rc, out, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "COD1",
            "--artifact", art, "--receipts", "%s,%s" % (r1, r2))
        self.assertEqual(rc, 0, "spawn-finish ok: %s" % err)

        entries = _entries(self.state)
        starts = [e for e in entries
                  if e.get("kind") == "start" and e.get("id") == "COD1"]
        ends = [e for e in entries
                if e.get("kind") == "end" and e.get("id") == "COD1"]
        self.assertEqual(len(starts), 1)
        self.assertEqual(len(ends), 1)
        st, en = starts[0], ends[0]
        self.assertTrue(st.get("spawn"), "start помечен spawn:true")
        self.assertEqual(st.get("role"), "code/coder.md")
        self.assertEqual(st.get("front"), FRONT)
        self.assertEqual(st.get("engine"), "local")
        self.assertEqual(en.get("verdict"), "Вердикт: OK")
        self.assertTrue(en.get("spawn"))
        self.assertEqual(en.get("source_artifact"), os.path.realpath(art))
        self.assertAlmostEqual(en.get("artifact_mtime", 0),
                               os.path.getmtime(art), delta=5.0)
        self.assertEqual(en.get("receipts"), [r1, r2])

        # порядок регистрации (детекторы фронта): docs-keeper до последнего
        # критика (needs_critic), git-warden после код-ран волны
        docs = _artifact(self.root, "docskeeper.md",
                         "# Docs\n\nВердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "DOC1",
            "--role", "code/docs-keeper.md", "--front", FRONT,
            "--artifact", docs)
        self.assertEqual(rc, 0, "docs-keeper без receipts — ок: %s" % err)
        # критики + git-warden той же волны — spawn-backfill
        critics = []
        for i, text in ((1, "Вердикт: PROBLEMS: нюанс"),
                        (2, "Вердикт: OK"),
                        (3, "Вердикт: PROBLEMS: ниты")):
            path = _artifact(self.root, "review-%d.md" % i,
                             "# Ревью %d\n\n%s\n" % (i, text))
            rc, _o, err = _cli_spawn(
                self.state, "spawn-backfill", "--id", "CRT%d" % i,
                "--role", "code/code-reviewer.md", "--front", FRONT,
                "--artifact", path)
            self.assertEqual(rc, 0, "критик зарегистрирован: %s" % err)
            critics.append("CRT%d" % i)
        gw = _artifact(self.root, "gitwarden-post.md",
                       "# Warden\n\nВердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "GW1",
            "--role", "code/git-warden.md", "--front", FRONT,
            "--artifact", gw)
        self.assertEqual(rc, 0, "git-warden без receipts — ок: %s" % err)

        # детекторы видят раны штатно (поля стандартные): волна прикрыта
        data = orchlib._load_fronts_at(self.state)
        entries = _entries(self.state)
        chips, _anchors = orchlib._front_scope_chips(
            data, entries, front_ids=[FRONT])
        self.assertNotIn(FRONT, chips["waves_no_critic"])
        self.assertNotIn(FRONT, chips["code_waves_no_gitwarden"])
        self.assertNotIn(FRONT, chips["fronts_no_prosecutor"])
        # health полигона: целевые fid-классы тихи, probes_missing тих
        health = orchlib.health_red_chips(state=self.state)
        self.assertEqual(health.get("waves_no_critic"), [])
        self.assertEqual(health.get("code_waves_no_gitwarden"), [])
        self.assertEqual(health.get("probes_missing"), [])
        _measure("MEASURE (а) spawn-start+finish → пара (spawn:true, "
                 "verdict из артефакта) в журнале; критики/warden той же "
                 "волны через spawn-backfill → waves_no_critic / "
                 "code_waves_no_gitwarden молчат")

    def test_usage_errors(self):
        rc, _o, _e = _cli_spawn(self.state, "spawn-start", "--id", "X")
        self.assertEqual(rc, 2, "без --role/--front — usage")
        rc, _o, _e = _cli_spawn(
            self.state, "spawn-start", "--id", "X", "--role", "code/coder.md")
        self.assertEqual(rc, 2, "без --front — usage")
        env = dict(os.environ)
        env["ORCHESTRATION_DIR"] = self.state
        p = subprocess.run(
            [sys.executable, ORCHLIB_CLI, "spawn-unknown"],
            cwd=REPO, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, universal_newlines=True)
        self.assertEqual(p.returncode, 2, "неизвестная подкоманда — usage")
        # --check-close прежний контракт не тронут
        rc, _o, _e = _cli_spawn(self.state, "--check-close")
        self.assertEqual(rc, 2)


# ---------------------------------------------------------------------------
# (б) код-рана с --receipts (валидные квитанции полигона) → probes_missing
#     тих; без receipts → красный; невалидный id → отказ/красный (fail-closed)
# ---------------------------------------------------------------------------


class TestProbeReceipts(SpawnTemp):
    def test_code_run_with_valid_receipts_quiet(self):
        _prosecutor(self.state, FRONT, self.now)
        r1 = _mk_receipt(self.state, "R-INV9")
        r2 = _mk_receipt(self.state, "R-REG9")
        art = _artifact(self.root, "coder9.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "COD9",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", "%s, %s" % (r1, r2))
        self.assertEqual(rc, 0, err)
        self.assertEqual(
            orchlib.probes_missing(state=self.state, scan_limit=False), [],
            "квитанции волны валидны → рана прикрыта")
        # fid-классы (критики/warden не регистрировались в этой фикстуре) —
        # не предмет (б); предмет: run_id-класс probes_missing не блокирует
        self.assertNotIn(
            "probes_missing:COD9",
            orchlib.close_blockers(FRONT, state=self.state, use_cache=False),
            "run_id-класс probes_missing по спавн-ране прикрыт")

    def test_code_run_without_receipts_red(self):
        # ран без receipts в журнал не попадает через регистратор (гард),
        # но может существовать как исторический спавн — детектор красный
        _prosecutor(self.state, FRONT, self.now)
        art = _artifact(self.root, "coder8.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "COD8",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT,
                         "код-рана без --receipts — отказ: %s" % err)
        # прямой посев той же ситуации (движковый ран мимо регистратора)
        _seed_run(self.state, "COD8R", FRONT, "code/coder.md",
                  self.now - 10, self.now - 5, spawn=True)
        self.assertEqual(
            orchlib.probes_missing(state=self.state, scan_limit=False),
            ["COD8R"], "spawn код-рана без receipts — красный, как раньше")

    def test_spawn_end_invalid_receipt_still_red(self):
        # receipts с невалидным id (ручная вклейка/протухшая квитанция) —
        # fail-closed: рана НЕ прикрыта
        _prosecutor(self.state, FRONT, self.now)
        r_ok = _mk_receipt(self.state, "R-GOOD")
        _seed_run(self.state, "COD7", FRONT, "code/coder.md",
                  self.now - 10, self.now - 5,
                  extra={"receipts": [r_ok, "R-NOSUCH"]}, spawn=True)
        self.assertEqual(
            orchlib.probes_missing(state=self.state, scan_limit=False),
            ["COD7"], "одна невалидная квитанция в списке — красный")

    def test_registrar_validates_receipt_ids(self):
        _prosecutor(self.state, FRONT, self.now)
        before = _raw_journal(self.state)
        art = _artifact(self.root, "coder6.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "COD6",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", "R-NOSUCH")
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT,
                         "невалидный id квитанции — отказ: %s" % err)
        self.assertIn("receipts_invalid", err)
        self.assertEqual(_raw_journal(self.state), before,
                         "отказ — журнал не тронут")
        _measure("MEASURE (б) код-рана с валидными --receipts → "
                 "probes_missing тих; без receipts → красный; невалидный "
                 "id — отказ регистратора/красный детектора (fail-closed)")


# ---------------------------------------------------------------------------
# (в) spawn-backfill из существующего артефакта → пара + kind=backfill;
#     ts=now, verdict из файла; форматы вердикт-строк
# ---------------------------------------------------------------------------


class TestSpawnBackfill(SpawnTemp):
    def test_backfill_triple_and_honesty(self):
        _prosecutor(self.state, FRONT, self.now)
        r1 = _mk_receipt(self.state, "R-INV5")
        body = ("# Отчёт волны\n\nработа\n\n## Вердикт: ГОТОВО — всё "
                "зелёное, приёмка 26+368\n")
        art = _artifact(self.root, "report.md", body)
        mtime = os.path.getmtime(art)
        t0 = time.time()
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "BF1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, 0, err)
        entries = _entries(self.state)
        by_kind = {}
        for e in entries:
            if e.get("id") != "BF1":
                continue
            by_kind.setdefault(e.get("kind"), []).append(e)
        self.assertEqual(len(by_kind.get("start", [])), 1)
        self.assertEqual(len(by_kind.get("end", [])), 1)
        self.assertEqual(len(by_kind.get("backfill", [])), 1)
        st, en, bf = (by_kind["start"][0], by_kind["end"][0],
                      by_kind["backfill"][0])
        # честность: ts=момент регистрации (НЕ исторический: файл старше)
        for e in (st, en, bf):
            self.assertGreaterEqual(e["ts"], t0 - 1)
            self.assertLessEqual(e["ts"], time.time() + 1)
        self.assertGreater(bf["ts"], mtime,
                           "ts регистрации строго новее mtime артефакта")
        self.assertEqual(bf["registered_ts"], bf["ts"])
        self.assertEqual(bf["id"], "BF1")
        self.assertEqual(bf["role"], "code/coder.md")
        self.assertEqual(bf["front"], FRONT)
        self.assertEqual(bf["source_artifact"], os.path.realpath(art))
        self.assertEqual(bf["artifact_mtime"], mtime)
        self.assertTrue(bf["verdict"].startswith("Вердикт: ГОТОВО"))
        # verdict-строка из файла, без выдумок
        self.assertEqual(en["verdict"], bf["verdict"])
        self.assertEqual(en.get("source_artifact"), os.path.realpath(art))
        self.assertEqual(en.get("artifact_mtime"), mtime)
        self.assertEqual(en.get("receipts"), [r1])
        self.assertTrue(st.get("spawn") and en.get("spawn"))
        # поле exit отсутствует: истина завершения — verdict (KR3), не
        # синтетический «код успеха» регистрации
        self.assertNotIn("exit", en, "spawn-end без поля exit")
        _measure("MEASURE (в) spawn-backfill → start+end+kind=backfill; "
                 "ts=момент регистрации (строго новее mtime файла), "
                 "registered_ts==ts, verdict/source_artifact/mtime — из "
                 "артефакта-факта; поле exit в spawn-записях отсутствует")

    def test_verdict_line_formats(self):
        # строгий формат — частный случай; суффикс/декор; голый заголовок;
        # итог-строка; последний инлайн побеждает; отсутствие — None
        cases = [
            ("Вердикт: OK", "Вердикт: OK"),
            ("**Вердикт: OK** — механизм реализован; SHA: **abc1234** (`abc`).",
             "Вердикт: OK"),
            ("## Вердикт: НЕ OK — 2 проблемы (одна валит прогон)",
             "Вердикт: НЕ OK — 2 проблемы (одна валит прогон)"),
            ("**Вердикт follow-up: ГОТОВО — 8/8 правок, коммит abc1234.**",
             "Вердикт: ГОТОВО — 8/8 правок, коммит abc1234."),
            ("## Вердикт\n\nПРОБЛЕМА.", "Вердикт: ПРОБЛЕМА."),
            ("Итог: запуск ранов живьём — зуб", "Вердикт: запуск ранов живьём — зуб"),
            ("таблица | 1 | KR1 ПРОБЛЕМА: текст |\nбез вердикта", None),
            ("", None),
        ]
        for text, expected in cases:
            self.assertEqual(orchlib.spawn_verdict_from_text(text), expected,
                             "формат вердикт-строки: %r" % text[:40])
        # несколько инлайн-вердиктов → последний (итоговый)
        multi = ("**Вердикт: ПРОБЛЕМА — первый круг**\n\n"
                 "**Вердикт follow-up-2: ГОТОВО — всё закрыто**\n")
        self.assertEqual(
            orchlib.spawn_verdict_from_text(multi),
            "Вердикт: ГОТОВО — всё закрыто")
        # голый заголовок: вердикт = первая непустая строка ниже
        bare = "## Вердикт\n\n   \nПРОБЛЕМА 1 (критерий 2) — не держит.\n"
        self.assertEqual(
            orchlib.spawn_verdict_from_text(bare),
            "Вердикт: ПРОБЛЕМА 1 (критерий 2) — не держит.")


# ---------------------------------------------------------------------------
# (г) артефакт отсутствует / пустой / без вердикт-строки / чужой start →
#     отказ, журнал не тронут
# ---------------------------------------------------------------------------


class TestArtifactGuards(SpawnTemp):
    def _refuse(self, rc, err, needle, before):
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn(needle, err)
        self.assertEqual(_raw_journal(self.state), before,
                         "отказ гарда — журнал не тронут")

    def test_missing_artifact(self):
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "G1",
            "--role", "code/code-reviewer.md", "--front", FRONT,
            "--artifact", os.path.join(self.root, "nope.md"))
        self._refuse(rc, err, "artifact_missing", before)

    def test_empty_artifact(self):
        path = _artifact(self.root, "empty.md", "")
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "G2",
            "--role", "code/code-reviewer.md", "--front", FRONT,
            "--artifact", path)
        self._refuse(rc, err, "artifact_empty", before)

    def test_artifact_without_verdict(self):
        path = _artifact(self.root, "noverdict.md",
                         "# Отчёт\n\nработа сделана, но вердикта нет\n")
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "G3",
            "--role", "code/code-reviewer.md", "--front", FRONT,
            "--artifact", path)
        self._refuse(rc, err, "artifact_no_verdict", before)

    def test_finish_without_start_and_foreign_start(self):
        path = _artifact(self.root, "ok.md", "Вердикт: OK\n")
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "GHOST",
            "--artifact", path)
        self._refuse(rc, err, "no_spawn_start", before)
        # start чужой обёртки (не spawn) — finish откажет
        _seed_run(self.state, "WRAP1", FRONT, "code/coder.md",
                  self.now - 10)
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "WRAP1", "--artifact", path)
        self._refuse(rc, err, "not_spawn_start", before)
        # front обязателен: CLI без --front — usage (2); гард регистратора
        # front_required — прямой вызов (публичная точка orchlib)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "G4",
            "--role", "code/code-reviewer.md", "--artifact", path)
        self.assertEqual(rc, 2, "нет --front — usage: %s" % err)
        ok, msg = orchlib.spawn_register(
            "backfill", "G4", role="code/code-reviewer.md", front=None,
            artifact=path, state=self.state)
        self.assertFalse(ok)
        self.assertEqual(msg, "front_required")
        self.assertEqual(_raw_journal(self.state), before)
        _measure("MEASURE (г) артефакт отсутствует/пуст/без вердикт-"
                 "строки, finish без spawn-start, front не задан → отказ "
                 "exit 3, журнал байт-в-байт не тронут")


# ---------------------------------------------------------------------------
# (д) повторная регистрация id → отказ (дедуп под flock), дублей нет
# ---------------------------------------------------------------------------


class TestDedup(SpawnTemp):
    def test_backfill_repeat_refused(self):
        _prosecutor(self.state, FRONT, self.now)
        r1 = _mk_receipt(self.state, "R-DUP")
        art = _artifact(self.root, "dup.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "D1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, 0, err)
        after_first = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "D1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT,
                         "повторная регистрация id — отказ: %s" % err)
        self.assertIn("duplicate_start", err)
        self.assertEqual(_raw_journal(self.state), after_first,
                         "дубль не пишется")

    def test_start_finish_lifecycle_dedup(self):
        art = _artifact(self.root, "life.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "D2",
            "--role", "code/git-warden.md", "--front", FRONT)
        self.assertEqual(rc, 0, err)
        after_start = _raw_journal(self.state)
        # старт дважды — отказ; backfill по живому старту — отказ
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "D2",
            "--role", "code/git-warden.md", "--front", FRONT)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_start", err)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "D2",
            "--role", "code/git-warden.md", "--front", FRONT,
            "--artifact", art)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_start", err)
        self.assertEqual(_raw_journal(self.state), after_start)
        # finish один раз ок, второй — отказ
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "D2", "--artifact", art)
        self.assertEqual(rc, 0, err)
        after_finish = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "D2", "--artifact", art)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_end", err)
        self.assertEqual(_raw_journal(self.state), after_finish,
                         "повторный finish не дублирует end")
        _measure("MEASURE (д) повторный spawn-start/backfill того же id и "
                 "второй spawn-finish → отказ exit 3 (duplicate_*), "
                 "журнал не растёт")


# ---------------------------------------------------------------------------
# (е) движковый ран БЕЗ регистрации → детекторы слепы (вижу только проб-раны
#     обёртки → waves_no_critic/code_waves_no_gitwarden красные); после
#     backfill волны → --check-close → [] — полный сценарий закрытия
# ---------------------------------------------------------------------------


class TestBeforeAfterRegistration(SpawnTemp):
    def test_full_close_scenario_before_after(self):
        _prosecutor(self.state, FRONT_E, self.now)
        # видимые журналу волны = проб-раны обёртки (cmd_probe-семантика),
        # у каждого — валидная квитанция §3 (probes_missing тих)
        for rid in ("W-P1", "W-P2"):
            _seed_run(self.state, rid, FRONT_E, "code/coder.md",
                      self.now - 50, self.now - 40)
            _mk_receipt(self.state, rid)

        # ДО: движковая волна (кодер+критики+warden) не регистрирована —
        # детекторы видят только код-раны без критиков/warden → отказ
        rc, _o, err = _cli_check_close(self.state, FRONT_E)
        self.assertEqual(rc, 1, "до регистрации — отказ: %s" % err)
        self.assertIn("waves_no_critic:%s" % FRONT_E, err)
        self.assertIn("code_waves_no_gitwarden:%s" % FRONT_E, err)
        blockers = orchlib.close_blockers(
            FRONT_E, state=self.state, use_cache=False)
        self.assertEqual(
            blockers,
            ["waves_no_critic:%s" % FRONT_E,
             "code_waves_no_gitwarden:%s" % FRONT_E])

        # движковая волна: артефакты есть, в журнале ранов нет
        art_coder = _artifact(self.root, "e-coder.md",
                              "# Отчёт\n\nВердикт: ГОТОВО\n")
        art_gw_pre = _artifact(self.root, "e-gw-pre.md", "Вердикт: OK\n")
        art_gw_post = _artifact(self.root, "e-gw-post.md", "Вердикт: OK\n")
        art_docs = _artifact(self.root, "e-docs.md", "Вердикт: OK\n")
        plan = [("C5-E-coder", "code/coder.md", art_coder, "W-P1,W-P2")]
        for i, txt in ((1, "Вердикт: PROBLEMS: замечания"),
                       (2, "Вердикт: PROBLEMS: ниты"),
                       (3, "Вердикт: OK")):
            plan.append(("C5-E-review-%d" % i, "code/code-reviewer.md",
                         _artifact(self.root, "e-review-%d.md" % i,
                                   "# Ревью\n\n%s\n" % txt), None))
        plan.append(("C5-E-gitwarden-pre", "code/git-warden.md",
                     art_gw_pre, None))
        plan.append(("C5-E-gitwarden-post", "code/git-warden.md",
                     art_gw_post, None))
        plan.append(("C5-E-docskeeper", "code/docs-keeper.md",
                     art_docs, None))
        # порядок регистрации (детекторы фронта сверяют ts последнего
        # критика/warden с ранами): кодер → warden-pre → docs-keeper →
        # критики → warden-post; ts везде = момент регистрации
        order = ["C5-E-coder", "C5-E-gitwarden-pre", "C5-E-docskeeper",
                 "C5-E-review-1", "C5-E-review-2", "C5-E-review-3",
                 "C5-E-gitwarden-post"]
        by_id = {p[0]: p for p in plan}
        for run_id in order:
            _rid, role, path, receipts = by_id[run_id]
            args = ["spawn-backfill", "--id", _rid, "--role", role,
                    "--front", FRONT_E, "--artifact", path]
            if receipts:
                args += ["--receipts", receipts]
            rc, _o, err = _cli_spawn(self.state, *args)
            self.assertEqual(rc, 0, "backfill %s: %s" % (_rid, err))

        # ПОСЛЕ: волна видна и прикрыта — блокеров нет
        rc, out, _err = _cli_check_close(self.state, FRONT_E)
        self.assertEqual(rc, 0, "после backfill — close OK: %s" % out)
        self.assertEqual(
            orchlib.close_blockers(
                FRONT_E, state=self.state, use_cache=False), [])
        # probes_missing по-прежнему тих: проб-раны с квитанциями +
        # спавн-кодер прикрыт receipts волны
        self.assertEqual(
            orchlib.probes_missing(state=self.state, scan_limit=False), [])
        _measure("MEASURE (е) до/после: движковая волна без регистрации → "
                 "--check-close exit 1 (waves_no_critic + "
                 "code_waves_no_gitwarden); spawn-backfill волны из "
                 "артефактов → --check-close exit 0, блокеров нет")


# ---------------------------------------------------------------------------
# FOLLOW-UP (ревью ×3): дедуп source_artifact (realpath, вкл. симлинк);
# spawn-abandon висящего старта (idle-детекторы возвращаются); секрет в
# вердикт-строке; строгость флагов по режиму
# ---------------------------------------------------------------------------


class TestFollowup(SpawnTemp):
    def test_artifact_dedup_realpath_and_symlink(self):
        # один source_artifact (в т.ч. симлинк на него) не регистрируется
        # под вторым id; id-дедуп остаётся (тот же id → duplicate_start)
        r1 = _mk_receipt(self.state, "R-FA")
        art = _artifact(self.root, "one.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "FA-1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, 0, err)
        after_first = _raw_journal(self.state)
        # другой id, тот же файл → отказ
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "FA-2",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_artifact", err)
        self.assertEqual(_raw_journal(self.state), after_first,
                         "дубль артефакта не пишется")
        # симлинк на тот же файл — тот же источник (realpath)
        link = os.path.join(self.root, "art", "one-link.md")
        os.symlink(art, link)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "FA-3",
            "--role", "code/code-reviewer.md", "--front", FRONT,
            "--artifact", link)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_artifact", err)
        self.assertEqual(_raw_journal(self.state), after_first)
        # тот же id повторно → id-дедуп (не артефактный)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "FA-1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_start", err)
        # spawn-finish другого рана с тем же артефактом → тоже отказ
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "FA-4",
            "--role", "code/git-warden.md", "--front", FRONT)
        self.assertEqual(rc, 0, err)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "FA-4", "--artifact", art)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_artifact", err)
        # другой артефакт — регистрируется свободно
        art2 = _artifact(self.root, "two.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "FA-4", "--artifact", art2)
        self.assertEqual(rc, 0, err)
        _measure("MEASURE (FU-1) один source_artifact (вкл. симлинк) — "
                 "ровно один ран: повторный backfill/finish под другим id → "
                 "отказ duplicate_artifact, журнал не тронут; id-дедуп "
                 "сохранён (duplicate_start)")

    def test_spawn_abandon_restores_idle_detectors(self):
        # висящий spawn-start навсегда делал idle=False → fid-детекторы
        # фронта подавлены; abandon закрывает ран честной пометкой
        _prosecutor(self.state, FRONT, self.now)
        # базовая линия: законченная волна (проб-ран обёртки с квитанцией)
        # честно видна детекторам
        _seed_run(self.state, "W-A", FRONT, "code/coder.md",
                  self.now - 60, self.now - 50)
        _mk_receipt(self.state, "W-A")
        base = orchlib.close_blockers(FRONT, state=self.state, use_cache=False)
        self.assertIn("waves_no_critic:%s" % FRONT, base)
        self.assertIn("code_waves_no_gitwarden:%s" % FRONT, base)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "HANG1",
            "--role", "code/coder.md", "--front", FRONT)
        self.assertEqual(rc, 0, err)
        # ДО abandon: открытый старт держит idle=False — волна «невидима»
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertNotIn("waves_no_critic:%s" % FRONT, blockers,
                         "висящий старт подавил idle-детектор (сцена KR1)")
        self.assertNotIn("code_waves_no_gitwarden:%s" % FRONT, blockers)
        # abandon без причины — usage; с секретом в причине — гард
        rc, _o, _e = _cli_spawn(self.state, "spawn-abandon", "--id", "HANG1")
        self.assertEqual(rc, 2)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-abandon", "--id", "HANG1",
            "--reason", "умер, ключ sk-abcdef0123456789abcdef0123456789")
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("secrets_in_argv", err)
        # честный abandon с фактом в причине
        rc, _o, err = _cli_spawn(
            self.state, "spawn-abandon", "--id", "HANG1",
            "--reason", "движковый ран умер без артефакта, отчёта нет")
        self.assertEqual(rc, 0, err)
        ends = [e for e in _entries(self.state)
                if e.get("kind") == "end" and e.get("id") == "HANG1"]
        self.assertEqual(len(ends), 1)
        en = ends[0]
        self.assertEqual(
            en.get("verdict"),
            "ABANDONED: движковый ран умер без артефакта, отчёта нет")
        self.assertTrue(en.get("spawn"))
        self.assertNotIn("source_artifact", en)
        self.assertNotIn("exit", en)
        self.assertNotIn("receipts", en)
        # ПОСЛЕ abandon: ран закрыт → idle вернулся, волна честно видна
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertIn("waves_no_critic:%s" % FRONT, blockers)
        self.assertIn("code_waves_no_gitwarden:%s" % FRONT, blockers)
        # abandon-код-рана без квитанций остаётся красной в probes_missing
        # (fail-closed: артефакта/приёмки нет — рана не прикрыта)
        self.assertIn("HANG1",
                      orchlib.probes_missing(state=self.state,
                                             scan_limit=False))
        # abandon неизвестного/закрытого рана — отказ
        rc, _o, err = _cli_spawn(
            self.state, "spawn-abandon", "--id", "GHOST-X",
            "--reason", "нет рана")
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("no_spawn_start", err)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-abandon", "--id", "HANG1",
            "--reason", "повтор")
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("duplicate_end", err)
        _measure("MEASURE (FU-2) spawn-abandon: законченная волна видна → "
                 "висящий spawn-start подавил idle-детекторы → end с "
                 "verdict=ABANDONED:<факт> (без exit/source_artifact) → "
                 "волна снова видна честно; abandon код-раны без квитанций "
                 "остаётся красной в probes_missing")

    def test_verdict_secret_refused(self):
        # секрет, случайно попавший в вердикт-строку артефакта, не
        # переезжает в журнал (KR1): гард secrets_in_verdict
        r1 = _mk_receipt(self.state, "R-SEC")
        art = _artifact(
            self.root, "leak.md",
            "Вердикт: OK — ключ sk-abcdef0123456789abcdef0123456789 утёк\n")
        before = _raw_journal(self.state)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-backfill", "--id", "SEC1",
            "--role", "code/coder.md", "--front", FRONT,
            "--artifact", art, "--receipts", r1)
        self.assertEqual(rc, orchlib.SPAWN_REFUSE_EXIT, err)
        self.assertIn("secrets_in_verdict", err)
        self.assertEqual(_raw_journal(self.state), before,
                         "секретный вердикт не пишется в журнал")

    def test_mode_flags_strictness(self):
        # неприменимые к режиму флаги — usage-отказ exit 2, не молчаливое
        # игнорирование (KR3): --receipts на start, --role на finish,
        # --artifact на start, --front на finish, всё лишнее на abandon
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "STX",
            "--role", "code/git-warden.md", "--front", FRONT,
            "--receipts", "R1")
        self.assertEqual(rc, 2, err)
        art = _artifact(self.root, "stx.md", "Вердикт: OK\n")
        rc, _o, err = _cli_spawn(
            self.state, "spawn-start", "--id", "STX",
            "--role", "code/git-warden.md", "--front", FRONT,
            "--artifact", art)
        self.assertEqual(rc, 2, err)
        rc, _o, _e = _cli_spawn(
            self.state, "spawn-start", "--id", "STX",
            "--role", "code/git-warden.md", "--front", FRONT)
        self.assertEqual(rc, 0)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "STX",
            "--role", "code/git-warden.md", "--artifact", art)
        self.assertEqual(rc, 2, err)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "STX",
            "--front", FRONT, "--artifact", art)
        self.assertEqual(rc, 2, err)
        rc, _o, err = _cli_spawn(
            self.state, "spawn-abandon", "--id", "STX",
            "--reason", "x", "--receipts", "R1")
        self.assertEqual(rc, 2, err)
        # применимые флаги по-прежнему работают
        rc, _o, err = _cli_spawn(
            self.state, "spawn-finish", "--id", "STX", "--artifact", art)
        self.assertEqual(rc, 0, err)
        self.assertEqual(_raw_journal(self.state).count(b'"kind": "start"'), 1,
                         "за usage-отказы журнал не рос лишними стартами")
        _measure("MEASURE (FU-3) строгость argv: неприменимые к режиму "
                 "флаги (--receipts на start, --role/--front на finish, "
                 "--artifact на start, лишнее на abandon) → exit 2 до "
                 "записи; секрет в вердикт-строке → secrets_in_verdict")


# ---------------------------------------------------------------------------
# (ж) регресс ×2: сьюты K1–K3 + обёрточные (run-exec) зелёные дважды —
#     поведение стандартных обёрточных ранов НЕ изменилось
# ---------------------------------------------------------------------------


class TestRegressionTwice(unittest.TestCase):
    def test_k1_k3_and_wrapper_suites_twice(self):
        env = dict(os.environ)
        env.pop("ORCHESTRATION_DIR", None)
        for attempt in (1, 2):
            p = subprocess.run(
                [sys.executable, "-m", "pytest"] + list(REGRESS_SUITES)
                + ["-q"],
                cwd=REPO, env=env, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, universal_newlines=True)
            tail = (p.stdout or "").strip().splitlines()
            tail = tail[-1] if tail else ""
            self.assertEqual(
                p.returncode, 0,
                "регресс-прогон %d/2 красный: %s" % (attempt, tail))
            sys.stdout.write("регресс %d/2: %s\n" % (attempt, tail))
        _measure("MEASURE (ж) регресс ×2: K1–K3 + обёрточные сьюты "
                 "(supervision_dead / invariants_gate / close_gate / "
                 "receipt_wrap / stall_exec) зелёные оба прогона — "
                 "поведение стандартных обёрточных ранов не изменилось; "
                 "полный tests/ ×2 — приёмочный замер волны (отчёт)")


if __name__ == "__main__":
    unittest.main()
