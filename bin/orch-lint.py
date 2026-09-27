#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Линт инвариантов оркестрации: rules/manifest + роли + SHA-ссылки.

Проверяет весь набор:
  - manifest: у каждой карточки id/type/кому/когда/категория/run_ref/born_at;
    hit — число; дублей id нет
  - файлы карточек соответствуют манифесту (и наоборот)
  - роли: _index.md ↔ файлы на диске
  - SHA-ссылки в карточках (золотая ссылка / пример) живы в git кита
  - deep: cause-cleared (ослабление детекторов без CAUSE-CLEARED) и
    иммутабельность born_at в истории rules/manifest.json после baseline

Выход: список нарушений в stdout (по строке); exit 1 при любом, 0 если чисто.

Usage:
  python3 bin/orch-lint.py [--kit-dir DIR]
"""
from __future__ import print_function

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

# Baseline cause-cleared/born_at: HEAD кита до feat-коммита гейта (C3-LINT).
# Дубль: runs/C3-LINT/baseline.sha в state-сессии волны.
ORCH_LINT_BASELINE_SHA = "78f1481e7a623cb441564f727b781c3b6eb5a774"


def main(argv=None):
    orchlib.utf8_stdio()
    ap = argparse.ArgumentParser(description="Orchestration invariants lint")
    ap.add_argument(
        "--kit-dir", default=None,
        help="корень кита (по умолчанию — родитель bin/)")
    args = ap.parse_args(argv)
    kit_dir = args.kit_dir
    if kit_dir:
        kit_dir = os.path.abspath(kit_dir)
    # CLI — deep=True (git-walk); health зовёт orch_lint_violations() без deep.
    viols = orchlib.orch_lint_violations(
        kit_dir=kit_dir, deep=True, baseline=ORCH_LINT_BASELINE_SHA)
    for v in viols:
        sys.stdout.write(v + "\n")
    if viols:
        sys.stdout.write("orch-lint: %d violation(s)\n" % len(viols))
        return 1
    sys.stdout.write("orch-lint: OK\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
