#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Детерминированное меню параметров: скрипт пишет params.json/compass.md сам.

Зачем скрипт, а не «попросить агента словами»: модель может «не согласиться» и
не применить. Здесь применение — это запись файла с валидацией; агент только
запускает скрипт и показывает вывод. Дальше хук сам доносит новые значения.

  python3 menu.py --show
  python3 menu.py --set review.reviewers_per_diff=5 execution.parallel_per_task=2
  python3 menu.py --task "текст задачи"          # или --task-file файл
  python3 menu.py --interactive                   # терминальный опрос (локально)

Ключи --set: любые из схемы (см. orchlib.DEFAULTS/RANGES). Кроссплатформенно,
python3.6+, stdlib. Выход: 0 — применено, 2 — ошибка валидации.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402


def parse_set(pairs):
    out = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError("непонятный аргумент (нужно key=value): %r" % pair)
        key, _, val = pair.partition("=")
        sec, _, field = key.partition(".")
        if sec not in orchlib.DEFAULTS or field not in orchlib.DEFAULTS[sec]:
            raise ValueError("неизвестный ключ: %s (допустимы: %s)" % (
                key, ", ".join(sorted("%s.%s" % (s, f)
                                      for s in orchlib.DEFAULTS
                                      for f in orchlib.DEFAULTS[s]))))
        default = orchlib.DEFAULTS[sec][field]
        if isinstance(default, bool):
            val = val.strip().lower() in ("true", "1", "yes", "да")
        elif isinstance(default, int):
            try:
                val = int(val)
            except ValueError:
                raise ValueError("%s: ожидается целое, получено %r" % (key, val))
        out.setdefault(sec, {})[field] = val
    return out


def show():
    p = orchlib.load_params()
    print(orchlib.params_summary(p))
    print("compass: %s" % orchlib.compass_path(p))
    try:
        with open(orchlib.compass_path(p), "r", encoding="utf-8") as f:
            head = "".join(f.readlines()[:12])
        print("--- compass (первые строки) ---")
        print(head.rstrip())
    except Exception:
        print("(compass не найден)")
    return 0


def apply_sets(pairs):
    p = orchlib.load_params()
    patch = parse_set(pairs)
    for sec, fields in patch.items():
        p.setdefault(sec, {}).update(fields)
    orchlib.save_params(p)  # валидация внутри; ValueError = выход 2
    print("ПРИМЕНЕНО:")
    for sec, fields in patch.items():
        for k, v in fields.items():
            print("  %s.%s = %r" % (sec, k, v))
    print("Хук сверки донесёт значения при следующем сообщении/тике.")
    return 0


def set_task(text, task_file):
    p = orchlib.load_params()
    if task_file:
        with open(task_file, "r", encoding="utf-8") as f:
            text = f.read()
    if not text or not text.strip():
        print("ошибка: задача пустая", file=sys.stderr)
        return 2
    path = orchlib.compass_path(p)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    print("ЗАДАЧА записана: %s (%d символов)" % (path, len(text)))
    return 0


QUESTIONS = [
    ("execution.parallel_per_task", "Параллельных агентов на 1 задачу"),
    ("review.reviewers_per_diff", "Критиков на каждый дифф"),
    ("review.max_rounds", "Круги ревью до схождения"),
    ("execution.timeout_s", "Таймаут прогона, сек"),
    ("reground.every_min", "Сверка курса, каждые N мин"),
]


def interactive():
    p = orchlib.load_params()
    patch = {}
    for key, title in QUESTIONS:
        sec, _, field = key.partition(".")
        cur = p.get(sec, {}).get(field)
        raw = input("%s [%s]: " % (title, cur)).strip()
        if not raw or raw == str(cur):
            continue
        try:
            patch.setdefault(sec, {})[field] = int(raw)
        except ValueError:
            print("  (пропущено: не число)")
    if not patch:
        print("без изменений")
        return 0
    for sec, fields in patch.items():
        p.setdefault(sec, {}).update(fields)
    try:
        orchlib.save_params(p)
    except ValueError as e:
        print("ошибка: %s" % e, file=sys.stderr)
        return 2
    print("ПРИМЕНЕНО: %s" % patch)
    return 0


def main():
    orchlib.utf8_stdio()
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(__doc__ + "\n")
        return 0
    cmd = args[0]
    try:
        if cmd == "--show":
            return show()
        if cmd == "--set":
            if len(args) < 2:
                raise ValueError("--set требует key=value ...")
            return apply_sets(args[1:])
        if cmd == "--task":
            if len(args) < 2:
                raise ValueError("--task требует текст в кавычках")
            return set_task(" ".join(args[1:]), None)
        if cmd == "--task-file":
            if len(args) < 2:
                raise ValueError("--task-file требует путь")
            return set_task(None, args[1])
        if cmd == "--interactive":
            return interactive()
        raise ValueError("неизвестная подкоманда: %s" % cmd)
    except ValueError as e:
        print("ошибка: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
