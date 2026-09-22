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
        "hierarchy": "auto",          # auto | on | off — режим иерархии (генералов/фронтов)
    },
    "task": {
        "description_file": ".orchestration/compass.md",
    },
    "execution": {
        "executor": "auto",        # auto | local-cursor | cursor-cloud | subagents
        "on_cursor_fail": "ask",    # ask | wait | subagents — поведение при отказе курсора
        "parallel_per_task": 3,     # N слепых исполнителей для READ-ONLY задач (поиск/аудит); пишущая задача — 1 исполнитель + волна критиков; spike — отдельное решение; дефолт 3
        "timeout_s": 1800,          # верхняя граница прогона исполнителя
        "retry_on_fail": 1,         # перезапусков при фейле (по доктрине: 1 раз)
        "ask_before_runs": 20,      # спросить владельца, если прогноз пачки > N прогонов
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
    "compass": {
        "max_session_chars": 8500,  # лимит сессионного/общего compass (символы)
        "max_front_chars": 4000,    # лимит compass фронта/полковника (fronts/**)
        "guard_poll_s": 2,          # интервал сторожа панели (сек)
    },
}

RANGES = {  # (min, max) для целочисленных полей
    "execution.parallel_per_task": (1, 8),
    "execution.timeout_s": (60, 21600),
    "execution.retry_on_fail": (0, 3),
    "execution.ask_before_runs": (5, 200),
    "review.reviewers_per_diff": (1, 8),
    "review.max_rounds": (1, 6),
    "orchestrator.budget_usd": (0, 1000),
    "reground.every_min": (1, 120),
    "reground.every_n_calls": (5, 500),
    "compass.max_session_chars": (100, 200000),
    "compass.max_front_chars": (100, 200000),
    "compass.guard_poll_s": (1, 60),
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


def state_dir_note():
    """Предупреждение, если найденный state не под cwd (чужой .orchestration выше).

    Пустая строка — state под текущим cwd (или совпадает с ожидаемым локальным).
    Не вызывать из reground/хуков: тишина в хуках обязательна.
    """
    state = os.path.abspath(find_state_dir())
    cwd = os.path.abspath(os.getcwd())
    try:
        if os.path.commonpath([cwd, state]) == cwd:
            return ""
    except ValueError:
        pass  # другой диск (Windows)
    return ("state выше по дереву: %s "
            "(запускай из папки проекта или задай ORCHESTRATION_DIR)" % state)


def state_path(name):
    d = find_state_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def utf8_stdio():
    """UTF-8 для stdio независимо от локали (Windows: пайпы = cp1251/cp866).
    Движки шлют и ждут UTF-8; errors=replace — битый байт не должен ронять хук."""
    import io as _io
    for name in ("stdout", "stderr", "stdin"):
        s = getattr(sys, name, None)
        if s is None:
            continue
        try:
            if hasattr(s, "reconfigure"):          # python 3.7+
                s.reconfigure(encoding="utf-8", errors="replace")
            elif hasattr(s, "buffer"):             # python 3.6 fallback
                wrapper = _io.TextIOWrapper(s.buffer, encoding="utf-8", errors="replace")
                setattr(sys, name, wrapper)
        except Exception:
            pass


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
                with open(tpl, "r", encoding="utf-8-sig") as f:
                    seed = json.load(f)
            except Exception:
                seed = {}
        merged = _merge(DEFAULTS, seed)
        save_params(merged)
        _bootstrap_compass(merged)
        return merged
    try:
        with open(pf, "r", encoding="utf-8-sig") as f:
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
    exe = p.get("execution", {}).get("executor")
    if exe not in ("auto", "local-cursor", "cursor-cloud", "subagents"):
        errs.append("execution.executor: ожидается auto|local-cursor|cursor-cloud|subagents")
    en = p.get("orchestration", {}).get("enabled")
    if not isinstance(en, bool):
        errs.append("orchestration.enabled: ожидается true/false")
    hier = p.get("orchestration", {}).get("hierarchy")
    if hier not in ("auto", "on", "off"):
        errs.append("orchestration.hierarchy: ожидается auto|on|off")
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


SESSION_ID_MAX = 64  # Windows MAX_PATH: короче id — меньше риск упереться в лимит


def safe_name(session_id):
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default"))[:SESSION_ID_MAX] or "default"


def session_dir(session_id):
    import re
    raw = re.sub(r"[^A-Za-z0-9._-]", "_", str(session_id or "default")) or "default"
    # legacy ≤80 и имена с list_sessions — не режем, если каталог уже есть
    existing = os.path.join(sessions_dir(), raw)
    if os.path.isdir(existing):
        return existing
    d = os.path.join(sessions_dir(), raw[:SESSION_ID_MAX])
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


def seed_session_compass(p, sid):
    """Скопировать общий шаблон compass в сессионный, если его ещё нет.
    Fail-open: любая ошибка → False (хук не должен ронять сессию)."""
    try:
        path = session_compass_path(p, sid)
        if os.path.exists(path):
            return False
        src = compass_path(p)
        if not os.path.exists(src):
            _bootstrap_compass(p)
        if os.path.exists(src):
            with open(src, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            tpl = os.path.join(KIT_DIR, "compass.md")
            text = ""
            if os.path.exists(tpl):
                with open(tpl, "r", encoding="utf-8") as f:
                    text = f.read()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return True
    except Exception:
        return False


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
    # D:/... — абсолютный на Windows; на POSIX isabs=False, join спрятал бы другой диск
    drive_abs = len(rel) >= 3 and rel[0].isalpha() and rel[1] == ":" and rel[2] in "/\\"
    if os.path.isabs(rel) or drive_abs:
        path = os.path.normpath(rel)
    else:
        path = os.path.normpath(os.path.join(root, rel))
    # запрет выхода за корень проекта
    try:
        if not os.path.commonpath([root, path]) == root:
            return os.path.join(st, "compass.md")  # fallback: безопасный путь
    except ValueError:
        # другой диск (Windows): commonpath([C:\..., D:\...]) бросает ValueError
        return os.path.join(st, "compass.md")
    return path


def compass_limits(p=None):
    """Лимиты compass: (session_chars, front_chars). Дефолты 8500/4000 без секции."""
    c = (p or {}).get("compass") if isinstance(p, dict) else None
    if not isinstance(c, dict):
        c = {}
    try:
        sess = int(c.get("max_session_chars", DEFAULTS["compass"]["max_session_chars"]))
    except (TypeError, ValueError):
        sess = DEFAULTS["compass"]["max_session_chars"]
    try:
        front = int(c.get("max_front_chars", DEFAULTS["compass"]["max_front_chars"]))
    except (TypeError, ValueError):
        front = DEFAULTS["compass"]["max_front_chars"]
    return sess, front


def compass_session_limit(p=None):
    return compass_limits(p)[0]


def compass_front_limit(p=None):
    return compass_limits(p)[1]


def find_compass_files():
    """Все compass.md состояния: общий, sessions/*/compass.md, fronts/**/compass.md."""
    st = find_state_dir()
    out = []
    root = os.path.join(st, "compass.md")
    if os.path.isfile(root):
        out.append(root)
    sess_root = os.path.join(st, "sessions")
    if os.path.isdir(sess_root):
        try:
            names = os.listdir(sess_root)
        except OSError:
            names = []
        for name in names:
            path = os.path.join(sess_root, name, "compass.md")
            if os.path.isfile(path):
                out.append(path)
    fronts_root = os.path.join(st, "fronts")
    if os.path.isdir(fronts_root):
        for dirpath, _dirnames, filenames in os.walk(fronts_root):
            if "compass.md" in filenames:
                out.append(os.path.join(dirpath, "compass.md"))
    return out


def compass_limit_for_path(p, path):
    """Сессионный/общий — session limit; fronts/** — front limit."""
    sess_lim, front_lim = compass_limits(p)
    st = find_state_dir()
    fronts_root = os.path.normpath(os.path.join(st, "fronts"))
    norm = os.path.normpath(os.path.abspath(path))
    try:
        if os.path.commonpath([norm, fronts_root]) == fronts_root:
            return front_lim
    except ValueError:
        pass
    return sess_lim


def compass_char_size(path):
    """Число символов (unicode) в файле; None если не читается."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return len(f.read())
    except Exception:
        return None


def compass_overflows(p=None):
    """Список превышений [{path, size, limit}] по всем compass состояния."""
    if p is None:
        p = load_params()
    result = []
    for path in find_compass_files():
        size = compass_char_size(path)
        if size is None:
            continue
        limit = compass_limit_for_path(p, path)
        if size > limit:
            result.append({"path": path, "size": size, "limit": limit})
    return result


def format_compass_overflows(overflows):
    """Громкий блок превышений (по строке на файл). Пустая строка если нет."""
    if not overflows:
        return ""
    lines = []
    for o in overflows:
        lines.append(
            "⛔ COMPASS ПРЕВЫШЕН: %s: %d символов при лимите %d. "
            "Хвост НЕ виден вклейками. Ужми файл: историю — в артефакты, не в compass."
            % (o["path"], o["size"], o["limit"])
        )
    return "\n".join(lines)


def recent_sessions(max_age_s=86400):
    """Id сессий, тронутых за последние max_age_s сек (mtime last-seen или каталога)."""
    now = time.time()
    out = []
    try:
        for s in list_sessions():
            try:
                age = now - float(s.get("last_seen") or 0)
            except (TypeError, ValueError):
                continue
            if age <= max_age_s:
                out.append(s["id"])
    except Exception:
        return []
    return out


def emit_pending_compass_guard(sid_or_none, overflows):
    """Пишет pending_compass_guard.json как reground.cmd_heartbeat.

    Всегда пишет <state>/pending_compass_guard.json; плюс в сессии:
    sid_or_none задан → в эту сессию; None → во все recent_sessions().
    Ошибки — stderr, не падать.
    """
    try:
        try:
            flag_st = state_path("pending_compass_guard.json")
            with open(flag_st, "w", encoding="utf-8") as f:
                json.dump({"overflows": overflows}, f, ensure_ascii=False)
        except Exception as e:
            sys.stderr.write("compass guard state flag failed: %s\n" % e)
        if sid_or_none is None:
            sids = recent_sessions()
        else:
            sids = [sid_or_none]
        for sid in sids:
            try:
                flag_g = os.path.join(session_dir(sid), "pending_compass_guard.json")
                with open(flag_g, "w", encoding="utf-8") as f:
                    json.dump({"overflows": overflows}, f, ensure_ascii=False)
            except Exception as e:
                sys.stderr.write("compass guard flag failed: %s\n" % e)
    except Exception as e:
        sys.stderr.write("compass guard emit failed: %s\n" % e)


def write_compass_checked(p, path, text):
    """Атомарная запись compass с проверкой лимита. (ok, info), без исключений наружу.

    size = len(text); limit = compass_limit_for_path(p, path).
    ok=True → tempfile + os.replace; info={size,limit,path}.
    ok=False → файл не трогать; info то же + error.
    """
    info = {"size": 0, "limit": 0, "path": path}
    try:
        if text is None:
            text = ""
        size = len(text)
        limit = compass_limit_for_path(p, path)
        info = {"size": size, "limit": limit, "path": path}
        if size > limit:
            info["error"] = "превышен лимит: %d символов при лимите %d" % (size, limit)
            return False, info
        d = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(d, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=d, prefix=".compass-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except Exception:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception:
                    pass
            raise
        return True, info
    except Exception as e:
        info["error"] = str(e)
        return False, info


def is_compass_path(path):
    """True если путь — compass.md под state (общий / sessions / fronts/**)."""
    if not path:
        return False
    norm = os.path.normpath(os.path.abspath(path))
    if os.path.basename(norm) != "compass.md":
        return False
    st = os.path.normpath(find_state_dir())
    try:
        return os.path.commonpath([norm, st]) == st
    except ValueError:
        return False


def read_compass(p, limit=None):
    if limit is None:
        limit = compass_session_limit(p)
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
    orch = p.get("orchestration", {})
    return (
        "Параметры пачки: исполнители — {mode}; "
        "параллельных исполнителей на задачу {par}; "
        "критиков на каждый дифф {rev}; круги ревью до {rns} (дальше — стоп и доклад владельцу); "
        "таймаут прогона {tmo} c; перезапуск при фейле {ret}. "
        "Контроль курса: сверка не реже чем каждые {emin} мин (или {ecall} вызовов инструментов); "
        "предстарт-порог: спросить владельца при пачке > {abr} прогонов. "
        "Режим иерархии: {hier}"
    ).format(
        mode=ex.get("executor", "subagents"),
        par=ex.get("parallel_per_task"), rev=rv.get("reviewers_per_diff"),
        rns=rv.get("max_rounds"), tmo=ex.get("timeout_s"), ret=ex.get("retry_on_fail"),
        emin=rg.get("every_min"), ecall=rg.get("every_n_calls"),
        abr=ex.get("ask_before_runs"),
        hier=orch.get("hierarchy", "auto"),
    )


# --- граф фронтов большого проекта (<state>/fronts.json) ---

FRONT_STATUSES = ("planned", "running", "blocked", "done", "failed")
EMPTY_FRONTS = {"goal": "", "fronts": [], "notes": ""}


def fronts_path():
    return state_path("fronts.json")


def load_fronts():
    """Читает fronts.json; если файла нет — пустая структура."""
    pf = fronts_path()
    if not os.path.exists(pf):
        return {"goal": "", "fronts": [], "notes": ""}
    try:
        with open(pf, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return {"goal": "", "fronts": [], "notes": ""}
    if not isinstance(data, dict):
        return {"goal": "", "fronts": [], "notes": ""}
    return {
        "goal": data.get("goal", "") if isinstance(data.get("goal"), str) else "",
        "fronts": data.get("fronts") if isinstance(data.get("fronts"), list) else [],
        "notes": data.get("notes", "") if isinstance(data.get("notes"), str) else "",
    }


def front_compass_path(fid):
    """Путь compass фронта: <state>/fronts/<safe_id>/compass.md."""
    return os.path.join(find_state_dir(), "fronts", safe_name(fid), "compass.md")


def _fronts_cycle_dfs(ids, deps_map):
    """DFS: True если есть цикл. ids — порядок обхода."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {i: WHITE for i in ids}

    def dfs(u):
        color[u] = GRAY
        for v in deps_map.get(u, []):
            if v not in color:
                continue
            if color[v] == GRAY:
                return True
            if color[v] == WHITE and dfs(v):
                return True
        color[u] = BLACK
        return False

    for i in ids:
        if color[i] == WHITE and dfs(i):
            return True
    return False


def validate_fronts(f):
    """Проверка графа фронтов; возвращает список ошибок (пустой = ок)."""
    errs = []
    if not isinstance(f, dict):
        return ["fronts: ожидается объект"]
    if "goal" in f and not isinstance(f.get("goal"), str):
        errs.append("goal: ожидается строка")
    if "notes" in f and not isinstance(f.get("notes"), str):
        errs.append("notes: ожидается строка")
    fronts = f.get("fronts")
    if fronts is None:
        fronts = []
    if not isinstance(fronts, list):
        errs.append("fronts: ожидается список")
        return errs
    ids = []
    seen = set()
    for i, fr in enumerate(fronts):
        if not isinstance(fr, dict):
            errs.append("fronts[%d]: ожидается объект" % i)
            continue
        fid = fr.get("id")
        if not isinstance(fid, str) or not fid.strip():
            errs.append("fronts[%d].id: ожидается непустая строка" % i)
            continue
        if fid in seen:
            errs.append("дубликат id: %s" % fid)
        else:
            seen.add(fid)
            ids.append(fid)
        for field in ("title", "role", "compass"):
            v = fr.get(field)
            if v is None:
                errs.append("fronts[%d].%s: обязательное поле" % (i, field))
            elif not isinstance(v, str):
                errs.append("fronts[%d].%s: ожидается строка" % (i, field))
        deps = fr.get("deps")
        if deps is None:
            errs.append("fronts[%d].deps: обязательное поле" % i)
        elif not isinstance(deps, list):
            errs.append("fronts[%d].deps: ожидается список" % i)
        else:
            for d in deps:
                if not isinstance(d, str):
                    errs.append("fronts[%d].deps: элементы — строки" % i)
                    break
        st = fr.get("status")
        if st not in FRONT_STATUSES:
            errs.append("fronts[%d].status: ожидается %s, получено %r" % (
                i, "|".join(FRONT_STATUSES), st))
    id_set = set(ids)
    deps_map = {}
    for i, fr in enumerate(fronts):
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not isinstance(fid, str) or fid not in id_set:
            continue
        deps = fr.get("deps") if isinstance(fr.get("deps"), list) else []
        deps_map[fid] = [d for d in deps if isinstance(d, str)]
        for d in deps_map[fid]:
            if d not in id_set:
                errs.append("неизвестный dep: %s → %s" % (fid, d))
    if id_set and _fronts_cycle_dfs(ids, deps_map):
        errs.append("цикл в deps")
    return errs


def save_fronts(f):
    """Атомарная запись fronts.json после валидации."""
    errs = validate_fronts(f)
    if errs:
        raise ValueError(errs)
    out = {
        "goal": f.get("goal", "") if isinstance(f.get("goal"), str) else "",
        "fronts": f.get("fronts") if isinstance(f.get("fronts"), list) else [],
        "notes": f.get("notes", "") if isinstance(f.get("notes"), str) else "",
    }
    pf = fronts_path()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".fronts-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pf)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def front_waves(f):
    """Волны фронтов (топосорт по deps). При цикле — ValueError."""
    if not isinstance(f, dict):
        raise ValueError(["fronts: ожидается объект"])
    fronts = f.get("fronts") if isinstance(f.get("fronts"), list) else []
    order = []
    deps_map = {}
    for fr in fronts:
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        if fid in deps_map:
            raise ValueError(["дубликат id: %s" % fid])
        order.append(fid)
        deps = fr.get("deps") if isinstance(fr.get("deps"), list) else []
        deps_map[fid] = [d for d in deps if isinstance(d, str)]
    id_set = set(order)
    for fid, deps in deps_map.items():
        for d in deps:
            if d not in id_set:
                raise ValueError(["неизвестный dep: %s → %s" % (fid, d)])
    if order and _fronts_cycle_dfs(order, deps_map):
        raise ValueError(["цикл в deps"])
    done = set()
    remaining = set(order)
    waves = []
    while remaining:
        wave = [fid for fid in order
                if fid in remaining and all(d in done for d in deps_map[fid])]
        if not wave:
            raise ValueError(["цикл в deps"])
        waves.append(wave)
        done.update(wave)
        remaining -= set(wave)
    return waves
