#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-C5 K2 (б): мёртвые инварианты — чип invariants_not_run, Инвариант 2.

Секция «## Инварианты (машиночитаемые…» приказа фронта становится
состоянием: каждый инвариант погашен валидной tool-only квитанцией §3
(cmd_sha256 + front-резолв по полному журналу) — иначе чип
invariants_not_run с перечнем непогашенных. Фикстуры 1–11 приказа
полковника C5-K2.

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_invariants_gate.py -q
State: только ORCHESTRATION_DIR=/tmp/inv-…; живой /root/.orchestration не пишем.
"""
from __future__ import print_function

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
PANEL_INDEX = os.path.join(REPO, "panel", "index.html")
LIVE_STATE = "/root/.orchestration"
FRONT_A = "F-K2A"
FRONT_B = "F-K2B"
CMD1 = "python3 -m pytest tests/test_a.py -q"
CMD2 = "python3 -m pytest tests/test_b.py -q"
SECTION_HEADER = (
    "## Инварианты (машиночитаемые — строгий формат "
    "«Инвариант N: <cmd> → <оракул>»; приёмка требует прогона КАЖДОГО)")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


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
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


def _seed_journal(state, entries):
    path = os.path.join(state, "journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _mk_state(prefix="inv"):
    root = tempfile.mkdtemp(prefix=prefix + "-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    _assert_not_live(state)
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "k2 invariants gate",
        "fronts": [
            {"id": FRONT_A, "title": "k2a", "status": "active",
             "owns": ["bin/orchlib.py", "panel/index.html",
                      "panel/server.py", "tests/test_invariants_gate.py"]},
            {"id": FRONT_B, "title": "k2b", "status": "active",
             "owns": ["tests/**"]},
        ],
    })
    _write_json(os.path.join(state, "params.json"), {
        "orchestration": {"hierarchy": "on", "enabled": True},
        "execution": {"retry_on_fail": 1, "timeout_s": 30},
        "budgets": {"warn_runs_per_front": 60, "hard_runs_per_front": 0},
    })
    open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8").close()
    return root, state


def _write_order(state, fid, rows=None, extra_before="", extra_in_section="",
                 header=SECTION_HEADER):
    """Синтетический приказ фронта; rows — строки «- Инвариант N: … → …».

    header=None — приказ БЕЗ машинной секции (проза, фикстуры тишины).
    """
    lines = ["# Приказ фронту %s — k2 фикстуры" % fid, "", "## Цель",
             "синтетика полигона /tmp.", ""]
    if extra_before:
        lines.extend(extra_before.splitlines())
        lines.append("")
    if header is not None:
        lines.append(header)
        lines.append("")
        if rows:
            lines.extend(rows)
            lines.append("")
        if extra_in_section:
            lines.extend(extra_in_section.splitlines())
            lines.append("")
    lines.append("## Границы")
    lines.append("- полигоны /tmp.")
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


def _seed_probe_run(state, rid, front, cmd, ts=None):
    """Валидная tool-only квитанция §3 рана rid фронта front (writer)."""
    _seed_journal(state, [{
        "ts": ts if ts is not None else time.time(),
        "kind": "start", "id": rid, "engine": "local",
        "front": front, "role": "code/coder.md",
    }])
    ok, info = orchlib.write_probe_receipt(
        probe=rid, cmd=cmd, exit_code=0, oracle_match=True,
        critic_id=rid, artifact=rid, run_id=rid, state=state)
    assert ok, info
    return info


def _seed_handmade_receipt(state, rid, front, cmd, generator=None):
    """Рукописная квитанция §3 (все поля валидны, sha подсчитан вручную)."""
    _seed_journal(state, [{
        "ts": time.time(), "kind": "start", "id": rid,
        "engine": "local", "front": front, "role": "code/coder.md",
    }])
    d = os.path.join(state, "runs", rid)
    os.makedirs(d, exist_ok=True)
    _assert_not_live(d)
    sha = hashlib.sha256(cmd.encode("utf-8")).hexdigest()
    block = (
        "probe: %s\ncmd: %s\nexit: 0\noracle_match: true\nts: %s\n"
        "critic_id: %s\nartifact: %s\ncmd_sha256: %s\n"
        % (rid, cmd, time.time(), rid, rid, sha))
    if generator is not None:
        block += "generator: %s\n" % generator
    with open(os.path.join(d, "probe-receipt.md"), "w",
              encoding="utf-8") as f:
        f.write(block)
    return os.path.join(d, "probe-receipt.md")


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


def _live_journal_growth_violation(grown):
    """Строка чипа invariants_not_run в приросте живого journal или None.

    Прирост чужими записями живой сессии (start/end/end-пары пробы и её
    харнесса, ноты) — штатно: K2 — computed-детектор, journal-писателя у
    волны нет.
    """
    for line in grown.splitlines():
        if b"invariants_not_run" in line and b'"chip"' in line:
            return line
    return None


def tearDownModule():
    # K2 — computed-детектор без journal-писателя: живой state не пишем
    # вовсе; journal живой сессии может прирастать чужими записями (раны
    # квитанционного харнесса/пробы), но БЕЗ чипов invariants_not_run
    # (writer-квита у волны отсутствует); runs-артефакты харнесса —
    # housekeeping (см. _live_housekeeping), зубы — на месте.
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
                    "live journal получил чип invariants_not_run: %r"
                    % bad[:200])
            continue
        if _live_housekeeping(rel):
            continue  # маркеры живости/харнесса живой сессии — не волна
        assert now_stat == before, "live state touched: %s" % rel


class InvTemp(unittest.TestCase):
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


# ---------------------------------------------------------------------------
# (1) 2 инварианта, 1 погашен валидной квитанцией → чип перечисляет
#     ТОЛЬКО непрогнанный
# ---------------------------------------------------------------------------


class TestOneCoveredListsOnlyUnrun(InvTemp):
    def test_one_of_two_covered(self):
        _write_order(self.state, FRONT_A,
                     [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        _seed_probe_run(self.state, "INV1-A", FRONT_A, CMD1)
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:2" % FRONT_A],
            "чип перечисляет ТОЛЬКО непрогнанный инвариант")
        chips = orchlib.health_red_chips(state=self.state)
        self.assertEqual(chips.get("invariants_not_run"), ["%s:2" % FRONT_A])
        _measure("MEASURE (1) 2 инварианта / 1 валидная квитанция → чип "
                 "перечисляет только непрогнанный (fid:N)")


# ---------------------------------------------------------------------------
# (2) оба погашены → чипа нет
# ---------------------------------------------------------------------------


class TestBothCoveredNoChip(InvTemp):
    def test_both_covered(self):
        _write_order(self.state, FRONT_A,
                     [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        _seed_probe_run(self.state, "INV2-A", FRONT_A, CMD1)
        _seed_probe_run(self.state, "INV2-B", FRONT_A, CMD2)
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])
        chips = orchlib.health_red_chips(state=self.state)
        self.assertEqual(chips.get("invariants_not_run"), [])
        _measure("MEASURE (2) оба инварианта погашены → чипа нет")


# ---------------------------------------------------------------------------
# (3) рукописная квитанция (без generator / generator не tool-only) →
#     чип остаётся
# ---------------------------------------------------------------------------


class TestHandmadeReceiptDoesNotClear(InvTemp):
    def test_handmade_without_generator(self):
        _write_order(self.state, FRONT_A,
                     [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        _seed_handmade_receipt(self.state, "INV3-A", FRONT_A, CMD1)
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:1" % FRONT_A, "%s:2" % FRONT_A],
            "рукописная квитанция без generator не гасит")

    def test_handmade_generator_not_tool_only(self):
        _write_order(self.state, FRONT_A,
                     [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        _seed_handmade_receipt(self.state, "INV3-B", FRONT_A, CMD1,
                               generator="by-hand-2026")
        _seed_handmade_receipt(self.state, "INV3-C", FRONT_A, CMD2,
                               generator="orch-probe-receipt (вручную)")
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:1" % FRONT_A, "%s:2" % FRONT_A],
            "generator вручную не tool-only — не гасит")
        _measure("MEASURE (3) рукописная квитанция (без generator / с "
                 "generator не tool-only) → чип остаётся")


# ---------------------------------------------------------------------------
# (4) квитанция чужого фронта при совпадающем cmd (тот же sha) → чип
#     остаётся (front-резолв)
# ---------------------------------------------------------------------------


class TestForeignFrontSameCmdDoesNotClear(InvTemp):
    def test_same_cmd_other_front(self):
        _write_order(self.state, FRONT_A, [_inv_row(1, CMD1)])
        _write_order(self.state, FRONT_B, [_inv_row(1, CMD1)])
        _seed_probe_run(self.state, "INV4-B", FRONT_B, CMD1)
        ids = orchlib.invariants_not_run(state=self.state)
        self.assertEqual(ids, ["%s:1" % FRONT_A],
                         "чужой front_id при том же sha не гасит")
        self.assertNotIn(FRONT_B, " ".join(ids))
        _measure("MEASURE (4) квитанция чужого фронта с тем же cmd → чип "
                 "остаётся (front-резолв по journal-рану)")


# ---------------------------------------------------------------------------
# (5) правка cmd в приказе после прогона → снова красный; sha байт-в-байт
# ---------------------------------------------------------------------------


class TestCmdEditRedAgain(InvTemp):
    def test_cmd_edit_breaks_sha(self):
        order = _write_order(self.state, FRONT_A,
                             [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        _seed_probe_run(self.state, "INV5-A", FRONT_A, CMD1)
        _seed_probe_run(self.state, "INV5-B", FRONT_A, CMD2)
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])
        # правка cmd инварианта 2 — контракт изменился, sha расходится
        with open(order, "r", encoding="utf-8") as f:
            text = f.read()
        with open(order, "w", encoding="utf-8") as f:
            f.write(text.replace(CMD2, CMD2 + " -k new"))
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:2" % FRONT_A],
            "правка cmd в приказе → снова красный (фича: контракт изменился)")

    def test_sha_is_byte_exact_no_space_normalization(self):
        # cmd квитанции отличается ТОЛЬКО двойным пробелом → sha не совпал
        _write_order(self.state, FRONT_A, [_inv_row(1, CMD1)])
        _seed_probe_run(self.state, "INV5-C", FRONT_A,
                        CMD1.replace(" -q", "  -q"))
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:1" % FRONT_A],
            "sha байт-в-байт: лишний пробел в cmd — не тот же инвариант")
        _measure("MEASURE (5) правка cmd / отличие пробелом → sha "
                 "расходится → снова красный")


# ---------------------------------------------------------------------------
# (6) мусор формата / пропуск номера / дубль номера → parse-причина в чипе
# ---------------------------------------------------------------------------


class TestParseDeviations(InvTemp):
    def _parse_ids(self, fid, rows):
        _write_order(self.state, fid, rows)
        return orchlib.invariants_not_run(state=self.state)

    def test_format_garbage_line(self):
        # строка-список без « → <оракул>» — отклонение формата (строка 9)
        ids = self._parse_ids(FRONT_A, [
            _inv_row(1, CMD1),
            "- Инвариант 2: %s без стрелки и оракула" % CMD2,
        ])
        self.assertEqual(ids, ["%s:parse:line=9:format" % FRONT_A])

    def test_number_gap(self):
        ids = self._parse_ids(FRONT_A, [
            _inv_row(1, CMD1), _inv_row(3, CMD2)])
        self.assertEqual(ids, ["%s:parse:line=9:numbering" % FRONT_A])

    def test_number_dup(self):
        ids = self._parse_ids(FRONT_A, [
            _inv_row(1, CMD1), _inv_row(2, CMD2), _inv_row(2, CMD1)])
        self.assertEqual(ids, ["%s:parse:line=10:numbering" % FRONT_A])

    def test_numbering_starts_at_one(self):
        ids = self._parse_ids(FRONT_A, [_inv_row(2, CMD1)])
        self.assertEqual(ids, ["%s:parse:line=8:numbering" % FRONT_A])

    def test_leading_zero_number_rejected(self):
        # «Инвариант 01» — не каноническое целое: строгий формат, не номер 1
        ids = self._parse_ids(
            FRONT_A, ["- Инвариант 01: %s → exit 0" % CMD1])
        self.assertEqual(ids, ["%s:parse:line=8:format" % FRONT_A])
        _measure("MEASURE (6) мусор формата / пропуск / дубль номера / "
                 "ведущий ноль в секции → чип с parse-причиной и строкой")


# ---------------------------------------------------------------------------
# (7) проза с «Инвариант …» вне секции + колонельский order с секцией →
#     тишина (скоуп)
# ---------------------------------------------------------------------------


class TestScopeProseAndColonelSilence(InvTemp):
    def test_prose_outside_and_colonel_order_silent(self):
        # приказ фронта БЕЗ машинной секции: строка формата живёт прозаикой
        # вне секции (скоуп детектора — секция, не текст-паттерн)
        _write_order(
            self.state, FRONT_A, rows=None, header=None,
            extra_before=_inv_row(1, CMD1))
        # колонельский приказ С секцией — вне скопа детектора
        col = os.path.join(self.state, "fronts", FRONT_A,
                           "colonels", "C5-K2X", "order.md")
        _assert_not_live(col)
        os.makedirs(os.path.dirname(col), exist_ok=True)
        with open(col, "w", encoding="utf-8") as f:
            f.write("# Приказ полковнику\n\n%s\n\n%s\n"
                    % (SECTION_HEADER, _inv_row(1, CMD1)))
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state), [],
            "проза вне секции и колонельский приказ — тишина")
        chips = orchlib.health_red_chips(state=self.state)
        self.assertEqual(chips.get("invariants_not_run"), [])
        _measure("MEASURE (7) «- Инвариант …» вне секции + колонельский "
                 "order с секцией → тишина (скоуп)")


# ---------------------------------------------------------------------------
# (8) нет секции → тишина; заголовок без строк → parse-причина
# ---------------------------------------------------------------------------


class TestNoSectionAndHeaderNoRows(InvTemp):
    def test_no_section_silence(self):
        # прозовая строка «Инвариант 1: … → …» без секции и без маркера
        # списка — не инвариант
        _write_order(self.state, FRONT_A, rows=None, header=None,
                     extra_before="Инвариант 1: %s → exit 0" % CMD1)
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])
        inv, err = orchlib.front_invariants_state(FRONT_A, state=self.state)
        self.assertIsNone(inv)
        self.assertIsNone(err)

    def test_header_without_rows_parse(self):
        _write_order(self.state, FRONT_A, rows=None,
                     extra_in_section="Семантика без строк формата.")
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:parse:no_rows" % FRONT_A])
        _measure("MEASURE (8) нет секции → тишина; заголовок без строк → "
                 "parse-причина (не fail-open)")


# ---------------------------------------------------------------------------
# (9) журнал >5000 строк: квитанция за пределами окна → чип гасится
#     (полный журнал, не HEALTH_JOURNAL_SCAN_LIMIT)
# ---------------------------------------------------------------------------


class TestFullJournalBeyondWindow(InvTemp):
    def test_receipt_start_beyond_window_clears(self):
        _write_order(self.state, FRONT_A,
                     [_inv_row(1, CMD1), _inv_row(2, CMD2)])
        # квитанционные раны — ПЕРВЫЕ строки журнала, затем 5100 филлеров:
        # за пределами окна HEALTH_JOURNAL_SCAN_LIMIT=5000
        now = time.time()
        entries = [
            {"ts": now - 500, "kind": "start", "id": "INV9-A",
             "engine": "local", "front": FRONT_A, "role": "code/coder.md"},
            {"ts": now - 499, "kind": "end", "id": "INV9-A", "exit": 0},
            {"ts": now - 498, "kind": "start", "id": "INV9-B",
             "engine": "local", "front": FRONT_A, "role": "code/coder.md"},
            {"ts": now - 497, "kind": "end", "id": "INV9-B", "exit": 0},
        ]
        for i in range(5100):
            entries.append({"ts": now - 496 + i * 0.001, "kind": "note",
                            "id": "filler-%d" % i})
        _seed_journal(self.state, entries)
        for rid, cmd in (("INV9-A", CMD1), ("INV9-B", CMD2)):
            ok, info = orchlib.write_probe_receipt(
                probe=rid, cmd=cmd, exit_code=0, oracle_match=True,
                critic_id=rid, artifact=rid, run_id=rid, state=self.state)
            self.assertTrue(ok, info)
        self.assertGreater(
            len(orchlib._journal_entries_at(self.state)),
            orchlib.HEALTH_JOURNAL_SCAN_LIMIT)
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state), [],
            "front-резолв по ПОЛНОМУ журналу: квитанция за окном гасит")
        chips = orchlib.health_red_chips(state=self.state)
        self.assertEqual(chips.get("invariants_not_run"), [])
        _measure("MEASURE (9) журнал >5000 строк: квитанция за пределами "
                 "окна корректно гасит чип (полный журнал)")


# ---------------------------------------------------------------------------
# (10) живой order.md F-C5 → парсер даёт РОВНО 3 (read-only, skipif)
# ---------------------------------------------------------------------------


class TestLiveFrontOrder(unittest.TestCase):
    LIVE_ORDER = orchlib.front_order_path("F-C5", state=LIVE_STATE)

    @unittest.skipUnless(
        os.path.isdir(LIVE_STATE) and os.path.isfile(LIVE_ORDER),
        "нет живого state / приказа F-C5")
    def test_live_order_parses_exactly_three(self):
        # «ровно 3» + номера 1..3 + валидность формата; БЕЗ дословных cmd
        # живого приказа (хрупкость при эволюции секции в круге 3/close)
        text = orchlib._read_order_text(self.LIVE_ORDER)
        invariants, parse_error = orchlib.parse_front_invariants(text)
        self.assertIsNone(parse_error, "живой приказ не parse-fail")
        self.assertIsNotNone(invariants)
        self.assertEqual([i["num"] for i in invariants], [1, 2, 3],
                         "живой order.md F-C5 → РОВНО 3 инварианта")
        for inv in invariants:
            self.assertTrue(inv["cmd"].strip(),
                            "cmd инварианта непуст: %r" % inv)
            self.assertTrue(inv["oracle"].strip(),
                            "оракул инварианта непуст: %r" % inv)
        _measure("MEASURE (10) живой order.md F-C5 → парсер даёт ровно 3 "
                 "(read-only замер, живой state не писался)")


# ---------------------------------------------------------------------------
# (11) label invariants_not_run в панели; чип в /api/health
# ---------------------------------------------------------------------------


class TestPanelLabelAndApiHealth(InvTemp):
    def test_label_and_api_health_key(self):
        with open(PANEL_INDEX, "r", encoding="utf-8") as f:
            html = f.read()
        self.assertIn("HEALTH_CHIP_LABELS", html)
        self.assertIn("invariants_not_run:", html)
        # ключ пасс-тру в /api/health (panel/server.py — counts/ids)
        server = _load_mod("panel_server_inv",
                           os.path.join(REPO, "panel", "server.py"))
        payload = server._health_payload()
        self.assertIn("invariants_not_run", payload["counts"])
        self.assertIn("invariants_not_run", payload["ids"])
        self.assertEqual(payload["counts"]["invariants_not_run"], 0)
        _measure("MEASURE (11) label invariants_not_run в панели + чип в "
                 "/api/health (пасс-тру из health_red_chips)")


# ---------------------------------------------------------------------------
# Скоуп-параметр детектора (механика 2: параметр готов для K3 —
# синтетический список из одного fid независимо от статуса фронта)
# ---------------------------------------------------------------------------


class TestScopeParamReadyForK3(InvTemp):
    def test_explicit_front_ids_ignore_status(self):
        _write_json(os.path.join(self.state, "fronts.json"), {
            "goal": "k2 scope", "fronts": [
                {"id": FRONT_A, "title": "k2a", "status": "done",
                 "owns": ["tests/**"]},
            ],
        })
        _write_order(self.state, FRONT_A, [_inv_row(1, CMD1)])
        # active-скоуп по умолчанию: done-фронт — тишина (панель до K3
        # не меняется)
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])
        chips = orchlib.health_red_chips(state=self.state)
        self.assertEqual(chips.get("invariants_not_run"), [])
        # синтетический список из одного fid (K3: close-гейт) — красный
        # независимо от статуса фронта
        self.assertEqual(
            orchlib.invariants_not_run(
                state=self.state, front_ids=[FRONT_A]),
            ["%s:1" % FRONT_A])
        _measure("MEASURE (scope) front_ids-параметр: done-фронт тих в "
                 "active-ветке, но красен по синтетическому списку (K3)")


# ---------------------------------------------------------------------------
# FOLLOW-UP (ревью ×3, ремонтный круг): # -комментарии внутри секции,
# дубль секции, битые байты приказа, ведущие нули, стрелка в cmd
# ---------------------------------------------------------------------------


class TestSectionCommentLines(InvTemp):
    """(KR1-1) «#»-строка внутри секции — комментарий, не терминатор."""

    def test_hash_comment_mid_section_keeps_rows(self):
        _write_order(self.state, FRONT_A, [
            _inv_row(1, CMD1),
            "# комментарий внутри секции — не конец перечня",
            _inv_row(2, CMD2),
        ])
        invariants, parse_error = orchlib.front_invariants_state(
            FRONT_A, state=self.state)
        self.assertIsNone(parse_error)
        self.assertEqual([i["num"] for i in invariants], [1, 2],
                         "инварианты до и после #-комментария видны")
        # инвариант ПОСЛЕ комментария гасится своей квитанцией (не потерян)
        _seed_probe_run(self.state, "INV-F1", FRONT_A, CMD2)
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:1" % FRONT_A],
            "счёт тот же: 2 инварианта, чип перечисляет только первый")
        _measure("MEASURE (FU-KR1-1) # -комментарий посередине секции → "
                 "инварианты до/после видны, счёт тот же")


class TestDuplicateSection(InvTemp):
    """(KR1-2/KR2-1) вторая машинная секция — parse-красный, не first-wins."""

    def test_second_machine_section_parse_red(self):
        order = _write_order(self.state, FRONT_A, [_inv_row(1, CMD1)])
        with open(order, "a", encoding="utf-8") as f:
            f.write("\n%s\n\n%s\n"
                    % (SECTION_HEADER, _inv_row(2, CMD2)))
        self.assertEqual(
            orchlib.invariants_not_run(state=self.state),
            ["%s:parse:duplicate_section" % FRONT_A],
            "дубль секции не затеняет первую — parse-причина")
        _measure("MEASURE (FU-KR1-2) вторая машинная секция → чип "
                 "fid:parse:duplicate_section (тишина/first-wins нет)")


class TestBrokenBytesOrder(InvTemp):
    """(KR2-3) битые байты приказа — parse-причина, не тишина (errors=replace)."""

    def test_invalid_utf8_row_parse_red(self):
        path = orchlib.front_order_path(FRONT_A, state=self.state)
        _assert_not_live(path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        head = (
            "# Приказ фронту %s — k2 фикстуры\n\n## Цель\nтекст.\n\n"
            % FRONT_A + SECTION_HEADER + "\n\n" + _inv_row(1, CMD1) + "\n"
        ).encode("utf-8")
        # строка 2: стрелка заменена невалидными utf-8 байтами
        bad_row = ("- Инвариант 2: %s → exit 0\n"
                   % CMD2).encode("utf-8").replace(
            "→".encode("utf-8"), b"\xff\xfe")
        tail = "\n## Границы\n- конец\n".encode("utf-8")
        with open(path, "wb") as f:
            f.write(head + bad_row + tail)
        ids = orchlib.invariants_not_run(state=self.state)
        self.assertEqual(
            ids, ["%s:parse:line=9:format" % FRONT_A],
            "битый байт в строке формата → parse-причина (не тишина)")
        _measure("MEASURE (FU-KR2-3) битые байты order.md (errors=replace) "
                 "→ парсер видит мусор → parse-красный")


class TestCmdWithArrow(InvTemp):
    """(KR1-3) cmd со стрелкой: рез по ПОСЛЕДНЕЙ «→», инвариант гасится."""

    def test_cmd_containing_arrow_split_at_last(self):
        cmd = "python3 -c 'print(\"a → b\")' -q"
        _write_order(self.state, FRONT_A, [_inv_row(1, cmd)])
        invariants, parse_error = orchlib.front_invariants_state(
            FRONT_A, state=self.state)
        self.assertIsNone(parse_error)
        self.assertEqual(invariants[0]["cmd"], cmd,
                         "cmd не режется по первой стрелке")
        self.assertEqual(invariants[0]["oracle"], "exit 0")
        # не липкий красный: квитанция с этим cmd гасит инвариант
        _seed_probe_run(self.state, "INV-F2", FRONT_A, cmd)
        self.assertEqual(orchlib.invariants_not_run(state=self.state), [])
        _measure("MEASURE (FU-KR1-3) cmd с «→» режется по последней "
                 "стрелке и гасится квитанцией (липкого красного нет)")


# ---------------------------------------------------------------------------
# FOLLOW-UP 2 (квитанционная проба F-C5-K2-REG): территория квитанционного
# харнесса в housekeeping-оракуле живого state; зубы не ослаблены
# ---------------------------------------------------------------------------


class TestLiveOracleReceiptHarness(unittest.TestCase):
    """Оракульные решения по путям харнесса — без записей в живой state.

    Проба приёмки (--probe, живая сессия) и её окружение пишут в
    /root/.orchestration ВО ВРЕМЯ pytest: sessions/*/runs/** (run.log,
    prompt*, probe-receipt.md — writer §3 и обёртка), cursor-run-* в
    корне (раны без --session, напр. auto-prosecutor харнесса),
    прирост journal.jsonl без чипов. Всё это — тишина оракула.
    """

    def test_harness_writes_are_housekeeping(self):
        for rel in (
            # симуляция записи в sessions/<x>/runs/** во время окна
            "sessions/s1/runs/test-artifact.md",
            "sessions/s1/runs/F-C5-K2-REG/run.log",
            "sessions/s1/runs/F-C5-K2-REG/probe-receipt.md",
            "sessions/s1/runs/F-C5-K2-REG/prompt.run.md",
            # обёртка ранов без --session (флейк F-C5-K2-REG:
            # cursor-run-prosecutor-auto-F-C5-6.log рос в окне оракула)
            "cursor-run-prosecutor-auto-F-C5-6.log",
            "cursor-run-F-C5-K2-INV2.pid",
            "cursor-run-X.TOMBSTONE",
        ):
            self.assertTrue(_live_housekeeping(rel),
                            "тишина оракула: %s" % rel)
        # прежние housekeeping-маркеры живой сессии остаются
        for rel in ("sessions/s1/last-seen", "sessions/s1/pending_x.json",
                    "sessions/s1/null_series.json",
                    "sessions/s1/enabled.json", "counters/nudge.json",
                    "counters/front-runs-F-C5.json.lock/owner"):
            self.assertTrue(_live_housekeeping(rel), rel)

    def test_oracle_teeth_stay_red(self):
        for rel in (
            "counters/front-runs-TEST.json",
            "fronts.json",
            "fronts/F-C5/compass.md",
            "fronts/F-C5/prosecutor/note.md",
            "fronts/F-C5/colonels/C5-K2/order.md",
            "params.json",
            "agent-SYN1.json",
            "sessions/s1/fronts-dirty.json",
            # cursor-run-* глушится ТОЛЬКО в корне state (KR1+KR3 fix2)
            "fronts/F-C5/cursor-run-leak.md",
            "audit/cursor-run-x.log",
            "sessions/s1/cursor-run-x.pid",
            # решение fix2 (KR2, тайминги журнала): спавн-артефакты
            # автопрокурора пишутся синхронно в journal_end предыдущего
            # рана — строго ВНЕ окна оракула; в окне это аномалия → красный
            "prompt-prosecutor-auto-F-C5-8.md",
            "prompt-F-C5-K2-REG.run.md",
            "runs/RCPT-CMDPROBE/probe-receipt.md",
        ):
            self.assertFalse(_live_housekeeping(rel),
                             "зуб оракула: %s обязан краснеть" % rel)

    def test_journal_growth_probe_records_ok_chip_red(self):
        start = json.dumps({
            "ts": 1, "kind": "start", "id": "F-C5-K2-REG",
            "front": "F-C5", "role": "code/coder.md",
        }, ensure_ascii=False).encode("utf-8")
        end = json.dumps({
            "ts": 2, "kind": "end", "id": "F-C5-K2-REG", "exit": 0,
        }, ensure_ascii=False).encode("utf-8")
        self.assertIsNone(
            _live_journal_growth_violation(start + b"\n" + end))
        chip = json.dumps({
            "ts": 3, "kind": "chip", "name": "invariants_not_run",
            "id": "F-C5",
        }, ensure_ascii=False).encode("utf-8")
        self.assertIsNotNone(
            _live_journal_growth_violation(start + b"\n" + chip + b"\n"),
            "чип invariants_not_run в приросте — красный")
        _measure("MEASURE (FU2) харнесс-записи (sessions/*/runs/**, "
                 "cursor-run-* ТОЛЬКО в корне, journal-прирост без чипов) "
                 "— тишина; front-runs-*, fronts.json/compass/prosecutor, "
                 "глубокие cursor-run-*, prompt-*/runs спавна, чип — "
                 "красные")


if __name__ == "__main__":
    unittest.main(verbosity=2)
