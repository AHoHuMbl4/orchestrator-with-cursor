#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Машиночитаемый статус прогона из его лога («бедный proof.json»).

Читает stream-json лог cursor-agent / раннера и печатает одну строку JSON:
  log, exit, verdict (OK|PROBLEMS|BLOCKED|NONE), report_present, retries, last_lines.

  python3 verdict.py <лог>

Кроссплатформенно, python3.6+, stdlib. Выход всегда 0 (нет traceback на
отсутствующем/битом логе — тогда exit=UNKNOWN, report_present=false).
"""
from __future__ import print_function

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

TAIL_CHARS = 16000  # ~8000 по доктрине; запас — result-событие в хвосте вытесняет assistant
LAST_LINES_CHARS = 200

# Последняя строка-вердикт (не «Вердикт: OK/PROBLEMS» в прозе):
#   Вердикт/Итог: OK[.]; Вердикт: PROBLEMS|BLOCKED...; **OK**; PROBLEMS:|BLOCKED:
#   Популярный суффикс «| PROBLEMS: нет/—/none» → OK (явное «нет», не PROBLEMS).
VERDICT_OK_RE = re.compile(
    r"(?i)(?:(?:вердикт|итог)\s*:\s*\**\s*ok\**\s*\.?\s*$|^\s*\*\*\s*ok\s*\*\*\s*\.?\s*$)"
)
VERDICT_OK_EMPTY_PROBLEMS_RE = re.compile(
    r"(?i)^\s*(?:вердикт|итог)\s*:\s*\**\s*ok\**\s*"
    r"\|\s*problems\s*:\s*(?:нет|—|–|none)\s*\.?\s*$"
)
VERDICT_BAD_RE = re.compile(
    r"(?i)(?:(?:вердикт|итог)\s*:\s*\**\s*(problems|blocked)\b|^\s*\**\s*(problems|blocked)\s*:)"
)
EXIT_RE = re.compile(r"^EXIT=(.*)\s*$")
RETRY_RE = re.compile(r"^RETRY=")


def _read_text(path):
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read()


def _parse_exit_retries(text):
    exit_code = "UNKNOWN"
    retries = 0
    for line in text.splitlines():
        m = EXIT_RE.match(line.strip())
        if m:
            exit_code = m.group(1).strip() or "UNKNOWN"
        if RETRY_RE.match(line.strip()):
            retries += 1
    return exit_code, retries


def _assistant_texts(text):
    """Все текстовые куски assistant из stream-json (одна строка = одно событие)."""
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except (ValueError, TypeError):
            continue
        if not isinstance(obj, dict) or obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        parts = []
        for c in content:
            if isinstance(c, dict) and c.get("type") == "text":
                t = c.get("text")
                if isinstance(t, str) and t:
                    parts.append(t)
        if parts:
            out.append("\n".join(parts))
    return out


def _report_present(text, report_text):
    if report_text:
        return True
    tail = text[-TAIL_CHARS:] if text else ""
    return ('"type":"assistant"' in tail) and ("text" in tail)


def _extract_verdict(report_text):
    if not report_text:
        return "NONE"
    found = None
    for line in report_text.splitlines():
        s = line.strip()
        if not s:
            continue
        if VERDICT_OK_EMPTY_PROBLEMS_RE.search(s) or VERDICT_OK_RE.search(s):
            found = "OK"
            continue
        m = VERDICT_BAD_RE.search(s)
        if m:
            token = next((g for g in m.groups() if g), "").upper()
            if token in ("PROBLEMS", "BLOCKED"):
                found = token
    return found if found else "NONE"


def _last_lines(report_text):
    if not report_text:
        return ""
    flat = re.sub(r"\s+", " ", report_text).strip()
    if len(flat) > LAST_LINES_CHARS:
        flat = flat[-LAST_LINES_CHARS:]
    return flat


def analyze(path):
    result = {
        "log": path,
        "exit": "UNKNOWN",
        "verdict": "NONE",
        "report_present": False,
        "retries": 0,
        "last_lines": "",
    }
    try:
        if not os.path.isfile(path):
            return result
        text = _read_text(path)
    except (OSError, IOError):
        return result

    exit_code, retries = _parse_exit_retries(text)
    texts = _assistant_texts(text)
    report = texts[-1] if texts else ""
    present = _report_present(text, report)
    verdict = _extract_verdict(report) if present else "NONE"

    result["exit"] = exit_code
    result["retries"] = retries
    result["report_present"] = bool(present)
    result["verdict"] = verdict
    result["last_lines"] = _last_lines(report)
    return result


def main(argv=None):
    orchlib.utf8_stdio()
    argv = argv if argv is not None else sys.argv[1:]
    path = argv[0] if argv else ""
    if not path:
        path = ""
    out = analyze(path)
    sys.stdout.write(json.dumps(out, ensure_ascii=False, separators=(",", ": ")))
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
