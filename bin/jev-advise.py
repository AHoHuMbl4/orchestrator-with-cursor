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
      [--state-text TEXT | --state-file PATH]
      [--question id:type:instructions… | --questions-file PATH]
      [--defer-below N] [--confirm-below N] [--yes-at N]
      [--api-url URL] [--key-path PATH] [--session-id ID]

Таблица маршрутизации: routing/jev-table.json (обязательна).
Журнал: <state>/jev-calls.jsonl.
Кроссплатформенно, python3.6+, только stdlib.
"""
from __future__ import print_function

import argparse
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
    if args.state_text is not None:
        return args.state_text
    if args.state_file is not None:
        with open(args.state_file, "r", encoding="utf-8") as f:
            text = f.read()
        # JSON object/array as state if file is valid JSON structured
        try:
            parsed = json.loads(text)
            if isinstance(parsed, (dict, list)):
                return parsed
        except (ValueError, TypeError):
            pass
        return text
    return None


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
    ap = argparse.ArgumentParser(
        description="Jev Decisions API CLI (fail-open, stdlib)")
    ap.add_argument("--list-table", action="store_true",
                    help="напечатать routing/jev-table.json и выйти")
    ap.add_argument("--table-path", default=TABLE_PATH,
                    help="путь к таблице точек (по умолчанию routing/jev-table.json)")
    ap.add_argument("--point", help="id точки маршрутизации из таблицы")
    ap.add_argument("--caller", help="front/run-id вызывающего")
    st = ap.add_mutually_exclusive_group()
    st.add_argument("--state-text", help="state как строка")
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
                "point": args.point,
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

    if not args.point or not args.caller:
        return fail_open("нужны --point и --caller (или --list-table)")

    try:
        table = load_table(args.table_path)
        point = point_by_id(table, args.point)
    except Exception as e:
        return fail_open("таблица/точка: %s" % e)

    state = _load_state(args)
    if state is None:
        return fail_open("нужен --state-text или --state-file")

    try:
        questions = _load_questions(args)
    except Exception as e:
        return fail_open("questions: %s" % e)
    if not questions:
        return fail_open("пустой questions: задайте --question или --questions-file")

    defer_below, confirm_below, yes_at = resolve_thresholds(
        point, args.defer_below, args.confirm_below, args.yes_at)

    try:
        key = load_openrouter_key(key_path)
    except Exception as e:
        return fail_open("ключ: %s" % e)

    try:
        response = jev_decisions(
            state, questions,
            model=args.model,
            key=key,
            session_id=args.session_id,
            api_url=args.api_url,
        )
    except Exception as e:
        return fail_open(_mask_secrets(e, key))

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

    usage = response.get("usage") or {}
    cost = usage.get("cost")
    bands, confs, nouls = _journal_advice_fields(advice)
    result = {
        "ok": True,
        "point": args.point,
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
    try:
        append_journal(state_dir, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "caller": args.caller,
            "point": args.point,
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
