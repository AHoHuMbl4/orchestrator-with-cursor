#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""CLI-воронка квитанций §3 (write|validate).

write делегирует ТОЛЬКО в orchlib.write_probe_receipt — второй генератор запрещён.
validate → parse_probe_receipt, exit 0/1 + reason.

Прецедент воронки: bin/write-compass.py.
Кроссплатформенно, python3.6+, stdlib.
"""
from __future__ import print_function

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402


def _cmd_write(args):
    if args.oracle is not None and args.oracle_match is not None:
        sys.stderr.write("укажите либо --oracle, либо --oracle-match, не оба\n")
        return 1
    if args.oracle is not None:
        oracle_match = (int(args.exit) == int(args.oracle))
    elif args.oracle_match is not None:
        om = str(args.oracle_match).strip().lower()
        if om not in ("true", "false"):
            sys.stderr.write("--oracle-match: true|false\n")
            return 1
        oracle_match = (om == "true")
    else:
        sys.stderr.write("нужен --oracle N или --oracle-match true|false\n")
        return 1

    ok, info = orchlib.write_probe_receipt(
        probe=args.probe,
        cmd=args.cmd,
        exit_code=args.exit,
        oracle_match=oracle_match,
        critic_id=args.critic_id,
        artifact=args.artifact,
        run_id=args.run_id,
        path=args.file,
    )
    if not ok:
        sys.stderr.write("write failed: %s\n" % info)
        return 1
    sys.stdout.write("%s\n" % info)
    return 0


def _cmd_validate(args):
    if not args.file and not args.run_id:
        sys.stderr.write("нужен --file PATH или --run-id X\n")
        return 1
    text = None
    art_path = None
    run_id = args.run_id
    if args.file:
        path = os.path.normpath(os.path.abspath(args.file))
        if not os.path.isfile(path):
            sys.stderr.write("file not found: %s\n" % path)
            return 1
        text = orchlib._read_text_silent(path)
    else:
        paths = orchlib.find_probe_receipts(run_id)
        if not paths:
            sys.stderr.write("no receipt for run-id: %s\n" % run_id)
            return 1
        # берём первую (своя dir приоритетнее в find_probe_receipts)
        text = orchlib._read_text_silent(paths[0])
    if run_id:
        art_path = orchlib.wave_probe_artifact_path(run_id)
    ok, reason = orchlib.parse_probe_receipt(
        text, artifact_path=art_path, run_id=run_id)
    if ok:
        sys.stdout.write("%s\n" % reason)
        return 0
    sys.stderr.write("%s\n" % reason)
    return 1


def main(argv=None):
    orchlib.utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Воронка квитанций §3: write | validate")
    sub = ap.add_subparsers(dest="cmd_name")

    w = sub.add_parser("write", help="записать квитанцию через orchlib writer")
    w.add_argument("--probe", required=True)
    w.add_argument("--cmd", required=True)
    w.add_argument("--exit", type=int, required=True)
    w.add_argument("--oracle", type=int, default=None,
                   help="ожидаемый exit → oracle_match=(exit==oracle)")
    w.add_argument("--oracle-match", default=None,
                   help="true|false")
    w.add_argument("--critic-id", required=True)
    w.add_argument("--artifact", required=True)
    w.add_argument("--run-id", required=True)
    w.add_argument("--file", default=None, help="путь probe-receipt.md")
    w.set_defaults(func=_cmd_write)

    v = sub.add_parser("validate", help="проверить квитанцию parse_probe_receipt")
    v.add_argument("--run-id", default=None)
    v.add_argument("--file", default=None)
    v.set_defaults(func=_cmd_validate)

    args = ap.parse_args(argv)
    if not getattr(args, "cmd_name", None):
        ap.print_help(sys.stderr)
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
