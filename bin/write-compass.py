#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-воронка записи compass с проверкой лимита (мгновенный enforcement).

  write-compass.py --path <compass> (--text-file <файл> | --stdin)
                   [--session <sid>] [--expect-refuse]

Exit: 0 записано; 1 ошибка чтения/записи; 2 превышение лимита (файл не
меняется, pending-флаг); 3 путь не является compass-путём состояния.
Кроссплатформенно, python3.6+, stdlib.
"""
from __future__ import print_function

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402


def _read_text(args):
    if args.text_file:
        with open(args.text_file, "r", encoding="utf-8") as f:
            return f.read()
    if args.stdin:
        return sys.stdin.read()
    return None


def _refuse_overflow(path, attempt, limit):
    """Честный refuse-payload: attempt=N (отказ), file=M (текущий размер)."""
    file_m = orchlib.compass_char_size(path)
    if file_m is None:
        file_m = 0
    return {"path": path, "limit": limit, "attempt": attempt, "file": file_m}


def _emit_test_flag_and_consume(session, overflow):
    """Пишет test-флаг и сразу удаляет — обычным сессиям не рассылается."""
    payload = {"overflows": [overflow], "test": True}
    written = []
    try:
        flag_st = orchlib.state_path("pending_compass_guard.json")
        d = os.path.dirname(flag_st)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(flag_st, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        written.append(flag_st)
        if session:
            sdir = orchlib.session_dir(session)
            os.makedirs(sdir, exist_ok=True)
            flag_g = os.path.join(sdir, "pending_compass_guard.json")
            with open(flag_g, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
            written.append(flag_g)
    except Exception as e:
        sys.stderr.write("compass test-flag write failed: %s\n" % e)
    for fp in written:
        try:
            os.unlink(fp)
        except Exception:
            pass


def main():
    orchlib.utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Атомарная запись compass с проверкой лимита символов")
    ap.add_argument("--path", required=True, help="путь к compass.md")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text-file", help="файл с текстом compass")
    src.add_argument("--stdin", action="store_true", help="читать текст из stdin")
    ap.add_argument("--session", default=None, help="id сессии для pending-гарда")
    ap.add_argument(
        "--expect-refuse", action="store_true",
        help="тест-режим: ожидаемый отказ — test-флаг потребляется, ordinary pending не рассылается")
    args = ap.parse_args()

    try:
        text = _read_text(args)
    except Exception as e:
        sys.stderr.write("не удалось прочитать текст: %s\n" % e)
        return 1
    if text is None:
        sys.stderr.write("нужен --text-file или --stdin\n")
        return 1

    path = os.path.normpath(os.path.abspath(args.path))
    try:
        p = orchlib.load_params()
    except ValueError as e:
        sys.stderr.write("%s\n" % e)
        return 1

    if not orchlib.is_compass_path(path):
        sys.stderr.write(
            "путь не является compass-путём состояния "
            "(общий / sessions/*/compass.md / fronts/**/compass.md): %s\n" % path)
        return 3

    ok, info = orchlib.write_compass_checked(p, path, text)
    size = info.get("size", len(text))
    limit = info.get("limit", 0)

    if not ok:
        if size > limit:
            overflow = _refuse_overflow(path, size, limit)
            msg = orchlib.format_compass_overflows([overflow])
            if msg:
                sys.stderr.write(msg + "\n")
            if args.expect_refuse:
                _emit_test_flag_and_consume(args.session, overflow)
            else:
                orchlib.emit_pending_compass_guard(args.session, [overflow])
            return 2
        sys.stderr.write("запись не удалась: %s\n" % info.get("error", "unknown"))
        return 1

    sys.stdout.write("written %s %d chars\n" % (path, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
