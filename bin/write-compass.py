#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-воронка записи compass с проверкой лимита (мгновенный enforcement).

  write-compass.py --path <compass> (--text-file <файл> | --stdin) [--session <sid>]

Exit: 0 записано; 1 ошибка чтения/записи; 2 превышение лимита (файл не
меняется, pending-флаг); 3 путь не является compass-путём состояния.
Кроссплатформенно, python3.6+, stdlib.
"""
from __future__ import print_function

import argparse
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


def main():
    orchlib.utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Атомарная запись compass с проверкой лимита символов")
    ap.add_argument("--path", required=True, help="путь к compass.md")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text-file", help="файл с текстом compass")
    src.add_argument("--stdin", action="store_true", help="читать текст из stdin")
    ap.add_argument("--session", default=None, help="id сессии для pending-гарда")
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
    p = orchlib.load_params()

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
            sys.stderr.write(
                "⛔ COMPASS ПРЕВЫШЕН: %s: %d символов при лимите %d. "
                "Ужми: историю — в артефакты.\n" % (path, size, limit))
            overflow = {"path": path, "size": size, "limit": limit}
            orchlib.emit_pending_compass_guard(args.session, [overflow])
            return 2
        sys.stderr.write("запись не удалась: %s\n" % info.get("error", "unknown"))
        return 1

    sys.stdout.write("written %s %d chars\n" % (path, size))
    return 0


if __name__ == "__main__":
    sys.exit(main())
