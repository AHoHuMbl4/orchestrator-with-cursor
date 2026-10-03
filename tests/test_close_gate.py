#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-C5 K3 (в): front-close гейт — отказ done при красных чипах волн, Инв. 3.

Переход фронта в done при непустых close_blockers (allowlist 8) → отказ с
перечнем на всех путях: save_fronts (ValueError), обе POST-ветки панели
(HTTP 400), CLI --check-close (ненулевой exit); vim-правка fronts.json мимо
save_fronts → пост-чип front_closed_red (писатель — скан orchlib, дедуп
«один чип на событие»). Grandfathering: блокеры только из событий ПОСЛЕ
CLOSE_GATE_SHIP_TS; legacy-waivers уважены. Фикстуры 1–11 приказа
полковника C5-K3.

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_close_gate.py -q
State: только ORCHESTRATION_DIR=/tmp/k3-…; живой /root/.orchestration не пишем
(единственные живые замеры — read-only: close_blockers по done-фронтам).
"""
from __future__ import print_function

import importlib.util
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
PANEL_INDEX = os.path.join(REPO, "panel", "index.html")
ORCHLIB_CLI = os.path.join(REPO, "bin", "orchlib.py")
OWNS_PATH = os.path.join(REPO, "bin", "owns.py")
LIVE_STATE = "/root/.orchestration"
FRONT = "F-K3A"
FRONT_B = "F-K3B"
CMD1 = "python3 -m pytest tests/test_a.py -q"
SECTION_HEADER = (
    "## Инварианты (машиночитаемые — строгий формат "
    "«Инвариант N: <cmd> → <оракул>»; приёмка требует прогона КАЖДОГО)")

if BIN not in sys.path:
    sys.path.insert(0, BIN)
if PANEL not in sys.path:
    sys.path.insert(0, PANEL)

import orchlib  # noqa: E402
import server as panel_server  # noqa: E402


def _load_mod(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


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


def _mk_state(prefix="k3", fronts=None):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "k3 close gate",
        "fronts": fronts if fronts is not None else [
            _front(FRONT, "active", ["bin/orchlib.py", "panel/**",
                                     "tests/test_close_gate.py"]),
            _front(FRONT_B, "active", ["tests/**"]),
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


def _seed_run(state, rid, front, role, ts_start, ts_end=None, extra=None):
    """start(+end)-пара рана role на фронте front (journal)."""
    entries = [{"ts": ts_start, "kind": "start", "id": rid,
                "engine": "local", "front": front, "role": role}]
    if ts_end is not None:
        end = {"ts": ts_end, "kind": "end", "id": rid, "exit": 0}
        if extra:
            end.update(extra)
        entries.append(end)
    _seed_journal(state, entries)


def _seed_chip(state, name, front, ts, **extra):
    e = {"ts": ts, "kind": "chip", "name": name, "front": front}
    e.update(extra)
    _seed_journal(state, [e])
    return e


def _write_order(state, fid, rows=None):
    lines = ["# Приказ фронту %s — k3 фикстуры" % fid, "", "## Цель",
             "синтетика полигона /tmp.", "", SECTION_HEADER, ""]
    if rows:
        lines.extend(rows)
        lines.append("")
    lines.extend(["## Границы", "- полигоны /tmp."])
    path = orchlib.front_order_path(fid, state=state)
    _assert_not_live(path)
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def _inv_row(num, cmd, oracle="exit 0"):
    return "- Инвариант %d: %s → %s" % (num, cmd, oracle)


def _vim_close(state, fid):
    """Прямая запись fronts.json мимо save_fronts (vim-обход)."""
    path = os.path.join(state, "fronts.json")
    with open(path, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    for fr in data.get("fronts") or []:
        if isinstance(fr, dict) and fr.get("id") == fid:
            fr["status"] = "done"
    _write_json(path, data)
    orchlib._fronts_base = None  # type: ignore[attr-defined]
    return data


def _disk_status(state, fid):
    with open(os.path.join(state, "fronts.json"), "r",
              encoding="utf-8-sig") as f:
        data = json.load(f)
    for fr in data.get("fronts") or []:
        if fr.get("id") == fid:
            return fr.get("status")
    return None


def _close_via_save(state, fid):
    """Переход fid → done через orchlib.save_fronts (гейтный путь)."""
    orchlib._fronts_base = None  # type: ignore[attr-defined]
    data = orchlib.load_fronts()
    for fr in data.get("fronts") or []:
        if fr.get("id") == fid:
            fr["status"] = "done"
    orchlib.save_fronts(data)


def _chips_in_journal(state, name=None):
    out = []
    for e in orchlib._journal_entries_at(state):
        if e.get("kind") != "chip":
            continue
        if name is not None and e.get("name") != name:
            continue
        out.append(e)
    return out


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
    (паттерн tests/test_supervision_dead.py / test_invariants_gate.py)."""
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
    """Строка чипа front_closed_red в приросте живого journal или None.

    Прирост чужими записями живой сессии — штатно; K3 пишет чипы ТОЛЬКО в
    полигоны (живой state — замеры read-only).
    """
    for line in grown.splitlines():
        if b"front_closed_red" in line and b'"chip"' in line:
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
                    "live journal получил чип front_closed_red: %r"
                    % bad[:200])
            continue
        if _live_housekeeping(rel):
            continue
        assert now_stat == before, "live state touched: %s" % rel


class CloseTemp(unittest.TestCase):
    def setUp(self):
        self.root, self.state = _mk_state()
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        self.now = time.time()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        orchlib._CLOSE_BLOCKERS_CACHE.clear()
        shutil.rmtree(self.root, ignore_errors=True)

    # --- билдеры красных событий 8 классов allowlist ----------------------

    def _build_class(self, chip_class, fid=FRONT, kit_dir=None):
        """Красное событие класса chip_class (ts > now) на полигона."""
        now = self.now
        if chip_class == "supervision_dead":
            _seed_chip(self.state, "supervision_dead", fid, now, id="SUP1")
            return "supervision_dead:SUP1"
        if chip_class == "multi_write_front":
            _seed_chip(self.state, "multi_write_front", fid, now)
            return "multi_write_front:%s" % fid
        if chip_class == "probes_missing":
            _seed_run(self.state, "W1", fid, "code/coder.md", now - 10,
                      now - 5)
            return "probes_missing:W1"
        if chip_class == "chip_silenced":
            # панель полигона не объявляет label probes_missing →
            # сырой probes_missing «заглушен» UI
            _seed_run(self.state, "W1", fid, "code/coder.md", now - 10,
                      now - 5)
            return "chip_silenced:W1"
        if chip_class == "invariants_not_run":
            _write_order(self.state, fid, [_inv_row(1, CMD1)])
            # активность фронта пост-cutoff (scout — не wave-work,_fid-классы тихи)
            _seed_run(self.state, "SC1", fid, "meta/web-scout.md", now - 10,
                      now - 5)
            return "invariants_not_run:%s:1" % fid
        if chip_class == "fronts_no_prosecutor":
            _seed_run(self.state, "COL1", fid, "meta/front-colonel.md",
                      now - 20, now - 10)
            return "fronts_no_prosecutor:%s" % fid
        if chip_class == "waves_no_critic":
            _seed_run(self.state, "PRO1", fid, "meta/front-prosecutor.md",
                      now - 30, now - 25)
            _seed_run(self.state, "COL1", fid, "meta/front-colonel.md",
                      now - 20, now - 10)
            return "waves_no_critic:%s" % fid
        if chip_class == "code_waves_no_gitwarden":
            _seed_run(self.state, "PRO1", fid, "meta/front-prosecutor.md",
                      now - 40, now - 35)
            _seed_run(self.state, "COD1", fid, "code/coder.md",
                      now - 30, now - 20)
            _seed_run(self.state, "CRT1", fid, "code/code-reviewer.md",
                      now - 15, now - 5)
            return "code_waves_no_gitwarden:%s" % fid
        raise AssertionError("неизвестный класс: %s" % chip_class)

    def _kit_without_label(self):
        """kit_dir полигона: panel/index.html БЕЗ label probes_missing."""
        kit = os.path.join(self.root, "kit")
        os.makedirs(os.path.join(kit, "panel"), exist_ok=True)
        with open(os.path.join(kit, "panel", "index.html"), "w",
                  encoding="utf-8") as f:
            f.write("<!doctype html><html></html>\n")
        return kit


ALLOWLIST_CLASSES = (
    "probes_missing", "chip_silenced", "invariants_not_run",
    "supervision_dead", "multi_write_front", "fronts_no_prosecutor",
    "waves_no_critic", "code_waves_no_gitwarden",
)


# ---------------------------------------------------------------------------
# (1) каждый из 8 классов allowlist: красное событие (ts>cutoff) →
#     close_blockers содержит → done через save_fronts → ValueError
#     с перечнем (класс указан)
# ---------------------------------------------------------------------------


class TestAllowlistEightClasses(CloseTemp):
    def test_each_class_blocks_close_with_listing(self):
        for chip_class in ALLOWLIST_CLASSES:
            with self.subTest(chip_class=chip_class):
                self.root, self.state = _mk_state()
                os.environ["ORCHESTRATION_DIR"] = self.state
                orchlib._fronts_base = None  # type: ignore[attr-defined]
                kit_dir = None
                expected = self._build_class(chip_class)
                prev_kit = None
                if chip_class == "chip_silenced":
                    # synthetic kit: панель БЕЗ label probes_missing (гейт
                    # берёт kit по умолчанию — патчим на полигон)
                    prev_kit = orchlib.KIT_DIR
                    orchlib.KIT_DIR = self._kit_without_label()  # type: ignore
                try:
                    blockers = orchlib.close_blockers(
                        FRONT, state=self.state, kit_dir=kit_dir,
                        use_cache=False)
                    self.assertIn(expected, blockers,
                                  "%s в перечне блокеров: %s" % (expected,
                                                                 blockers))
                    # done через save_fronts → ValueError с перечнем
                    with self.assertRaises(ValueError) as ctx:
                        _close_via_save(self.state, FRONT)
                finally:
                    if prev_kit is not None:
                        orchlib.KIT_DIR = prev_kit  # type: ignore
                text = "; ".join(str(x) for x in ctx.exception.args[0])
                self.assertIn("close-гейт", text)
                self.assertIn(expected, text,
                              "перечень называет класс и элемент")
                # отказ ДО persist: диск не изменился
                self.assertEqual(_disk_status(self.state, FRONT), "active")
        _measure("MEASURE (1) все 8 классов allowlist: красное событие "
                 "ts>cutoff → close_blockers содержит → save_fronts "
                 "ValueError с перечнем (класс назван), диск не тронут")

    def test_allowlist_exactly_eight_commit_no_verify_excluded(self):
        self.assertEqual(len(orchlib.CLOSE_GATE_ALLOWLIST), 8)
        self.assertEqual(set(orchlib.CLOSE_GATE_ALLOWLIST),
                         set(ALLOWLIST_CLASSES))
        self.assertNotIn("commit_no_verify",
                         orchlib.CLOSE_GATE_ALLOWLIST,
                         "commit_no_verify исключён (решение командующего)")
        # шумный чип вне allowlist не блокирует
        _seed_chip(self.state, "commit_no_verify", FRONT, self.now)
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state, use_cache=False),
            [])


# ---------------------------------------------------------------------------
# (2) обе ветки резолва: run_id-классы (start.front==fid) И fid-классы
#     (фронт в скоупе при status=done — active-фильтр не опустошает)
# ---------------------------------------------------------------------------


class TestResolveBranches(CloseTemp):
    def test_run_id_branch_resolves_start_front(self):
        # (ii-1) элемент = run_id: резолв start.front==fid
        _seed_run(self.state, "W1", FRONT, "code/coder.md",
                  self.now - 10, self.now - 5)
        _seed_run(self.state, "WX", FRONT_B, "code/coder.md",
                  self.now - 10, self.now - 5)
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertIn("probes_missing:W1", blockers)
        self.assertFalse(any("WX" in b for b in blockers),
                         "чужой run (start.front≠fid) не резолвится на fid")

    def test_fid_branch_done_front_not_emptied_by_active_filter(self):
        # (ii-2) элемент = fid: прямое сравнение, фронт УЖЕ done (vim) —
        # active-фильтр не опустошает computed-блокеры
        _seed_run(self.state, "COL1", FRONT, "meta/front-colonel.md",
                  self.now - 20, self.now - 10)
        _vim_close(self.state, FRONT)
        entries = orchlib._journal_entries_at(self.state)
        data = orchlib._load_fronts_at(self.state)
        # close-режим: fid в скоупе независимо от статуса
        chips_close, _anchors = orchlib._front_scope_chips(
            data, entries, front_ids=[FRONT])
        self.assertIn(FRONT, chips_close["waves_no_critic"])
        # health-режим: active-фильтр на месте (поведение панели не менялось)
        chips_health, _a2 = orchlib._front_scope_chips(data, entries)
        self.assertNotIn(FRONT, chips_health["waves_no_critic"])
        # скоуп-независимое вычисление пост-чипа/гейта видит блокер
        self.assertIn("waves_no_critic:%s" % FRONT,
                      orchlib.close_blockers(FRONT, state=self.state,
                                             use_cache=False))
        _measure("MEASURE (2) обе ветки резолва: run_id по start.front; "
                 "fid-класс при status=done в скоупе (active-фильтр не "
                 "опустошает), health-режим неизменен")


# ---------------------------------------------------------------------------
# (3) vim-обход: прямая запись fronts.json мимо save_fronts → пост-чип
#     front_closed_red при первом скане/сохранении; повторный скан НЕ
#     добавляет второй чип (дедуп) — на всех 8 классах allowlist
# ---------------------------------------------------------------------------


class TestVimBypassPostChip(CloseTemp):
    def test_vim_close_all_classes_chip_and_dedup(self):
        for chip_class in ALLOWLIST_CLASSES:
            with self.subTest(chip_class=chip_class):
                self.root, self.state = _mk_state()
                os.environ["ORCHESTRATION_DIR"] = self.state
                orchlib._fronts_base = None  # type: ignore[attr-defined]
                orchlib._CLOSE_BLOCKERS_CACHE.clear()
                kit_dir = None
                self._build_class(chip_class)
                if chip_class == "chip_silenced":
                    kit_dir = self._kit_without_label()
                _vim_close(self.state, FRONT)
                # первый скан — чип появляется (писатель orchlib)
                found = orchlib.front_closed_red_scan(
                    state=self.state, kit_dir=kit_dir)
                self.assertEqual([f for f, _b in found], [FRONT],
                                 "скан обнаружил закрытие с красными")
                chips = _chips_in_journal(self.state,
                                          orchlib.FRONT_CLOSED_RED_CHIP)
                self.assertEqual(len(chips), 1,
                                 "ровно один чип на событие: %s" % chips)
                self.assertEqual(chips[0].get("front"), FRONT)
                # повторный скан — дедуп, второго чипа нет
                orchlib.front_closed_red_scan(state=self.state,
                                              kit_dir=kit_dir)
                self.assertEqual(
                    len(_chips_in_journal(
                        self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1,
                    "повторный скан НЕ добавляет второй чип")
                # health-зеркало видит
                health = orchlib.health_red_chips(state=self.state,
                                                   kit_dir=kit_dir)
                self.assertEqual(health.get("front_closed_red"), [FRONT])
                # сохранение (любое) тоже не дублирует
                orchlib._fronts_base = None  # type: ignore[attr-defined]
                data = orchlib.load_fronts()
                data["notes"] = "resave"
                orchlib.save_fronts(data)
                self.assertEqual(
                    len(_chips_in_journal(
                        self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1,
                    "сохранение не добавляет второй чип")
        _measure("MEASURE (3) vim-обход на ВСЕХ 8 классах → пост-чип "
                 "front_closed_red при первом скане; повторный скан/"
                 "сохранение дубль не пишет (дедуп)")

    def test_vim_close_chip_written_at_save(self):
        # писатель срабатывает и при сохранении (без предварительного скана)
        _seed_run(self.state, "COL1", FRONT, "meta/front-colonel.md",
                  self.now - 20, self.now - 10)
        _vim_close(self.state, FRONT)
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        data = orchlib.load_fronts()
        data["notes"] = "trigger scan"
        orchlib.save_fronts(data)
        self.assertEqual(
            len(_chips_in_journal(
                self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1,
            "пост-чип пишется при сохранении (vim-обход без скана)")


# ---------------------------------------------------------------------------
# (4) grandfathering: событие ts<cutoff у done-фронта → тишина;
#     legacy-waiver → тишина; событие ts>cutoff → красный
# ---------------------------------------------------------------------------


class TestGrandfathering(CloseTemp):
    def test_pre_ship_event_silent(self):
        cutoff = orchlib.CLOSE_GATE_SHIP_TS
        self.assertLess(cutoff, time.time(),
                        "константа корабля — в прошлом (легаси до неё)")
        # все события легаси-done ДО корабля гейта → тишина
        _seed_chip(self.state, "multi_write_front", FRONT, cutoff - 1000)
        _seed_chip(self.state, "supervision_dead", FRONT, cutoff - 500,
                   id="SUPOLD")
        _seed_run(self.state, "WOLD", FRONT, "code/coder.md",
                  cutoff - 400, cutoff - 300)
        _write_order(self.state, FRONT, [_inv_row(1, CMD1)])
        _seed_run(self.state, "SCOLD", FRONT, "meta/web-scout.md",
                  cutoff - 200, cutoff - 100)
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state, use_cache=False),
            [], "легаси-события до cutoff не блокируют")
        # done через save_fronts проходит (гейт тих для легаси)
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done")
        # пост-чип не пишется для чистого легаси
        self.assertEqual(
            orchlib.front_closed_red_scan(state=self.state), [])
        self.assertEqual(
            _chips_in_journal(self.state,
                              orchlib.FRONT_CLOSED_RED_CHIP), [])

    def test_legacy_waiver_respected(self):
        # событие ПОСЛЕ cutoff, но покрытое legacy-waiver (run ts < canon_ts)
        cut = 1000.0
        _seed_run(self.state, "WW1", FRONT, "code/coder.md", 1050.0, 1100.0)
        kit = os.path.join(self.root, "kit")
        _write_json(os.path.join(kit, "tests", "adversarial",
                                 "legacy-waivers.json"), {
            "canon_ts_epoch": 1200.0,
            "waivers": [{"run_id": "WW1", "chip": "probes_missing",
                         "reason": "pre-canon-v1"}],
        })
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, kit_dir=kit, cutoff_ts=cut,
            use_cache=False)
        self.assertNotIn("probes_missing:WW1", blockers,
                         "legacy-waiver вычёркивает блокер")
        # без реестра — тот же рун красный (зуб на месте)
        blockers_no_waiver = orchlib.close_blockers(
            FRONT, state=self.state, cutoff_ts=cut, use_cache=False)
        self.assertIn("probes_missing:WW1", blockers_no_waiver)

    def test_post_cutoff_event_red(self):
        # событие после константы корабля → красный (ts=now > ship ts)
        _seed_chip(self.state, "multi_write_front", FRONT, self.now)
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertIn("multi_write_front:%s" % FRONT, blockers)
        _measure("MEASURE (4) grandfathering: до cutoff — тишина (гейт "
                 "пропускает done, чипа нет); waiver — тишина; после "
                 "cutoff — красный")

    def test_live_done_fronts_grandfathered_readonly(self):
        # (е) 25 живых done-фронтов не краснеют; read-only замер
        if not (os.path.isdir(LIVE_STATE)
                and os.path.isfile(os.path.join(LIVE_STATE, "fronts.json"))):
            self.skipTest("нет живого state")
        data = orchlib._load_fronts_at(LIVE_STATE)
        done = [fr.get("id") for fr in data.get("fronts") or []
                if isinstance(fr, dict) and fr.get("status") == "done"]
        self.assertTrue(done, "в живом state есть done-фронты")
        red = []
        for fid in done:
            blockers = orchlib.close_blockers(
                fid, state=LIVE_STATE, use_cache=False)
            if blockers:
                red.append((fid, blockers))
        self.assertEqual(red, [],
                         "легаси-done фронты тихи после корабля гейта")
        _measure("MEASURE (live, read-only) %d done-фронтов живого state: "
                 "close_blockers пусты у всех (grandfathering)" % len(done))


# ---------------------------------------------------------------------------
# (5) чистый фронт (все 8 классов пусты) → done проходит, exit CLI 0
# ---------------------------------------------------------------------------


class TestCleanCloseAndCli(CloseTemp):
    def test_clean_front_closes(self):
        # все 8 классов пусты: нет волн, чипов, приказа с инвариантами
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done")
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state,
                                   use_cache=False), [])
        # пост-чип не пишется
        self.assertEqual(
            orchlib.front_closed_red_scan(state=self.state), [])
        env = dict(os.environ)
        env["ORCHESTRATION_DIR"] = self.state
        r = subprocess.run(
            [sys.executable, ORCHLIB_CLI, "--check-close", FRONT],
            cwd=REPO, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("close OK", r.stdout)
        _measure("MEASURE (5) чистый фронт: done проходит через гейт, "
                 "пост-чипа нет, CLI --check-close exit 0")

    def test_quiet_front_with_quiet_waves_closes(self):
        # волны БЫЛИ, но все закрыты по канону: критик после волны,
        # прокурор, git-warden после кодера, чипов нет
        _seed_run(self.state, "PRO1", FRONT, "meta/front-prosecutor.md",
                  self.now - 60, self.now - 55)
        _seed_run(self.state, "COD1", FRONT, "code/coder.md",
                  self.now - 50, self.now - 40)
        _seed_run(self.state, "CRT1", FRONT, "code/code-reviewer.md",
                  self.now - 35, self.now - 30)
        _seed_run(self.state, "GW1", FRONT, "code/git-warden.md",
                  self.now - 25, self.now - 20)
        # квитанция §3 для код-волны (writer)
        ok, info = orchlib.write_probe_receipt(
            probe="COD1", cmd="true", exit_code=0, oracle_match=True,
            critic_id="CRT1", artifact="COD1", run_id="COD1",
            state=self.state)
        self.assertTrue(ok, info)
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state,
                                   use_cache=False), [],
            "канонная волна не блокирует close")
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done")


# ---------------------------------------------------------------------------
# (6) чужие чипы (front≠fid) не блокируют close fid
# ---------------------------------------------------------------------------


class TestForeignChipsDontBlock(CloseTemp):
    def test_foreign_red_does_not_block(self):
        now = self.now
        # весь спектр красного на ЧУЖОМ фронте
        _seed_chip(self.state, "supervision_dead", FRONT_B, now, id="SUPB")
        _seed_chip(self.state, "multi_write_front", FRONT_B, now)
        _seed_run(self.state, "WB", FRONT_B, "code/coder.md", now - 10,
                  now - 5)
        _write_order(self.state, FRONT_B, [_inv_row(1, CMD1)])
        _seed_run(self.state, "SCB", FRONT_B, "meta/web-scout.md", now - 3,
                  now - 2)
        _seed_run(self.state, "COLB", FRONT_B, "meta/front-colonel.md",
                  now - 20, now - 10)
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state,
                                   use_cache=False), [],
            "чужие чипы/детекторы не блокируют close fid")
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done")
        # чужой фронт при этом сам красен (зуб на месте)
        self.assertTrue(orchlib.close_blockers(
            FRONT_B, state=self.state, use_cache=False))
        _measure("MEASURE (6) чужие чипы (front≠fid) не блокируют close "
                 "fid; чужой фронт красен у себя")


# ---------------------------------------------------------------------------
# (7) lock-гигиена: блокеры из кэша mtime; close не даёт lock-busy при
#     журнале-фикстуре >5000 строк (тяжёлый скан вне замка)
# ---------------------------------------------------------------------------


class TestLockHygiene(CloseTemp):
    def test_heavy_scan_outside_lock_no_lock_busy(self):
        # красный чип ПОСЛЕ него 5100 filler-строк: событие за окном 5000,
        # но полный журнал видит его; скан — вне замка
        _seed_chip(self.state, "supervision_dead", FRONT, self.now - 50,
                   id="DEEP1")
        filler = []
        for i in range(5100):
            filler.append({"ts": self.now - 49 + i * 0.001, "kind": "note",
                           "id": "filler-%d" % i})
        _seed_journal(self.state, filler)
        with open(os.path.join(self.state, "journal.jsonl"),
                  encoding="utf-8") as f:
            n_lines = sum(1 for _ in f)
        self.assertGreater(n_lines, orchlib.HEALTH_JOURNAL_SCAN_LIMIT)

        lock_held = {"v": False}
        orig_acquire = orchlib._dir_lock_acquire
        orig_release = orchlib._dir_lock_release
        orig_core = orchlib._close_blockers_core
        calls = {"n": 0}

        def _acquire(*a, **kw):
            r = orig_acquire(*a, **kw)
            if r:
                lock_held["v"] = True
            return r

        def _release(*a, **kw):
            r = orig_release(*a, **kw)
            lock_held["v"] = False
            return r

        def _core(fids, state, kit_dir, cutoff_ts):
            self.assertFalse(lock_held["v"],
                             "тяжёлый скан блокеров ПОД замком fronts.lock")
            calls["n"] += 1
            return orig_core(fids, state, kit_dir, cutoff_ts)

        orchlib._dir_lock_acquire = _acquire  # type: ignore
        orchlib._dir_lock_release = _release  # type: ignore
        orchlib._close_blockers_core = _core  # type: ignore
        try:
            t0 = time.time()
            with self.assertRaises(ValueError) as ctx:
                _close_via_save(self.state, FRONT)
            dt = time.time() - t0
            text = "; ".join(str(x) for x in ctx.exception.args[0])
            self.assertIn("supervision_dead:DEEP1", text,
                          "полный журнал: событие за окном 5000 блокирует")
            self.assertFalse(lock_held["v"], "замок отпущен")
            self.assertLess(dt, 30.0, "close не даёт lock-busy/таймаут")
            self.assertGreaterEqual(calls["n"], 1)

            # кэш по mtime: первый вызов греет кэш (preflight гейт-ключа не
            # делит с close_blockers), дальше — без пересчёта ядра
            orchlib.close_blockers(FRONT, state=self.state)
            warmed = calls["n"]
            orchlib.close_blockers(FRONT, state=self.state)
            orchlib.close_blockers(FRONT, state=self.state)
            self.assertEqual(calls["n"], warmed,
                             "кэш mtime: повторные вызовы без пересчёта")
            # запись в journal инвалидирует кэш
            _seed_chip(self.state, "multi_write_front", FRONT, self.now)
            orchlib.close_blockers(FRONT, state=self.state)
            self.assertEqual(calls["n"], warmed + 1,
                             "mtime journal изменился → пересчёт")
        finally:
            orchlib._dir_lock_acquire = orig_acquire  # type: ignore
            orchlib._dir_lock_release = orig_release  # type: ignore
            orchlib._close_blockers_core = orig_core  # type: ignore
        _measure("MEASURE (7) журнал >5000 строк: полный журнал блокирует; "
                 "тяжёлый скан ТОЛЬКО вне замка (assert в обёртке), "
                 "lock-busy нет; кэш mtime работает, запись инвалидирует")

    def test_state_change_under_lock_forces_recompute(self):
        # между preflight и захватом замка journal менялся → пересчёт вне
        # замка, гейт всё равно отказывает (сверка mtime под замком)
        _seed_chip(self.state, "supervision_dead", FRONT, self.now,
                   id="RACE1")
        orig_preflight = orchlib._close_gate_preflight
        calls = {"n": 0}

        def _preflight(f, pf, state=None, kit_dir=None, cutoff_ts=None):
            r = orig_preflight(f, pf, state=state, kit_dir=kit_dir,
                               cutoff_ts=cutoff_ts)
            if calls["n"] == 0:
                # имитируем concurrent-запись в journal ПОСЛЕ preflight
                _seed_chip(self.state, "multi_write_front", FRONT,
                           time.time())
            calls["n"] += 1
            return r

        orchlib._close_gate_preflight = _preflight  # type: ignore
        try:
            with self.assertRaises(ValueError) as ctx:
                _close_via_save(self.state, FRONT)
            text = "; ".join(str(x) for x in ctx.exception.args[0])
            self.assertIn("multi_write_front:%s" % FRONT, text,
                          "запись после preflight учтена пересчётом")
            self.assertGreaterEqual(calls["n"], 2,
                                    "mtime изменился → повторный preflight")
        finally:
            orchlib._close_gate_preflight = orig_preflight  # type: ignore
        self.assertEqual(_disk_status(self.state, FRONT), "active")


# ---------------------------------------------------------------------------
# (8) panel: POST /api/fronts/status done при красных → 400 с перечнем
#     (обе POST-ветки; легаси-новые-статусы по-прежнему через
#     _persist_fronts_data)
# ---------------------------------------------------------------------------


class TestPanelClose400Http(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="k3-panel-", dir="/tmp")
        self.state = os.path.join(self.tmp, "state")
        os.makedirs(self.state, exist_ok=True)
        _assert_not_live(self.state)
        _write_json(os.path.join(self.state, "fronts.json"), {
            "goal": "k3 panel",
            "fronts": [
                _front(FRONT, "active", ["bin/orchlib.py"]),
                _front(FRONT_B, "active", ["tests/**"]),
            ],
            "notes": "",
        })
        _write_json(os.path.join(self.state, "params.json"),
                    orchlib.DEFAULTS)
        now = time.time()
        _seed_run(self.state, "PCOL", FRONT, "meta/front-colonel.md",
                  now - 20, now - 10)
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        os.environ["ORCHESTRATION_DIR"] = self.state
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        self._httpd = None
        self._thread = None
        self._port = None

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
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        orchlib._CLOSE_BLOCKERS_CACHE.clear()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _start(self):
        self._httpd = HTTPServer(("127.0.0.1", 0), panel_server.Handler)
        self._port = self._httpd.server_address[1]
        self._thread = threading.Thread(target=self._httpd.serve_forever,
                                        daemon=True)
        self._thread.start()
        time.sleep(0.05)

    def _post(self, path, body):
        url = "http://127.0.0.1:%s%s" % (self._port, path)
        req = Request(url, data=json.dumps(body).encode("utf-8"),
                      headers={"Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=10) as resp:
                return resp.getcode(), json.loads(resp.read().decode("utf-8"))
        except HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                obj = json.loads(raw)
            except Exception:
                obj = {"error": raw}
            return e.code, obj

    def _get(self, path):
        with urlopen("http://127.0.0.1:%s%s" % (self._port, path),
                     timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))

    def test_status_done_red_http_400_no_bypass(self):
        self._start()
        code, obj = self._post("/api/fronts/status",
                               {"id": FRONT, "status": "done"})
        self.assertEqual(code, 400, obj)
        self.assertIn("close-гейт", obj.get("error", ""))
        self.assertIn("waves_no_critic:%s" % FRONT, obj.get("error", ""))
        self.assertEqual(_disk_status(self.state, FRONT), "active",
                         "bypass закрыт: прямая запись не выполняется")
        _measure("MEASURE (8a) POST /api/fronts/status done при красных → "
                 "400 с перечнем; _persist-байпас закрыт")

    def test_fronts_post_done_red_http_400(self):
        self._start()
        data = orchlib.load_fronts()
        for fr in data["fronts"]:
            if fr["id"] == FRONT:
                fr["status"] = "done"
        code, obj = self._post("/api/fronts", data)
        self.assertEqual(code, 400, obj)
        self.assertIn("close-гейт", obj.get("error", ""))
        self.assertEqual(_disk_status(self.state, FRONT), "active")
        _measure("MEASURE (8b) POST /api/fronts (полный fronts.json) с "
                 "done при красных → 400 (ветка не сломана)")

    def test_legacy_new_status_bypass_preserved(self):
        # легаси-кейс «новые статусы, orchlib отстаёт»: статус неизвестен
        # orchlib (не done) → _persist_fronts_data по-прежнему работает
        self._start()
        prev = orchlib.FRONT_STATUSES
        orchlib.FRONT_STATUSES = tuple(
            s for s in prev if s != "stalled")  # type: ignore
        try:
            code, obj = self._post("/api/fronts/status",
                                   {"id": FRONT_B, "status": "stalled"})
        finally:
            orchlib.FRONT_STATUSES = prev  # type: ignore
        self.assertEqual(code, 200, obj)
        self.assertTrue(obj.get("ok"))
        self.assertEqual(_disk_status(self.state, FRONT_B), "stalled",
                         "легаси-новые-статусы идут через bypass")
        _measure("MEASURE (8c) легаси-новые-статусы (orchlib отстаёт, "
                 "не done) → _persist_fronts_data сохранён")

    def test_api_health_has_front_closed_red(self):
        # vim-закрытие с красными + health-скан → чип + ключ в /api/health
        _vim_close(self.state, FRONT)
        self._start()
        code, obj = self._get("/api/health")
        self.assertEqual(code, 200)
        self.assertIn("front_closed_red", obj["counts"])
        self.assertIn("front_closed_red", obj["ids"])
        self.assertEqual(obj["ids"]["front_closed_red"], [FRONT])
        self.assertEqual(
            len(_chips_in_journal(
                self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1,
            "health-скан написал чип ровно один раз")
        _measure("MEASURE (11b) /api/health: ключ front_closed_red в "
                 "counts/ids; health-скан — писатель чипа")


# ---------------------------------------------------------------------------
# (9) CLI --check-close: красный → ненулевой exit + перечень; чистый → 0;
#     owns.py не импортирует orchlib (assert по исходнику)
# ---------------------------------------------------------------------------


class TestCliCheckClose(CloseTemp):
    def _cli(self, *args):
        env = dict(os.environ)
        env["ORCHESTRATION_DIR"] = self.state
        return subprocess.run(
            [sys.executable, ORCHLIB_CLI] + list(args),
            cwd=REPO, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            timeout=120)

    def test_red_nonzero_exit_with_listing(self):
        _seed_run(self.state, "W9", FRONT, "code/coder.md",
                  self.now - 10, self.now - 5)
        r = self._cli("--check-close", FRONT)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(r.returncode, 1)
        self.assertIn("close-гейт", r.stderr)
        self.assertIn("probes_missing:W9", r.stderr)
        _measure("MEASURE (9a) CLI --check-close красный → exit 1 + "
                 "перечень в stderr")

    def test_clean_zero_exit(self):
        r = self._cli("--check-close", FRONT_B)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("close OK", r.stdout)

    def test_usage_error(self):
        r = self._cli()
        self.assertEqual(r.returncode, 2)
        self.assertIn("использование", r.stderr)

    def test_owns_does_not_import_orchlib(self):
        with open(OWNS_PATH, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertNotIn("orchlib", src,
                         "owns.py — zero-dep слой, не импортирует orchlib")
        self.assertNotIn("import orchlib", src)
        _measure("MEASURE (9b) owns.py не импортирует orchlib (assert по "
                 "исходнику); CLI чистый → 0; usage → 2")


# ---------------------------------------------------------------------------
# (10) скоуп-рефактор не изменил поведение активных детекторов (снимок
#      health на полигоне до/после — ожидания = поведению до K3)
# ---------------------------------------------------------------------------


class TestActiveDetectorsUnchanged(CloseTemp):
    def test_health_snapshot_matches_pre_k3_semantics(self):
        # F-A: active, волна colonel без прокурора → fnp + wnc красны
        _seed_run(self.state, "A1", "F-A", "meta/front-colonel.md",
                  self.now - 30, self.now - 20)
        # F-D: DONE, кодер без git-warden после → cwng красен и для done
        #     (поведение до K3: у code_waves_no_gitwarden фильтра статуса
        #     не было); fnp/wnc — только active → тихи
        _seed_run(self.state, "D1", "F-D", "code/coder.md",
                  self.now - 30, self.now - 20)
        # F-C: active, канон: прокурор + кодер + критик + git-warden
        _seed_run(self.state, "C1", "F-C", "meta/front-prosecutor.md",
                  self.now - 60, self.now - 55)
        _seed_run(self.state, "C2", "F-C", "code/coder.md",
                  self.now - 50, self.now - 40)
        _seed_run(self.state, "C3", "F-C", "code/code-reviewer.md",
                  self.now - 35, self.now - 30)
        _seed_run(self.state, "C4", "F-C", "code/git-warden.md",
                  self.now - 25, self.now - 20)
        ok, info = orchlib.write_probe_receipt(
            probe="C2", cmd="true", exit_code=0, oracle_match=True,
            critic_id="C3", artifact="C2", run_id="C2", state=self.state)
        self.assertTrue(ok, info)
        _write_json(os.path.join(self.state, "fronts.json"), {
            "goal": "k3 scope snapshot",
            "fronts": [
                _front("F-A", "active", ["a/**"]),
                _front("F-D", "done", ["d/**"]),
                _front("F-C", "active", ["c/**"]),
            ],
            "notes": "",
        })
        orchlib._fronts_base = None  # type: ignore[attr-defined]
        health = orchlib.health_red_chips(state=self.state)
        self.assertEqual(health.get("fronts_no_prosecutor"), ["F-A"])
        self.assertEqual(health.get("waves_no_critic"), ["F-A"])
        self.assertEqual(health.get("code_waves_no_gitwarden"), ["F-D"],
                         "code_waves_no_gitwarden и для done — как до K3")
        # probes_missing: active-скоуп по умолчанию (done-фронт тих)
        _seed_run(self.state, "DX", "F-D", "code/coder.md",
                  self.now - 10, self.now - 5)
        health2 = orchlib.health_red_chips(state=self.state)
        self.assertEqual(health2.get("probes_missing"), [],
                         "done-фронт вне active-скоупа probes_missing")
        self.assertIn("DX", orchlib.probes_missing(
            state=self.state, front_ids=["F-D"]),
            "синтетический скоуп видит (K2-паттерн)")
        _measure("MEASURE (10) снимок health полигона: fnp/wnc — только "
                 "active, cwng — и done (поведение до K3), probes_missing "
                 "active-скоуп — скоуп-рефактор поведение НЕ изменил")

    def test_health_front_closed_red_mirror_lifecycle(self):
        # закрыт красным → чип; ре-открыт → чип живёт (не погашен);
        # пере-закрыт чистым (ЧЕРЕЗ ГЕЙТ — маркер front_closed_clean) →
        # погашен; НОВОЕ красное закрытие → НОВЫЙ чип («на событие»)
        _seed_run(self.state, "R1", FRONT, "meta/front-colonel.md",
                  self.now - 30, self.now - 20)
        _vim_close(self.state, FRONT)
        orchlib.front_closed_red_scan(state=self.state)
        self.assertEqual(
            orchlib.front_closed_red_ids(state=self.state), [FRONT])
        # ре-открытие: событие живёт до исправления
        data = orchlib.load_fronts()
        for fr in data["fronts"]:
            if fr["id"] == FRONT:
                fr["status"] = "active"
        orchlib.save_fronts(data)
        self.assertEqual(
            orchlib.front_closed_red_ids(state=self.state), [FRONT],
            "ре-открытие чип не гасит")
        # исправление: чистое пере-закрытие ЧЕРЕЗ гейт (прокурор + критик +
        # git-warden новее волны) — маркер front_closed_clean в journal
        _seed_run(self.state, "R2", FRONT, "meta/front-prosecutor.md",
                  self.now - 19, self.now - 18)
        _seed_run(self.state, "R3", FRONT, "code/code-reviewer.md",
                  self.now - 17, self.now - 12)
        _seed_run(self.state, "R4", FRONT, "code/git-warden.md",
                  self.now - 11, self.now - 10)
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done",
                         "чистое пере-закрытие проходит гейт")
        notes = [e for e in orchlib._journal_entries_at(self.state)
                 if e.get("kind") == "note"
                 and e.get("note") == orchlib.FRONT_CLOSED_CLEAN_NOTE]
        self.assertEqual([n.get("front") for n in notes], [FRONT],
                         "маркер чистого закрытия written гейтом")
        orchlib.front_closed_red_scan(state=self.state)
        self.assertEqual(
            orchlib.front_closed_red_ids(state=self.state), [],
            "пере-закрытие чистым гасит зеркало (чип в истории остаётся)")
        self.assertEqual(
            len(_chips_in_journal(
                self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1,
            "дубль чипа при чистом пере-закрытии не пишется")
        # НОВОЕ красное закрытие после погашения → НОВЫЙ чип: дедуп
        # «один чип на СОБЫТИЕ», не «на фронт» (замер ревью: было «1»)
        _seed_run(self.state, "R5", FRONT, "meta/front-colonel.md",
                  self.now - 2, self.now - 1)
        _vim_close(self.state, FRONT)
        orchlib.front_closed_red_scan(state=self.state)
        self.assertEqual(
            len(_chips_in_journal(
                self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 2,
            "второе красное закрытие пишет НОВЫЙ чип (событие новое)")
        self.assertEqual(
            orchlib.front_closed_red_ids(state=self.state), [FRONT],
            "зеркало краснеет по новому событию")
        _measure("MEASURE (10b) зеркало front_closed_red: закрыт красным → "
                 "красный; ре-открыт → живёт; пере-закрыт чистым (маркер "
                 "front_closed_clean через гейт) → погашен; НОВОЕ красное "
                 "→ НОВЫЙ чип (дедуп «на событие», FU-нит 4)")


# ---------------------------------------------------------------------------
# (11) label front_closed_red в HEALTH_CHIP_LABELS; ключ в /api/health
# ---------------------------------------------------------------------------


class TestPanelLabelAndApiHealth(CloseTemp):
    def test_label_and_api_health_key(self):
        with open(PANEL_INDEX, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("HEALTH_CHIP_LABELS", html)
        self.assertIn("front_closed_red:", html,
                      "label front_closed_red в HEALTH_CHIP_LABELS")
        # пасс-тру ключ в /api/health (модуль панели; чистый полигон)
        server = _load_mod("panel_server_k3",
                           os.path.join(REPO, "panel", "server.py"))
        payload = server._health_payload()
        self.assertIn("front_closed_red", payload["counts"])
        self.assertIn("front_closed_red", payload["ids"])
        self.assertEqual(payload["counts"]["front_closed_red"], 0)
        _measure("MEASURE (11) label front_closed_red в HEALTH_CHIP_LABELS "
                 "+ ключ в /api/health (пасс-тру из health_red_chips)")


# ---------------------------------------------------------------------------
# FOLLOW-UP (ремонтный круг по ревью ×3): полный журнал для run_id-классов,
# устойчивый читатель журнала, fail-closed скана, kit в кэш-ключе,
# мусорный ts — края зафиксированы фикстурами
# ---------------------------------------------------------------------------


class TestFullJournalRunIdClasses(CloseTemp):
    """(FU-блокер 1) run_id-классы в close-пути — ПОЛНЫЙ журнал.

    Каноничная волна без квитанции §3, вытесненная из окна 5000 filler-строк:
    окно прозрачно, полный журнал видит; гейт отказывает, пост-чип пишет.
    """

    def test_buried_canonical_wave_blocks_close_and_chips(self):
        now = self.now
        _seed_run(self.state, "FUP1", FRONT, "meta/front-prosecutor.md",
                  now - 40, now - 35)
        _seed_run(self.state, "FUC1", FRONT, "code/coder.md",
                  now - 30, now - 20)
        _seed_run(self.state, "FUC2", FRONT, "code/code-reviewer.md",
                  now - 15, now - 10)
        _seed_run(self.state, "FUG1", FRONT, "code/git-warden.md",
                  now - 9, now - 5)
        # квитанции §3 НЕТ; волна вытесняется из окна 5000 строками шума
        filler = []
        for i in range(5100):
            filler.append({"ts": now - 4 + i * 0.001, "kind": "note",
                           "id": "fu-filler-%d" % i})
        _seed_journal(self.state, filler)
        # окно прозрачно (демонстрация дыры), полный журнал видит
        self.assertEqual(
            orchlib.probes_missing(state=self.state, front_ids=[FRONT]),
            [], "окно 5000 не видит вытесненную волну (до фикса)")
        self.assertEqual(
            orchlib.probes_missing(state=self.state, front_ids=[FRONT],
                                   scan_limit=False),
            ["FUC1"], "полный журнал видит волну без квитанции")
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertEqual(blockers, ["probes_missing:FUC1"],
                         "каноничное созвездие: единственный блокер — "
                         "probes_missing, и он за окном 5000")
        with self.assertRaises(ValueError) as ctx:
            _close_via_save(self.state, FRONT)
        text = "; ".join(str(x) for x in ctx.exception.args[0])
        self.assertIn("probes_missing:FUC1", text,
                      "гейт отказывает по вытесненной за окно волне")
        self.assertEqual(_disk_status(self.state, FRONT), "active")
        # пост-чип пишет (скан использует то же ядро — полный журнал)
        _vim_close(self.state, FRONT)
        found = orchlib.front_closed_red_scan(state=self.state)
        self.assertEqual(found, [(FRONT, ["probes_missing:FUC1"])])
        self.assertEqual(
            len(_chips_in_journal(
                self.state, orchlib.FRONT_CLOSED_RED_CHIP)), 1)
        _measure("MEASURE (FU-1) каноничная волна без квитанции + 5100 "
                 "filler: close_blockers НЕ пуст, save_fronts отказывает, "
                 "пост-чип пишет (полный журнал, не окно 5000)")


class TestResilientJournalReader(CloseTemp):
    """(FU-блокер 2) оборванный мультибайтный хвост не слепит гейт.

    bytes + decode("utf-8","replace") в _journal_entries_at (K1-паттерн
    сторожа): до фикта текстовый читатель падал UnicodeDecodeError →
    [] → все 8 классов слепы → беспрепятственное done.
    """

    def test_broken_multibyte_tail_does_not_blind_gate(self):
        _seed_chip(self.state, "supervision_dead", FRONT, self.now,
                   id="SUP9")
        jpath = os.path.join(self.state, "journal.jsonl")
        with open(jpath, "ab") as f:
            f.write('{"ts": 1, "kind": "end", "id": "crash-tail", "f'.encode(
                "utf-8") + b'\xff\xfe\x80')
        entries = orchlib._journal_entries_at(self.state)
        self.assertTrue(any(e.get("id") == "SUP9" for e in entries),
                        "живой чип читается сквозь битый хвост")
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertIn("supervision_dead:SUP9", blockers,
                      "гейт видит красный чип при битом хвосте журнала")
        with self.assertRaises(ValueError) as ctx:
            _close_via_save(self.state, FRONT)
        text = "; ".join(str(x) for x in ctx.exception.args[0])
        self.assertIn("supervision_dead:SUP9", text)
        self.assertEqual(_disk_status(self.state, FRONT), "active")
        _measure("MEASURE (FU-2) журнал с оборванным мультибайтным хвостом "
                 "+ живой supervision_dead-чип → close_blockers НЕ пуст, "
                 "save_fronts отказывает (decode replace)")


class TestFailClosedScanError(CloseTemp):
    """(FU-желательно 3) ошибка скана = блокер-неизвестен → отказ закрытия.

    Пост-чип и health-зеркало при ошибке событие НЕ утверждают (чип не
    пишется); отказ даёт сам гейт (close_scan_error) и CLI.
    """

    def test_scan_error_refuses_close_and_cli(self):
        _seed_run(self.state, "COL9", FRONT, "meta/front-colonel.md",
                  self.now - 20, self.now - 10)
        orig = orchlib._load_fronts_at

        def _boom(state):
            raise RuntimeError("boom-poly")

        orchlib._load_fronts_at = _boom  # type: ignore
        try:
            blockers = orchlib.close_blockers(
                FRONT, state=self.state, use_cache=False)
            self.assertEqual(len(blockers), 1, blockers)
            self.assertTrue(blockers[0].startswith("close_scan_error:"),
                            blockers)
            self.assertIn("boom-poly", blockers[0])
            with self.assertRaises(ValueError) as ctx:
                _close_via_save(self.state, FRONT)
            text = "; ".join(str(x) for x in ctx.exception.args[0])
            self.assertIn("close_scan_error", text,
                          "гейт fail-closed: ошибка скана = отказ")
            self.assertEqual(_disk_status(self.state, FRONT), "active")
        finally:
            orchlib._load_fronts_at = orig  # type: ignore
        # CLI fail-closed — на дисковом источнике ошибки (нечитаемый
        # журнал), сабпроцесс не видит in-process патча: см. тест ниже
        _measure("MEASURE (FU-3a) ошибка скана (in-process) → "
                 "close_scan_error: гейт отказывает, диск не тронут")

    def test_scan_error_no_postchip_no_mirror_claim(self):
        _seed_run(self.state, "COL9", FRONT, "meta/front-colonel.md",
                  self.now - 20, self.now - 10)
        _vim_close(self.state, FRONT)
        orig = orchlib._front_scope_chips

        def _boom(data, entries, front_ids=None):
            raise RuntimeError("boom-scope")

        orchlib._front_scope_chips = _boom  # type: ignore
        try:
            self.assertEqual(
                orchlib.front_closed_red_scan(state=self.state), [],
                "ошибка скана — чип не пишется (событие не подтверждено)")
            self.assertEqual(
                _chips_in_journal(
                    self.state, orchlib.FRONT_CLOSED_RED_CHIP), [])
            self.assertEqual(
                orchlib.front_closed_red_ids(state=self.state), [],
                "зеркало не утверждает событие при ошибке скана")
        finally:
            orchlib._front_scope_chips = orig  # type: ignore
        _measure("MEASURE (FU-3) ошибка скана → close_scan_error: гейт и "
                 "CLI отказывают; пост-чип/зеркало событие не утверждают")

    def test_wholly_unparseable_journal_fail_closed(self):
        jpath = os.path.join(self.state, "journal.jsonl")
        with open(jpath, "wb") as f:
            f.write(b"\xff\xfe not json at all\n\x80\x81 garbage")
        blockers = orchlib.close_blockers(
            FRONT, state=self.state, use_cache=False)
        self.assertEqual(len(blockers), 1, blockers)
        self.assertTrue(
            blockers[0].startswith("close_scan_error:journal_unparseable"),
            blockers)
        with self.assertRaises(ValueError) as ctx:
            _close_via_save(self.state, FRONT)
        text = "; ".join(str(x) for x in ctx.exception.args[0])
        self.assertIn("journal_unparseable", text,
                      "целиком нечитаемый журнал — отказ, не тишина")
        self.assertEqual(_disk_status(self.state, FRONT), "active")
        # CLI fail-closed: источник ошибки на диске — сабпроцесс видит её
        env = dict(os.environ)
        env["ORCHESTRATION_DIR"] = self.state
        r = subprocess.run(
            [sys.executable, ORCHLIB_CLI, "--check-close", FRONT],
            cwd=REPO, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
            timeout=120)
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("close_scan_error", r.stderr,
                      "CLI fail-closed: ненулевой exit с причиной")
        _measure("MEASURE (FU-3b) целиком нечитаемый журнал → "
                 "close_scan_error:journal_unparseable → отказ закрытия "
                 "(гейт + CLI exit 1 с причиной)")


class TestCacheKeyIncludesKit(CloseTemp):
    """(FU-нит 5) kit_dir в кэш-ключе блокеров (прецедент _health_mtime_key)."""

    def test_kit_content_change_invalidates_cache(self):
        _seed_run(self.state, "KITW", FRONT, "code/coder.md",
                  self.now - 10, self.now - 5)
        kit = os.path.join(self.root, "kit")
        os.makedirs(os.path.join(kit, "panel"), exist_ok=True)
        idx = os.path.join(kit, "panel", "index.html")
        with open(idx, "w", encoding="utf-8") as f:
            f.write("<html>no labels</html>\n")
        b1 = orchlib.close_blockers(FRONT, state=self.state, kit_dir=kit)
        self.assertIn("chip_silenced:KITW", b1,
                      "панель кита без label — probes_missing заглушен")
        # тот же kit_dir, содержимое panel изменилось (label появился):
        # journal/fronts не менялись — кэш обязан инвалидироваться по kit
        with open(idx, "w", encoding="utf-8") as f:
            f.write('<html>const L = {probes_missing: "нет пробы"};</html>\n')
        b2 = orchlib.close_blockers(FRONT, state=self.state, kit_dir=kit)
        self.assertNotIn("chip_silenced:KITW", b2,
                         "смена кита видна без записи в journal/fronts")
        self.assertIn("probes_missing:KITW", b2)
        _measure("MEASURE (FU-5) kit в кэш-ключе: смена panel/index.html "
                 "кита (label chip_silenced) инвалидирует кэш блокеров")


class TestGarbageTsSkipped(CloseTemp):
    """(FU-нит 6) записи с нечисловым ts не якорят и не блокируют.

    Край fail-closed — целиком нечитаемый журнал (см. TestFailClosedScanError);
    частичный мусорный ts — пропуск записи (не «блокирует вечно»).
    """

    def test_non_numeric_ts_records_do_not_anchor_or_block(self):
        _seed_chip(self.state, "multi_write_front", FRONT, "not-a-number")
        _seed_journal(self.state, [
            {"ts": "garbage", "kind": "start", "id": "GT1",
             "engine": "local", "front": FRONT,
             "role": "meta/front-colonel.md"},
            {"ts": "garbage", "kind": "end", "id": "GT1", "exit": 0},
        ])
        self.assertEqual(
            orchlib.close_blockers(FRONT, state=self.state,
                                   use_cache=False), [],
            "мусорный ts — запись пропускается (не якорит, не блокирует)")
        _close_via_save(self.state, FRONT)
        self.assertEqual(_disk_status(self.state, FRONT), "done")
        self.assertEqual(
            orchlib.front_closed_red_scan(state=self.state), [],
            "пост-чип по мусорным ts не пишется")
        _measure("MEASURE (FU-6) мусорный ts в chip/wave-записях → пропуск "
                 "(блокеров нет, close проходит); край fail-closed — "
                 "нечитаемый журнал целиком → close_scan_error")


if __name__ == "__main__":
    unittest.main()
