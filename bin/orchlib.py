#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая библиотека оркестрации: state-каталог, params.json, валидация.

Кроссплатформенно (Linux/macOS/Windows), python3.6+, только stdlib.
Единственный источник правды — .orchestration/params.json (+ compass.md).
"""
import json
import os
import sys
import tempfile
import time

KIT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULTS = {
    "orchestration": {
        "enabled": True,              # тумблер: False — работать напрямую, без исполнителей
    },
    "task": {
        "description_file": ".orchestration/compass.md",
    },
    "execution": {
        "executor": "auto",        # auto | local-cursor | cursor-cloud | subagents
        "on_cursor_fail": "ask",    # ask | wait | subagents — поведение при отказе курсора
        "parallel_per_task": 1,     # параллельных агентов на 1 задачу одновременно
        "timeout_s": 1800,          # верхняя граница прогона исполнителя
        "retry_on_fail": 1,         # перезапусков при фейле (по доктрине: 1 раз)
    },
    "review": {
        "reviewers_per_diff": 3,    # сколькими агентами перепроверять каждый дифф
        "max_rounds": 3,            # круги ревью до схождения, дальше — доклад владельцу
    },
    "orchestrator": {               # применяется при ЗАПУСКЕ новой сессии, не на лету
        "claude": "default",        # роли резолвятся по .orchestration/discovered.json
        "claude_effort": "high",
        "codex": "default",
        "codex_effort": "high",
        "budget_usd": 20,
    },
    "reground": {                   # периодическое одёргивание — PRIMARY, по времени
        "every_min": 10,            # минут с последней сверки -> напоминание
        "every_n_calls": 40,        # запасной триггер: вызовов инструментов
        "compact_reground": True,   # повторный инжект после компакшна (SessionStart)
    },
    "panel": {
        "host": "127.0.0.1",
        "port": 8765,
    },
}

RANGES = {  # (min, max) для целочисленных полей
    "execution.parallel_per_task": (1, 8),
    "execution.timeout_s": (60, 21600),
    "execution.retry_on_fail": (0, 3),
    "review.reviewers_per_diff": (1, 8),
    "review.max_rounds": (1, 6),
    "orchestrator.budget_usd": (0, 1000),
    "reground.every_min": (1, 120),
    "reground.every_n_calls": (5, 500),
}


def find_state_dir():
    """State-каталог: $ORCHESTRATION_DIR, иначе ближайший .orchestration от cwd вверх."""
    env = os.environ.get("ORCHESTRATION_DIR")
    if env:
        return os.path.abspath(env)
    cur = os.getcwd()
    while True:
        cand = os.path.join(cur, ".orchestration")
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return os.path.join(os.getcwd(), ".orchestration")


def state_path(name):
    d = find_state_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def params_file():
    return state_path("params.json")


def _merge(base, over):
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_params():
    """Читает params.json; при первом запуске сеет из шаблона kit/params.json."""
    pf = params_file()
    if not os.path.exists(pf):
        tpl = os.path.join(KIT_DIR, "params.json")
        seed = {}
        if os.path.exists(tpl):
            try:
                with open(tpl, "r", encoding="utf-8") as f:
                    seed = json.load(f)
            except Exception:
                seed = {}
        merged = _merge(DEFAULTS, seed)
        save_params(merged)
        _bootstrap_compass(merged)
        return merged
    try:
        with open(pf, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        sys.stderr.write("params.json битый (%s); используется канон\n" % e)
        return dict(DEFAULTS)
    return _merge(DEFAULTS, data)


def validate_params(p):
    errs = []
    for key, (lo, hi) in RANGES.items():
        sec, _, field = key.partition(".")
        val = p.get(sec, {}).get(field)
        if not isinstance(val, int) or isinstance(val, bool) or not (lo <= val <= hi):
            errs.append("%s: ожидается целое %d..%d, получено %r" % (key, lo, hi, val))
    for sec, field in (("task", "description_file"), ("panel", "host")):
        v = p.get(sec, {}).get(field)
        if not isinstance(v, str) or not v.strip():
            errs.append("%s.%s: ожидается непустая строка" % (sec, field))
    ocf = p.get("execution", {}).get("on_cursor_fail")
    if ocf not in ("ask", "wait", "subagents"):
        errs.append("execution.on_cursor_fail: ожидается ask|wait|subagents")
    en = p.get("orchestration", {}).get("enabled")
    if not isinstance(en, bool):
        errs.append("orchestration.enabled: ожидается true/false")
    port = p.get("panel", {}).get("port")
    if not isinstance(port, int) or not (1 <= port <= 65535):
        errs.append("panel.port: ожидается 1..65535")
    return errs


def save_params(p):
    errs = validate_params(p)
    if errs:
        raise ValueError("; ".join(errs))
    pf = params_file()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".params-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(p, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, pf)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _bootstrap_compass(p):
    """При первом запуске — посеять compass из шаблона kit/compass.md."""
    path = compass_path(p)
    if os.path.exists(path):
        return
    tpl = os.path.join(KIT_DIR, "compass.md")
    text = ""
    if os.path.exists(tpl):
        try:
            with open(tpl, "r", encoding="utf-8") as f:
                text = f.read()
        except Exception:
            text = ""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


# --- per-session состояние (несколько параллельных сессий в одной папке) ---

def sessions_dir():
    d = os.path.join(find_state_dir(), "sessions")
    os.makedirs(d, exist_ok=True)
    return d


def session_dir(session_id):
    import re
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))[:80] or "default"
    d = os.path.join(sessions_dir(), safe)
    os.makedirs(d, exist_ok=True)
    return d


def session_override(session_id):
    """Явный вкл/выкл конкретной сессии (файл enabled.json), None = нет override."""
    p = os.path.join(session_dir(session_id), "enabled.json")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f).get("enabled")
    except Exception:
        return None


def session_effective_enabled(p, session_id):
    ov = session_override(session_id)
    if ov is not None:
        return bool(ov)
    return bool(p.get("orchestration", {}).get("enabled", True))


def session_compass_path(p, session_id):
    """Compass КОНКРЕТНОЙ сессии — всегда сессионный путь, без fallback.
    Если файла нет — агент создаёт его при первой задаче (правило скилла)."""
    return os.path.join(session_dir(session_id), "compass.md")


def touch_session(session_id):
    try:
        with open(os.path.join(session_dir(session_id), "last-seen"), "w", encoding="utf-8") as f:
            f.write(str(int(time.time())))
    except Exception:
        pass


def list_sessions():
    out = []
    for name in os.listdir(sessions_dir()):
        p = os.path.join(sessions_dir(), name)
        if not os.path.isdir(p):
            continue
        ls = os.path.join(p, "last-seen")
        try:
            mtime = os.path.getmtime(ls if os.path.exists(ls) else p)
        except OSError:
            mtime = 0
        out.append({"id": name, "last_seen": mtime,
                    "override": session_override(name),
                    "has_compass": os.path.exists(os.path.join(p, "compass.md"))})
    return sorted(out, key=lambda s: -s.get("last_seen", 0))


def compass_path(p):
    """Абсолютный путь к compass-файлу; path traversal запрещён."""
    rel = p.get("task", {}).get("description_file", DEFAULTS["task"]["description_file"])
    st = find_state_dir()
    root = os.path.dirname(st)
    if os.path.isabs(rel):
        path = os.path.normpath(rel)
    else:
        path = os.path.normpath(os.path.join(root, rel))
    # запрет выхода за корень проекта
    if not os.path.commonpath([root, path]) == root:
        return os.path.join(st, "compass.md")  # fallback: безопасный путь
    return path


def read_compass(p, limit=9000):
    try:
        with open(compass_path(p), "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return "(compass-файл не найден; задача не задана)"
    if len(text) > limit:
        text = text[:limit] + "\n... (обрезано, полный текст: %s)" % compass_path(p)
    return text


def params_summary(p):
    """Короткая фактическая сводка параметров для вклейки в контекст."""
    ex, rv, rg = p.get("execution", {}), p.get("review", {}), p.get("reground", {})
    return (
        "Параметры пачки: исполнители — {mode}; "
        "параллельных исполнителей на задачу {par}; "
        "критиков на каждый дифф {rev}; круги ревью до {rns} (дальше — стоп и доклад владельцу); "
        "таймаут прогона {tmo} c; перезапуск при фейле {ret}. "
        "Контроль курса: сверка не реже чем каждые {emin} мин (или {ecall} вызовов инструментов)."
    ).format(
        mode=ex.get("executor", "subagents"),
        par=ex.get("parallel_per_task"), rev=rv.get("reviewers_per_diff"),
        rns=rv.get("max_rounds"), tmo=ex.get("timeout_s"), ret=ex.get("retry_on_fail"),
        emin=rg.get("every_min"), ecall=rg.get("every_n_calls"),
    )
