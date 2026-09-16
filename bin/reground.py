#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-ground движок: периодически возвращает агента к заданию и параметрам.

ГЛАВНЫЙ ТРИГГЕР — ВРЕМЯ (every_min), запасной — счётчик вызовов (every_n_calls).
Компакшн — лишь дополнительная точка (SessionStart после compact), не основная:
агент дрейфит задолго до компакшна, поэтому поправка идёт по таймеру.

Подкоманды:
  session-start  — вклейка params+compass в начало сессии (и после компакшна)
  post-tool      — счётчик/таймер на каждый tool call (Claude/Codex hooks)
  heartbeat      — wall-clock для Kimi SessionHeartbeat (60-секундный тик)
  stop           — зарезервировано, молчит (не мешает завершению turn)

Формат вывода: --format json (Claude/Codex: hookSpecificOutput.additionalContext)
или text (Kimi: stdout дописывается в контекст при exit 0).

Кроссплатформенно (Windows: python/py -3), python3.6+, только stdlib.
Выход всегда 0, кроме реальных ошибок чтения (тогда 0 тоже — хук не должен
ронять сессию; ошибка пишется в stderr).
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

NUDGE_TEXT = (
    "Сверка курса (автоматическая, с последней сверки прошло {minutes} мин, "
    "{calls} вызовов инструментов).\n"
    "{summary}\n"
    "Задание и критерий приёмки: {compass}\n"
    "Перечитай compass-файл сейчас и коротко подтверди: (1) текущая цель; "
    "(2) текущий шаг; (3) ведёт ли следующий шаг к цели. "
    "При расхождении вернись к последнему подтверждённому шагу и отметь это."
)

SESSION_TEXT = (
    "Контекст пачки (вклеен автоматически).\n"
    "{summary}\n\n"
    "Задание (compass, полный текст):\n"
    "{compass_text}\n\n"
    "Правило курса: сверка с compass идёт периодически по таймеру; "
    "перед каждой волной/пачкой — перечитай compass и params заново."
)


def emit(fmt, event, text):
    """json — для Claude/Codex hooks; text — для Kimi (stdout append)."""
    if fmt == "text":
        sys.stdout.write(text)
        return
    payload = {"hookSpecificOutput": {"hookEventName": event, "additionalContext": text}}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False))


def safe_name(s):
    return re.sub(r"[^A-Za-z0-9_.-]", "_", s)[:80] or "default"


def counter_file(session_id):
    d = os.path.join(orchlib.find_state_dir(), "counters")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, safe_name(session_id) + ".json")


def read_stdin_json():
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def enabled(p):
    """Тумблер из params: False — режим оркестрации выключен, хуки молчат."""
    return bool(p.get("orchestration", {}).get("enabled", True))


def cmd_session_start(engine, fmt):
    p0 = orchlib.load_params()
    if not enabled(p0):
        return
    p = p0
    text = SESSION_TEXT.format(
        summary=orchlib.params_summary(p),
        compass_text=orchlib.read_compass(p),
    )[:9500]
    emit(fmt, "SessionStart", text)


def cmd_post_tool(engine, fmt):
    p0 = orchlib.load_params()
    if not enabled(p0):
        return
    ev = read_stdin_json()
    session_id = ev.get("session_id") or ev.get("sessionId") or "default"
    p = orchlib.load_params()
    rg = p.get("reground", {})
    every_min = int(rg.get("every_min", 7))
    every_calls = int(rg.get("every_n_calls", 40))

    cf = counter_file(session_id)
    now = time.time()
    data = {"start_ts": now, "calls": 0, "calls_at_nudge": 0, "last_nudge_ts": now}
    try:
        with open(cf, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass

    data["calls"] = int(data.get("calls", 0)) + 1
    elapsed = now - float(data.get("last_nudge_ts", now))
    since_nudge_calls = data["calls"] - int(data.get("calls_at_nudge", 0))

    triggered = None
    if elapsed >= every_min * 60:
        triggered = "%d мин" % int(elapsed // 60)
    elif since_nudge_calls >= every_calls:
        triggered = "%d вызовов" % since_nudge_calls

    if triggered:
        text = NUDGE_TEXT.format(
            minutes=int(elapsed // 60), calls=since_nudge_calls,
            summary=orchlib.params_summary(p),
            compass=orchlib.compass_path(p),
        )[:9000]
        emit(fmt, "PostToolUse", text)
        data["last_nudge_ts"] = now
        data["calls_at_nudge"] = data["calls"]

    try:
        with open(cf, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write("counter write failed: %s\n" % e)


def cmd_heartbeat(engine, fmt):
    p0 = orchlib.load_params()
    if not enabled(p0):
        return
    """Kimi SessionHeartbeat: на входе uptime_ms; таймер — по аптайму (монотонно)."""
    ev = read_stdin_json()
    p = orchlib.load_params()
    every_min = int(p.get("reground", {}).get("every_min", 7))
    uptime_ms = ev.get("uptime_ms")
    if not isinstance(uptime_ms, (int, float)):
        return
    cf = os.path.join(orchlib.find_state_dir(), "counters", "heartbeat.json")
    os.makedirs(os.path.dirname(cf), exist_ok=True)
    last = None
    try:
        with open(cf, "r", encoding="utf-8") as f:
            last = json.load(f).get("uptime_ms")
    except Exception:
        pass
    if last is None:
        last = uptime_ms  # первый тик: инициализация без напоминания
    elif (uptime_ms - last) >= every_min * 60 * 1000:
        text = NUDGE_TEXT.format(
            minutes=int((uptime_ms - last) // 60000),
            calls=0,
            summary=orchlib.params_summary(p),
            compass=orchlib.compass_path(p),
        )[:9000]
        emit(fmt, "SessionHeartbeat", text)
        last = uptime_ms
    try:
        with open(cf, "w", encoding="utf-8") as f:
            json.dump({"uptime_ms": last}, f)
    except Exception as e:
        sys.stderr.write("counter write failed: %s\n" % e)


def cmd_prompt_submit(engine, fmt):
    p0 = orchlib.load_params()
    if not enabled(p0):
        return
    """UserPromptSubmit: вклеить параметры при первом сообщении и при ИЗМЕНЕНИИ
    params/compass (mtime+размер). Гарантия: сообщение владельца обрабатывается
    вместе с актуальными значениями, даже если агент «не согласен»."""
    p = orchlib.load_params()
    marks = {}
    for name, path in (("params", orchlib.params_file()),
                       ("compass", orchlib.compass_path(p))):
        try:
            st = os.stat(path)
            marks[name] = "%d:%d" % (st.st_mtime_ns if hasattr(os.stat_result, "st_mtime_ns") else int(st.st_mtime), st.st_size)
        except OSError:
            marks[name] = "none"
    mf = os.path.join(orchlib.find_state_dir(), "counters", "prompt-submit.json")
    os.makedirs(os.path.dirname(mf), exist_ok=True)
    prev = {}
    try:
        with open(mf, "r", encoding="utf-8") as f:
            prev = json.load(f)
    except Exception:
        pass
    if prev == marks:
        return  # не менялось — молчим, не засоряем контекст
    changed = [n for n in marks if prev.get(n) != marks[n]]
    what = " (изменились: %s)" % ", ".join(changed) if prev else ""
    text = (
        "Актуальные параметры пачки{what}:\n{summary}\n"
        "Задание: {compass}\n"
        "Эти значения — из файла; следующее сообщение владельца обрабатывается "
        "с ними. Расхождение с ними — ошибка курса."
    ).format(what=what, summary=orchlib.params_summary(p),
             compass=orchlib.compass_path(p))
    emit(fmt, "UserPromptSubmit", text[:9000])
    try:
        with open(mf, "w", encoding="utf-8") as f:
            json.dump(marks, f)
    except Exception as e:
        sys.stderr.write("marker write failed: %s\n" % e)


def main():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        sys.stdout.write(__doc__ + "\n")
        return 0
    cmd = args[0]
    engine, fmt = "claude", "json"
    rest = args[1:]
    i = 0
    while i < len(rest):
        if rest[i] == "--engine":
            engine = rest[i + 1]; i += 2
        elif rest[i] == "--format":
            fmt = rest[i + 1]; i += 2
        else:
            i += 1
    if engine == "kimi":
        fmt = "text"
    try:
        if cmd == "session-start":
            cmd_session_start(engine, fmt)
        elif cmd == "post-tool":
            cmd_post_tool(engine, fmt)
        elif cmd == "prompt-submit":
            cmd_prompt_submit(engine, fmt)
        elif cmd == "heartbeat":
            cmd_heartbeat(engine, fmt)
        elif cmd == "stop":
            pass  # зарезервировано: молчим, не мешаем завершению turn
        else:
            sys.stderr.write("unknown subcommand: %s\n" % cmd)
            return 2
    except Exception as e:
        sys.stderr.write("reground error: %s\n" % e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
