#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MC-C1: офлайн-тесты must-check (подмена jev_decisions, без сети).

Группы: (а) thin/full plan; (б) negative разрешающих слов;
(в) fail-open; (г) id vs --must-text; (д) неизвестная роль.

Запуск: cd /root/orchestrator-with-cursor && python3 -m pytest tests/test_mustcheck.py -q
"""
from __future__ import print_function

import importlib.util
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
ADVISE_PATH = os.path.join(REPO, "bin", "jev-advise.py")
TABLE_PATH = os.path.join(REPO, "routing", "jev-table.json")
MUSTMAP_PATH = os.path.join(REPO, "audit", "mustmap", "mustmap.json")

POINT_ID = "must-check"
# MM колонела/all status=prompt: свита / warden (реальные id из mustmap)
MUST_SUITE = "MM-131"
MUST_WARDEN = "MM-132"
# all/prompt: label содержит «разрешено» — регрессия scrub (не схлопывать подсветку)
MUST_SCRUB = "MM-190"

# (б) запрет разрешающих формулировок в выводе
APPROVE_RE = re.compile(
    r"разреш|можно старт|approved|green to go",
    re.IGNORECASE,
)

# Тонкий план: без квитанций / warden / свиты / критиков
THIN_PLAN = (
    "Волна: три работы по CLI. "
    "Исполнители пишут дифф, полковник принимает по вердикту. "
    "Компас через воронку. Без параллельных развилок."
)

# Полный план: явно закрывает свиту / warden / критиков / квитанции
# (опора на MM-131, MM-132, MM-209, MM-228 из mustmap colonel/all status=prompt)
FULL_PLAN = (
    "План волны полковника.\n"
    "Свита обязательна: opportunity-advisor на развилках; "
    "критики плана (fact-checker) и критики приёмки; "
    "raw-brief-synthesizer; git-warden чекпоинт; docs-keeper; "
    "simplicity-warden после значимой код-волны.\n"
    "План через критиков до старта работ.\n"
    "Приёмка=функция: проба-green и проба-red на /tmp-полигоне; "
    "квитанция probe-receipt снимает probes_missing.\n"
)


def _load_advise():
    spec = importlib.util.spec_from_file_location("jev_advise_mustcheck", ADVISE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fixture_choice(choice, confidence, qid=POINT_ID):
    ans = {"type": "choice", "choice": choice}
    if confidence is not None:
        ans["confidence"] = confidence
    return {
        "model": "typesafe/jev-1.13",
        "answers": {qid: ans},
        "usage": {"cost": 0},
    }


def _write_plan(td, text, name="plan.txt"):
    path = os.path.join(td, name)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def _run_must_check(mod, argv, decisions_fixture, state_dir=None):
    """Вызвать main(must-check) с подменой jev_decisions.

    stdout: строка подсветки + JSON. Возврат (exit, highlight, data, stderr).
    """
    if state_dir is None:
        state_dir = tempfile.mkdtemp(prefix="mustcheck-")

    def _fake_decisions(state, questions, **kwargs):
        if callable(decisions_fixture):
            return decisions_fixture(state, questions, **kwargs)
        return decisions_fixture

    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with mock.patch.object(mod, "jev_decisions", side_effect=_fake_decisions):
        with mock.patch.object(mod.orchlib, "find_state_dir",
                               return_value=state_dir):
            with mock.patch.object(mod, "load_openrouter_key",
                                   return_value="test-key-not-real"):
                with redirect_stdout(out_buf):
                    with redirect_stderr(err_buf):
                        code = mod.main(argv)
    raw = out_buf.getvalue()
    lines = [ln for ln in raw.splitlines() if ln.strip() != ""]
    highlight = lines[0] if lines else ""
    data = {}
    if len(lines) >= 2:
        data = json.loads(lines[1])
    elif len(lines) == 1:
        # usage-error может не печатать JSON; попытка парса
        try:
            data = json.loads(lines[0])
            highlight = data.get("highlight") or highlight
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    return code, highlight, data, err_buf.getvalue()


def _base_argv(plan_path, extra=None):
    argv = [
        "--point", POINT_ID,
        "--role", "colonel",
        "--plan-file", plan_path,
        "--caller", "MC-C1/test",
        "--table-path", TABLE_PATH,
    ]
    if extra:
        argv.extend(extra)
    return argv


def _ids_from_highlight(highlight):
    """Извлечь MM-* id из строки «подсвечено: MM-131: …; MM-132: …»."""
    return re.findall(r"\bMM-\d+\b", highlight or "")


class TestMustCheckGroupA_ThinFull(unittest.TestCase):
    """(а) тонкий план → подсветка MUST свиты/warden; полный → чисто (advisory)."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()
        # sanity: реальные id есть в mustmap для colonel prompt
        with open(MUSTMAP_PATH, "r", encoding="utf-8") as f:
            mustmap = json.load(f)
        by_id = {m["id"]: m for m in mustmap["imperatives"]}
        for mid in (MUST_SUITE, MUST_WARDEN):
            item = by_id[mid]
            assert item.get("to") == "colonel"
            assert item.get("status") == "prompt"

    def test_a_thin_plan_highlights_suite_warden(self):
        with tempfile.TemporaryDirectory(prefix="mc-a-thin-") as td:
            plan = _write_plan(td, THIN_PLAN)
            # тонкий: Jev возвращает релевантные MUST свиты/warden
            fix = _fixture_choice([MUST_SUITE, MUST_WARDEN], 0.9)
            code, highlight, data, _err = _run_must_check(
                self.mod, _base_argv(plan), fix)
        self.assertEqual(code, 0)
        self.assertTrue(data.get("ok"))
        self.assertIn("подсвечено:", highlight)
        self.assertIn(MUST_SUITE, highlight)
        self.assertIn(MUST_WARDEN, highlight)
        selected = data.get("selected") or []
        self.assertIn(MUST_SUITE, selected)
        self.assertIn(MUST_WARDEN, selected)
        self.assertNotEqual(highlight, "чисто (advisory)")

    def test_a_full_plan_clean_advisory(self):
        with tempfile.TemporaryDirectory(prefix="mc-a-full-") as td:
            plan = _write_plan(td, FULL_PLAN)
            # полный: пустой choice / low band → без подсветки
            fix = _fixture_choice([], 0.1)
            code, highlight, data, _err = _run_must_check(
                self.mod, _base_argv(plan), fix)
        self.assertEqual(code, 0)
        self.assertIn("чисто (advisory)", highlight)
        self.assertEqual(data.get("highlight"), "чисто (advisory)")
        self.assertEqual(data.get("selected") or [], [])


class TestMustCheckGroupB_NegativeApprove(unittest.TestCase):
    """(б) вывод НЕ содержит разрешающих слов (полный план И с подсветкой).

    Проверяем пользовательский вывод must-check: строка подсветки +
    scrub-поля highlight/advisory_text/error. Выборка ограничена
    MM-131/MM-132 (--must-ids), чтобы не ловить «разрешено» из чужих
    текстов mustmap в criteria (ложное срабатывание regex).
    """

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def _user_facing_blob(self, highlight, data, err):
        parts = [
            highlight or "",
            str(data.get("highlight") or ""),
            str(data.get("advisory_text") or ""),
            str(data.get("error") or ""),
            err or "",
        ]
        return "\n".join(parts)

    def _assert_no_approve(self, blob, label):
        m = APPROVE_RE.search(blob)
        self.assertIsNone(
            m,
            "%s: запрещённая формулировка %r в выводе:\n%s"
            % (label, m.group(0) if m else None, blob[:500]),
        )

    def test_b_full_plan_no_approve_words(self):
        ids_flag = "%s,%s" % (MUST_SUITE, MUST_WARDEN)
        with tempfile.TemporaryDirectory(prefix="mc-b-full-") as td:
            plan = _write_plan(td, FULL_PLAN)
            code, highlight, data, err = _run_must_check(
                self.mod,
                _base_argv(plan, ["--must-ids", ids_flag]),
                _fixture_choice([], 0.2))
        self.assertEqual(code, 0)
        self.assertIn("чисто (advisory)", highlight)
        self._assert_no_approve(
            self._user_facing_blob(highlight, data, err), "full-plan")

    def test_b_highlight_fixture_no_approve_words(self):
        ids_flag = "%s,%s" % (MUST_SUITE, MUST_WARDEN)
        with tempfile.TemporaryDirectory(prefix="mc-b-hi-") as td:
            plan = _write_plan(td, THIN_PLAN)
            code, highlight, data, err = _run_must_check(
                self.mod,
                _base_argv(plan, ["--must-ids", ids_flag]),
                _fixture_choice([MUST_SUITE, MUST_WARDEN], 0.95))
        self.assertEqual(code, 0)
        self.assertIn("подсвечено:", highlight)
        self._assert_no_approve(
            self._user_facing_blob(highlight, data, err), "highlight")


class TestMustCheckGroupC_FailOpen(unittest.TestCase):
    """(в) API недоступен (jev_decisions raises) → пустая подсветка + причина, exit 0."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_c_failopen_api_down(self):
        def boom(*_a, **_k):
            raise RuntimeError("simulated API down")

        with tempfile.TemporaryDirectory(prefix="mc-c-fo-") as td:
            plan = _write_plan(td, THIN_PLAN)
            code, highlight, data, _err = _run_must_check(
                self.mod, _base_argv(plan), boom)
        self.assertEqual(code, 0)
        self.assertEqual(highlight, "чисто (advisory)")
        self.assertEqual(data.get("highlight"), "чисто (advisory)")
        self.assertEqual(data.get("selected") or [], [])
        self.assertFalse(data.get("ok"))
        err = data.get("error") or ""
        self.assertTrue(err.startswith("причина:"), err)
        self.assertIn("simulated API down", err)


class TestMustCheckGroupD_IdVsMustText(unittest.TestCase):
    """(г) id-режим и --must-text → те же MUST-id в подсветке на одном кейсе."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_d_id_mode_and_must_text_same_ids(self):
        must_ids_flag = "%s,%s" % (MUST_SUITE, MUST_WARDEN)
        fix = _fixture_choice([MUST_SUITE, MUST_WARDEN], 0.9)

        with tempfile.TemporaryDirectory(prefix="mc-d-") as td:
            plan = _write_plan(td, THIN_PLAN)
            # id-режим (краткие labels, без --must-text)
            code1, hi1, data1, _ = _run_must_check(
                self.mod,
                _base_argv(plan, ["--must-ids", must_ids_flag]),
                fix)
            # --must-text: полные тексты MUST в criteria
            code2, hi2, data2, _ = _run_must_check(
                self.mod,
                _base_argv(plan, ["--must-ids", must_ids_flag, "--must-text"]),
                fix)

        self.assertEqual(code1, 0)
        self.assertEqual(code2, 0)
        ids1 = _ids_from_highlight(hi1)
        ids2 = _ids_from_highlight(hi2)
        self.assertEqual(sorted(ids1), sorted(ids2))
        self.assertEqual(sorted(ids1), sorted([MUST_SUITE, MUST_WARDEN]))
        self.assertEqual(
            sorted(data1.get("selected") or []),
            sorted(data2.get("selected") or []),
        )
        # labels различаются (brief vs full), но id в подсветке совпадают
        self.assertIn("подсвечено:", hi1)
        self.assertIn("подсвечено:", hi2)


class TestMustCheckGroupE_UnknownRole(unittest.TestCase):
    """(д) несуществующая роль (admiral) → понятная ошибка, exit ≠ 0."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()

    def test_e_admiral_usage_error(self):
        with tempfile.TemporaryDirectory(prefix="mc-e-") as td:
            plan = _write_plan(td, THIN_PLAN)
            state_dir = tempfile.mkdtemp(prefix="mustcheck-e-")
            out_buf = io.StringIO()
            err_buf = io.StringIO()

            def boom(*_a, **_k):
                raise AssertionError("jev_decisions must not be called")

            with mock.patch.object(self.mod, "jev_decisions", side_effect=boom):
                with mock.patch.object(self.mod.orchlib, "find_state_dir",
                                       return_value=state_dir):
                    with mock.patch.object(self.mod, "load_openrouter_key",
                                           return_value="k"):
                        with redirect_stdout(out_buf):
                            with redirect_stderr(err_buf):
                                code = self.mod.main([
                                    "--point", POINT_ID,
                                    "--role", "admiral",
                                    "--plan-file", plan,
                                    "--caller", "MC-C1/e",
                                    "--table-path", TABLE_PATH,
                                ])
        self.assertNotEqual(code, 0)
        err = err_buf.getvalue()
        self.assertIn("must-check:", err)
        self.assertTrue(
            "admiral" in err or "роль" in err.lower(),
            "ожидалась понятная ошибка роли, stderr=%r" % err,
        )


class TestMustCheckScrubMM190Regression(unittest.TestCase):
    """Регрессия: scrub «разрешено» в label MM-190 НЕ схлопывает подсветку."""

    @classmethod
    def setUpClass(cls):
        cls.mod = _load_advise()
        with open(MUSTMAP_PATH, "r", encoding="utf-8") as f:
            mustmap = json.load(f)
        by_id = {m["id"]: m for m in mustmap["imperatives"]}
        item = by_id[MUST_SCRUB]
        assert item.get("to") == "all"
        assert item.get("status") == "prompt"
        assert "разрешено" in (item.get("text") or "").lower()

    def test_scrub_mm190_keeps_highlight(self):
        with tempfile.TemporaryDirectory(prefix="mc-scrub-") as td:
            plan = _write_plan(td, THIN_PLAN)
            code, highlight, data, _err = _run_must_check(
                self.mod,
                _base_argv(plan, ["--must-ids", MUST_SCRUB]),
                _fixture_choice([MUST_SCRUB], 0.9),
            )
        self.assertEqual(code, 0)
        self.assertTrue(
            highlight.startswith("подсвечено: MM-190"),
            "ожидали подсветку MM-190, получили: %r" % highlight,
        )
        self.assertNotEqual(highlight, "чисто (advisory)")
        self.assertNotEqual(data.get("highlight"), "чисто (advisory)")
        self.assertIn(MUST_SCRUB, data.get("selected") or [])
        # scrub in-place: токен «разрешено» заменён, но префикс подсветки жив
        self.assertNotIn("разрешено", highlight.lower())


if __name__ == "__main__":
    unittest.main()
