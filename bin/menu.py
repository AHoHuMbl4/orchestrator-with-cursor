#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Детерминированное меню параметров: скрипт пишет params.json/compass.md сам.

Зачем скрипт, а не «попросить агента словами»: модель может «не согласиться» и
не применить. Здесь применение — это запись файла с валидацией; агент только
запускает скрипт и показывает вывод. Дальше хук сам доносит новые значения.

  python3 menu.py --show [--session <id>]
  python3 menu.py --set review.reviewers_per_diff=5 execution.parallel_per_task=2
  python3 menu.py --task "текст задачи" --session <id>
  python3 menu.py --task-file файл --session <id>
  python3 menu.py --reset-template                   # аварийно: восстановить шаблон из kit
  python3 menu.py --interactive                      # терминальный опрос (локально)

Сессия: --session <id> либо env ORCH_SESSION_ID (id из хук-вклейки «Сессия: <id>»).
Глобальный .orchestration/compass.md — ЧИСТЫЙ ШАБЛОН; править только в панели
(«Расширенные»). Рабочая задача сессии — только в
.orchestration/sessions/<id>/compass.md.

Ключи --set: любые из схемы (см. orchlib.DEFAULTS/RANGES). Кроссплатформенно,
python3.6+, stdlib. Выход: 0 — применено, 2 — ошибка валидации.

execution.executor: auto | cursor-cloud | subagents.
  auto         — cursor-cloud при наличии ключа API, иначе вопрос владельцу;
  cursor-cloud — только удалённый API Cursor;
  subagents    — субагенты движка (по явному «да» владельца).
Локальный cursor-agent в продукте не используется.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

TASK_NEEDS_SESSION_MSG = (
    "задача сессии требует --session <id> (или env ORCH_SESSION_ID). "
    "Глобальный compass — шаблон; редактирование — только панель, раздел «Расширенные». "
    "Не знаю сессию? Смотри .orchestration/sessions/ или хук-вклейку (Сессия: <id>)"
)

GLOBAL_TEMPLATE_REFUSED_MSG = (
    "Редактирование общего шаблона — только панель, раздел „Расширенные“. "
    "Задача пишется в compass сессии (--session <id> или env ORCH_SESSION_ID)"
)


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


def extract_opts(args):
    """Вытащить --session <id> и устаревший --global-template; вернуть (rest, session, global_template).

    --global-template распознаётся (без unknown-флага), но запись по нему всегда отказ.
    """
    rest = []
    session = None
    global_template = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--session":
            if i + 1 >= len(args):
                raise ValueError("--session требует <id>")
            session = args[i + 1]
            i += 2
        elif a == "--global-template":
            global_template = True
            i += 1
        else:
            rest.append(a)
            i += 1
    return rest, session, global_template


def resolve_session_id(session_cli):
    """--session <sid> > env ORCH_SESSION_ID."""
    if session_cli:
        return session_cli
    env = os.environ.get("ORCH_SESSION_ID")
    if env and env.strip():
        return env.strip()
    return None


def show(session_cli=None):
    p = orchlib.load_params()
    sid = resolve_session_id(session_cli)
    if sid:
        path = orchlib.session_compass_path(p, sid)
        print("Показан compass сессии %s" % sid)
    else:
        path = orchlib.compass_path(p)
        print("Общий стартовый шаблон (редактирование — панель, Расширенные)")
    print(orchlib.params_summary(p))
    print("compass: %s" % path)
    try:
        with open(path, "r", encoding="utf-8") as f:
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


def set_task(text, task_file, session_cli=None, global_template=False):
    p = orchlib.load_params()
    if task_file:
        with open(task_file, "r", encoding="utf-8") as f:
            text = f.read()
    if not text or not text.strip():
        print("ошибка: задача пустая", file=sys.stderr)
        return 2
    if global_template:
        print(GLOBAL_TEMPLATE_REFUSED_MSG, file=sys.stderr)
        return 2
    sid = resolve_session_id(session_cli)
    if sid:
        path = orchlib.session_compass_path(p, sid)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        print("ЗАДАЧА записана: %s (%d символов)" % (path, len(text)))
        return 0
    print(TASK_NEEDS_SESSION_MSG, file=sys.stderr)
    return 2


def reset_template():
    p = orchlib.load_params()
    src = os.path.join(orchlib.KIT_DIR, "compass.md")
    dst = orchlib.compass_path(p)
    if not os.path.isfile(src):
        print("ошибка: нет kit-шаблона: %s" % src, file=sys.stderr)
        return 2
    with open(src, "r", encoding="utf-8") as f:
        content = f.read()
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(content)
    print("Глобальный шаблон-compass восстановлен из %s" % src)
    print("Старое содержимое %s перезаписано." % dst)
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
    raw = sys.argv[1:]
    if not raw or raw[0] in ("-h", "--help"):
        sys.stdout.write(__doc__ + "\n")
        return 0
    try:
        args, session_cli, global_template = extract_opts(raw)
    except ValueError as e:
        print("ошибка: %s" % e, file=sys.stderr)
        return 2
    if not args:
        sys.stdout.write(__doc__ + "\n")
        return 0
    cmd = args[0]
    try:
        if cmd == "--show":
            return show(session_cli=session_cli)
        if cmd == "--set":
            if len(args) < 2:
                raise ValueError("--set требует key=value ...")
            return apply_sets(args[1:])
        if cmd == "--task":
            if len(args) < 2:
                raise ValueError("--task требует текст в кавычках")
            return set_task(" ".join(args[1:]), None,
                            session_cli=session_cli,
                            global_template=global_template)
        if cmd == "--task-file":
            if len(args) < 2:
                raise ValueError("--task-file требует путь")
            return set_task(None, args[1],
                            session_cli=session_cli,
                            global_template=global_template)
        if cmd == "--reset-template":
            return reset_template()
        if cmd == "--interactive":
            return interactive()
        raise ValueError("неизвестная подкоманда: %s" % cmd)
    except ValueError as e:
        print("ошибка: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
