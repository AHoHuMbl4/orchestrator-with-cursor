#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-обёртка Jev Decisions API (OpenRouter) — stdlib only.

POST https://openrouter.ai/api/alpha/decisions, model typesafe/jev-1.13.
Ключ: <state>/openrouter.key через orchlib.find_state_dir (override: --key-path).
Ключ НИКОГДА не пишется в stdout/stderr/журнал.

Стартовые пороги (спека порог не задаёт; зафиксированы здесь):
  Choice/Score: defer_below=0.5, confirm_below=0.75
  Noul: yes_at=0.6

Bands: Choice/Score — low/mid/high по confidence; conf отсутствует → absent
(defer вызывающему). Noul — yes/below по noul vs yes_at. low → defer («решай сам»).
Fail-open: API-недоступность/ошибка → пустой ответ + причина, exit 0.

  jev-advise.py --list-table
  jev-advise.py --point <id> --caller <front/run-id>
      [--state TEXT | --state-text TEXT | --state-file PATH]
      [--question id:type:instructions… | --questions-file PATH]
      [--defer-below N] [--confirm-below N] [--yes-at N]
      [--api-url URL] [--key-path PATH] [--session-id ID]
  jev-advise.py --caller <front/run-id>   # ad-hoc Score/Choice/Noul БЕЗ --point
      --question…|--questions-file PATH [--defer-below N] [--confirm-below N]

Таблица маршрутизации: routing/jev-table.json (обязательна).
Журнал: <state>/jev-calls.jsonl.
Кроссплатформенно, python3.6+, только stdlib.
"""
from __future__ import print_function

import argparse
import difflib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
MODEL_ID = "typesafe/jev-1.13"

HTTP_TIMEOUT_S = 10.0
MAX_RETRIES = 2
BACKOFF_INITIAL_S = 0.5
BACKOFF_MAX_S = 5.0
CALL_BUDGET_S = 30.0
RETRY_STATUSES = frozenset([408, 429] + list(range(500, 600)))
NO_RETRY_STATUSES = frozenset([400, 401, 402, 403, 404, 413, 422])

# Стартовые пороги (см. docstring)
DEFAULT_DEFER_BELOW = 0.5
DEFAULT_CONFIRM_BELOW = 0.75
DEFAULT_YES_AT = 0.6

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE_PATH = os.path.join(REPO_ROOT, "routing", "jev-table.json")

QUESTION_TYPES = frozenset(["choice", "noul", "score"])


class _AdviseArgumentParser(argparse.ArgumentParser):
    """Понятные ошибки: алиасы state + подсказки для unrecognized флагов."""

    def _canon_option_index(self):
        """option_string → label группы алиасов; flat — все каноны."""
        opt_to_group = {}
        flat = []
        for action in self._actions:
            opts = list(action.option_strings or [])
            if not opts:
                continue
            # короткие алиасы первыми: --state/--state-text
            label = "/".join(sorted(opts, key=len))
            for o in opts:
                flat.append(o)
                opt_to_group[o] = label
        return flat, opt_to_group

    def _hint_unrecognized(self, tokens):
        flat, opt_to_group = self._canon_option_index()
        parts = []
        for flag in tokens:
            if not flag.startswith("-"):
                continue
            matches = difflib.get_close_matches(
                flag, flat, n=5, cutoff=0.5)
            groups = []
            for m in matches:
                g = opt_to_group[m]
                if g not in groups:
                    groups.append(g)
            if groups:
                parts.append(
                    "unrecognized: %s → возможно %s" % (flag, groups[0]))
            else:
                parts.append("unrecognized: %s" % flag)
        return "; ".join(parts) if parts else None

    def error(self, message):
        msg = message or ""
        # взаимная исключительность --state/--state-text ↔ --state-file
        if "not allowed with argument" in msg and (
                "state-text" in msg or "state-file" in msg or
                "--state" in msg):
            msg = "--state/--state-text и --state-file взаимно исключают"
        elif msg.startswith("unrecognized arguments:"):
            tokens = msg.split(":", 1)[1].strip().split()
            hinted = self._hint_unrecognized(tokens)
            if hinted:
                msg = hinted
        elif msg.startswith("ambiguous option:"):
            # allow_abbrev=False обычно исключает; страховка для префиксов
            # "ambiguous option: --stat could match --state-text, --state, ..."
            flag = msg.split(":", 1)[1].strip().split()[0]
            hinted = self._hint_unrecognized([flag])
            if hinted:
                msg = hinted
        self.print_usage(sys.stderr)
        self.exit(2, "%s: error: %s\n" % (self.prog, msg))


def _mask_secrets(text, key=None):
    """Убрать ключ / Authorization из текста ошибок."""
    if text is None:
        return ""
    s = str(text)
    if key:
        if key in s:
            s = s.replace(key, "***")
        # фрагменты ключа длиной ≥8
        for n in (16, 12, 8):
            if len(key) >= n and key[:n] in s:
                s = s.replace(key[:n], "***")
    s = re.sub(r"(?i)(Authorization\s*[:=]\s*Bearer\s+)\S+",
               r"\1***", s)
    s = re.sub(r"(?i)(Bearer\s+)\S+", r"\1***", s)
    return s


def load_openrouter_key(path):
    key = open(path, "r", encoding="utf-8").read().strip()
    if not key:
        raise RuntimeError("пустой ключ: %s" % path)
    return key


def default_key_path():
    return os.path.join(orchlib.find_state_dir(), "openrouter.key")


def load_table(path=None):
    p = path or TABLE_PATH
    with open(p, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict) or "points" not in data:
        raise ValueError("таблица без points: %s" % p)
    return data


def point_by_id(table, point_id):
    for pt in table.get("points") or []:
        if pt.get("id") == point_id:
            return pt
    raise KeyError("точка не найдена в таблице: %s" % point_id)


def resolve_thresholds(point, defer_below=None, confirm_below=None, yes_at=None):
    """Пороги: CLI override > таблица точки > стартовые дефолты."""
    db = DEFAULT_DEFER_BELOW
    cb = DEFAULT_CONFIRM_BELOW
    ya = DEFAULT_YES_AT
    if point:
        if point.get("defer_below") is not None:
            db = float(point["defer_below"])
        if point.get("confirm_below") is not None:
            cb = float(point["confirm_below"])
        if point.get("yes_at") is not None:
            ya = float(point["yes_at"])
    if defer_below is not None:
        db = float(defer_below)
    if confirm_below is not None:
        cb = float(confirm_below)
    if yes_at is not None:
        ya = float(yes_at)
    return db, cb, ya


def advise_choice_or_score(answer, *, defer_below, confirm_below):
    """Choice/Score: bands low/mid/high; absent confidence → absent."""
    conf = answer.get("confidence")
    out = {
        "type": answer.get("type"),
        "choice": answer.get("choice"),
        "score": answer.get("score"),
        "probabilities": answer.get("probabilities"),
    }
    if conf is None:
        out["band"] = "absent"
        out["action"] = "defer"
        out["reason"] = "решай сам"
        return out
    out["confidence"] = conf
    if conf < defer_below:
        out["band"] = "low"
        out["action"] = "defer"
        out["reason"] = "решай сам"
    elif conf < confirm_below:
        out["band"] = "mid"
        out["action"] = "confirm"
    else:
        out["band"] = "high"
        out["action"] = "auto"
    return out


def advise_noul(answer, *, yes_at):
    p = answer.get("noul")
    if p is None:
        return {
            "type": "noul",
            "band": "absent",
            "action": "defer",
            "reason": "решай сам",
        }
    band = "yes" if float(p) >= yes_at else "below"
    out = {"type": "noul", "noul": p, "band": band}
    if band == "below":
        out["action"] = "defer"
        out["reason"] = "решай сам"
    else:
        out["action"] = "yes"
    return out


def advise_answer(answer, *, defer_below, confirm_below, yes_at):
    t = (answer.get("type") or "").lower()
    if t == "noul":
        return advise_noul(answer, yes_at=yes_at)
    if t in ("choice", "score"):
        return advise_choice_or_score(
            answer, defer_below=defer_below, confirm_below=confirm_below)
    return {
        "type": t or None,
        "band": "absent",
        "action": "defer",
        "reason": "решай сам",
        "raw": answer,
    }


def jev_decisions(state, questions, *, model=MODEL_ID, key=None,
                  key_path=None, session_id=None, api_url=None):
    if not questions:
        raise ValueError("пустой questions")
    if key is None:
        key = load_openrouter_key(key_path or default_key_path())
    body = {"model": model, "state": state, "questions": questions}
    if session_id is not None:
        if len(session_id) > 256:
            raise ValueError("session_id длиннее 256")
        body["session_id"] = session_id
    payload = json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    url = api_url or DECISIONS_URL
    deadline = time.monotonic() + CALL_BUDGET_S
    attempt = 0
    last_err = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("бюджет вызова %.1f с исчерпан" % CALL_BUDGET_S)
        req = urllib.request.Request(
            url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                    req, timeout=min(HTTP_TIMEOUT_S, remaining)) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body_bytes = b""
            try:
                body_bytes = exc.read() or b""
            except Exception:
                pass
            detail = _mask_secrets(
                body_bytes.decode("utf-8", errors="replace"), key)
            last_err = "HTTP %s: %s" % (exc.code, detail[:500])
            if exc.code in NO_RETRY_STATUSES or exc.code not in RETRY_STATUSES:
                raise RuntimeError(last_err)
            if attempt >= MAX_RETRIES:
                raise RuntimeError(last_err)
            delay = min(BACKOFF_MAX_S, BACKOFF_INITIAL_S)
            if time.monotonic() + delay > deadline:
                raise RuntimeError(last_err)
            time.sleep(delay)
            attempt += 1
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last_err = _mask_secrets(exc, key)
            if attempt >= MAX_RETRIES:
                raise RuntimeError("connection/timeout: %s" % last_err)
            delay = min(BACKOFF_MAX_S, BACKOFF_INITIAL_S)
            if time.monotonic() + delay > deadline:
                raise RuntimeError("connection/timeout: %s" % last_err)
            time.sleep(delay)
            attempt += 1


def append_journal(state_dir, record):
    path = os.path.join(state_dir, "jev-calls.jsonl")
    line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _parse_question_arg(spec):
    """id:type:instructions[::criteria_json]

    criteria_json — объект (Choice) или массив (Score).
    """
    parts = spec.split(":", 2)
    if len(parts) < 3:
        raise ValueError(
            "формат --question: id:type:instructions[::criteria_json], "
            "получено: %r" % spec)
    qid, qtype, rest = parts[0].strip(), parts[1].strip().lower(), parts[2]
    if qtype not in QUESTION_TYPES:
        raise ValueError("неизвестный type %r (choice|noul|score)" % qtype)
    criteria = None
    instructions = rest
    if "::" in rest:
        instructions, crit_raw = rest.split("::", 1)
        criteria = json.loads(crit_raw)
    q = {"type": qtype, "instructions": instructions}
    if criteria is not None:
        q["criteria"] = criteria
    elif qtype in ("choice", "score"):
        raise ValueError(
            "для %s нужен criteria: id:%s:instructions::{...|[...]}"
            % (qtype, qtype))
    return qid, q


def _load_questions(args):
    questions = {}
    if args.questions_file:
        with open(args.questions_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("--questions-file: ожидался объект id→question")
        for qid, q in data.items():
            if not isinstance(q, dict):
                raise ValueError("вопрос %r не объект" % qid)
            q = dict(q)
            if "type" in q and isinstance(q["type"], str):
                q["type"] = q["type"].lower()
            questions[qid] = q
    for spec in (args.question or []):
        qid, q = _parse_question_arg(spec)
        questions[qid] = q
    return questions


def _load_state(args):
    """Вернуть state; сырой текст — через _load_state_raw (observer raw_tail)."""
    state, _raw = _load_state_raw(args)
    return state


def _load_state_raw(args):
    """(state, raw_text). raw_text — вход без изменений (для raw_tail)."""
    if args.state_text is not None:
        return args.state_text, args.state_text
    if args.state_file is not None:
        if args.state_file == "-":
            text = sys.stdin.read()
        else:
            with open(args.state_file, "r", encoding="utf-8") as f:
                text = f.read()
        # JSON object/array as state if file is valid JSON structured
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return parsed, text
        except (ValueError, TypeError):
            pass
        return text, text
    return None, None


def _journal_advice_fields(advice_map):
    bands = {}
    confidences = {}
    nouls = {}
    for qid, adv in (advice_map or {}).items():
        if not isinstance(adv, dict):
            continue
        if "band" in adv:
            bands[qid] = adv["band"]
        if "confidence" in adv:
            confidences[qid] = adv["confidence"]
        if "noul" in adv:
            nouls[qid] = adv["noul"]
    return bands, confidences, nouls


# --- F-JEVFAST C2: advisory wrappers (importable; existing paths untouched) ---

ADVISORY_AUTO_POINTS = frozenset([
    "scout-need",
    "mechanic-vs-fork",
    "repair-followup-or-fresh",
    "focus-hint-diff",
    "observer-journal-brief",
    "chip-triage",
    "retro-card-hint",
    "run-queue-prio",
])

HINT_BLOCK_FOOTER = (
    "advisory: темы для ТЕХ ЖЕ ×3 критиков; N критиков и круги НЕ меняются"
)

_PATH_LIKE_RE = re.compile(
    r"(?:^\./)|(?:^/)|(?:[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)|"
    r"(?:\.(?:py|json|md|html|sh|ts)\b)",
    re.IGNORECASE,
)
_SEARCH_DIRECTIVE_RE = re.compile(
    r"смотри в|ищи в|проверь файл|где искать|look in|see file",
    re.IGNORECASE,
)

_BADGE_ADVISORY_BANDS = frozenset(["yes", "mid", "high"])
_BADGE_DEFER_BANDS = frozenset(["below", "low", "absent"])

_CHOICE_CARDINALITY = {
    "focus-hint-diff": (1, 5),
    "observer-journal-brief": (3, 6),
    "retro-card-hint": (1, 1),
    "chip-triage": (1, 1),
}


def badge_from_journal_record(record):
    """Journal record → badge: fail|advisory|defer|None.

    status=error → fail; иначе band-словарь: любое в {yes,mid,high} → advisory;
    все в {below,low,absent} или пусто → defer. Нечитаемая запись → None.
    """
    if not isinstance(record, dict):
        return None
    if record.get("status") == "error":
        return "fail"
    if record.get("status") not in ("ok", "error", None):
        # неизвестный status при наличии band — ниже
        pass
    bands = record.get("band")
    if bands is None:
        if record.get("status") == "ok":
            return "defer"
        return None
    if not isinstance(bands, dict):
        return None
    if not bands:
        return "defer"
    vals = list(bands.values())
    if any(v in _BADGE_ADVISORY_BANDS for v in vals):
        return "advisory"
    if all(v in _BADGE_DEFER_BANDS for v in vals):
        return "defer"
    # смешанные/неизвестные — conservative defer
    return "defer"


def normalize_chip_input(data):
    """Нормализация chip-triage входа → плоский список id (порядок сохранён).

    Формы:
      (i) список объектов с id → id поля;
      (ii) словарь {chip: [ids]} → «<chip>#<n>» по позиции (0-based);
      (iii) конверт health {counts, ids:{chip:[...]}, ...} → unwrap .ids → (ii).
    """
    if isinstance(data, str):
        try:
            parsed = json.loads(data)
            if isinstance(parsed, (dict, list)):
                data = parsed
            else:
                return []
        except (ValueError, TypeError):
            return []
    if data is None:
        return []
    if isinstance(data, list):
        out = []
        for item in data:
            if isinstance(item, dict) and item.get("id") is not None:
                out.append(str(item["id"]))
            elif isinstance(item, str):
                out.append(item)
        return out
    if isinstance(data, dict):
        # (iii) health envelope
        if "ids" in data and isinstance(data["ids"], dict):
            data = data["ids"]
        out = []
        for chip, ids in data.items():
            if not isinstance(ids, list):
                continue
            for n, _val in enumerate(ids):
                out.append("%s#%d" % (chip, n))
        return out
    return []


def coerce_structured_state(state):
    """Если state — JSON-строка объекта/массива → распарсить (chip/queue)."""
    if isinstance(state, str):
        try:
            parsed = json.loads(state)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, TypeError):
            pass
    return state


def normalize_choice_list(choice):
    """choice API: str | list → list[str] (без фильтрации)."""
    if choice is None:
        return []
    if isinstance(choice, list):
        return [str(c) for c in choice if c is not None and str(c) != ""]
    if isinstance(choice, str):
        s = choice.strip()
        return [s] if s else []
    return [str(choice)]


def validate_choice_against_criteria(choice, criteria, min_n, max_n):
    """choice ⊆ criteria keys (dict) или элементов (list); лишнее → max_n; <min_n → [].

    choice: str | list[str] → normalize_choice_list.
    dict-criteria (Choice API record): допустимы КЛЮЧИ объекта.
    list-criteria (Score / совместимость): допустимы элементы списка.
    """
    if isinstance(criteria, dict):
        crit = [str(k) for k in criteria.keys()]
    else:
        crit = [str(c) for c in (criteria or [])]
    crit_set = set(crit)
    picked = []
    for c in normalize_choice_list(choice):
        if c in crit_set and c not in picked:
            picked.append(c)
        if len(picked) >= max_n:
            break
    if len(picked) < min_n:
        return []
    return picked


def is_path_or_search_theme(text):
    """Защитный фильтр: пути и директивы поиска."""
    if text is None:
        return True
    s = str(text)
    if _PATH_LIKE_RE.search(s):
        return True
    if _SEARCH_DIRECTIVE_RE.search(s):
        return True
    return False


def filter_safe_themes(themes):
    return [t for t in (themes or []) if not is_path_or_search_theme(t)]


def force_advisory_actions(advice_map):
    """Переопределить advice.*.action → advisory (ядровое auto не в stdout)."""
    out = {}
    for qid, adv in (advice_map or {}).items():
        if isinstance(adv, dict):
            a = dict(adv)
            a["action"] = "advisory"
            out[qid] = a
        else:
            out[qid] = adv
    return out


def _primary_advice(advice_map):
    if not advice_map:
        return {}
    if len(advice_map) == 1:
        return next(iter(advice_map.values())) or {}
    # prefer first non-absent
    for adv in advice_map.values():
        if isinstance(adv, dict) and adv.get("band") not in ("absent", "low"):
            return adv
    return next(iter(advice_map.values())) or {}


def _band_of(advice_map):
    adv = _primary_advice(advice_map)
    return (adv or {}).get("band") or "absent"


def build_auto_questions(point, state):
    """Собрать questions из точки с auto_question:true (+ динамика chip/queue)."""
    pid = point.get("id")
    qtype = (point.get("type") or "").lower()
    instructions = point.get("instructions") or ""
    criteria = point.get("criteria")
    state = coerce_structured_state(state)

    if pid == "chip-triage":
        ids = normalize_chip_input(state)
        # Choice API: criteria — record {id: краткая метка}, не массив
        crit_obj = {cid: cid for cid in ids}
        return {
            pid: {
                "type": "choice",
                "instructions": instructions,
                "criteria": crit_obj,
            }
        }

    if pid == "run-queue-prio":
        runs = state if isinstance(state, list) else []
        ids = []
        for item in runs:
            if isinstance(item, dict) and item.get("id") is not None:
                ids.append(str(item["id"]))
            elif isinstance(item, str):
                ids.append(item)
        if not ids:
            # один вопрос из таблицы (без хардкода criteria)
            q = {"type": "score", "instructions": instructions}
            if criteria is not None:
                q["criteria"] = criteria
            return {pid: q}
        out = {}
        for rid in ids:
            q = {
                "type": "score",
                "instructions": instructions,
            }
            if criteria is not None:
                q["criteria"] = criteria
            out[rid] = q
        return out

    q = {"type": qtype, "instructions": instructions}
    if criteria is not None and qtype in ("choice", "score"):
        q["criteria"] = criteria
    elif qtype in ("choice", "score"):
        # без criteria — пусть API/валидация решит; для Noul criteria нет
        pass
    return {pid: q}


def _advisory_phrase(recommendation):
    return (
        "advisory: %s; решение за командиром"
        % recommendation
    )


def advisory_text_for_point(point_id, advice_map, extra=None):
    """Человекочитаемая строка: всегда «advisory» + «решение за командиром»."""
    extra = extra or {}
    band = _band_of(advice_map)
    adv = _primary_advice(advice_map)

    if point_id == "scout-need":
        if band == "yes":
            return _advisory_phrase(
                "локальных фактов достаточно — web-разведка НЕ нужна (no-scout)")
        return _advisory_phrase(
            "сомнение → звать scout (консервативно)")

    if point_id == "mechanic-vs-fork":
        if band == "yes":
            return _advisory_phrase("механика follow-up (без advisor)")
        return _advisory_phrase("считать развилкой (advisor)")

    if point_id == "repair-followup-or-fresh":
        # yes и below/absent → follow-up; ниже — пометка про fresh
        base = "ремонт — follow-up на том же контексте"
        if band != "yes":
            base += (
                "; командир вправе взять fresh при сомнении "
                "в чистоте контекста"
            )
        return _advisory_phrase(base)

    if point_id == "focus-hint-diff":
        if extra.get("hint_block") and extra["hint_block"] != "без подсветки":
            return _advisory_phrase(
                "подсветка тем для тех же ×3 критиков (N/круги не меняются)")
        return _advisory_phrase("без подсветки (полный слепой промт)")

    if point_id == "observer-journal-brief":
        if extra.get("brief_theses"):
            return _advisory_phrase(
                "brief_theses для промта наблюдателя (наблюдатель остаётся)")
        return _advisory_phrase(
            "без сводки — сырой journal-хвост")

    if point_id == "chip-triage":
        if extra.get("used_fifo"):
            return _advisory_phrase(
                "порядок FIFO (исходный); triage неуверен")
        choice = extra.get("choice")
        return _advisory_phrase(
            "первым разбирать %s (остальные в исходном порядке)"
            % (choice or "?"))

    if point_id == "retro-card-hint":
        cats = extra.get("categories") or []
        if cats:
            return _advisory_phrase(
                "категория карточки-подсказки: %s (текст пишет командир)"
                % cats[0])
        return _advisory_phrase("без карточки-подсказки")

    if point_id == "run-queue-prio":
        if extra.get("used_original"):
            return _advisory_phrase(
                "исходный порядок плана (все low/absent)")
        return _advisory_phrase(
            "порядок по score desc (стабильный тай-брейк исходным)")

    # generic
    return _advisory_phrase("band=%s action не авто" % band)


def wrap_focus_hint_diff(point, advice_map, answers):
    """→ hint_block (описания выбранных slug + футер) или «без подсветки»."""
    criteria = point.get("criteria") or {}
    band = _band_of(advice_map)
    amin, amax = _CHOICE_CARDINALITY["focus-hint-diff"]
    choice_raw = None
    # choice из answers или advice
    for src in (answers or {}).values():
        if isinstance(src, dict) and "choice" in src:
            choice_raw = src.get("choice")
            break
    if choice_raw is None:
        for src in (advice_map or {}).values():
            if isinstance(src, dict) and "choice" in src:
                choice_raw = src.get("choice")
                break
    themes = []
    if band in ("mid", "high"):
        keys = validate_choice_against_criteria(
            choice_raw, criteria, amin, amax)
        # hint_block: ОПИСАНИЯ (values) выбранных slug-ключей
        if isinstance(criteria, dict):
            themes = [str(criteria[k]) for k in keys if k in criteria]
        else:
            themes = keys
        themes = filter_safe_themes(themes)
    if not themes or band in ("low", "absent"):
        return {
            "hint_block": "без подсветки",
            "advisory_text": advisory_text_for_point(
                "focus-hint-diff", advice_map, {"hint_block": "без подсветки"}),
        }
    block = "\n".join(themes + ["", HINT_BLOCK_FOOTER])
    return {
        "hint_block": block,
        "themes": themes,
        "advisory_text": advisory_text_for_point(
            "focus-hint-diff", advice_map, {"hint_block": block}),
    }


def wrap_observer_journal_brief(point, advice_map, answers, raw_tail):
    criteria = point.get("criteria") or {}
    band = _band_of(advice_map)
    amin, amax = _CHOICE_CARDINALITY["observer-journal-brief"]
    choice_raw = None
    for src in (answers or {}).values():
        if isinstance(src, dict) and "choice" in src:
            choice_raw = src.get("choice")
            break
    if choice_raw is None:
        for src in (advice_map or {}).values():
            if isinstance(src, dict) and "choice" in src:
                choice_raw = src.get("choice")
                break
    out = {"raw_tail": raw_tail if raw_tail is not None else ""}
    theses = []
    if band in ("mid", "high"):
        # стабильные id = ключи criteria-record
        theses = validate_choice_against_criteria(
            choice_raw, criteria, amin, amax)
    if theses:
        out["brief_theses"] = theses
    out["advisory_text"] = advisory_text_for_point(
        "observer-journal-brief", advice_map,
        {"brief_theses": theses})
    return out


def wrap_chip_triage(point, advice_map, answers, original_order):
    original_order = list(original_order or [])
    # динамический criteria-record: ключи = normalized ids
    criteria_obj = {cid: cid for cid in original_order}
    band = _band_of(advice_map)
    amin, amax = _CHOICE_CARDINALITY["chip-triage"]
    choice_raw = None
    for src in (answers or {}).values():
        if isinstance(src, dict) and "choice" in src:
            choice_raw = src.get("choice")
            break
    if choice_raw is None:
        for src in (advice_map or {}).values():
            if isinstance(src, dict) and "choice" in src:
                choice_raw = src.get("choice")
                break
    picked = []
    if band in ("mid", "high"):
        picked = validate_choice_against_criteria(
            choice_raw, criteria_obj, amin, amax)
    used_fifo = True
    if picked:
        choice = picked[0]
        rest = [x for x in original_order if x != choice]
        recommended = [choice] + rest
        used_fifo = False
    else:
        choice = None
        recommended = list(original_order)
    # инвариант: перестановка
    if sorted(recommended) != sorted(original_order):
        recommended = list(original_order)
        used_fifo = True
        choice = None
    return {
        "original_order": original_order,
        "recommended_order": recommended,
        "choice": choice,
        "used_fifo": used_fifo,
        "advisory_text": advisory_text_for_point(
            "chip-triage", advice_map,
            {"choice": choice, "used_fifo": used_fifo}),
    }


def wrap_run_queue_prio(point, advice_map, answers, original_order,
                        defer_below):
    original_order = list(original_order or [])
    # all low/absent → original
    all_weak = True
    scores = {}
    for rid in original_order:
        adv = (advice_map or {}).get(rid) or {}
        ans = (answers or {}).get(rid) or {}
        band = adv.get("band") or "absent"
        conf = adv.get("confidence")
        if conf is None:
            conf = ans.get("confidence")
        if band not in ("low", "absent") and conf is not None:
            try:
                if float(conf) >= float(defer_below):
                    all_weak = False
            except (TypeError, ValueError):
                pass
        elif band in ("mid", "high", "yes"):
            all_weak = False
        sc = ans.get("score")
        if sc is None:
            sc = adv.get("score")
        if sc is None:
            scores[rid] = None  # lowest
        else:
            try:
                scores[rid] = float(sc)
            except (TypeError, ValueError):
                scores[rid] = None

    if all_weak or not original_order:
        recommended = list(original_order)
        used_original = True
    else:
        # sort score desc; None = lowest; stable by original index
        def sort_key(rid):
            idx = original_order.index(rid)
            sc = scores.get(rid)
            if sc is None:
                return (1, 0.0, idx)  # after all numbered
            return (0, -sc, idx)

        recommended = sorted(original_order, key=sort_key)
        used_original = False

    # инвариант перестановки
    if sorted(recommended) != sorted(original_order):
        recommended = list(original_order)
        used_original = True

    return {
        "original_order": original_order,
        "recommended_order": recommended,
        "used_original": used_original,
        "advisory_text": advisory_text_for_point(
            "run-queue-prio", advice_map,
            {"used_original": used_original}),
    }


def wrap_retro_card_hint(point, advice_map, answers):
    criteria = point.get("criteria") or {}
    band = _band_of(advice_map)
    amin, amax = _CHOICE_CARDINALITY["retro-card-hint"]
    choice_raw = None
    for src in (answers or {}).values():
        if isinstance(src, dict) and "choice" in src:
            choice_raw = src.get("choice")
            break
    if choice_raw is None:
        for src in (advice_map or {}).values():
            if isinstance(src, dict) and "choice" in src:
                choice_raw = src.get("choice")
                break
    cats = []
    if band in ("mid", "high"):
        # card_category = выбранный ключ (dont|do|case|none)
        cats = validate_choice_against_criteria(
            choice_raw, criteria, amin, amax)
    out = {
        "advisory_text": advisory_text_for_point(
            "retro-card-hint", advice_map, {"categories": cats}),
    }
    if cats:
        out["card_category"] = cats[0]
    return out


def wrap_noul_point(point_id, advice_map):
    return {
        "advisory_text": advisory_text_for_point(point_id, advice_map),
    }


def apply_advisory_wrapper(point, advice_map, answers, state, raw_tail,
                           defer_below):
    """Пост-обработка результата для auto_question точек. Добавки в result."""
    pid = point.get("id")
    advice_map = force_advisory_actions(advice_map)
    state = coerce_structured_state(state)
    extra = {}
    if pid == "focus-hint-diff":
        extra = wrap_focus_hint_diff(point, advice_map, answers)
    elif pid == "observer-journal-brief":
        extra = wrap_observer_journal_brief(
            point, advice_map, answers, raw_tail)
    elif pid == "chip-triage":
        original = normalize_chip_input(state)
        extra = wrap_chip_triage(point, advice_map, answers, original)
    elif pid == "run-queue-prio":
        original = []
        if isinstance(state, list):
            for item in state:
                if isinstance(item, dict) and item.get("id") is not None:
                    original.append(str(item["id"]))
                elif isinstance(item, str):
                    original.append(item)
        extra = wrap_run_queue_prio(
            point, advice_map, answers, original, defer_below)
    elif pid == "retro-card-hint":
        extra = wrap_retro_card_hint(point, advice_map, answers)
    elif pid in ("scout-need", "mechanic-vs-fork", "repair-followup-or-fresh"):
        extra = wrap_noul_point(pid, advice_map)
    else:
        extra = {"advisory_text": advisory_text_for_point(pid, advice_map)}
    return advice_map, extra


def fail_open_advisory_extras(point, state, raw_tail):
    """Поля fallback ответа для advisory-точек при fail-open."""
    if not point:
        return {}
    pid = point.get("id")
    state = coerce_structured_state(state)
    extra = {"fallback": point.get("fallback")}
    if pid == "observer-journal-brief":
        extra["raw_tail"] = raw_tail if raw_tail is not None else ""
        extra["advisory_text"] = _advisory_phrase(
            "без сводки — fail-open, сырой journal-хвост")
    elif pid == "chip-triage":
        original = normalize_chip_input(state)
        extra["original_order"] = original
        extra["recommended_order"] = list(original)
        extra["advisory_text"] = _advisory_phrase(
            "порядок FIFO (fail-open)")
    elif pid == "run-queue-prio":
        original = []
        if isinstance(state, list):
            for item in state:
                if isinstance(item, dict) and item.get("id") is not None:
                    original.append(str(item["id"]))
                elif isinstance(item, str):
                    original.append(item)
        extra["original_order"] = original
        extra["recommended_order"] = list(original)
        extra["advisory_text"] = _advisory_phrase(
            "исходный порядок плана (fail-open)")
    elif pid == "focus-hint-diff":
        extra["hint_block"] = "без подсветки"
        extra["advisory_text"] = _advisory_phrase(
            "без подсветки (fail-open)")
    elif pid in ADVISORY_AUTO_POINTS:
        extra["advisory_text"] = _advisory_phrase(
            "fail-open → fallback точки; решение за командиром")
    return extra


def cmd_list_table(table_path):
    table = load_table(table_path)
    out = {
        "schemaVersion": table.get("schemaVersion"),
        "path": table_path,
        "points": table.get("points") or [],
    }
    sys.stdout.write(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
    return 0


def main(argv=None):
    orchlib.utf8_stdio()
    ap = _AdviseArgumentParser(
        description="Jev Decisions API CLI (fail-open, stdlib)",
        allow_abbrev=False)
    ap.add_argument("--list-table", action="store_true",
                    help="напечатать routing/jev-table.json и выйти")
    ap.add_argument("--table-path", default=TABLE_PATH,
                    help="путь к таблице точек (по умолчанию routing/jev-table.json)")
    ap.add_argument("--point",
                    help="id точки из таблицы; опционален при "
                         "--question/--questions-file (ad-hoc)")
    ap.add_argument("--caller", help="front/run-id вызывающего")
    st = ap.add_mutually_exclusive_group()
    st.add_argument("--state-text", "--state", dest="state_text",
                    help="state как строка (--state — алиас)")
    st.add_argument("--state-file", help="state из файла (текст или JSON)")
    ap.add_argument("--question", action="append", default=[],
                    help="id:type:instructions[::criteria_json] (repeatable)")
    ap.add_argument("--questions-file",
                    help="JSON-объект id→question")
    ap.add_argument("--defer-below", type=float, default=None)
    ap.add_argument("--confirm-below", type=float, default=None)
    ap.add_argument("--yes-at", type=float, default=None)
    ap.add_argument("--api-url", default=DECISIONS_URL)
    ap.add_argument("--key-path", default=None,
                    help="override пути к openrouter.key (тесты)")
    ap.add_argument("--session-id", default=None)
    ap.add_argument("--model", default=MODEL_ID)
    args = ap.parse_args(argv)

    if args.list_table:
        try:
            return cmd_list_table(args.table_path)
        except Exception as e:
            sys.stderr.write("list-table: %s\n" % e)
            # fail-open: пустой ответ + причина
            sys.stdout.write(json.dumps(
                {"points": [], "error": str(e)}, ensure_ascii=False) + "\n")
            return 0

    # --- advise path ---
    empty = {
        "ok": False,
        "answers": {},
        "advice": {},
        "error": None,
        "point": args.point,
        "caller": args.caller,
    }
    key = None
    state_dir = orchlib.find_state_dir()
    key_path = args.key_path or default_key_path()

    def fail_open(reason, **extra):
        msg = _mask_secrets(reason, key)
        result = dict(empty)
        result["error"] = msg
        result.update(extra)
        try:
            bands, confs, nouls = _journal_advice_fields(result.get("advice"))
            append_journal(state_dir, {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "caller": args.caller,
                "point": result.get("point"),
                "question_ids": list((result.get("advice") or {}).keys()),
                "band": bands,
                "confidence": confs or None,
                "noul": nouls or None,
                "cost": None,
                "status": "error",
                "error": msg,
            })
        except Exception:
            pass
        sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
        return 0

    if not args.caller:
        return fail_open("нужен --caller (или --list-table)")

    has_adhoc_qs = bool(args.question) or bool(args.questions_file)
    if not args.point and not has_adhoc_qs:
        return fail_open("нужны --point и --caller (или --list-table)")

    # --point опционален при ad-hoc (--question/--questions-file + caller)
    if args.point:
        try:
            table = load_table(args.table_path)
            point = point_by_id(table, args.point)
        except Exception as e:
            return fail_open("таблица/точка: %s" % e)
        journal_point = args.point
    else:
        point = {}
        journal_point = "ad-hoc"
    empty["point"] = journal_point
    # fallback в fail-open JSON — только для новых auto_question точек
    # (legacy ответы без лишнего поля)
    if point.get("auto_question") and point.get("fallback") is not None:
        empty["fallback"] = point.get("fallback")

    state, raw_tail = _load_state_raw(args)
    if state is None:
        return fail_open("нужен --state-text или --state-file")

    def fail_open_point(reason, **extra):
        """fail-open с полями advisory-точек (raw_tail / FIFO / fallback)."""
        merged = fail_open_advisory_extras(point, state, raw_tail)
        merged.update(extra)
        return fail_open(reason, **merged)

    try:
        questions = _load_questions(args)
    except Exception as e:
        return fail_open_point("questions: %s" % e)
    # Автосборка: только auto_question:true И нет явного --question/--questions-file
    # (пустой --questions-file={} тоже «явный» → побеждает, без автосборки)
    if (not questions and point.get("auto_question")
            and not has_adhoc_qs):
        try:
            questions = build_auto_questions(point, state)
        except Exception as e:
            return fail_open_point("auto_question: %s" % e)
    if not questions:
        # legacy путь (в т.ч. need-advisor без --question) — прежнее сообщение
        return fail_open("пустой questions: задайте --question или --questions-file")

    defer_below, confirm_below, yes_at = resolve_thresholds(
        point, args.defer_below, args.confirm_below, args.yes_at)

    try:
        key = load_openrouter_key(key_path)
    except Exception as e:
        return fail_open_point("ключ: %s" % e)

    try:
        response = jev_decisions(
            state, questions,
            model=args.model,
            key=key,
            session_id=args.session_id,
            api_url=args.api_url,
        )
    except Exception as e:
        return fail_open_point(_mask_secrets(e, key))

    answers = response.get("answers") or {}
    advice = {}
    for qid, ans in answers.items():
        if isinstance(ans, dict):
            advice[qid] = advise_answer(
                ans,
                defer_below=defer_below,
                confirm_below=confirm_below,
                yes_at=yes_at,
            )
        else:
            advice[qid] = {
                "band": "absent",
                "action": "defer",
                "reason": "решай сам",
            }

    wrap_extra = {}
    if point.get("auto_question") or point.get("id") in ADVISORY_AUTO_POINTS:
        advice, wrap_extra = apply_advisory_wrapper(
            point, advice, answers, state, raw_tail, defer_below)

    usage = response.get("usage") or {}
    cost = usage.get("cost")
    bands, confs, nouls = _journal_advice_fields(advice)
    result = {
        "ok": True,
        "point": journal_point,
        "caller": args.caller,
        "fallback": point.get("fallback"),
        "thresholds": {
            "defer_below": defer_below,
            "confirm_below": confirm_below,
            "yes_at": yes_at,
        },
        "model": response.get("model"),
        "answers": answers,
        "advice": advice,
        "usage": usage,
        "error": None,
    }
    result.update(wrap_extra)
    try:
        append_journal(state_dir, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "caller": args.caller,
            "point": journal_point,
            "question_ids": list(questions.keys()),
            "band": bands,
            "confidence": confs or None,
            "noul": nouls or None,
            "cost": cost,
            "status": "ok",
            "error": None,
        })
    except Exception as e:
        result["journal_error"] = _mask_secrets(e, key)

    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
