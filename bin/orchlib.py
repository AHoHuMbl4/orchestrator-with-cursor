#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая библиотека оркестрации: state-каталог, params.json, валидация.

Кроссплатформенно (Linux/macOS/Windows), python3.6+, только stdlib.
Единственный источник правды — .orchestration/params.json (+ compass.md).
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time

try:
    import fcntl
except ImportError:  # Windows / среды без fcntl
    fcntl = None

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
    "budgets": {
        "warn_runs_per_front": 60,  # churn-датчик: used>warn → лог+pending, запуск продолжается
        "hard_runs_per_front": 0,   # жёсткий стоп: used>hard при hard>0 → exit 7; 0 = выключен
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
    foreign = state_is_foreign()
    if not foreign:
        return ""
    return ("state выше по дереву: %s "
            "(запускай из папки проекта или задай ORCHESTRATION_DIR)" % foreign)


def state_is_foreign(cwd=None):
    """Путь к state, если найденный state не лежит в cwd (выше / чужой); иначе None.

    Логика согласована с state_dir_note / find_state_dir:
    commonpath(state, cwd) == cwd → свой (state под cwd).
    """
    if cwd is None:
        cwd = os.getcwd()
    state = os.path.abspath(find_state_dir())
    cwd = os.path.abspath(cwd)
    try:
        if os.path.commonpath([cwd, state]) == cwd:
            return None
    except ValueError:
        pass  # другой диск (Windows)
    return state


def state_path(name):
    d = find_state_dir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


# --- летописец вызовов (journal.jsonl) ---------------------------------

# Шапка промта (только первые 3 строки): «роль: path.md» / «role:» / «Роль:».
# Упоминания путей ролей в теле текста НЕ считаются (ловушка №54).
_ROLE_HEADER_RE = re.compile(
    r"^(?:роль|Роль|role):\s*([A-Za-z0-9_/.-]+\.md)\s*$")
_GATE_MARKERS = (
    "FRONT_BUDGET_WARN", "BUDGET_HARD", "SECRETS_IN_PROMPT", "FRONT_CLOSED",
    "FRONT_REQUIRED", "API_KEY_REQUIRED")
_COMPASS_GATE_RE = re.compile(r"COMPASS_OVERFLOW[A-Z0-9_]*")
# осмысленный вердикт без JSON-хвоста (OK | PROBLEMS:… | BLOCKED:…)
_VERDICT_RE = re.compile(
    r"Вердикт:\s*(?:(OK)\b|(PROBLEMS|BLOCKED)\b(:[^\n\\\"\r]*)?)")
_VERDICT_MAX_LEN = 200

# --- секрет-сканер (единая точка кита) -----------------------------------

# Невидимые для нормализованной копии: U+200B..U+200D, U+2060, U+FEFF, U+00AD
_SECRET_INVISIBLE_RE = re.compile("[\u200b\u200c\u200d\u2060\ufeff\u00ad]")
# Изолированные пробельные прогоны ровно 1..2 (не часть более длинного) — схлоп только в копии
_SECRET_SHORT_WS_RE = re.compile(
    r"(?<![ \t\n\r\f\v])[ \t\n\r\f\v]{1,2}(?![ \t\n\r\f\v])"
)

# Текущие 6 из run-exec/run-cloud — как есть; далее расширения по приказу SEC-C1.
SECRET_PATTERNS = (
    re.compile(r"crsr_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"BEGIN [A-Z0-9 ]*PRIVATE KEY"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
    re.compile(r"sk-(?:or-v1|proj|ant)-[A-Za-z0-9]{20,}"),
    # \b + \s* — после схлопа коротких ws разделитель может исчезнуть (Bearer<token>);
    # \b не даёт матчить вклейку вроде phraseBearer… из FP «Bearer token»
    re.compile(r"\bBearer\s*[A-Za-z0-9]{20,}"),
    re.compile(r"(?i)(?:token|api_key|apikey)\s*=\s*[A-Za-z0-9]{20,}"),
)


def _normalize_secret_text(text):
    """Нормализованная копия только для матчинга (исходный text не меняется)."""
    t = _SECRET_INVISIBLE_RE.sub("", text)
    t = _SECRET_SHORT_WS_RE.sub("", t)
    return t


def scan_secrets(text):
    """Первый совпавший паттерн (pattern.pattern) или None.

    Матчинг идёт по (а) сыром тексту и (б) нормализованной копии только для
    матчинга: срез невидимых (U+200B..U+200D, U+2060, U+FEFF, U+00AD) +
    схлопывание пробельных прогонов ≤2 символов (разрыв ключа). Глобальную
    склейку всего текста не делать; паттерны требуют префикс + длинную
    base62-подобную основу.
    """
    raw = text or ""
    norm = _normalize_secret_text(raw)
    for rx in SECRET_PATTERNS:
        if rx.search(raw) or rx.search(norm):
            return rx.pattern
    return None


def journal_path():
    """Путь к <state>/journal.jsonl (каталог state создаётся при необходимости)."""
    return state_path("journal.jsonl")


def journal_append(entry):
    """Дописать одну JSON-строку в journal.jsonl. Ошибки — stderr, без raise."""
    try:
        path = journal_path()
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                try:
                    f.write(line)
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            else:
                f.write(line)
    except Exception as e:
        try:
            sys.stderr.write("journal_append failed: %s\n" % e)
        except Exception:
            pass


def journal_read(limit=500):
    """Последние N валидных JSON-объектов из journal.jsonl; битые строки — skip."""
    try:
        limit = int(limit)
    except Exception:
        limit = 500
    if limit < 0:
        limit = 0
    path = journal_path()
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception as e:
        try:
            sys.stderr.write("journal_read failed: %s\n" % e)
        except Exception:
            pass
        return []
    out = []
    for raw in reversed(lines):
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
        if len(out) >= limit:
            break
    out.reverse()
    return out


def extract_prompt_role(text):
    """Роль только из шапки: первые 3 строки, паттерн «роль: path.md». Тело — игнор."""
    if not text:
        return None
    for line in text.splitlines()[:3]:
        m = _ROLE_HEADER_RE.match(line)
        if m:
            return m.group(1)
    return None


def normalize_journal_role(role):
    """Каталог-относительный путь роли (code/coder.md), не абсолютный."""
    if not isinstance(role, str) or not role:
        return role
    r = role.replace("\\", "/")
    marker = "/references/roles/"
    if marker in r:
        return r.split(marker, 1)[1]
    # абсолютный/длинный путь с /roles/<rel>
    idx = r.find("/roles/")
    if idx >= 0:
        tail = r[idx + len("/roles/"):]
        if tail and not tail.startswith("/") and "/" in tail:
            return tail
    return role


def resolve_journal_parent(run_id):
    """parent из ORCH_RUN_ID; не наследуется → null; не сам свой id."""
    parent = os.environ.get("ORCH_RUN_ID") or None
    if not parent:
        return None
    if run_id and parent == run_id:
        return None
    return parent


def resolve_run_role(role_flag, prompt_text):
    """Приоритет: --role > шапка промта > None. Роль — каталог-относительная."""
    if role_flag:
        return normalize_journal_role(role_flag)
    return normalize_journal_role(extract_prompt_role(prompt_text))


# Exit code: нет --front/--no-front при hierarchy != off.
FRONT_REQUIRED_EXIT = 8


def resolve_front_launch(front=None, no_front_reason=None):
    """Общая проверка FRONT_REQUIRED для run-exec / run-cloud.

    Returns (front_out, no_front_reason_out, refuse_msg).
    refuse_msg is None → запуск разрешён; иначе текст отказа (exit 8).
    hierarchy=off без фронта → front=None, reason=\"hierarchy-off\".
    """
    front = front if (isinstance(front, str) and front.strip()) else None
    reason = (no_front_reason.strip()
              if isinstance(no_front_reason, str) and no_front_reason.strip()
              else None)
    if front and reason:
        return (None, None,
                "FRONT_REQUIRED: укажите либо --front, либо --no-front, не оба")
    if front:
        return (front, None, None)
    # ValueError от load_params (битый JSON) — наружу явно; гейт не молчит DEFAULTS.
    hier = (load_params().get("orchestration") or {}).get("hierarchy")
    if hier == "off":
        return (None, "hierarchy-off", None)
    if reason:
        return (None, reason, None)
    return (None, None,
            "FRONT_REQUIRED: укажите --front <id> или --no-front \"<причина>\"")


def _verdict_matches(text):
    """Все осмысленные «Вердикт: …» в тексте (в порядке появления)."""
    out = []
    for m in _VERDICT_RE.finditer(text or ""):
        if m.group(1):
            v = "Вердикт: OK"
        else:
            kind = m.group(2) or ""
            rest = m.group(3) or ""
            v = ("Вердикт: %s%s" % (kind, rest)).rstrip()
        if len(v) > _VERDICT_MAX_LEN:
            v = v[:_VERDICT_MAX_LEN]
        out.append(v)
    return out


def _stream_assistant_result_texts(obj):
    """Тексты из NDJSON type=assistant (text) и type=result."""
    texts = []
    if not isinstance(obj, dict):
        return texts
    kind = obj.get("type")
    if kind == "assistant":
        msg = obj.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    t = part.get("text")
                    if isinstance(t, str) and t:
                        texts.append(t)
        # редкий плоский delta
        t = obj.get("text")
        if isinstance(t, str) and t:
            texts.append(t)
    elif kind == "result":
        r = obj.get("result")
        if isinstance(r, str) and r:
            texts.append(r)
    return texts


def _gate_token_from_wrapper_line(line):
    """Маркер эмиссии обёртки или None. JSON tool_call-строки — skip."""
    s = line.strip()
    if not s or s.startswith("{"):
        return None
    if s.startswith("COMPASS_OVERFLOW"):
        m = _COMPASS_GATE_RE.match(s)
        if not m:
            return None
        tok = m.group(0)
        rest = s[len(tok):]
        if rest == "" or rest.startswith("=") or rest[0].isspace():
            return tok
        return None
    for name in _GATE_MARKERS:
        # «NAME=…» (бюджет/секреты) и «NAME: …» (FRONT_REQUIRED refuse-msg)
        if s == name or s.startswith(name + "=") or s.startswith(name + ":"):
            return name
    return None


def journal_log_meta(log_path, n=400):
    """Из лога: (verdict|None, gates:list). Ошибки чтения → (None, []).

    gates — по всему файлу (plain-эмиссии обёртки, не JSON); иначе маркеры
    у начала лога (SECRETS_IN_PROMPT и т.п.) терялись при длинных прогонах.
    verdict — последнее осмысленное «Вердикт: …» из хвоста n строк
    (assistant/result NDJSON; fallback: regex по хвосту).
    """
    verdict = None
    gates = []
    seen = set()
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return None, []

    # gates: весь файл — маркеры гейтов пишутся в начале до тела прогона
    for line in lines:
        gtok = _gate_token_from_wrapper_line(line)
        if gtok and gtok not in seen:
            seen.add(gtok)
            gates.append(gtok)

    tail = lines[-n:] if len(lines) > n else lines
    stream_verdicts = []
    for line in tail:
        s = line.strip()
        # NDJSON stream → тексты assistant/result
        if s.startswith("{"):
            try:
                obj = json.loads(s)
            except Exception:
                continue
            for text in _stream_assistant_result_texts(obj):
                stream_verdicts.extend(_verdict_matches(text))

    if stream_verdicts:
        verdict = stream_verdicts[-1]
    else:
        # fallback: последнее совпадение строгого паттерна в хвосте
        blob = "".join(tail)
        found = _verdict_matches(blob)
        if found:
            verdict = found[-1]

    return verdict, gates


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
        raise ValueError("params.json битый: %s (путь: %s)" % (e, pf))
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
    """Громкий блок превышений (по строке на файл). Пустая строка если нет.

    Dual-mode:
    - запись с ключом attempt (refuse pending): «попытка N / в файле M …»
    - запись только с size (live_ov / heartbeat): прежний текст «size символов при лимите».
    """
    if not overflows:
        return ""
    lines = []
    for o in overflows:
        path = o.get("path", "")
        limit = o.get("limit", 0)
        if "attempt" in o:
            n = o.get("attempt", 0)
            m = o.get("file")
            if m is None:
                m = 0
            lines.append(
                "⛔ COMPASS ПРЕВЫШЕН: %s: попытка %d / в файле %d символов (лимит %d). "
                "Хвост НЕ виден вклейками. Ужми файл: историю — в артефакты, не в compass."
                % (path, n, m, limit)
            )
        else:
            lines.append(
                "⛔ COMPASS ПРЕВЫШЕН: %s: %d символов при лимите %d. "
                "Хвост НЕ виден вклейками. Ужми файл: историю — в артефакты, не в compass."
                % (path, o.get("size", 0), limit)
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

    Формы overflows:
    - refuse (write-compass): {path, limit, attempt:N, file:M} — N=размер
      отказанной записи, M=текущий размер файла (нет файла → 0/null);
    - live/heartbeat: {path, size, limit} — живой размер файла (как раньше).
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


def emit_pending_budget_warn(fid, used, warn):
    """Пишет <state>/pending_budget_warn.json (датчик заноса фронта).

    Ключи: fid, used, warn + additionalContext/message — самодостаточный
    текст для вклейки в additionalContext. Доставку вклейки делает reground
    (отдельный фикс). Ошибки — stderr, не падать.
    """
    msg = (
        "⚠️ Фронт %s съел %s прогонов без закрытия — проверь, не застрял ли; "
        "если это осознанная сложность — игнорируй" % (fid, used)
    )
    payload = {
        "fid": fid,
        "used": used,
        "warn": warn,
        "additionalContext": msg,
        "message": msg,
    }
    try:
        flag_st = state_path("pending_budget_warn.json")
        with open(flag_st, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
    except Exception as e:
        sys.stderr.write("budget warn flag failed: %s\n" % e)


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

FRONT_STATUSES = (
    "proposed", "active", "stalled", "cancelled", "rejected", "done",
)
# Legacy-алиасы: принимаются при чтении/валидации, хранятся как канон.
FRONT_STATUS_LEGACY = {
    "planned": "proposed",
    "running": "active",
    "blocked": "stalled",
    "failed": "rejected",
}
EMPTY_FRONTS = {"goal": "", "fronts": [], "notes": ""}


def normalize_front_status(st):
    """Канонический статус: legacy-алиас → новый; иначе st как есть."""
    if isinstance(st, str) and st in FRONT_STATUS_LEGACY:
        return FRONT_STATUS_LEGACY[st]
    return st


def _migrate_fronts_list(fronts):
    """In-place: legacy status → канон в списке фронтов."""
    if not isinstance(fronts, list):
        return
    for fr in fronts:
        if not isinstance(fr, dict):
            continue
        st = fr.get("status")
        if isinstance(st, str) and st in FRONT_STATUS_LEGACY:
            fr["status"] = FRONT_STATUS_LEGACY[st]


def fronts_path():
    return state_path("fronts.json")


def load_fronts():
    """Читает fronts.json; если файла нет — пустая структура.

    Legacy-статусы (planned/running/blocked/failed) мигрируются в канон
    при чтении (in-memory; файл не переписывается).
    """
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
    fronts = data.get("fronts") if isinstance(data.get("fronts"), list) else []
    _migrate_fronts_list(fronts)
    return {
        "goal": data.get("goal", "") if isinstance(data.get("goal"), str) else "",
        "fronts": fronts,
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
        canon = normalize_front_status(st)
        # Принимаем канон и legacy-алиасы; после миграции хранятся канонические.
        if canon not in FRONT_STATUSES:
            errs.append("fronts[%d].status: ожидается %s (или legacy planned|running|blocked|failed), получено %r" % (
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


def save_fronts(f, timeout_s=5.0):
    """Атомарная запись fronts.json после валидации.

    Legacy-статусы принимаются и перед записью нормализуются в канон.
    Запись под каталог-замком fronts.json.lock; при busy — RuntimeError
    (не ValueError: панель ловит ValueError и пишет в обход замка).
    """
    pf = fronts_path()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    lock_dir = pf + ".lock"
    held = _dir_lock_acquire(lock_dir, timeout_s=timeout_s)
    if not held:
        raise RuntimeError("fronts lock busy: %s" % lock_dir)
    try:
        fronts = f.get("fronts") if isinstance(f.get("fronts"), list) else []
        _migrate_fronts_list(fronts)
        errs = validate_fronts(f if isinstance(f, dict) else {"fronts": fronts})
        if errs:
            raise ValueError(errs)
        out = {
            "goal": f.get("goal", "") if isinstance(f.get("goal"), str) else "",
            "fronts": fronts,
            "notes": f.get("notes", "") if isinstance(f.get("notes"), str) else "",
        }
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
    finally:
        _dir_lock_release(lock_dir)


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


# --- версия кита, статусы фронтов, счётчики ---

def _write_json_atomic(path, obj):
    """Атомарная запись JSON (tempfile + os.replace), как save_params/save_fronts."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".orch-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
            f.write("\n")
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


def kit_version():
    """Первые 10 hex-символов sha256 от содержимого KIT_DIR/SHA256SUMS; иначе unknown."""
    path = os.path.join(KIT_DIR, "SHA256SUMS")
    try:
        with open(path, "rb") as f:
            data = f.read()
        return hashlib.sha256(data).hexdigest()[:10]
    except Exception:
        return "unknown"


def front_status(fid):
    """status фронта с id==fid из fronts.json; нет файла/фронта/поля → None.

    Legacy-алиасы возвращаются уже нормализованными в канон.
    """
    data = load_fronts()
    for fr in data.get("fronts") or []:
        if not isinstance(fr, dict):
            continue
        if fr.get("id") == fid:
            if "status" not in fr:
                return None
            st = fr.get("status")
            return st if isinstance(st, str) else None
    return None


def _dir_lock_acquire(lock_dir, timeout_s=5.0, stale_s=30.0):
    """Каталог-замок через os.mkdir (атомарен на Linux+Windows).

    True — замок взят; False — не удалось (вызывать без блокировки).
    Ошибки — тихий stderr, без raise.
    """
    deadline = time.time() + float(timeout_s)
    sleep_s = 0.05
    while True:
        try:
            os.mkdir(lock_dir)
            return True
        except FileExistsError:
            try:
                age = time.time() - os.path.getmtime(lock_dir)
                if age > float(stale_s):
                    stolen = "%s.stale-%d-%.6f" % (
                        lock_dir, os.getpid(), time.time())
                    try:
                        os.rename(lock_dir, stolen)
                    except Exception:
                        pass
                    else:
                        try:
                            os.rmdir(stolen)
                        except Exception:
                            try:
                                sys.stderr.write(
                                    "orchlib: stale-lock cleanup failed: %s\n"
                                    % stolen)
                            except Exception:
                                pass
                    continue
            except Exception as exc:
                try:
                    sys.stderr.write(
                        "orchlib: lock mtime check failed: %s\n" % exc)
                except Exception:
                    pass
            if time.time() >= deadline:
                try:
                    sys.stderr.write(
                        "orchlib: lock busy, proceed unlocked: %s\n"
                        % lock_dir)
                except Exception:
                    pass
                return False
            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.5, 0.5)
        except Exception as exc:
            try:
                sys.stderr.write(
                    "orchlib: lock acquire failed: %s\n" % exc)
            except Exception:
                pass
            return False


def _dir_lock_release(lock_dir):
    """Снять каталог-замок; ошибки — тихий stderr."""
    try:
        os.rmdir(lock_dir)
    except Exception as exc:
        try:
            sys.stderr.write(
                "orchlib: lock release failed: %s\n" % exc)
        except Exception:
            pass


def bump_front_runs(fid, timeout_s=5.0):
    """Инкремент used в counters/front-runs-<safe_fid>.json → (used, warn, hard).

    Пороги из params.budgets: warn_runs_per_front (дефолт 60),
    hard_runs_per_front (дефолт 0 = выключен).
    Инкремент под каталог-замком <счётчик>.lock (mkdir); если замок
    не взят за timeout — RuntimeError (явный отказ, без тихого инкремента).
    """
    warn, hard = 60, 0
    try:
        bud = load_params().get("budgets") or {}
        if isinstance(bud, dict):
            if "warn_runs_per_front" in bud:
                warn = int(bud["warn_runs_per_front"])
            if "hard_runs_per_front" in bud:
                hard = int(bud["hard_runs_per_front"])
    except Exception:
        warn, hard = 60, 0
    counters = os.path.join(find_state_dir(), "counters")
    os.makedirs(counters, exist_ok=True)
    path = os.path.join(counters, "front-runs-%s.json" % safe_name(fid))
    lock_dir = path + ".lock"
    held = False
    try:
        held = _dir_lock_acquire(lock_dir, timeout_s=timeout_s)
    except Exception as exc:
        try:
            sys.stderr.write(
                "orchlib: lock unexpected: %s\n" % exc)
        except Exception:
            pass
        held = False
    if not held:
        raise RuntimeError("front-runs lock busy: %s" % lock_dir)
    try:
        used = 0
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "used" in data:
                    used = int(data["used"])
            except Exception:
                used = 0
        used += 1
        _write_json_atomic(path, {"used": used})
        return (used, warn, hard)
    finally:
        _dir_lock_release(lock_dir)


def active_fronts():
    """id фронтов со status proposed|active (+legacy running|planned).

    load_fronts уже мигрирует legacy → канон; дополнительно принимаем
    сырые legacy на случай прямого вызова без миграции.
    """
    active = ("proposed", "active", "running", "planned")
    data = load_fronts()
    out = []
    for fr in data.get("fronts") or []:
        if not isinstance(fr, dict):
            continue
        if fr.get("status") in active:
            fid = fr.get("id")
            if isinstance(fid, str) and fid:
                out.append(fid)
    return out


def _kit_version_counter_path(sid):
    return os.path.join(
        find_state_dir(), "counters",
        "kit-version-%s.json" % safe_name(sid))


def last_seen_kit_version(sid):
    """version из counters/kit-version-<safe_sid>.json; нет файла/поля → None."""
    path = _kit_version_counter_path(sid)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "version" not in data:
            return None
        v = data.get("version")
        return v if isinstance(v, str) else None
    except Exception:
        return None


def mark_kit_version(sid, v):
    """Записать {"version": v} в counters/kit-version-<safe_sid>.json атомарно."""
    path = _kit_version_counter_path(sid)
    _write_json_atomic(path, {"version": v})


# --- детекторы (нуджи хука UserPromptSubmit) -----------------------------

def _order_has_basis(text):
    """True, если есть строка «подход:…» / «подход (…):…» или «без советников…» (после lstrip).

    Принимаются только две формы подхода: «подход:» и «подход (» (с «):» или без).
    Любое иное «подход*» (напр. «подходчик:») — не основание.
    """
    if not text:
        return False
    for line in text.splitlines():
        s = line.lstrip()
        if s.startswith("без советников"):
            return True
        if s.startswith("подход:") or s.startswith("подход ("):
            return True
    return False


def orders_without_basis(state=None):
    """Относительные пути order.md без строки обоснования (posix).

    Обходит fronts/<id>/order.md и fronts/<id>/colonels/<cid>/order.md.
    Пустой/битый файл — пропуск. Ошибки ФС — [] / skip.
    """
    try:
        if state is None:
            state = find_state_dir()
        fronts_root = os.path.join(state, "fronts")
        if not os.path.isdir(fronts_root):
            return []
        out = []
        for dirpath, _dirnames, filenames in os.walk(fronts_root):
            if "order.md" not in filenames:
                continue
            path = os.path.join(dirpath, "order.md")
            try:
                with open(path, "r", encoding="utf-8-sig") as f:
                    text = f.read()
            except Exception:
                continue
            if not text or not text.strip():
                continue
            if _order_has_basis(text):
                continue
            rel = os.path.relpath(path, state)
            out.append(rel.replace(os.sep, "/"))
        return out
    except Exception:
        return []


def _role_is_wave_work(role):
    """Роль = работа волны (полковник / code/ / исполнитель домена)."""
    role = normalize_journal_role(role)
    if not isinstance(role, str) or not role:
        return False
    if role == "meta/front-colonel.md" or role.endswith("front-colonel.md"):
        return True
    if role.startswith("code/"):
        return True
    if "/" in role and not role.startswith("meta/"):
        return True
    return False


def _role_is_prosecutor(role):
    role = normalize_journal_role(role)
    if not isinstance(role, str) or not role:
        return False
    return (role == "meta/front-prosecutor.md"
            or role.endswith("front-prosecutor.md"))


def _load_fronts_at(state):
    """fronts.json из явного state; legacy status → канон in-memory."""
    pf = os.path.join(state, "fronts.json")
    if not os.path.exists(pf):
        return {"goal": "", "fronts": [], "notes": ""}
    try:
        with open(pf, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        return {"goal": "", "fronts": [], "notes": ""}
    if not isinstance(data, dict):
        return {"goal": "", "fronts": [], "notes": ""}
    fronts = data.get("fronts") if isinstance(data.get("fronts"), list) else []
    _migrate_fronts_list(fronts)
    return {
        "goal": data.get("goal", "") if isinstance(data.get("goal"), str) else "",
        "fronts": fronts,
        "notes": data.get("notes", "") if isinstance(data.get("notes"), str) else "",
    }


def _journal_entries_at(state):
    """Все валидные записи journal.jsonl из state (хронологический порядок)."""
    path = os.path.join(state, "journal.jsonl")
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except FileNotFoundError:
        return []
    except Exception:
        return []
    out = []
    for raw in lines:
        s = raw.strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


# Грейс детектора «волна без прокурора»: не флагать, пока с earliest
# wave-work start в окне прошло меньше этого числа секунд (P11).
WAVES_WITHOUT_PROSECUTOR_GRACE_S = 180


def waves_without_prosecutor(state=None, window_runs=30):
    """id active-фронтов, где в окне start-записей есть работа, но нет прокурора.

    Окно: последние window_runs kind==start с front==fid. Тихие ошибки → [].
    Грейс P11: не флагать, пока с earliest wave-work start в окне прошло
    < WAVES_WITHOUT_PROSECUTOR_GRACE_S и прокурора нет (новые start грейс
    не продлевают). Прокурор: start в окне или end после якоря.
    """
    try:
        if state is None:
            state = find_state_dir()
        try:
            window_runs = int(window_runs)
        except Exception:
            window_runs = 30
        if window_runs < 0:
            window_runs = 0
        data = _load_fronts_at(state)
        journal = _journal_entries_at(state)
        starts_by_id = _journal_start_index(journal)
        now = time.time()
        out = []
        for fr in data.get("fronts") or []:
            if not isinstance(fr, dict):
                continue
            if fr.get("status") != "active":
                continue
            fid = fr.get("id")
            if not isinstance(fid, str) or not fid:
                continue
            starts = [
                e for e in journal
                if e.get("kind") == "start" and e.get("front") == fid
            ]
            if window_runs:
                starts = starts[-window_runs:]
            else:
                starts = []
            worked = False
            has_pros = False
            earliest_work_ts = None
            for e in starts:
                role = e.get("role")
                if _role_is_prosecutor(role):
                    has_pros = True
                if _role_is_wave_work(role):
                    worked = True
                    try:
                        ts = float(e.get("ts"))
                    except (TypeError, ValueError):
                        continue
                    if earliest_work_ts is None or ts < earliest_work_ts:
                        earliest_work_ts = ts
            # Прокурор по end после якоря: роль с парного start или с end.
            if worked and not has_pros and earliest_work_ts is not None:
                for e in journal:
                    if e.get("kind") != "end":
                        continue
                    try:
                        ets = float(e.get("ts"))
                    except (TypeError, ValueError):
                        continue
                    if ets < earliest_work_ts:
                        continue
                    rid = e.get("id")
                    st = starts_by_id.get(rid) if rid else None
                    if st is not None:
                        if st.get("front") != fid:
                            continue
                        role = st.get("role")
                    else:
                        if e.get("front") != fid:
                            continue
                        role = e.get("role")
                    if _role_is_prosecutor(role):
                        has_pros = True
                        break
            if worked and not has_pros:
                if (earliest_work_ts is not None
                        and (now - earliest_work_ts)
                        < WAVES_WITHOUT_PROSECUTOR_GRACE_S):
                    continue
                out.append(fid)
        return out
    except Exception:
        return []


# --- детектор «командир руками» (P17) -------------------------------------
COMMANDER_HANDS_WINDOW_S = 20 * 60
COMMANDER_HANDS_TASK_CALLS = 25
COMMANDER_HANDS_BLOCK = (
    "⛔ РАБОТА РУКАМИ: задача без исполнителей — сформулируй карточку и "
    "запусти run-exec/run-cloud; мета-вопросы (статус/компас/панель) — можно"
)
_OPEN_CHECKLIST_RE = re.compile(r"(?m)^\s*[-*]\s+\[\s\]\s+\S")
_DOCTRINE_PATH_HINTS = (
    "skills/orchestration",
    "/orchestration/references",
    "SKILL.md",
    "MAP.md",
    "roles/_index.md",
    "roles/_template.md",
    "/compass.md",
    "write-compass.py",
    "session-entry.py",
)


def compass_has_open_checklist(path):
    """True, если в compass есть незакрытый пункт чеклиста `- [ ] …`."""
    if not path or not isinstance(path, str):
        return False
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            text = f.read()
    except Exception:
        return False
    return bool(_OPEN_CHECKLIST_RE.search(text or ""))


def session_has_open_task(sid, p=None):
    """Открытая задача сессии = непустой компас-чеклист."""
    try:
        if p is None:
            p = load_params()
        return compass_has_open_checklist(session_compass_path(p, sid))
    except Exception:
        return False


def _is_doctrine_path(path):
    if not isinstance(path, str) or not path.strip():
        return False
    low = path.replace("\\", "/").lower()
    for hint in _DOCTRINE_PATH_HINTS:
        if hint.lower() in low:
            return True
    if "/roles/" in low and low.endswith(".md"):
        return True
    return False


def _is_meta_shell_command(cmd):
    """Разрешённые мета-команды командира (не считаются «задачными»)."""
    if not isinstance(cmd, str) or not cmd.strip():
        return False
    s = cmd.strip()
    low = s.lower()
    if "session-entry" in low:
        return True
    if "write-compass" in low:
        return True
    if re.search(r"(^|[;&|]\s*|/)panel(\.sh)?(\s|$)", low):
        return True
    if "panel/server" in low or "/panel " in low:
        return True
    if re.search(r"(?:^|[\s;|&])--status(?:\s|$)", s):
        return True
    if re.search(r"(?:^|[\s;|&])--list(?:\s|$)", s):
        return True
    if re.search(r"\bgit\s+(status|log)\b", low):
        return True
    # ls/grep/wc/cat — только чтение доктрины
    if re.match(r"^(ls|grep|rg|wc|cat|head|tail)\b", low):
        return _is_doctrine_path(s)
    return False


def is_meta_tool_event(ev):
    """True, если PostToolUse-событие — разрешённая мета-команда командира."""
    if not isinstance(ev, dict):
        return False
    tool = ev.get("tool_name") or ev.get("toolName") or ev.get("tool") or ""
    if not isinstance(tool, str):
        tool = str(tool)
    ti = ev.get("tool_input") or ev.get("toolInput") or {}
    if not isinstance(ti, dict):
        ti = {}
    tlow = tool.lower()
    if tlow in ("shell", "bash", "powershell", "cmd"):
        cmd = ti.get("command") or ti.get("cmd") or ""
        return _is_meta_shell_command(cmd)
    path = (ti.get("file_path") or ti.get("filePath") or ti.get("path")
            or ti.get("target_directory") or "")
    if tlow in ("read", "grep", "rg", "glob", "list_dir", "listdir"):
        # grep может нести path отдельно
        gpath = path or ti.get("pattern") or ""
        if isinstance(ti.get("path"), str):
            gpath = ti.get("path")
        return _is_doctrine_path(str(path or gpath))
    return False


def session_counter_path(session_id, state=None):
    """counters/<safe_sid>.json — счётчики хука (calls / task_calls)."""
    if state is None:
        state = find_state_dir()
    d = os.path.join(state, "counters")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, safe_name(session_id) + ".json")


def read_session_counter(session_id, state=None):
    path = session_counter_path(session_id, state=state)
    data = {"start_ts": time.time(), "calls": 0, "calls_at_nudge": 0,
            "last_nudge_ts": time.time(), "task_calls": 0}
    try:
        with open(path, "r", encoding="utf-8") as f:
            loaded = json.load(f)
        if isinstance(loaded, dict):
            data.update(loaded)
    except Exception:
        pass
    try:
        data["task_calls"] = int(data.get("task_calls") or 0)
    except Exception:
        data["task_calls"] = 0
    try:
        data["calls"] = int(data.get("calls") or 0)
    except Exception:
        data["calls"] = 0
    return data


def write_session_counter(session_id, data, state=None):
    path = session_counter_path(session_id, state=state)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return True
    except Exception as e:
        try:
            sys.stderr.write("session counter write failed: %s\n" % e)
        except Exception:
            pass
        return False


def bump_session_task_calls(session_id, ev=None, state=None):
    """Инкремент calls; task_calls — только если вызов не мета."""
    data = read_session_counter(session_id, state=state)
    now = time.time()
    if "start_ts" not in data:
        data["start_ts"] = now
    data["calls"] = int(data.get("calls") or 0) + 1
    if ev is None or not is_meta_tool_event(ev):
        data["task_calls"] = int(data.get("task_calls") or 0) + 1
    write_session_counter(session_id, data, state=state)
    return data


def journal_wrapper_starts_since(state=None, since_ts=None):
    """start обёрток run-exec/run-cloud (engine local|cloud) с ts >= since_ts."""
    try:
        if state is None:
            state = find_state_dir()
        out = []
        for e in _journal_entries_at(state):
            if e.get("kind") != "start":
                continue
            eng = e.get("engine")
            if eng not in ("local", "cloud"):
                continue
            if since_ts is not None:
                try:
                    if float(e.get("ts") or 0) < float(since_ts):
                        continue
                except (TypeError, ValueError):
                    continue
            out.append(e)
        return out
    except Exception:
        return []


def commander_hands_active(sid, p=None, state=None, now=None):
    """Громкий блок: открытая задача + окно без start обёрток.

    Окно открывается при task_calls >= COMMANDER_HANDS_TASK_CALLS
    ИЛИ elapsed(start_ts) >= COMMANDER_HANDS_WINDOW_S. Мета-вызовы в
    task_calls не входят (см. bump_session_task_calls / is_meta_tool_event).
    """
    try:
        if now is None:
            now = time.time()
        if state is None:
            state = find_state_dir()
        if p is None:
            try:
                p = load_params()
            except Exception:
                p = DEFAULTS
        if not session_has_open_task(sid, p):
            return False
        ctr = read_session_counter(sid, state=state)
        try:
            start_ts = float(ctr.get("start_ts") or now)
        except (TypeError, ValueError):
            start_ts = now
        try:
            task_calls = int(ctr.get("task_calls") or 0)
        except (TypeError, ValueError):
            task_calls = 0
        elapsed = now - start_ts
        window_open = (
            task_calls >= COMMANDER_HANDS_TASK_CALLS
            or elapsed >= COMMANDER_HANDS_WINDOW_S
        )
        if not window_open:
            return False
        since = max(start_ts, now - COMMANDER_HANDS_WINDOW_S)
        if journal_wrapper_starts_since(state=state, since_ts=since):
            return False
        return True
    except Exception:
        return False


# --- детектор «командирская волна без фронта» (F-C2) -----------------------
COMMANDER_NO_FRONT_SERIES = 3
COMMANDER_NO_FRONT_WINDOW_RUNS = 10
COMMANDER_NO_FRONT_MASK_RE = re.compile(
    r"(?i)\b(smoke|смоук|e2e|замер|probe|проба|checkpoint|чекпоинт|"
    r"read-?only|audit|аудит|scout|разведка)\b"
)
COMMANDER_NO_FRONT_BLOCK = (
    "⛔ КОМАНДИРСКАЯ ВОЛНА БЕЗ ФРОНТА: %d запусков --no-front при живой "
    "иерархии (%s) — серия = волна: подними фронт (fronts.json+генерал+приказ) "
    "или зафиксируй решение владельца. Фикс = фронт. Смоук/замер/разведка — "
    "помечай причину."
)


def format_commander_no_front_block(series):
    """Текст громкого блока по списку из commander_no_front_series (≤350)."""
    if not series:
        return ""
    ids = []
    for item in series:
        rid = item.get("id") if isinstance(item, dict) else None
        if rid is not None:
            ids.append(str(rid))
    ids_s = ",".join(ids) if ids else "?"
    msg = COMMANDER_NO_FRONT_BLOCK % (len(series), ids_s)
    if len(msg) > 350:
        msg = msg[:349] + "…"
    return msg


def commander_no_front_series(sid=None, state=None, p=None, now=None):
    """Серия unmasked --no-front в окне обёрток при живой иерархии.

    Возвращает список {id, reason, ts} при len >= COMMANDER_NO_FRONT_SERIES,
    иначе []. Субагент (extra.event.session_id==sid) и hierarchy=off /
    нет живых фронтов → [].
    """
    try:
        if now is None:
            now = time.time()
        if state is None:
            state = find_state_dir()
        if p is None:
            try:
                p = load_params()
            except Exception:
                p = DEFAULTS
        # скоупинг: сессия субагента по extra.event.session_id (НЕ по parent —
        # parent у SubagentStart = sid командующего). Kimi пишет event.session_id
        # = parent (хук в контексте командующего) — это не сессия субагента.
        if sid:
            for e in _journal_entries_at(state):
                if e.get("kind") != "start":
                    continue
                if e.get("engine") != "engine-subagent":
                    continue
                extra = e.get("extra") or {}
                ev = extra.get("event") if isinstance(extra, dict) else None
                if not isinstance(ev, dict):
                    continue
                esid = ev.get("session_id") or ev.get("sessionId")
                if esid is None or str(esid) != str(sid):
                    continue
                parent = e.get("parent")
                if parent is not None and str(parent) == str(sid):
                    continue
                return []
        hier = (p.get("orchestration") or {}).get("hierarchy")
        if hier == "off":
            return []
        fronts_file = os.path.join(state, "fronts.json")
        if not os.path.exists(fronts_file):
            return []
        data = _load_fronts_at(state)
        alive = False
        for fr in data.get("fronts") or []:
            if not isinstance(fr, dict):
                continue
            if fr.get("status") in ("active", "stalled", "proposed"):
                alive = True
                break
        if not alive:
            return []
        starts = []
        for e in _journal_entries_at(state):
            if e.get("kind") != "start":
                continue
            if e.get("engine") not in ("local", "cloud"):
                continue
            starts.append(e)
        window = starts[-COMMANDER_NO_FRONT_WINDOW_RUNS:]
        # серия = подряд идущие unmasked no-front; --front / маска / hierarchy-off
        # / пустая причина разрывают волну (см. приёмку (c)).
        best = []
        cur = []
        for e in window:
            reason = e.get("no_front_reason")
            ok = False
            if reason is not None:
                if not isinstance(reason, str):
                    reason = str(reason)
                if (reason.strip()
                        and reason.strip() != "hierarchy-off"
                        and not COMMANDER_NO_FRONT_MASK_RE.search(reason)):
                    ok = True
            if ok:
                cur.append({
                    "id": e.get("id"),
                    "reason": reason,
                    "ts": e.get("ts"),
                })
                if len(cur) > len(best):
                    best = list(cur)
            else:
                cur = []
        if len(best) >= COMMANDER_NO_FRONT_SERIES:
            return best
        return []
    except Exception:
        return []


def is_order_md_path(path, state=None):
    """True, если path — fronts/*/order.md или fronts/*/colonels/*/order.md."""
    if not isinstance(path, str) or not path.strip():
        return False
    try:
        if state is None:
            state = find_state_dir()
        abs_path = os.path.abspath(path)
        fronts_root = os.path.abspath(os.path.join(state, "fronts"))
        try:
            rel = os.path.relpath(abs_path, fronts_root)
        except Exception:
            return False
        if rel.startswith(".."):
            return False
        parts = rel.replace("\\", "/").split("/")
        if len(parts) == 2 and parts[1] == "order.md":
            return True
        if (len(parts) == 4 and parts[1] == "colonels"
                and parts[3] == "order.md"):
            return True
        return False
    except Exception:
        return False


def _role_is_critic(role):
    if not isinstance(role, str) or not role:
        return False
    r = normalize_journal_role(role)
    return r in ("code/code-reviewer.md", "research/fact-checker.md")


def _role_is_gitwarden(role):
    if not isinstance(role, str) or not role:
        return False
    r = normalize_journal_role(role)
    return r == "code/git-warden.md" or r.endswith("/git-warden.md")


def _role_is_coder(role):
    if not isinstance(role, str) or not role:
        return False
    r = normalize_journal_role(role)
    return r.startswith("code/coder")


# Честный потолок скана журнала для чипов панели (S4).
HEALTH_JOURNAL_SCAN_LIMIT = 5000
# Окно вокруг advisor↔scout без parent (cloud OR-ветка); не путать со scan_limit.
ADVISOR_SCOUT_WINDOW_S = 3600

_EXECUTOR_CHILD_EXCLUDE = frozenset({
    "code/docs-keeper.md",
    "code/simplicity-warden.md",
    "meta/raw-brief-synthesizer.md",
})


def _role_is_colonel(role):
    role = normalize_journal_role(role)
    if not isinstance(role, str) or not role:
        return False
    return (role == "meta/front-colonel.md"
            or role.endswith("front-colonel.md"))


def _role_is_executor_child(role):
    """Доменный исполнитель под полковником (не свита / не командир)."""
    if not _role_is_wave_work(role):
        return False
    if _role_is_critic(role) or _role_is_gitwarden(role):
        return False
    r = normalize_journal_role(role)
    if not isinstance(r, str) or not r:
        return False
    if r in _EXECUTOR_CHILD_EXCLUDE:
        return False
    if r.startswith("meta/front-"):
        return False
    return True


def _journal_parent_empty(parent):
    return parent is None or parent == ""


def _journal_start_index(entries):
    """id → последняя start-запись (для стыковки end↔start)."""
    idx = {}
    for e in entries:
        if e.get("kind") == "start" and e.get("id"):
            idx[e["id"]] = e
    return idx


# Механическая маска значимости для чипа wave_no_docs (git pathspec).
# :(glob) обязателен для bin/*.py: без него git трактует * рекурсивно (как **).
WAVE_DOCS_MASK_PATHSPECS = (
    ":(glob)bin/*.py",
    "panel/server.py",
    "skills/orchestration/**",
)


def _git_version_tuple():
    """(major, minor) установленного git или None."""
    try:
        r = subprocess.run(
            ["git", "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=5,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    m = re.search(r"(\d+)\.(\d+)", r.stdout or "")
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)))


def _wave_docs_mask_pathspecs(kit_dir):
    """Pathspecs маски; на git<2.29 — fallback os.listdir для bin/*.py."""
    specs = list(WAVE_DOCS_MASK_PATHSPECS)
    ver = _git_version_tuple()
    if ver is not None and ver >= (2, 29):
        return specs
    # :(glob) с 2.29; старше — явные пути верхнего уровня bin/*.py.
    sys.stderr.write(
        "orchlib: git<2.29: :(glob) unavailable; "
        "falling back to os.listdir bin/*.py for wave_no_docs mask\n"
    )
    out = []
    for spec in specs:
        if spec == ":(glob)bin/*.py":
            bin_dir = os.path.join(kit_dir, "bin")
            try:
                names = os.listdir(bin_dir)
            except OSError:
                names = []
            for name in sorted(names):
                if name.endswith(".py") and os.path.isfile(
                    os.path.join(bin_dir, name)
                ):
                    out.append(os.path.join("bin", name))
        else:
            out.append(spec)
    return out


def _last_mask_commit(kit_dir):
    """Последний коммит по маске значимости: (unix_ct:int, short_h:str) или None."""
    if not kit_dir:
        return None
    try:
        r = subprocess.run(
            [
                "git", "-C", kit_dir, "log", "-1", "--format=%ct %h", "--",
            ] + _wave_docs_mask_pathspecs(kit_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    out = (r.stdout or "").strip()
    if not out:
        return None
    parts = out.split(None, 1)
    if len(parts) != 2:
        return None
    try:
        ct = int(parts[0])
    except (TypeError, ValueError):
        return None
    h = parts[1].strip()
    if not h:
        return None
    return (ct, h)


# --- rules cards (F-RULES R1) -----------------------------------------------

RULES_ACTIVE_LIMIT = 25
RULES_CATEGORY_LIMIT = 20
RULES_DEAD_WAVES = 10
# Age-grace: hit=0 карточка моложе K волн (wave_ends с ts > born_at||created) ≠ мёртвая.
RULES_DEAD_AGE_WAVES = 10
RULES_JEV_TIMEOUT_S = 35.0
RULES_JEV_SHORTLIST_MAX = 10
RULES_KOMU = frozenset({
    "commander", "general", "colonel", "executor", "wrapper", "panel"})
RULES_KOGDA = frozenset({
    "decomposition", "launch", "acceptance", "retro",
    "prompt-submit", "post-tool", "task-active"})
RULES_TYPES = frozenset({"DON'T", "DO", "CASE"})


def rules_dir(kit_dir=None):
    """Путь к <kit>/rules (карточки + manifest + archive)."""
    if kit_dir is None:
        kit_dir = KIT_DIR
    return os.path.join(kit_dir, "rules")


def rules_manifest_path(kit_dir=None):
    return os.path.join(rules_dir(kit_dir), "manifest.json")


def rules_cards_dir(kit_dir=None):
    return os.path.join(rules_dir(kit_dir), "cards")


def rules_archive_dir(kit_dir=None):
    return os.path.join(rules_dir(kit_dir), "archive")


def _rules_empty_manifest():
    return {"cards": [], "aliases": {}}


def load_manifest(kit_dir=None, allow_migrate=None):
    """Загрузить rules/manifest.json; нет файла → пустой {cards, aliases}.

    Одноразово проставляет born_at отсутствующим карточкам (migrate_rules_born_at)
    и сохраняет манифест при изменениях.
    allow_migrate=False или ORCH_RULES_NO_MIGRATE=1 — только чтение
    (изоляция /tmp-полигонов health_red_chips от записи в kit_dir; FA-FX3).
    """
    path = rules_manifest_path(kit_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return _rules_empty_manifest()
    except Exception:
        return _rules_empty_manifest()
    if not isinstance(data, dict):
        return _rules_empty_manifest()
    cards = data.get("cards")
    if not isinstance(cards, list):
        cards = []
    aliases = data.get("aliases")
    if not isinstance(aliases, dict):
        aliases = {}
    manifest = {"cards": cards, "aliases": aliases}
    if allow_migrate is None:
        allow_migrate = os.environ.get("ORCH_RULES_NO_MIGRATE") != "1"
    if allow_migrate:
        try:
            if migrate_rules_born_at(kit_dir, manifest=manifest):
                save_manifest(manifest, kit_dir)
        except Exception:
            pass
    return manifest


def save_manifest(manifest, kit_dir=None):
    """Атомарно записать rules/manifest.json."""
    path = rules_manifest_path(kit_dir)
    root = rules_dir(kit_dir)
    os.makedirs(root, exist_ok=True)
    payload = {
        "cards": list(manifest.get("cards") or []),
        "aliases": dict(manifest.get("aliases") or {}),
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    fd, tmp = tempfile.mkstemp(prefix="manifest.", suffix=".json", dir=root)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def _rules_iso_to_ts(iso):
    """ISO-8601 (%aI / fromisoformat) → unix float; сбой → None."""
    if not iso or not isinstance(iso, str):
        return None
    s = iso.strip()
    if not s:
        return None
    # 2026-09-27T15:46:37+00:00 / ...Z
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        # python 3.7+: fromisoformat
        from datetime import datetime
        dt = datetime.fromisoformat(s)
        return float(dt.timestamp())
    except Exception:
        pass
    try:
        import calendar
        from datetime import datetime
        for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S%z"):
            try:
                dt = datetime.strptime(s, fmt)
                return float(calendar.timegm(dt.utctimetuple())
                             if dt.tzinfo else dt.timestamp())
            except Exception:
                continue
    except Exception:
        pass
    return None


def _rules_git_added_ts(path, kit_dir=None):
    """ts первого добавления файла в git (diff-filter=A); нет → None."""
    if not path or not os.path.isfile(path):
        return None
    if kit_dir is None:
        kit_dir = KIT_DIR
    try:
        r = subprocess.run(
            ["git", "-C", kit_dir, "log", "--diff-filter=A",
             "--format=%aI", "--", path],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=15,
        )
        if r.returncode != 0:
            return None
        lines = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if not lines:
            return None
        # последняя строка log = самое раннее A? git log без --reverse → newest first
        # для --diff-filter=A обычно одна запись; берём последнюю (= oldest)
        return _rules_iso_to_ts(lines[-1])
    except Exception:
        return None


def migrate_rules_born_at(kit_dir=None, manifest=None):
    """Один проход: проставить born_at карточкам без поля.

    Источник: git log --diff-filter=A --format=%aI -- <card file>;
    fallback — mtime файла; затем created; иначе time.time().
    При первой простановке также пишет migrated_at=ts (born_at иммутабелен;
    дальнейшие переносы/resurrect обновляют только migrated_at).
    Возвращает число обновлённых записей. manifest=… — мутировать переданный
    объект без повторной загрузки (для load_manifest).
    """
    if kit_dir is None:
        kit_dir = KIT_DIR
    own = manifest is None
    if own:
        # прямой read без migrate-рекурсии
        path = rules_manifest_path(kit_dir)
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return 0
        if not isinstance(data, dict):
            return 0
        cards = data.get("cards") if isinstance(data.get("cards"), list) else []
        aliases = data.get("aliases") if isinstance(data.get("aliases"), dict) else {}
        manifest = {"cards": cards, "aliases": aliases}
    n = 0
    for c in manifest.get("cards") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if c.get("born_at") is not None:
            continue
        ts = None
        try:
            fpath = _rules_card_file_path(
                c, kit_dir, archived=bool(c.get("archived")))
            ts = _rules_git_added_ts(fpath, kit_dir=kit_dir)
            if ts is None and os.path.isfile(fpath):
                ts = float(os.path.getmtime(fpath))
        except Exception:
            ts = None
        if ts is None:
            try:
                ts = float(c.get("created") or 0) or None
            except (TypeError, ValueError):
                ts = None
        if ts is None:
            ts = time.time()
        c["born_at"] = float(ts)
        c["migrated_at"] = float(ts)
        n += 1
    if own and n:
        save_manifest(manifest, kit_dir)
    return n


def _rules_card_by_id(manifest, card_id):
    for c in manifest.get("cards") or []:
        if isinstance(c, dict) and c.get("id") == card_id:
            return c
    return None


def _rules_expand_categories(category, aliases):
    """Категория + алиасы подкатегорий (W0: старая → делегирует в под)."""
    if not category:
        return None
    out = [category]
    if isinstance(aliases, dict):
        kids = aliases.get(category)
        if isinstance(kids, list):
            for k in kids:
                if isinstance(k, str) and k and k not in out:
                    out.append(k)
    return out


def _rules_active_cards(manifest):
    """Активные = не archived."""
    out = []
    for c in manifest.get("cards") or []:
        if not isinstance(c, dict):
            continue
        if c.get("archived"):
            continue
        if c.get("id"):
            out.append(c)
    return out


def match_cards(komu, kogda, category=None, kit_dir=None, limit=3,
                state=None):
    """Список id по адресу (кому×когда×[категория]); hit desc, created asc.

    hit — из counters/rules-hits.json (стейт), не из manifest.
    Алиас W0: match по старой категории включает подкатегории из aliases.
    Безадресный / нет совпадений → []. Потолок доставки — limit (дефолт 3).
    """
    if komu not in RULES_KOMU or kogda not in RULES_KOGDA:
        return []
    manifest = load_manifest(kit_dir)
    hits = _rules_load_hits(state)
    cats = _rules_expand_categories(category, manifest.get("aliases"))
    matched = []
    for c in _rules_active_cards(manifest):
        if c.get("кому") != komu:
            continue
        if c.get("когда") != kogda:
            continue
        if cats is not None:
            cc = c.get("категория")
            if cc not in cats:
                continue
        matched.append(c)
    matched.sort(key=lambda x: (-int(hits.get(x.get("id")) or 0),
                                float(x.get("created") or 0),
                                str(x.get("id") or "")))
    if limit is not None:
        try:
            limit = int(limit)
        except Exception:
            limit = 3
        if limit >= 0:
            matched = matched[:limit]
    return [c["id"] for c in matched if c.get("id")]


def _rules_hits_path(state=None):
    if state is None:
        state = find_state_dir()
    counters = os.path.join(state, "counters")
    os.makedirs(counters, exist_ok=True)
    return os.path.join(counters, "rules-hits.json")


def _rules_load_hits(state=None):
    path = _rules_hits_path(state)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def _rules_save_hits(hits, state=None):
    path = _rules_hits_path(state)
    raw = json.dumps(hits, ensure_ascii=False, indent=2) + "\n"
    d = os.path.dirname(path)
    fd, tmp = tempfile.mkstemp(prefix="rules-hits.", suffix=".json", dir=d)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(raw)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass


def record_hit(card_id, kit_dir=None, state=None, extra=None):
    """Инкремент hit в стейте: counters/rules-hits.json + journal card_injected.

    Источник правды hit — только `.orchestration/counters/rules-hits.json`.
    rules/manifest.json не читаем и не пишем (поле manifest.hit игнорируется).
    Возвращает новый hit или None если id не найден в manifest.
    """
    if not card_id:
        return None
    if state is None:
        state = find_state_dir()
    manifest = load_manifest(kit_dir)
    card = _rules_card_by_id(manifest, card_id)
    if card is None:
        return None
    hits = _rules_load_hits(state)
    hit = int(hits.get(card_id) or 0) + 1
    hits[card_id] = hit
    _rules_save_hits(hits, state)
    entry = {
        "kind": "card_injected",
        "card": card_id,
        "ts": time.time(),
        "hit": hit,
    }
    if isinstance(extra, dict):
        for k, v in extra.items():
            if k not in entry and k not in ("kind",):
                entry[k] = v
    # journal_append пишет в find_state_dir(); временно ORCHESTRATION_DIR
    prev = os.environ.get("ORCHESTRATION_DIR")
    try:
        os.environ["ORCHESTRATION_DIR"] = state
        journal_append(entry)
    finally:
        if prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = prev
    return hit


def _rules_card_file_path(card, kit_dir=None, archived=False):
    cat = card.get("категория") or "_misc"
    cid = card.get("id") or "unknown"
    base = rules_archive_dir(kit_dir) if archived else rules_cards_dir(kit_dir)
    return os.path.join(base, cat, "%s.md" % cid)


def role_to_komu(role):
    """Каталог роли → уровень RULES_KOMU; неизвестная → None (безадресный).

    commander→commander, meta/front-general→general, meta/front-colonel→colonel,
    code/*→executor, обёртки/wrapper→wrapper, panel→panel; голый токен из
    RULES_KOMU — как есть.
    """
    if not isinstance(role, str) or not role.strip():
        return None
    r = normalize_journal_role(role)
    if not isinstance(r, str) or not r.strip():
        return None
    r = r.replace("\\", "/").strip().lstrip("./")
    # голый уровень или «wrapper.md»
    stem = r[:-3] if r.endswith(".md") and "/" not in r else r
    if stem in RULES_KOMU:
        return stem
    if r in RULES_KOMU:
        return r
    low = r.lower()
    base = low.split("/")[-1]
    if "commander" in base or low in ("commander", "commander.md"):
        return "commander"
    if low.startswith("meta/"):
        if "front-general" in low or base == "front-general.md":
            return "general"
        if "front-colonel" in low or base == "front-colonel.md":
            return "colonel"
        if "panel" in base or "/panel" in low:
            return "panel"
        return None
    if "wrapper" in base or low.startswith("wrapper/") or "/wrapper/" in low:
        return "wrapper"
    if low.startswith("code/"):
        return "executor"
    # прочие доменные роли каталога — исполнители
    if "/" in r and r.endswith(".md"):
        return "executor"
    return None


_RULES_SECTION_RE = re.compile(
    r"^##\s+(\S[^\n]*?)\s*$", re.MULTILINE)
_RULES_KEYFILE_RE = re.compile(
    r"(?:(?<![A-Za-z0-9_])(?:bin|rules|skills|panel|meta|code|references)"
    r"/[\w./-]+|[\w.-]+\.(?:py|md|json|sh|key))")


def _rules_parse_sections(text):
    """Разбор ## секций карточки → {имя_нижний: текст}."""
    if not text:
        return {}
    matches = list(_RULES_SECTION_RE.finditer(text))
    out = {}
    for i, m in enumerate(matches):
        name = (m.group(1) or "").strip().lower()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        # одна-две строки части; forematter до ## игнорируем
        lines = [ln.strip() for ln in body.splitlines() if ln.strip()
                 and not ln.strip().startswith("---")]
        out[name] = "\n".join(lines[:2]).strip()
    return out


def load_card_content(card_id, kit_dir=None):
    """Карточка по id: path/type/sections + essence/golden/failure. None если нет."""
    if not card_id:
        return None
    manifest = load_manifest(kit_dir)
    card = _rules_card_by_id(manifest, card_id)
    if card is None:
        return None
    path = _rules_card_file_path(card, kit_dir, archived=bool(card.get("archived")))
    text = ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        text = ""
    sections = _rules_parse_sections(text)
    ctype = card.get("type") or ""
    essence = (sections.get("правило") or sections.get("кейс")
               or sections.get("ловушка") or "")
    golden = (sections.get("золотая ссылка") or sections.get("пример") or "")
    failure = (sections.get("ловушка") or sections.get("признак")
               or sections.get("кейс") or "")
    return {
        "id": card_id,
        "type": ctype,
        "path": path,
        "кому": card.get("кому"),
        "когда": card.get("когда"),
        "категория": card.get("категория"),
        "sections": sections,
        "essence": essence,
        "golden": golden,
        "failure": failure,
        "text": text,
    }


def _rules_clip(s, n=80):
    s = re.sub(r"\s+", " ", (s or "").strip())
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)].rstrip() + "…"


def _rules_prompt_keyfiles(prompt_text):
    if not prompt_text:
        return []
    seen = []
    for m in _RULES_KEYFILE_RE.finditer(prompt_text):
        tok = m.group(0)
        if tok not in seen:
            seen.append(tok)
    return seen


def _rules_card_oneliner(card_id, kit_dir=None):
    """Однострочник кандидата для criteria Choice rules-apply."""
    info = load_card_content(card_id, kit_dir=kit_dir)
    if not info:
        return card_id
    ctype = info.get("type") or ""
    essence = _rules_clip(info.get("essence") or card_id, 80)
    cat = info.get("категория") or ""
    if cat:
        return "%s [%s] %s" % (ctype, cat, essence)
    return "%s %s" % (ctype, essence)


def _jev_advise_base_cmd():
    """argv-префикс jev-advise; ORCH_JEV_ADVISE — путь к моку/заглушке."""
    override = (os.environ.get("ORCH_JEV_ADVISE") or "").strip()
    path = override or os.path.join(KIT_DIR, "bin", "jev-advise.py")
    if path.endswith(".py"):
        return [sys.executable, path]
    return [path]


def _rules_run_jev_advise(point_id, caller, state_text, questions,
                          timeout_s=None):
    """subprocess jev-advise; fail-open → (None, reason). Успех → (dict, None)."""
    if timeout_s is None:
        timeout_s = RULES_JEV_TIMEOUT_S
    qpath = None
    try:
        fd, qpath = tempfile.mkstemp(prefix="jev-q.", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(questions, f, ensure_ascii=False)
        cmd = _jev_advise_base_cmd() + [
            "--point", str(point_id),
            "--caller", str(caller or "rules"),
            "--state-text", str(state_text or ""),
            "--questions-file", qpath,
        ]
        r = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=float(timeout_s),
        )
        if (r.stderr or "").strip():
            sys.stderr.write("jev-advise %s stderr: %s\n" % (
                point_id, (r.stderr or "").strip()[:500]))
        if r.returncode != 0:
            sys.stderr.write(
                "jev-advise %s exit %s (fail-open)\n" % (
                    point_id, r.returncode))
            return None, "exit %s" % r.returncode
        out = (r.stdout or "").strip()
        if not out:
            return None, "empty stdout"
        try:
            data = json.loads(out.splitlines()[-1])
        except Exception as e:
            return None, "bad json: %s" % e
        if not isinstance(data, dict):
            return None, "non-object"
        return data, None
    except subprocess.TimeoutExpired:
        sys.stderr.write(
            "jev-advise %s timeout (fail-open)\n" % point_id)
        return None, "timeout"
    except Exception as e:
        sys.stderr.write(
            "jev-advise %s error: %s (fail-open)\n" % (point_id, e))
        return None, str(e)
    finally:
        if qpath:
            try:
                os.unlink(qpath)
            except Exception:
                pass


def _rules_ids_from_choice_advice(advice, candidate_ids, limit=3):
    """Choice advice → 1–3 id из шорт-листа; defer/absent → []."""
    if not isinstance(advice, dict):
        return []
    if advice.get("action") == "defer" or advice.get("band") in (
            "low", "absent"):
        return []
    cands = [c for c in candidate_ids if c]
    cand_set = set(cands)
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    if limit == 0:
        return []
    probs = advice.get("probabilities")
    if isinstance(probs, dict) and probs:
        ranked = []
        for cid, p in probs.items():
            if cid not in cand_set:
                continue
            try:
                ranked.append((cid, float(p)))
            except Exception:
                continue
        ranked.sort(key=lambda x: (
            -x[1], cands.index(x[0]) if x[0] in cands else 999))
        out = [cid for cid, _ in ranked[:limit]]
        if out:
            return out
    choice = advice.get("choice")
    if isinstance(choice, list):
        return [c for c in choice if c in cand_set][:limit]
    if isinstance(choice, str) and choice in cand_set:
        return [choice]
    return []


def select_rule_card_ids(komu, kogda, category=None, kit_dir=None, limit=3,
                         state_text=None, caller=None, role=None,
                         task_hint=None):
    """Трёхступенчатый выбор id карточек для вклейки (Э2 / F-RULES R4).

    После сужения адреса (кому×когда×категория; Jev базу НЕ видит),
    n = |кандидаты|:
      - n==0 → [] , Jev нет
      - n≤3 → вставить все (≤limit), Jev НЕ вызывать
      - 4≤n≤10 → jev-advise точку rules-apply → 1–3 id
      - n>10 → топ-10 по hit (тай-брейк как match_cards) → ступень 4≤n≤10

    defer ответа Choice → без вставки ([]). Ошибка/таймаут jev → []
    (fail-open; вызывающий не падает).
    """
    if komu not in RULES_KOMU or kogda not in RULES_KOGDA:
        return []
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    if limit == 0:
        return []
    candidates = match_cards(
        komu, kogda, category=category, kit_dir=kit_dir, limit=None)
    n = len(candidates)
    if n == 0:
        return []
    if n <= 3:
        return candidates[:limit]
    shortlist = candidates
    if n > RULES_JEV_SHORTLIST_MAX:
        shortlist = candidates[:RULES_JEV_SHORTLIST_MAX]
    # 4≤n≤10 (или урезанный топ-10): Choice rules-apply
    criteria = {}
    for cid in shortlist:
        criteria[cid] = _rules_card_oneliner(cid, kit_dir=kit_dir)
    st = state_text
    if not st:
        parts = ["шаг: %s" % kogda, "роль: %s" % (role or komu)]
        hint = _rules_clip(task_hint or "", 160)
        if hint:
            parts.append(hint)
        st = "; ".join(parts)
    questions = {
        "rules-apply": {
            "type": "choice",
            "instructions": (
                "выбрать 1–3 карточки rules для вклейки на этом шаге"),
            "criteria": criteria,
        }
    }
    data, err = _rules_run_jev_advise(
        "rules-apply", caller or "rules-apply", st, questions)
    if err or not data:
        return []
    advice = (data.get("advice") or {}).get("rules-apply")
    return _rules_ids_from_choice_advice(advice, shortlist, limit=limit)


def _rules_format_inject_for_ids(ids, kit_dir=None, state=None, limit=3,
                                 extra=None):
    """Строки вклейки Э1 + record_hit для списка id."""
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    lines = []
    for cid in ids or []:
        if len(lines) >= limit:
            break
        info = load_card_content(cid, kit_dir=kit_dir)
        if not info:
            continue
        essence = _rules_clip(info.get("essence") or cid, 80)
        path = info.get("path") or ""
        lines.append("[rules/%s] %s → %s" % (cid, essence, path))
        try:
            record_hit(cid, kit_dir=kit_dir, state=state, extra=extra)
        except Exception:
            pass
    return lines


def format_step_inject_lines(komu, kogda, kit_dir=None, state=None, limit=3,
                             extra=None, role=None, task_hint=None,
                             caller=None):
    """Вклейки шага: 1–3 строк «[rules/<id>] <суть>≤80 → <path>» + record_hit.

    Выбор id — select_rule_card_ids (лестница Э2). Безадресный / пусто → [].
    """
    if komu not in RULES_KOMU or kogda not in RULES_KOGDA:
        return []
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    extra = dict(extra) if isinstance(extra, dict) else {}
    role = role or extra.get("role")
    caller = caller or extra.get("caller") or "step-inject"
    ids = select_rule_card_ids(
        komu, kogda, category=None, kit_dir=kit_dir, limit=limit,
        role=role, task_hint=task_hint, caller=caller)
    return _rules_format_inject_for_ids(
        ids, kit_dir=kit_dir, state=state, limit=limit, extra=extra)


def format_tried_before_lines(komu, kit_dir=None, state=None, limit=3,
                              extra=None, role=None, task_hint=None,
                              caller=None, state_text=None):
    """Noul tried-before перед launch-прецедентами (fail-open).

    Категории прошлых попыток адреса = категории DON'T/CASE на komu×launch.
    yes → «уже пробовали (категория X) — см. карточки» + DON'T/CASE категории;
    no/defer/fail → [].
    """
    if komu not in RULES_KOMU:
        return []
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    if limit == 0:
        return []
    manifest = load_manifest(kit_dir)
    addr_ids = match_cards(komu, "launch", category=None, kit_dir=kit_dir,
                           limit=None)
    cats = []
    cat_cards = {}
    for cid in addr_ids:
        card = _rules_card_by_id(manifest, cid)
        if not card:
            continue
        if card.get("type") not in ("DON'T", "CASE"):
            continue
        cat = card.get("категория")
        if not cat:
            continue
        if cat not in cat_cards:
            cat_cards[cat] = []
            cats.append(cat)
        cat_cards[cat].append(cid)
    if not cats:
        return []
    extra = dict(extra) if isinstance(extra, dict) else {}
    role = role or extra.get("role") or komu
    st = state_text
    if not st:
        parts = [
            "роль: %s" % role,
            "категории прошлых попыток: %s" % ", ".join(cats),
        ]
        hint = _rules_clip(task_hint or "", 160)
        if hint:
            parts.append(hint)
        st = "; ".join(parts)
    questions = {
        "tried-before": {
            "type": "noul",
            "instructions": (
                "пробовали ли такой подход раньше по категориям: %s"
                % ", ".join(cats)),
        }
    }
    data, err = _rules_run_jev_advise(
        "tried-before",
        caller or extra.get("caller") or "tried-before",
        st, questions)
    if err or not data:
        return []
    advice = (data.get("advice") or {}).get("tried-before") or {}
    if advice.get("action") != "yes" and advice.get("band") != "yes":
        return []
    lines = []
    for cat in cats:
        if len(lines) >= limit:
            break
        lines.append(
            "уже пробовали (категория %s) — см. карточки" % cat)
        for cid in cat_cards.get(cat) or []:
            if len(lines) >= limit:
                break
            info = load_card_content(cid, kit_dir=kit_dir)
            if not info:
                continue
            ctype = info.get("type") or ""
            if ctype == "DON'T":
                case = _rules_clip(
                    info.get("failure") or info.get("essence") or cid, 120)
                lines.append("осторожно: %s %s" % (cid, case))
            else:
                ref = _rules_clip(
                    info.get("golden") or info.get("essence") or cid, 120)
                lines.append("прецедент: %s %s" % (cid, ref))
            try:
                record_hit(cid, kit_dir=kit_dir, state=state, extra=extra)
            except Exception:
                pass
    return lines[:limit]


def format_precedent_lines(komu, prompt_text=None, kit_dir=None, state=None,
                           limit=3, extra=None):
    """Прецеденты до старта (launch): DO/CASE «прецедент:» + DON'T «осторожно:».

    Адрес DO/CASE: komu×launch. DON'T: тот же адрес и/или ключевые файлы промта
    в теле карточки. Суммарно ≤limit (дефолт 3). Безадресный → [].
    Выбор адресных id — select_rule_card_ids (лестница Э2); keyfile-DON'T
    дополняются поверх (Э1), без повторного Jev.
    """
    if komu not in RULES_KOMU:
        return []
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    if limit == 0:
        return []
    manifest = load_manifest(kit_dir)
    extra = dict(extra) if isinstance(extra, dict) else {}
    # шорт-лист через лестницу (n≤3 без Jev; 4–10 → rules-apply)
    addr_ids = select_rule_card_ids(
        komu, "launch", category=None, kit_dir=kit_dir, limit=None,
        role=extra.get("role"), caller=extra.get("caller") or "precedent",
        task_hint=_rules_clip(prompt_text or "", 160) or None)
    # limit=None в select → внутри станет 3; для прецедентов нужен полный
    # шорт-лист типов — при n≤3 select вернёт всех; при Jev — 1–3.
    # Добираем адресные id без обхода лестницы нельзя; используем результат.
    do_case = []
    dont = []
    for cid in addr_ids:
        card = _rules_card_by_id(manifest, cid)
        if not card:
            continue
        t = card.get("type")
        if t in ("DO", "CASE"):
            do_case.append(cid)
        elif t == "DON'T":
            dont.append(cid)
    # DON'T по ключевым файлам промта (карточки+manifest, не journal/git)
    keyfiles = _rules_prompt_keyfiles(prompt_text)
    if keyfiles:
        have = set(dont) | set(do_case)
        for card in _rules_active_cards(manifest):
            if card.get("type") != "DON'T":
                continue
            cid = card.get("id")
            if not cid or cid in have:
                continue
            info = load_card_content(cid, kit_dir=kit_dir)
            blob = ((info or {}).get("text") or "") + " " + " ".join(
                str(card.get(k) or "") for k in ("id", "категория", "run_ref"))
            if any(kf in blob for kf in keyfiles):
                dont.append(cid)
                have.add(cid)
    lines = []
    for cid in do_case:
        if len(lines) >= limit:
            break
        info = load_card_content(cid, kit_dir=kit_dir)
        if not info:
            continue
        ref = _rules_clip(info.get("golden") or info.get("essence") or cid, 120)
        lines.append("прецедент: %s %s" % (cid, ref))
        try:
            record_hit(cid, kit_dir=kit_dir, state=state, extra=extra)
        except Exception:
            pass
    for cid in dont:
        if len(lines) >= limit:
            break
        info = load_card_content(cid, kit_dir=kit_dir)
        if not info:
            continue
        case = _rules_clip(info.get("failure") or info.get("essence") or cid, 120)
        lines.append("осторожно: %s %s" % (cid, case))
        try:
            record_hit(cid, kit_dir=kit_dir, state=state, extra=extra)
        except Exception:
            pass
    return lines


def archive_lru(kit_dir=None, state=None):
    """>25 активных → старейшие counters-hit=0 в archive/; hit>0 не архивируется.

    hit — из counters/rules-hits.json (стейт), не из manifest.
    Возвращает список id, ушедших в archive. Активные после вызова ≤25
    (если хватает hit=0 кандидатов).
    """
    manifest = load_manifest(kit_dir)
    hits = _rules_load_hits(state)
    active = _rules_active_cards(manifest)
    limit = RULES_ACTIVE_LIMIT
    if len(active) <= limit:
        return []
    # кандидаты: counters-hit==0, старейшие created сначала
    zeros = [c for c in active
             if int(hits.get(c.get("id")) or 0) == 0]
    zeros.sort(key=lambda x: (float(x.get("created") or 0),
                              str(x.get("id") or "")))
    need = len(active) - limit
    moved = []
    arch_root = rules_archive_dir(kit_dir)
    os.makedirs(arch_root, exist_ok=True)
    for c in zeros:
        if need <= 0:
            break
        cid = c.get("id")
        if not cid:
            continue
        src = _rules_card_file_path(c, kit_dir, archived=False)
        dst = _rules_card_file_path(c, kit_dir, archived=True)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isfile(src):
            try:
                os.replace(src, dst)
            except Exception:
                try:
                    import shutil
                    shutil.move(src, dst)
                except Exception:
                    pass
        c["archived"] = True
        moved.append(cid)
        need -= 1
    if moved:
        save_manifest(manifest, kit_dir)
    return moved


def resurrect(card_id, kit_dir=None):
    """Вернуть карточку из archive/ в cards/ при повторе адреса/кейса.

    Снимает archived; born_at и created не меняет (иммутабельны); пишет
    migrated_at=now. LRU не вызывается здесь (вызывающий может archive_lru
    отдельно) — иначе hit=0 сразу уходит обратно.
    Возвращает True если воскрешена.
    """
    if not card_id:
        return False
    manifest = load_manifest(kit_dir)
    card = _rules_card_by_id(manifest, card_id)
    if card is None:
        return False
    if not card.get("archived"):
        return False
    src = _rules_card_file_path(card, kit_dir, archived=True)
    dst = _rules_card_file_path(card, kit_dir, archived=False)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(src):
        try:
            os.replace(src, dst)
        except Exception:
            try:
                import shutil
                shutil.move(src, dst)
            except Exception:
                pass
    card["archived"] = False
    card["migrated_at"] = time.time()
    save_manifest(manifest, kit_dir)
    return True


_RULES_ID_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _rules_slug(text, max_len=40):
    """ASCII-slug для id карточки."""
    s = (text or "").strip().lower()
    # простая транслит-таблица для частых кириллических корней
    tr = {
        "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
        "ж": "zh", "з": "z", "и": "i", "й": "j", "к": "k", "л": "l", "м": "m",
        "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
        "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sch",
        "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    }
    out = []
    for ch in s:
        if ch in tr:
            out.append(tr[ch])
        elif ("a" <= ch <= "z") or ("0" <= ch <= "9") or ch in "-_":
            out.append(ch)
        elif ch.isspace() or ch in "/\\.:;,":
            out.append("-")
        else:
            out.append("-")
    slug = _RULES_ID_SLUG_RE.sub("-", "".join(out)).strip("-")
    if not slug:
        slug = "card"
    return slug[:max_len].strip("-") or "card"


def _rules_type_prefix(тип):
    if тип == "DON'T":
        return "dont"
    if тип == "DO":
        return "do"
    if тип == "CASE":
        return "case"
    return "card"


def _rules_normalize_parts(части):
    """части → [(title, body≤2 строк)]. Канон: list[(title, body)]; иначе ValueError."""
    if not isinstance(части, (list, tuple)):
        raise ValueError("части must be list[(title, body)]")
    out = []
    for it in части:
        if not (isinstance(it, (list, tuple)) and len(it) >= 2):
            raise ValueError("части must be list[(title, body)]")
        title = (str(it[0]) if it[0] is not None else "").strip()
        if not title:
            continue
        lines = []
        for ln in str(it[1] or "").splitlines():
            s = ln.strip()
            if s:
                lines.append(s)
            if len(lines) >= 2:
                break
        out.append((title, "\n".join(lines)))
    return out


def add_rule_card(тип, категория, кому, когда, части, run_ref, kit_dir=None,
                  card_id=None):
    """Родить карточку: rules/cards/<кат>/<id>.md + запись manifest.

    Поля manifest: id/type/кому/когда/категория/hit/created/born_at/run_ref.
    Формат файла: frontmatter + ## части (≤2 строки/часть).
    части: list[(title, body)] или dict {title: body} (адаптер → list пар).
    Карточка с run_ref гасит rules_no_retro для этой волны (см. _rules_no_retro_ids).
    born_at — штамп рождения (ts); age-grace rules_dead смотрит born_at||created.
    Возвращает id; при невалидных аргументах — ValueError.
    """
    if тип not in RULES_TYPES:
        raise ValueError("unknown rules type: %r" % (тип,))
    if кому not in RULES_KOMU:
        raise ValueError("unknown rules кому: %r" % (кому,))
    if когда not in RULES_KOGDA:
        raise ValueError("unknown rules когда: %r" % (когда,))
    if not категория or not isinstance(категория, str):
        raise ValueError("категория required")
    if isinstance(части, dict):
        части = [(k, v) for k, v in части.items()]
    parts = _rules_normalize_parts(части)
    if not parts:
        raise ValueError("части required")
    manifest = load_manifest(kit_dir)
    if card_id:
        cid = str(card_id).strip()
    else:
        seed = parts[0][1] or parts[0][0] or категория
        base = "%s-%s" % (_rules_type_prefix(тип), _rules_slug(seed))
        cid = base
        n = 2
        while _rules_card_by_id(manifest, cid) is not None:
            cid = "%s-%d" % (base, n)
            n += 1
    if _rules_card_by_id(manifest, cid) is not None:
        raise ValueError("card id already exists: %s" % cid)
    active_in_cat = sum(
        1 for c in _rules_active_cards(manifest)
        if c.get("категория") == категория)
    if active_in_cat >= RULES_CATEGORY_LIMIT:
        raise ValueError(
            "category %r has %d active cards (limit %d); "
            "split into a subcategory (aliases) before adding"
            % (категория, active_in_cat, RULES_CATEGORY_LIMIT))
    now = time.time()
    card = {
        "id": cid,
        "type": тип,
        "кому": кому,
        "когда": когда,
        "категория": категория,
        "hit": 0,
        "created": now,
        "born_at": now,
        "run_ref": run_ref,
    }
    # файл
    path = _rules_card_file_path(card, kit_dir, archived=False)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fm = [
        "---",
        "id: %s" % cid,
        "type: %s" % тип,
        "кому: %s" % кому,
        "когда: %s" % когда,
        "категория: %s" % категория,
    ]
    if run_ref:
        fm.append("run-ref: %s" % run_ref)
    fm.append("---")
    body = []
    for title, text in parts:
        body.append("")
        body.append("## %s" % title)
        if text:
            body.append(text)
    raw = "\n".join(fm + body) + "\n"
    with open(path, "w", encoding="utf-8") as f:
        f.write(raw)
    manifest.setdefault("cards", []).append(card)
    save_manifest(manifest, kit_dir)
    return cid


# Гейт-отказы (стоп/refuse). Warn-датчики (FRONT_BUDGET_WARN,
# COMPASS_OVERFLOW*) сюда не входят — OK-волну problem не делают.
_RULES_GATE_REFUSALS = frozenset({
    "FRONT_REQUIRED", "FRONT_CLOSED", "BUDGET_HARD",
    "SECRETS_IN_PROMPT", "API_KEY_REQUIRED",
})
# exit-коды обёрток при отказе гейта (run-exec / run-cloud).
_GATE_REFUSE_EXITS = frozenset({5, 6, 7, 8, 9, 11, 12})


def _end_is_gate_refuse(exit_code, gates):
    """True если end — отказ гейта (exit∈_GATE_REFUSE_EXITS или refusal gates)."""
    try:
        if exit_code is not None and int(exit_code) in _GATE_REFUSE_EXITS:
            return True
    except (TypeError, ValueError):
        pass
    if isinstance(gates, list):
        for g in gates:
            if isinstance(g, str) and g in _RULES_GATE_REFUSALS:
                return True
    return False


def _rules_end_is_problem(entry):
    """end с PROBLEMS/BLOCKED в verdict или гейт-отказом в gates.

    Непустые gates → problem только при отказе (FRONT_REQUIRED /
    FRONT_CLOSED / BUDGET_HARD / SECRETS_IN_PROMPT / API_KEY_REQUIRED).
    Warn-датчики (FRONT_BUDGET_WARN и пр.) problem не считают.
    """
    if not isinstance(entry, dict) or entry.get("kind") != "end":
        return False
    gates = entry.get("gates") or []
    if isinstance(gates, list):
        for g in gates:
            if isinstance(g, str) and g in _RULES_GATE_REFUSALS:
                return True
    verdict = entry.get("verdict") or ""
    if not isinstance(verdict, str):
        verdict = str(verdict)
    if "PROBLEMS" in verdict or "BLOCKED" in verdict:
        return True
    return False


def _rules_wave_ends(entries):
    """end-записи ролей волны (для rules_dead N волн)."""
    starts = _journal_start_index(entries)
    out = []
    for e in entries:
        if e.get("kind") != "end":
            continue
        rid = e.get("id")
        st = starts.get(rid) if rid else None
        role = normalize_journal_role(st.get("role")) if st else None
        if role and _role_is_wave_work(role):
            out.append(e)
        elif st is None and rid:
            # синтетика /tmp без start — считаем волной по end
            out.append(e)
    return out


def _rules_no_retro_ids(entries, kit_dir=None):
    """Последняя проблемная волна без карточки run-ref → [id]; иначе [].

    Волна без ошибок → никогда не красный (пустой список, если последняя
    проблемная уже закрыта карточкой или проблемных нет).
    """
    problem_ends = [e for e in entries if _rules_end_is_problem(e)]
    if not problem_ends:
        return []
    last = problem_ends[-1]
    wid = last.get("id")
    if not wid:
        return []
    manifest = load_manifest(kit_dir)
    for c in manifest.get("cards") or []:
        if not isinstance(c, dict):
            continue
        ref = c.get("run_ref") or c.get("run-ref")
        if ref == wid:
            return []
    return [wid]


def _rules_manifest_has_run_ref(run_id, kit_dir=None):
    """Есть ли в manifest карточка с run_ref/run-ref == run_id."""
    if not run_id:
        return False
    manifest = load_manifest(kit_dir)
    for c in manifest.get("cards") or []:
        if not isinstance(c, dict):
            continue
        ref = c.get("run_ref") or c.get("run-ref")
        if ref == run_id:
            return True
    return False


def _rules_is_critic_role(role):
    r = normalize_journal_role(role)
    if not isinstance(r, str) or not r:
        return False
    low = r.replace("\\", "/").lower()
    base = low.split("/")[-1]
    return (
        base in ("code-reviewer.md", "fact-checker.md")
        or "code-reviewer" in low
        or "fact-checker" in low
    )


def _rules_verdict_is_ok(verdict):
    if not isinstance(verdict, str):
        return False
    v = verdict.strip()
    if not v.startswith("Вердикт:"):
        return False
    rest = v[len("Вердикт:"):].strip()
    if rest == "OK":
        return True
    return (rest.startswith("OK")
            and "PROBLEMS" not in rest
            and "BLOCKED" not in rest)


def _rules_verdict_is_problem(verdict):
    if not isinstance(verdict, str):
        return False
    return "PROBLEMS" in verdict or "BLOCKED" in verdict


def find_run_log(run_id, state=None, session=None, log_path=None):
    """Путь к логу волны: явный / sessions/.../run.log / cursor-run-<id>.log."""
    if log_path:
        return log_path
    if not run_id:
        return None
    if state is None:
        state = find_state_dir()
    candidates = []
    if session:
        candidates.append(
            os.path.join(state, "sessions", session, "runs", run_id, "run.log"))
    # любой sessions/*/runs/<id>/run.log
    sess_root = os.path.join(state, "sessions")
    if os.path.isdir(sess_root):
        try:
            for sid in os.listdir(sess_root):
                p = os.path.join(sess_root, sid, "runs", run_id, "run.log")
                candidates.append(p)
        except Exception:
            pass
    candidates.append(os.path.join(state, "cursor-run-%s.log" % run_id))
    candidates.append(os.path.join(state, "runs", run_id, "run.log"))
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return candidates[0] if candidates else None


def find_run_dir(run_id, state=None, session=None):
    """Каталог runs/<id>/ (есть prompt.run.md|artifact.md|run.log); иначе None."""
    if not run_id:
        return None
    if state is None:
        state = find_state_dir()
    candidates = []
    if session:
        candidates.append(
            os.path.join(state, "sessions", session, "runs", run_id))
    sess_root = os.path.join(state, "sessions")
    if os.path.isdir(sess_root):
        try:
            for sid in os.listdir(sess_root):
                candidates.append(
                    os.path.join(sess_root, sid, "runs", run_id))
        except Exception:
            pass
    candidates.append(os.path.join(state, "runs", run_id))
    markers = ("prompt.run.md", "artifact.md", "run.log", "probe-receipt.md")
    for d in candidates:
        if not d or not os.path.isdir(d):
            continue
        for m in markers:
            if os.path.isfile(os.path.join(d, m)):
                return d
    return None


# --- F-ACCEPT: probes_missing / chip_silenced (канон приёмки v1) -----------
# ADDITIVE MARKER: FA-C3 probes_missing begin

_PROBE_BLOCK_KEYS = (
    "проба:",
    "оракул:",
    "полигон:",
    "класс-доказательства:",
)
_PROBE_CLASS_RE = re.compile(
    r"класс-доказательства:\s*(function|oracle|narrative)\b", re.I)
_RECEIPT_FIELD_RE = re.compile(
    r"(?m)^\s*(probe|cmd|exit|oracle_match|ts|critic_id|artifact)\s*:\s*(.*\S)\s*$")
_RECEIPT_INLINE_RE = re.compile(
    r"\b(probe|cmd|exit|oracle_match|ts|critic_id|artifact)\s*:\s*([^;]+)")


def _role_is_code_or_fix_wave(role):
    """Волна кода/фикса: coder* или code/*fix* (не критик/warden/docs)."""
    role = normalize_journal_role(role)
    if not isinstance(role, str) or not role:
        return False
    if _role_is_coder(role):
        return True
    low = role.lower().replace("\\", "/")
    if not low.startswith("code/"):
        return False
    if _role_is_critic(role) or _role_is_gitwarden(role):
        return False
    if low in ("code/docs-keeper.md", "code/simplicity-warden.md"):
        return False
    return "fix" in low


def _read_text_silent(path):
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            return f.read()
    except Exception:
        return ""


def parse_probe_block(text):
    """Машиночитаемый блок пробы (§1): проба/оракул/полигон/класс-доказательства.

    Возвращает dict полей или None, если блок неполон/отсутствует.
    """
    if not text or not isinstance(text, str):
        return None
    found = {}
    for line in text.splitlines():
        s = line.lstrip()
        low = s.lower()
        for key in _PROBE_BLOCK_KEYS:
            if low.startswith(key) or s.startswith(key):
                # сохраняем канонический ключ без двоеточия
                k = key[:-1]
                val = s.split(":", 1)[1].strip() if ":" in s else ""
                if k not in found:
                    found[k] = val
                break
    if len(found) < 4:
        return None
    cls = found.get("класс-доказательства", "")
    if not _PROBE_CLASS_RE.search("класс-доказательства: " + cls):
        # допускаем значение уже без префикса
        if cls.strip().lower() not in ("function", "oracle", "narrative"):
            return None
    return found


def wave_has_probe_block(run_id, state=None):
    """True если prompt.run.md или artifact.md волны содержит блок пробы §1."""
    d = find_run_dir(run_id, state=state)
    if not d:
        return False
    for name in ("prompt.run.md", "artifact.md"):
        block = parse_probe_block(_read_text_silent(os.path.join(d, name)))
        if block:
            return True
    return False


def wave_probe_artifact_path(run_id, state=None):
    """Путь artifact.md волны (предпочтительно) или prompt.run.md с блоком."""
    d = find_run_dir(run_id, state=state)
    if not d:
        return None
    art = os.path.join(d, "artifact.md")
    if os.path.isfile(art):
        return art
    pr = os.path.join(d, "prompt.run.md")
    if os.path.isfile(pr) and parse_probe_block(_read_text_silent(pr)):
        return pr
    return art if os.path.isfile(art) else None


def _oracle_expected_exit(oracle_text):
    """Числовой exit из текста оракула; None если оракул не про exit."""
    if not oracle_text:
        return None
    m = re.search(r"exit\s*[:=]?\s*(\d+)", oracle_text, re.I)
    if m:
        return int(m.group(1))
    m = re.search(r"^\s*(\d+)\s*$", oracle_text.strip())
    if m:
        return int(m.group(1))
    return None


def _parse_receipt_records(text):
    """Список dict-записей квитанции (multiline или inline ;-поля)."""
    if not text or not isinstance(text, str):
        return []
    records = []
    # сначала inline-строки вида probe: …; cmd: …; exit: …
    for line in text.splitlines():
        if "probe:" not in line.lower() and not line.lstrip().lower().startswith(
                "probe:"):
            # строка без probe — не inline-запись
            if "cmd:" in line.lower() and "exit:" in line.lower():
                pass
            else:
                continue
        if ";" in line and "exit:" in line.lower():
            fields = {}
            for m in _RECEIPT_INLINE_RE.finditer(line):
                fields[m.group(1).lower()] = m.group(2).strip()
            if fields:
                records.append(fields)
    if records:
        return records
    # multiline блоки
    cur = {}
    for m in _RECEIPT_FIELD_RE.finditer(text):
        k = m.group(1).lower()
        v = m.group(2).strip()
        if k == "probe" and cur.get("probe"):
            records.append(cur)
            cur = {}
        cur[k] = v
    if cur:
        records.append(cur)
    return records


def parse_probe_receipt(text, artifact_path=None, run_id=None):
    """Валидация квитанции §3 → (ok: bool, reason: str).

    ok только если: все поля; exit числом; exit==оракулу (если числовой);
    oracle_match true; ts≥mtime(артефакта); artifact ссылается на волну;
    проза без cmd/exit = violation.
    """
    if not text or not isinstance(text, str) or not text.strip():
        return False, "empty"
    # проза без структуры
    if "cmd:" not in text.lower() or "exit:" not in text.lower():
        return False, "prose_no_cmd_exit"
    records = _parse_receipt_records(text)
    if not records:
        return False, "no_records"
    art_mtime = None
    oracle_exit = None
    if artifact_path and os.path.isfile(artifact_path):
        try:
            art_mtime = float(os.path.getmtime(artifact_path))
        except Exception:
            art_mtime = None
        block = parse_probe_block(_read_text_silent(artifact_path))
        if block:
            oracle_exit = _oracle_expected_exit(block.get("оракул", ""))
    art_base = None
    if artifact_path:
        art_base = os.path.basename(artifact_path)
    for rec in records:
        required = ("probe", "cmd", "exit", "oracle_match", "ts",
                    "critic_id", "artifact")
        for k in required:
            if k not in rec or rec[k] == "":
                return False, "missing_%s" % k
        try:
            exit_code = int(str(rec["exit"]).strip())
        except (TypeError, ValueError):
            return False, "exit_not_int"
        om = str(rec["oracle_match"]).strip().lower()
        if om not in ("true", "false"):
            return False, "oracle_match_bad"
        try:
            ts = float(str(rec["ts"]).strip())
        except (TypeError, ValueError):
            return False, "ts_not_number"
        if art_mtime is not None and ts < art_mtime:
            return False, "ts_stale"
        if oracle_exit is not None and exit_code != oracle_exit:
            return False, "exit_ne_oracle"
        # снятие чипа — только при oracle_match true
        if om != "true":
            return False, "oracle_match_false"
        art_ref = str(rec["artifact"]).strip()
        if run_id and art_ref not in (run_id, "artifact.md", art_base or ""):
            # допускаем путь, оканчивающийся на runs/<id>/artifact.md
            ok_ref = (
                art_ref.endswith("/" + run_id + "/artifact.md")
                or art_ref.endswith("\\" + run_id + "\\artifact.md")
                or run_id in art_ref.split("/")
                or run_id in art_ref.split("\\")
            )
            if not ok_ref and art_base and art_ref != art_base:
                return False, "artifact_mismatch"
    return True, "ok"


def find_probe_receipts(run_id, state=None):
    """Пути probe-receipt.md, относящиеся к волне (своя dir + чужие critic)."""
    if state is None:
        state = find_state_dir()
    out = []
    own = find_run_dir(run_id, state=state)
    if own:
        p = os.path.join(own, "probe-receipt.md")
        if os.path.isfile(p):
            out.append(p)
    sess_root = os.path.join(state, "sessions")
    if not os.path.isdir(sess_root):
        return out
    try:
        for sid in os.listdir(sess_root):
            runs = os.path.join(sess_root, sid, "runs")
            if not os.path.isdir(runs):
                continue
            for rid in os.listdir(runs):
                p = os.path.join(runs, rid, "probe-receipt.md")
                if not os.path.isfile(p):
                    continue
                if p in out:
                    continue
                txt = _read_text_silent(p)
                if run_id and run_id not in txt:
                    continue
                out.append(p)
    except Exception:
        pass
    return out


def wave_has_valid_probe_receipt(run_id, state=None):
    """True если есть ≥1 валидная квитанция §3 на артефакт волны."""
    art = wave_probe_artifact_path(run_id, state=state)
    for path in find_probe_receipts(run_id, state=state):
        ok, _reason = parse_probe_receipt(
            _read_text_silent(path), artifact_path=art, run_id=run_id)
        if ok:
            return True
    return False


def probes_missing(state=None, scan_limit=None):
    """id волн кода/фикса active-фронтов без блока пробы или без валидной квитанции.

    Скоп: только active-фронты (DON'T all-chips-green — чужие фронты не
    критерий приёмки текущего). Снятие только валидной квитанцией §3.
    Тихие ошибки → [].
    """
    try:
        if state is None:
            state = find_state_dir()
        if scan_limit is None:
            scan_limit = HEALTH_JOURNAL_SCAN_LIMIT
        path = os.path.join(state, "journal.jsonl")
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            return []
        except Exception:
            return []
        if scan_limit and len(lines) > scan_limit:
            lines = lines[-int(scan_limit):]
        entries = []
        for raw in lines:
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
        starts_by_id = _journal_start_index(entries)
        data = _load_fronts_at(state)
        active_fids = set()
        for fr in data.get("fronts") or []:
            if not isinstance(fr, dict):
                continue
            if fr.get("status") != "active":
                continue
            fid = fr.get("id")
            if isinstance(fid, str) and fid:
                active_fids.add(fid)
        out = []
        seen = set()
        for e in entries:
            if e.get("kind") != "end":
                continue
            rid = e.get("id")
            if not rid or rid in seen:
                continue
            st = starts_by_id.get(rid)
            if not st:
                continue
            fid = st.get("front")
            if fid not in active_fids:
                continue
            if not _role_is_code_or_fix_wave(st.get("role")):
                continue
            if st.get("readonly"):
                continue
            if _end_is_gate_refuse(e.get("exit"), e.get("gates") or []):
                continue
            has_block = wave_has_probe_block(rid, state=state)
            has_receipt = wave_has_valid_probe_receipt(rid, state=state)
            # нет блока ИЛИ нет валидной квитанции → красный
            if not has_block or not has_receipt:
                out.append(rid)
                seen.add(rid)
        return out
    except Exception:
        return []


def _panel_has_chip_label(chip_id, kit_dir=None):
    """True если panel/index.html объявляет label для chip_id."""
    if kit_dir is None:
        kit_dir = KIT_DIR
    path = os.path.join(kit_dir, "panel", "index.html")
    txt = _read_text_silent(path)
    if not txt:
        return False
    return ("%s:" % chip_id) in txt or ("%s :" % chip_id) in txt


def chip_silenced_ids(state=None, kit_dir=None, reported_probes=None):
    """id волн, где probes_missing погашен фильтром/UI без устранения причины.

    Срабатывает если сырой probes_missing непуст, а (a) ключ не в reported,
    или reported пуст при непустом raw, или (b) panel UI не объявляет чип.
    """
    try:
        if state is None:
            state = find_state_dir()
        if kit_dir is None:
            kit_dir = KIT_DIR
        raw = probes_missing(state=state)
        if not raw:
            return []
        silenced = False
        if reported_probes is None:
            # UI-глушение: нет label в панели
            if not _panel_has_chip_label("probes_missing", kit_dir=kit_dir):
                silenced = True
        else:
            if not isinstance(reported_probes, list):
                silenced = True
            elif len(reported_probes) == 0 and raw:
                silenced = True
            elif set(raw) - set(reported_probes):
                silenced = True
        if not silenced:
            # доп. проверка UI даже при корректном reported
            if not _panel_has_chip_label("probes_missing", kit_dir=kit_dir):
                silenced = True
        return list(raw) if silenced else []
    except Exception:
        return []

# ADDITIVE MARKER: FA-C3 probes_missing end


def _rules_wave_meta(run_id, state=None, session=None, log_path=None):
    """(verdict, gates) волны: journal_log_meta(log) → fallback journal end."""
    path = find_run_log(run_id, state=state, session=session, log_path=log_path)
    verdict, gates = None, []
    if path and os.path.isfile(path):
        verdict, gates = journal_log_meta(path)
    if verdict is not None or gates:
        return verdict, gates if isinstance(gates, list) else []
    # fallback: end-запись журнала
    if state is None:
        state = find_state_dir()
    for e in reversed(_journal_entries_at(state)):
        if e.get("kind") == "end" and e.get("id") == run_id:
            v = e.get("verdict")
            g = e.get("gates") or []
            if not isinstance(g, list):
                g = []
            return v, g
    return None, []


def _rules_wave_is_problem(verdict, gates):
    if isinstance(gates, list) and gates:
        return True
    return _rules_verdict_is_problem(verdict or "")


def _rules_wave_exemplary(entries, run_id):
    """Образцовая приёмка: критики ×3 OK (parent=run_id) + замер в journal.

    Замер: kind in (measure, замер) и/или поле measure/замер на связанной записи.
    """
    if not run_id:
        return False
    starts = _journal_start_index(entries)
    ok_critics = 0
    has_measure = False
    for e in entries:
        kind = e.get("kind")
        eid = e.get("id")
        if kind in ("measure", "замер"):
            if eid == run_id or e.get("run") == run_id or e.get("run_ref") == run_id:
                has_measure = True
            continue
        if kind != "end":
            continue
        st = starts.get(eid) if eid else None
        parent = (st or {}).get("parent") if st else None
        if e.get("measure") or e.get("замер"):
            if eid == run_id or parent == run_id or e.get("run_ref") == run_id:
                has_measure = True
        if parent == run_id and _rules_is_critic_role((st or {}).get("role")):
            if _rules_verdict_is_ok(e.get("verdict") or ""):
                ok_critics += 1
    return ok_critics >= 3 and has_measure


def format_retro_lines(run_id, state=None, kit_dir=None, session=None,
                       log_path=None):
    """Строки ретро-шага волны (конец волны / подкоманда retro).

    PROBLEMS/гейт без карточки run_ref → требование DON'T (чип — через health).
    Волна без ошибок → [] (чип чист); образцовая ×3 OK+замер → подсказка DO/CASE.
    """
    if not run_id:
        return []
    if state is None:
        state = find_state_dir()
    verdict, gates = _rules_wave_meta(
        run_id, state=state, session=session, log_path=log_path)
    if _rules_wave_is_problem(verdict, gates):
        if _rules_manifest_has_run_ref(run_id, kit_dir=kit_dir):
            return []
        # чип красный через health_red_chips/_rules_no_retro_ids при end в journal
        return [
            "ретро: заведи DON'T-карточку rules/cards/... (run %s)" % run_id
        ]
    # без ошибок — чип всегда чист; образцовая → подсказка (НЕ красный)
    entries = _journal_entries_at(state)
    if _rules_wave_exemplary(entries, run_id):
        return [
            "ретро: подумай о DO/CASE-карточке rules/cards/... (run %s)" % run_id
        ]
    return []


def _rules_card_waves_since(waves, created):
    """Число wave_ends с ts > created (возраст карточки в волнах)."""
    try:
        created_f = float(created or 0)
    except (TypeError, ValueError):
        created_f = 0.0
    n = 0
    for w in waves:
        try:
            wts = float(w.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if wts > created_f:
            n += 1
    return n


def _rules_dead_ids(entries, kit_dir=None, state=None):
    """Активные counters-hit=0 при ≥N=10 волн в журнале.

    hit — из counters/rules-hits.json (стейт); manifest.hit игнорируется.
    Age-grace (K=RULES_DEAD_AGE_WAVES): hit=0 моложе K волн
    (wave_ends журнала с ts > age_ts) не флагается — новичок ≠ мёртвый.
    age_ts = born_at если есть, иначе created.
    """
    waves = _rules_wave_ends(entries)
    if len(waves) < RULES_DEAD_WAVES:
        return []
    manifest = load_manifest(kit_dir)
    hits = _rules_load_hits(state)
    dead = []
    for c in _rules_active_cards(manifest):
        cid = c.get("id")
        if not cid or int(hits.get(cid) or 0) != 0:
            continue
        age_ts = c.get("born_at") if c.get("born_at") is not None else c.get("created")
        if _rules_card_waves_since(waves, age_ts) < RULES_DEAD_AGE_WAVES:
            continue
        dead.append(cid)
    return dead


def _rules_category_oversize(kit_dir=None):
    """Категории с >RULES_CATEGORY_LIMIT активных карточек."""
    manifest = load_manifest(kit_dir)
    counts = {}
    for c in _rules_active_cards(manifest):
        cat = c.get("категория") or ""
        if not cat:
            continue
        counts[cat] = counts.get(cat, 0) + 1
    return [cat for cat, n in sorted(counts.items())
            if n > RULES_CATEGORY_LIMIT]


# F-C1 D2: вечный генерал — wire-детектор resume-цепочки по фронтам.
_FRONT_GENERAL_PROMPT_MARK = "роль: meta/front-general.md"
_GENERAL_FRONT_ID_RE = re.compile(r"Фронт (F-[A-Z0-9]+)")
_AGENT_WIRE_DIR_RE = re.compile(r"^agent-.+")


def find_engine_home():
    """Корень движка (Kimi): ORCH_ENGINE_HOME | KIMI_CODE_HOME | KIMI_HOME | ~/.kimi-code."""
    for key in ("ORCH_ENGINE_HOME", "KIMI_CODE_HOME", "KIMI_HOME"):
        val = os.environ.get(key)
        if val:
            return os.path.abspath(os.path.expanduser(val))
    return os.path.join(os.path.expanduser("~"), ".kimi-code")


def _agent_wire_search_roots(state=None):
    """Каталоги, в которых искать agents/agent-*/wire.jsonl (fail-open).

    Порядок: ORCH_AGENTS_ROOT; state/agents (синтетика); сессии движка
    ({engine}/sessions/**/session_*/agents), предпочтительно session_id из
    state/sessions/.
    """
    roots = []
    seen = set()

    def _add(path):
        if not path:
            return
        ap = os.path.abspath(path)
        if ap in seen:
            return
        seen.add(ap)
        roots.append(ap)

    override = os.environ.get("ORCH_AGENTS_ROOT")
    if override:
        _add(override)
    if state:
        _add(os.path.join(state, "agents"))

    eng_sessions = os.path.join(find_engine_home(), "sessions")
    orch_sids = set()
    if state:
        try:
            sdir = os.path.join(state, "sessions")
            if os.path.isdir(sdir):
                for name in os.listdir(sdir):
                    if name.startswith("session_") and os.path.isdir(
                            os.path.join(sdir, name)):
                        orch_sids.add(name)
        except Exception:
            orch_sids = set()
    try:
        if os.path.isdir(eng_sessions):
            for wd in os.listdir(eng_sessions):
                wd_path = os.path.join(eng_sessions, wd)
                if not os.path.isdir(wd_path):
                    continue
                try:
                    children = os.listdir(wd_path)
                except Exception:
                    continue
                for child in children:
                    if not child.startswith("session_"):
                        continue
                    if orch_sids and child not in orch_sids:
                        continue
                    _add(os.path.join(wd_path, child, "agents"))
                # layout без wd-префикса: sessions/session_*/agents
                if wd.startswith("session_"):
                    if orch_sids and wd not in orch_sids:
                        continue
                    _add(os.path.join(wd_path, "agents"))
    except Exception:
        pass
    # Нет списка orch-сессий — уже добавили все session_*; если orch_sids
    # отфильтровал всё впустую, второй проход без фильтра не нужен: пусто
    # → без красного (fail-open). Если orch_sids пуст — фильтр не применялся.
    return roots


def _iter_agent_wire_paths(state=None):
    """Yield (agent_name, wire_path) для agents/agent-*/wire.jsonl."""
    for root in _agent_wire_search_roots(state):
        try:
            if not os.path.isdir(root):
                continue
            for name in sorted(os.listdir(root)):
                if not _AGENT_WIRE_DIR_RE.match(name):
                    continue
                wire = os.path.join(root, name, "wire.jsonl")
                if os.path.isfile(wire):
                    yield name, wire
        except Exception:
            continue


def _wire_prompt_texts(obj):
    """Тексты промтов из записи wire (turn.prompt.input[].text)."""
    if not isinstance(obj, dict):
        return []
    if obj.get("type") != "turn.prompt":
        return []
    texts = []
    inp = obj.get("input")
    if isinstance(inp, list):
        for item in inp:
            if isinstance(item, dict):
                t = item.get("text")
                if isinstance(t, str) and t:
                    texts.append(t)
            elif isinstance(item, str) and item:
                texts.append(item)
    elif isinstance(inp, str) and inp:
        texts.append(inp)
    return texts


def _scan_agent_general_fronts(wire_path):
    """(ordered_fronts, ts_first, ts_last) по wire; пусто → ([], None, None)."""
    ordered = []
    seen = set()
    ts_first = None
    ts_last = None
    try:
        with open(wire_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    continue
                texts = _wire_prompt_texts(obj)
                if not texts:
                    continue
                ts = obj.get("time")
                if ts is None:
                    ts = obj.get("ts") or obj.get("created_at")
                for text in texts:
                    if _FRONT_GENERAL_PROMPT_MARK not in text:
                        continue
                    m = _GENERAL_FRONT_ID_RE.search(text)
                    if not m:
                        continue
                    front = m.group(1)
                    if front not in seen:
                        seen.add(front)
                        ordered.append(front)
                        if ts_first is None:
                            ts_first = ts
                    ts_last = ts
    except Exception:
        return [], None, None
    return ordered, ts_first, ts_last


def general_resume_chain(state=None):
    """Цепочки resume: агент с >1 distinct фронтом генерала в wire.

    Источник истины — agents/agent-*/wire.jsonl сессий движка (корни из
    find_engine_home / ORCH_AGENTS_ROOT / state/agents). Недоступно/пусто → [].
    Элемент: «agent-N:[F-A,F-B] ts=first..last». Fail-open.
    """
    try:
        if state is None:
            state = find_state_dir()
        out = []
        for agent, wire in _iter_agent_wire_paths(state):
            fronts, ts_first, ts_last = _scan_agent_general_fronts(wire)
            if len(fronts) <= 1:
                continue
            out.append(
                "%s:[%s] ts=%s..%s" % (
                    agent,
                    ",".join(fronts),
                    ts_first if ts_first is not None else "?",
                    ts_last if ts_last is not None else "?",
                )
            )
        return out
    except Exception:
        return []


def general_resume_chain_warn(state=None, entries=None):
    """WARN-эвристика по journal: один subagent id — start/end с разными front.

    Только fallback, когда wire-корни недоступны/без wire-файлов.
    Не красный чип. Fail-open.
    """
    try:
        if state is None:
            state = find_state_dir()
        # Wire доступен и непуст → warn не нужен (истина в wire).
        if any(True for _ in _iter_agent_wire_paths(state)):
            return []
        if entries is None:
            path = os.path.join(state, "journal.jsonl")
            entries = []
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for raw in f:
                        s = raw.strip()
                        if not s:
                            continue
                        try:
                            obj = json.loads(s)
                        except Exception:
                            continue
                        if isinstance(obj, dict):
                            entries.append(obj)
            except Exception:
                return []
        # id → ordered distinct fronts from engine-subagent start with front=
        by_id = {}
        ts_first = {}
        ts_last = {}
        for e in entries:
            if not isinstance(e, dict):
                continue
            if e.get("engine") != "engine-subagent":
                continue
            if e.get("kind") not in ("start", "end"):
                continue
            fr = e.get("front")
            if not isinstance(fr, str) or not fr.startswith("F-"):
                continue
            eid = e.get("id") or ""
            if not eid:
                continue
            lst = by_id.setdefault(eid, [])
            if fr not in lst:
                lst.append(fr)
            ts = e.get("ts")
            if eid not in ts_first:
                ts_first[eid] = ts
            ts_last[eid] = ts
        out = []
        for eid, fronts in by_id.items():
            if len(fronts) <= 1:
                continue
            out.append(
                "journal:%s:[%s] ts=%s..%s" % (
                    eid,
                    ",".join(fronts),
                    ts_first.get(eid) if ts_first.get(eid) is not None else "?",
                    ts_last.get(eid) if ts_last.get(eid) is not None else "?",
                )
            )
        return out
    except Exception:
        return []


def health_red_chips(state=None, scan_limit=None, kit_dir=None):
    """Красные чипы панели + списки id (вкл. F-RULES: rules_*).

    Скан journal — последние HEALTH_JOURNAL_SCAN_LIMIT строк (константа).

    runs_no_front: только после активации гейта FRONT_REQUIRED в окне скана.
    Активация = ts первой end-записи с маркером FRONT_REQUIRED в gates;
    если маркера в окне нет — список пуст (история до гейта не шум).
    Отказы гейта (_end_is_gate_refuse: exit∈_GATE_REFUSE_EXITS или
    маркеры _RULES_GATE_REFUSALS) не считаются нарушением.

    wave_no_docs: mask-коммит (:(glob)bin/*.py, panel/server.py,
    skills/orchestration/**) новее последнего local docs-keeper end —
    список с %h mask-коммита. kit_dir=None → KIT_DIR (хук для /tmp-синтетики).

    rules_no_retro / rules_dead / manifest_category_oversize — база rules/
    в kit_dir (хук для /tmp-синтетики).

    general_resume_chain: красный по agents-wire (>1 фронт генерала на агента).
    general_resume_chain_warn: WARN-журнал-эвристика только если wire недоступен.
    """
    empty = {
        "runs_no_front": [],
        "orders_without_basis": [],
        "fronts_no_prosecutor": [],
        "waves_no_critic": [],
        "code_waves_no_gitwarden": [],
        "budget_warn": [],
        "advisors_without_scouts": [],
        "commander_no_children": [],
        "wave_no_docs": [],
        "rules_no_retro": [],
        "rules_dead": [],
        "manifest_category_oversize": [],
        "lint_failures": [],
        # ADDITIVE MARKER: FA-C3 chips
        "probes_missing": [],
        "chip_silenced": [],
        "general_resume_chain": [],
        "general_resume_chain_warn": [],
    }
    try:
        if state is None:
            state = find_state_dir()
        if kit_dir is None:
            kit_dir = KIT_DIR
        if scan_limit is None:
            scan_limit = HEALTH_JOURNAL_SCAN_LIMIT
        path = os.path.join(state, "journal.jsonl")
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except FileNotFoundError:
            lines = []
        except Exception:
            lines = []
        if scan_limit and len(lines) > scan_limit:
            lines = lines[-int(scan_limit):]
        entries = []
        for raw in lines:
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                entries.append(obj)

        starts_by_id = _journal_start_index(entries)
        # 1. runs_no_front: end без фронта ПОСЛЕ активации гейта FRONT_REQUIRED.
        # Активация = первый end с FRONT_REQUIRED в gates в окне; иначе [].
        # Отказы гейта (_end_is_gate_refuse) — работа гейта, не нарушение.
        gate_on_ts = None
        for e in entries:
            if e.get("kind") != "end":
                continue
            gates = e.get("gates") or []
            if isinstance(gates, list) and "FRONT_REQUIRED" in gates:
                gate_on_ts = e.get("ts")
                break
        runs_no_front = []
        if gate_on_ts is not None:
            for e in entries:
                if e.get("kind") != "end":
                    continue
                ets = e.get("ts")
                if ets is None or ets < gate_on_ts:
                    continue
                gates = e.get("gates") or []
                if _end_is_gate_refuse(e.get("exit"), gates):
                    continue  # отказ гейта — не нарушение
                rid = e.get("id")
                st = starts_by_id.get(rid) if rid else None
                if not st:
                    continue
                # SubagentStart (engine-subagent / id subagent:*) — не обёртка.
                if st.get("engine") == "engine-subagent":
                    continue
                if st.get("front") is not None:
                    continue
                if st.get("no_front_reason"):
                    continue  # hierarchy-off / --no-front — не считать
                runs_no_front.append(rid)

        orders = orders_without_basis(state)
        data = _load_fronts_at(state)
        fronts_no_prosecutor = []
        waves_no_critic = []
        code_waves_no_gitwarden = []
        budget_warn = []
        advisors_without_scouts = []
        commander_no_children = []
        wave_no_docs = []

        ends_with_meta = []
        for e in entries:
            if e.get("kind") != "end":
                continue
            rid = e.get("id")
            st = starts_by_id.get(rid) if rid else None
            if not st:
                continue
            ends_with_meta.append({
                "id": rid,
                "front": st.get("front"),
                "role": normalize_journal_role(st.get("role")),
                "gates": e.get("gates") or [],
                "exit": e.get("exit"),
                "readonly": bool(st.get("readonly")),
                "start_ts": st.get("ts"),
                "entry": e,
            })

        # P10: wave_no_docs — mask-коммит новее local docs-keeper end.
        last_docs_end_ts = None
        for e in entries:
            if e.get("kind") != "end":
                continue
            rid = e.get("id")
            st = starts_by_id.get(rid) if rid else None
            if not st:
                continue
            if normalize_journal_role(st.get("role")) != "code/docs-keeper.md":
                continue
            # Строго engine == "local" (journal; не local-cursor).
            if st.get("engine") != "local":
                continue
            ets = e.get("ts")
            if ets is None:
                continue
            try:
                ets_f = float(ets)
            except (TypeError, ValueError):
                continue
            if last_docs_end_ts is None or ets_f > last_docs_end_ts:
                last_docs_end_ts = ets_f
        mask = _last_mask_commit(kit_dir)
        if mask is not None:
            mask_ct, mask_h = mask
            if last_docs_end_ts is None or float(mask_ct) > last_docs_end_ts:
                wave_no_docs.append(mask_h)

        # P4: advisors_without_scouts — advisor-run id без web-scout.
        scout_starts = []
        advisor_starts = []
        for e in entries:
            if e.get("kind") != "start":
                continue
            rid = e.get("id")
            if not rid:
                continue
            role = normalize_journal_role(e.get("role"))
            if role == "meta/opportunity-advisor.md":
                advisor_starts.append(e)
            elif role == "meta/web-scout.md":
                scout_starts.append(e)

        for adv in advisor_starts:
            aid = adv.get("id")
            ats = adv.get("ts")
            afront = adv.get("front")
            found = False
            for sc in scout_starts:
                # 1) parent-путь
                if sc.get("parent") == aid:
                    found = True
                    break
                # 2) OR cloud без parent в окне + same front
                if sc.get("engine") != "cloud":
                    continue
                if not _journal_parent_empty(sc.get("parent")):
                    continue
                sts = sc.get("ts")
                if ats is None or sts is None:
                    continue
                try:
                    delta = abs(float(sts) - float(ats))
                except (TypeError, ValueError):
                    continue
                if delta > ADVISOR_SCOUT_WINDOW_S:
                    continue
                if sc.get("front") != afront:
                    continue
                found = True
                break
            if not found:
                advisors_without_scouts.append(aid)

        ended_ids = set()
        for e in entries:
            if e.get("kind") == "end" and e.get("id"):
                ended_ids.add(e["id"])

        for fr in data.get("fronts") or []:
            if not isinstance(fr, dict):
                continue
            fid = fr.get("id")
            if not isinstance(fid, str) or not fid:
                continue
            status = fr.get("status")
            f_ends = [x for x in ends_with_meta if x.get("front") == fid]
            started = {}
            for e in entries:
                if e.get("kind") == "start" and e.get("front") == fid:
                    if e.get("id"):
                        started[e["id"]] = True
                elif e.get("kind") == "end" and e.get("id") in started:
                    started[e["id"]] = False
            live = [rid for rid, alive in started.items() if alive]
            idle = not live

            wave_ends = [x for x in f_ends if _role_is_wave_work(x.get("role"))]
            # Работа, которой нужен критик: не сами критики и не git-warden
            # (ревизия после волны — доктрина, не «волна без критика»).
            # W0 аддитивно: readonly / gate-refuse тоже не «волна без критиков».
            needs_critic = [
                x for x in wave_ends
                if not _role_is_critic(x.get("role"))
                and not _role_is_gitwarden(x.get("role"))
                and not x.get("readonly")
                and not _end_is_gate_refuse(x.get("exit"), x.get("gates"))
            ]
            pros_ends = [x for x in f_ends if _role_is_prosecutor(x.get("role"))]
            if status == "active" and wave_ends and not pros_ends:
                fronts_no_prosecutor.append(fid)

            if idle and wave_ends:
                # Критик того же front (role fact-checker/code-reviewer).
                # Сравниваем start_ts исполнителя с end_ts последнего критика:
                # длинный colonel, стартовавший до критиков, не ложный плюс.
                # Только active: done/закрытые — не красный чип «волна сейчас».
                if status == "active":
                    last_critic_ts = None
                    for x in f_ends:
                        if _role_is_critic(x.get("role")):
                            last_critic_ts = x["entry"].get("ts")
                    after_critic = [
                        x for x in needs_critic
                        if last_critic_ts is None
                        or (x.get("start_ts") or 0) > last_critic_ts
                    ]
                    if after_critic:
                        waves_no_critic.append(fid)

                last_gw_ts = None
                for x in f_ends:
                    if _role_is_gitwarden(x.get("role")):
                        last_gw_ts = x["entry"].get("ts")
                coder_after = [
                    x for x in f_ends
                    if _role_is_coder(x.get("role"))
                    and (last_gw_ts is None
                         or (x["entry"].get("ts") or 0) > last_gw_ts)
                ]
                if coder_after:
                    code_waves_no_gitwarden.append(fid)

            # budget_warn: только active (канон после normalize/миграции).
            if normalize_front_status(status) == "active":
                for x in f_ends:
                    gates = x.get("gates") or []
                    hit = False
                    if isinstance(gates, list):
                        for g in gates:
                            if isinstance(g, str) and "FRONT_BUDGET_WARN" in g:
                                hit = True
                                break
                    if hit and fid not in budget_warn:
                        budget_warn.append(fid)

            # P5: commander_no_children — active front, ended colonel без executor.
            if status == "active":
                f_starts = [
                    e for e in entries
                    if e.get("kind") == "start" and e.get("front") == fid
                ]
                has_wave = any(
                    _role_is_wave_work(e.get("role")) for e in f_starts
                )
                if has_wave:
                    colonels = [
                        e for e in f_starts
                        if _role_is_colonel(e.get("role")) and e.get("id")
                    ]
                    for col in colonels:
                        cid = col["id"]
                        if cid not in ended_ids:
                            continue  # mid-flight — не флаг
                        has_exec = False
                        for e in entries:
                            if e.get("kind") != "start":
                                continue
                            if e.get("parent") != cid:
                                continue
                            if _role_is_executor_child(e.get("role")):
                                has_exec = True
                                break
                        if not has_exec and fid not in commander_no_children:
                            commander_no_children.append(fid)
                            break

        # FA-FX3: health не должен мигрировать rules в kit_dir (esp. /tmp-копии).
        _prev_no_mig = os.environ.get("ORCH_RULES_NO_MIGRATE")
        os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
        try:
            rules_no_retro = _rules_no_retro_ids(entries, kit_dir=kit_dir)
            rules_dead = _rules_dead_ids(entries, kit_dir=kit_dir, state=state)
            manifest_category_oversize = _rules_category_oversize(kit_dir=kit_dir)
        finally:
            if _prev_no_mig is None:
                os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
            else:
                os.environ["ORCH_RULES_NO_MIGRATE"] = _prev_no_mig
        try:
            lint_failures = orch_lint_violations(kit_dir=kit_dir)
        except Exception:
            lint_failures = []

        # ADDITIVE MARKER: FA-C3 probes_missing / chip_silenced
        try:
            probes = probes_missing(state=state, scan_limit=scan_limit)
        except Exception:
            probes = []
        try:
            silenced = chip_silenced_ids(
                state=state, kit_dir=kit_dir, reported_probes=probes)
        except Exception:
            silenced = []

        # F-C1 D2: general_resume_chain (wire) + warn fallback (journal)
        try:
            resume_chain = general_resume_chain(state=state)
        except Exception:
            resume_chain = []
        try:
            resume_warn = general_resume_chain_warn(
                state=state, entries=entries)
        except Exception:
            resume_warn = []

        return {
            "runs_no_front": runs_no_front,
            "orders_without_basis": orders,
            "fronts_no_prosecutor": fronts_no_prosecutor,
            "waves_no_critic": waves_no_critic,
            "code_waves_no_gitwarden": code_waves_no_gitwarden,
            "budget_warn": budget_warn,
            "advisors_without_scouts": advisors_without_scouts,
            "commander_no_children": commander_no_children,
            "wave_no_docs": wave_no_docs,
            "rules_no_retro": rules_no_retro,
            "rules_dead": rules_dead,
            "manifest_category_oversize": manifest_category_oversize,
            "lint_failures": lint_failures,
            "probes_missing": probes,
            "chip_silenced": silenced,
            "general_resume_chain": resume_chain,
            "general_resume_chain_warn": resume_warn,
        }
    except Exception:
        return empty


def prosecutor_pending_lock_dir(fid, state=None):
    """Путь lockdir counters/prosecutor-pending-<safe_fid>."""
    if state is None:
        state = find_state_dir()
    counters = os.path.join(state, "counters")
    os.makedirs(counters, exist_ok=True)
    return os.path.join(counters, "prosecutor-pending-%s" % safe_name(fid))


def next_auto_prosecutor_n(fid, state=None):
    """Следующий номер prosecutor-auto-<F>-<n> по journal + файлам промтов."""
    if state is None:
        state = find_state_dir()
    prefix = "prosecutor-auto-%s-" % fid
    nums = []
    for e in _journal_entries_at(state):
        rid = e.get("id")
        if isinstance(rid, str) and rid.startswith(prefix):
            tail = rid[len(prefix):]
            try:
                nums.append(int(tail))
            except Exception:
                pass
    try:
        for name in os.listdir(state):
            if name.startswith("prompt-" + prefix) and name.endswith(".md"):
                mid = name[len("prompt-" + prefix):-len(".md")]
                try:
                    nums.append(int(mid))
                except Exception:
                    pass
    except Exception:
        pass
    return (max(nums) + 1) if nums else 1


def auto_prosecutor_should_launch(fid, state=None):
    """Условия S2 (1–4) для фронта F после end роли волны. Без lock."""
    if not fid:
        return False
    if state is None:
        state = find_state_dir()
    entries = _journal_entries_at(state)
    # live runs of front F
    started = {}
    for e in entries:
        if e.get("kind") == "start" and e.get("front") == fid:
            rid = e.get("id")
            if rid:
                started[rid] = e
        elif e.get("kind") == "end" and e.get("id") in started:
            del started[e["id"]]
    if started:
        # есть живые — но прокурор среди живых?
        for e in started.values():
            if _role_is_prosecutor(e.get("role")):
                return False  # condition 4: live prosecutor
        return False  # condition 2: live non-prosecutor runs
    # last prosecutor end ts
    starts_by_id = _journal_start_index(entries)
    last_pros_end_ts = None
    wave_ends_after = 0
    for e in entries:
        if e.get("kind") != "end":
            continue
        rid = e.get("id")
        st = starts_by_id.get(rid)
        if not st or st.get("front") != fid:
            continue
        role = normalize_journal_role(st.get("role"))
        ts = e.get("ts") or 0
        if _role_is_prosecutor(role):
            last_pros_end_ts = ts
            wave_ends_after = 0
        elif _role_is_wave_work(role):
            if last_pros_end_ts is None or ts > last_pros_end_ts:
                wave_ends_after += 1
    return wave_ends_after >= 1


def release_auto_prosecutor_lock_if_any(run_id):
    """Снять counters/prosecutor-pending-<F> при end автопрокурора."""
    try:
        state = find_state_dir()
        entries = _journal_entries_at(state)
        start = None
        for e in entries:
            if e.get("kind") == "start" and e.get("id") == run_id:
                start = e
        if not start:
            return
        if not (start.get("auto") or _role_is_prosecutor(start.get("role"))):
            return
        # Снимаем lock только для авто-прокурора (auto=true) или id-префикса.
        rid = run_id or ""
        if not (start.get("auto") or rid.startswith("prosecutor-auto-")):
            return
        fid = start.get("front")
        if not fid:
            return
        lock_dir = prosecutor_pending_lock_dir(fid, state)
        _dir_lock_release(lock_dir)
    except Exception:
        pass


def _write_auto_prosecutor_prompt(path, fid):
    text = (
        "роль: meta/front-prosecutor.md\n\n"
        "Задача: аудит волны фронта %s — журнал фронта, обоснование приказов "
        "(order.md), компас в лимите; вердикт в конец отчёта.\n"
        "Критерий: Вердикт: OK или Вердикт: PROBLEMS с строками ПРОБЛЕМА:.\n"
    ) % fid
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def maybe_auto_prosecutor_after_end(run_id, spawn=True, wrapper_path=None):
    """S2: после end роли волны — detached автопрокурор (один на волну, lockdir).

    spawn=False — только решение+lock+промт (для замеров гонки без агента).
    wrapper_path=None → bin/run-exec.py рядом с этим модулем (спавн обёртки,
    не orchlib). Returns pros_id или None.
    """
    try:
        if not run_id:
            return None
        state = find_state_dir()
        entries = _journal_entries_at(state)
        start = None
        for e in entries:
            if e.get("kind") == "start" and e.get("id") == run_id:
                start = e
        if not start:
            return None
        fid = start.get("front")
        role = normalize_journal_role(start.get("role"))
        if not fid or not _role_is_wave_work(role):
            return None
        if not auto_prosecutor_should_launch(fid, state):
            return None
        lock_dir = prosecutor_pending_lock_dir(fid, state)
        # TTL 30 мин по mtime (как в спеке S2); timeout=0 — без ожидания (гонка).
        if not _dir_lock_acquire(lock_dir, timeout_s=0.0, stale_s=1800.0):
            return None
        try:
            # Повторная проверка под lock (гонка двух концов).
            if not auto_prosecutor_should_launch(fid, state):
                _dir_lock_release(lock_dir)
                return None
            n = next_auto_prosecutor_n(fid, state)
            pros_id = "prosecutor-auto-%s-%d" % (fid, n)
            # Промт: .orchestration/prompt-prosecutor-auto-<F>-<n>.md
            prompt_path = os.path.join(
                state, "prompt-prosecutor-auto-%s-%d.md" % (fid, n))
            _write_auto_prosecutor_prompt(prompt_path, fid)
            if not spawn:
                return pros_id
            if wrapper_path is None:
                wrapper_path = os.path.join(
                    os.path.dirname(os.path.abspath(__file__)), "run-exec.py")
            env = dict(os.environ)
            env["ORCH_RUN_AUTO"] = "1"
            # Не наследовать parent=текущий run как себя через ORCH_RUN_ID.
            env.pop("ORCH_RUN_ID", None)
            cmd = [
                sys.executable, wrapper_path,
                "--id", pros_id,
                "--front", fid,
                "--role", "meta/front-prosecutor.md",
                "--prompt-file", prompt_path,
                "--detach",
                "--no-reground-line",
            ]
            subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=env,
                start_new_session=True,
            )
            return pros_id
        except Exception:
            # После acquire любой сбой не должен оставлять lockdir до TTL.
            _dir_lock_release(lock_dir)
            raise
    except Exception as exc:
        try:
            sys.stderr.write("auto-prosecutor: %s\n" % exc)
        except Exception:
            pass
        return None


# --- orch-lint: инварианты rules/roles (P17) ------------------------------
_RULES_MANIFEST_REQUIRED = (
    "id", "type", "кому", "когда", "категория", "run_ref", "born_at", "hit",
)
_SHA_IN_TEXT_RE = re.compile(
    r"(?:git\s+)?\b([0-9a-f]*[a-f][0-9a-f]{5,39})\b", re.I)
# домен/файл.md — латиница + кириллица + () (как в _index.md)
_ROLE_INDEX_PATH_RE = re.compile(
    r"(?<![A-Za-zА-Яа-яЁё0-9_./-])"
    r"([A-Za-zА-Яа-яЁё0-9_-]+/[A-Za-zА-Яа-яЁё0-9_./()-]+\.md)")


def _orch_lint_git_sha_alive(sha, kit_dir):
    """True, если sha резолвится в git-объект кита."""
    if not sha or not kit_dir:
        return False
    try:
        r = subprocess.run(
            ["git", "-C", kit_dir, "rev-parse", "--verify", "%s^{commit}" % sha],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _orch_lint_index_role_paths(kit_dir):
    """Пути ролей из roles/_index.md (относительные, с .md)."""
    idx = os.path.join(
        kit_dir, "skills", "orchestration", "references", "roles", "_index.md")
    try:
        with open(idx, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return set()
    return set(_ROLE_INDEX_PATH_RE.findall(text or ""))


def _orch_lint_role_files(kit_dir):
    """Относительные пути .md ролей на диске (без _index/_template)."""
    root = os.path.join(
        kit_dir, "skills", "orchestration", "references", "roles")
    out = set()
    if not os.path.isdir(root):
        return out
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d != "__pycache__"]
        for fn in fns:
            if not fn.endswith(".md"):
                continue
            if fn in ("_index.md", "_template.md"):
                continue
            if fn.startswith("_"):
                continue
            rel = os.path.relpath(os.path.join(dp, fn), root)
            out.add(rel.replace("\\", "/"))
    return out


def _orch_lint_card_shas(card, kit_dir):
    """SHA из секций золотая ссылка/пример карточки."""
    cid = card.get("id")
    if not cid:
        return []
    info = load_card_content(cid, kit_dir=kit_dir)
    if not info:
        return []
    sections = info.get("sections") or {}
    blobs = []
    for key in ("золотая ссылка", "пример", "golden"):
        if sections.get(key):
            blobs.append(sections[key])
    if info.get("golden"):
        blobs.append(info["golden"])
    text = "\n".join(blobs)
    found = []
    for m in _SHA_IN_TEXT_RE.finditer(text or ""):
        sha = m.group(1)
        if sha and sha not in found:
            found.append(sha)
    return found


# --- orch-lint deep: cause-cleared + born_at history (C3-LINT) --------------
# Baseline = HEAD до feat-коммита гейта; коммиты строго после baseline.
ORCH_LINT_BASELINE_SHA = "78f1481e7a623cb441564f727b781c3b6eb5a774"
_ORCH_LINT_SCOPED = ("bin/orchlib.py", "bin/orch-lint.py")
_ORCH_LINT_DETECTOR_NAMES = (
    "rules_dead", "rules_undelivered", "rules_no_retro", "health_red_chips",
    "_rules_wave_ends", "RULES_DEAD_AGE_HOURS", "RULES_DEAD_WAVES",
    "RULES_ACTIVE_LIMIT", "probes_missing", "chip_silenced", "lint_failures",
)
_ORCH_LINT_CONST_ASSIGN_RE = re.compile(
    r"^([A-Z][A-Z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:#.*)?$")
_ORCH_LINT_GRACE_LIMIT_NAME_RE = re.compile(
    r"(?:GRACE|LIMIT|HOURS|WAVES|AGE)")
_ORCH_LINT_CHIP_KEY_RE = re.compile(r'^"([a-z][a-z0-9_]*)"\s*:')
_ORCH_LINT_CAUSE_RUN_RE = re.compile(r"CAUSE-CLEARED:([^\s:]+)")


def _orch_lint_git(kit_dir, args, timeout=60):
    try:
        r = subprocess.run(
            ["git", "-C", kit_dir] + list(args),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return r.stdout or ""
    except Exception:
        return None


def _orch_lint_commits_after(kit_dir, baseline, paths):
    """SHA коммитов строго после baseline, затрагивающих paths (oldest first)."""
    if not baseline or not kit_dir:
        return []
    out = _orch_lint_git(
        kit_dir,
        ["rev-list", "--reverse", "%s..HEAD" % baseline, "--"] + list(paths),
    )
    if out is None:
        return []
    return [ln.strip() for ln in out.splitlines() if ln.strip()]


def _orch_lint_commit_msg(kit_dir, sha):
    out = _orch_lint_git(kit_dir, ["log", "-1", "--format=%B", sha])
    return out if out is not None else ""


def _orch_lint_commit_diff(kit_dir, sha, paths):
    """Unified diff коммита vs первый родитель (scoped paths)."""
    out = _orch_lint_git(
        kit_dir,
        ["diff", "%s^" % sha, sha, "--"] + list(paths),
        timeout=120,
    )
    return out if out is not None else ""


def _orch_lint_split_diff_sides(diff_text):
    """Вернуть (minus_lines, plus_lines) без префикса -/+, без заголовков."""
    minus, plus = [], []
    for raw in (diff_text or "").splitlines():
        if not raw:
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        if raw.startswith("@@"):
            continue
        if raw.startswith("-"):
            minus.append(raw[1:])
        elif raw.startswith("+"):
            plus.append(raw[1:])
    return minus, plus


def _orch_lint_const_map(lines):
    """NAME -> numeric value из строк присваивания констант."""
    out = {}
    for ln in lines:
        s = ln.strip()
        m = _ORCH_LINT_CONST_ASSIGN_RE.match(s)
        if not m:
            continue
        name, val = m.group(1), m.group(2)
        try:
            out[name] = float(val) if "." in val else int(val)
        except ValueError:
            continue
    return out


def _orch_lint_is_limit_const(name):
    """LIMIT-константа (рост = усиление, не ослабление)."""
    return "LIMIT" in (name or "")


def _orch_lint_diff_weakens(diff_text):
    """True если scoped-дифф ослабляет зону детекторов (контракт C3-LINT)."""
    minus, plus = _orch_lint_split_diff_sides(diff_text)
    minus_text = "\n".join(minus)
    plus_text = "\n".join(plus)
    # 1) имя детектора исчезло из «-» (нет в «+») — удаление/замена.
    #    Чистый перенос: токен есть и в «-», и в «+» — не ослабление.
    for name in _ORCH_LINT_DETECTOR_NAMES:
        if name in minus_text and name not in plus_text:
            return True
    # 2) числовая константа детектора изменилась (любое), кроме роста LIMIT.
    old_c = _orch_lint_const_map(minus)
    new_c = _orch_lint_const_map(plus)
    watch = set(_ORCH_LINT_DETECTOR_NAMES) | set(old_c) | set(new_c)
    for name in watch:
        if name not in old_c or name not in new_c:
            continue
        if old_c[name] == new_c[name]:
            continue
        # рост LIMIT — усиление, не ослабление; иначе любое изменение = ослабление
        if _orch_lint_is_limit_const(name) and new_c[name] > old_c[name]:
            continue
        # изменение константы из списка детекторов или grace/limit-паттерна
        if (name in _ORCH_LINT_DETECTOR_NAMES
                or _ORCH_LINT_GRACE_LIMIT_NAME_RE.search(name)):
            return True
    # 3) новая grace/limit-константа в зоне детекторов
    for name, _val in new_c.items():
        if name in old_c:
            continue
        if _ORCH_LINT_GRACE_LIMIT_NAME_RE.search(name):
            return True
    # 4) удалён ключ чипа из health-словаря ("chip": в «-», нет в «+»)
    old_keys = set()
    new_keys = set()
    for ln in minus:
        m = _ORCH_LINT_CHIP_KEY_RE.match(ln.strip())
        if m:
            old_keys.add(m.group(1))
    for ln in plus:
        m = _ORCH_LINT_CHIP_KEY_RE.match(ln.strip())
        if m:
            new_keys.add(m.group(1))
    removed = old_keys - new_keys
    # чип-ключи = известные детекторные имена в snake_case
    chipish = set(n for n in _ORCH_LINT_DETECTOR_NAMES if n[:1].islower())
    if removed & chipish:
        return True
    return False


def _orch_lint_cause_cleared_ok(kit_dir, sha, state=None):
    """True если у коммита есть доказательство CAUSE-CLEARED (msg+artifact|fallback)."""
    msg = _orch_lint_commit_msg(kit_dir, sha) or ""
    # run-id: «CAUSE-CLEARED:<run-id>» + артефакт/run.log с замером
    m = _ORCH_LINT_CAUSE_RUN_RE.search(msg)
    if m:
        run_id = m.group(1).strip()
        if run_id and _orch_lint_cause_artifact_has_measure(run_id, state=state):
            return True
    # Fallback: «CAUSE-CLEARED: <команда + результат>» (пробел после ':') в msg
    for ln in msg.splitlines():
        idx = ln.find("CAUSE-CLEARED: ")
        if idx >= 0 and ln[idx + len("CAUSE-CLEARED: "):].strip():
            return True
    return False


def _orch_lint_cause_artifact_has_measure(run_id, state=None):
    """artifact.md|run.log прогона содержат «CAUSE-CLEARED:» + непустой замер."""
    if not run_id:
        return False
    if state is None:
        try:
            state = find_state_dir()
        except Exception:
            state = None
    if not state or not os.path.isdir(state):
        return False
    run_dir = find_run_dir(run_id, state=state)
    if not run_dir:
        return False
    for name in ("artifact.md", "run.log"):
        path = os.path.join(run_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except Exception:
            continue
        for ln in text.splitlines():
            if "CAUSE-CLEARED:" in ln:
                rest = ln.split("CAUSE-CLEARED:", 1)[1].strip()
                if rest:
                    return True
    return False


def _orch_lint_cause_cleared_violations(kit_dir, baseline, state=None):
    """Нарушения cause-cleared для коммитов после baseline по scoped-путям."""
    viols = []
    for sha in _orch_lint_commits_after(kit_dir, baseline, _ORCH_LINT_SCOPED):
        diff = _orch_lint_commit_diff(kit_dir, sha, _ORCH_LINT_SCOPED)
        if not diff or not _orch_lint_diff_weakens(diff):
            continue
        if _orch_lint_cause_cleared_ok(kit_dir, sha, state=state):
            continue
        short = sha[:12] if len(sha) > 12 else sha
        viols.append(
            "cause-cleared: commit %s weakens detector zone without CAUSE-CLEARED"
            % short)
    return viols


def _orch_lint_manifest_at(kit_dir, rev):
    """Разобрать rules/manifest.json на ревизии rev; None если нет/битый."""
    out = _orch_lint_git(
        kit_dir, ["show", "%s:rules/manifest.json" % rev], timeout=30)
    if out is None or not out.strip():
        return None
    try:
        data = json.loads(out)
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return data


def _orch_lint_born_at_map(manifest):
    """id -> born_at для карточек, у которых поле задано."""
    out = {}
    if not manifest:
        return out
    for c in manifest.get("cards") or []:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        if c.get("born_at") is None:
            continue
        out[c["id"]] = c.get("born_at")
    return out


def _orch_lint_born_at_equal(a, b):
    try:
        return float(a) == float(b)
    except (TypeError, ValueError):
        return a == b


def _orch_lint_born_at_violations(kit_dir, baseline):
    """born_at value→value restamp в rules/manifest.json после baseline."""
    viols = []
    for sha in _orch_lint_commits_after(
            kit_dir, baseline, ("rules/manifest.json",)):
        parent = _orch_lint_manifest_at(kit_dir, "%s^" % sha)
        cur = _orch_lint_manifest_at(kit_dir, sha)
        if parent is None or cur is None:
            continue
        old_m = _orch_lint_born_at_map(parent)
        new_m = _orch_lint_born_at_map(cur)
        for cid, old_v in old_m.items():
            if cid not in new_m:
                continue
            new_v = new_m[cid]
            if _orch_lint_born_at_equal(old_v, new_v):
                continue
            short = sha[:12] if len(sha) > 12 else sha
            viols.append(
                "born_at: card %s restamped in commit %s" % (cid, short))
    return viols


def orch_lint_violations(kit_dir=None, state=None, deep=False,
                         baseline=None):
    """Список нарушений инвариантов rules/roles; [] = чисто.

    Проверяет: поля manifest, hit числом, дубли id, файлы↔manifest,
    роли _index↔файлы, живые SHA-ссылки в карточках.
    deep=True: + cause-cleared (git-walk scoped) и born_at-история
    rules/manifest.json после baseline. deep=False (дефолт) — лёгкий subset
    для health_red_chips (без git-walk); CLI orch-lint передаёт deep=True.
    """
    if kit_dir is None:
        kit_dir = KIT_DIR
    viols = []
    # --- manifest ---
    path = rules_manifest_path(kit_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return ["manifest: cannot read %s (%s)" % (path, e)]
    if not isinstance(data, dict):
        return ["manifest: root is not an object"]
    cards = data.get("cards")
    if not isinstance(cards, list):
        return ["manifest: cards is not a list"]
    seen = {}
    for i, c in enumerate(cards):
        if not isinstance(c, dict):
            viols.append("manifest: cards[%d] is not an object" % i)
            continue
        cid = c.get("id")
        label = cid if cid else "cards[%d]" % i
        for key in _RULES_MANIFEST_REQUIRED:
            if key not in c:
                viols.append("manifest: %s missing field %s" % (label, key))
        hit = c.get("hit")
        # bool — subclass int; для lint требуем именно число, не bool
        if "hit" in c and (isinstance(hit, bool)
                           or not isinstance(hit, (int, float))):
            viols.append("manifest: %s hit is not a number" % label)
        if cid:
            if cid in seen:
                viols.append("manifest: duplicate id %s" % cid)
            else:
                seen[cid] = c
        # файл карточки
        if cid and c.get("категория"):
            fpath = _rules_card_file_path(
                c, kit_dir, archived=bool(c.get("archived")))
            if not os.path.isfile(fpath):
                viols.append(
                    "file: missing card file for %s → %s" % (cid, fpath))
    # orphan files under cards/ (и archive/) не в manifest
    for base_name, archived in (("cards", False), ("archive", True)):
        root = os.path.join(rules_dir(kit_dir), base_name)
        if not os.path.isdir(root):
            continue
        for dp, _dns, fns in os.walk(root):
            for fn in fns:
                if not fn.endswith(".md"):
                    continue
                cid = fn[:-3]
                if cid not in seen:
                    rel = os.path.relpath(os.path.join(dp, fn), rules_dir(kit_dir))
                    viols.append(
                        "file: orphan card %s not in manifest" % rel.replace("\\", "/"))
                else:
                    # archived flag vs location
                    c = seen[cid]
                    want_arch = bool(c.get("archived"))
                    if want_arch != archived:
                        viols.append(
                            "file: %s archived=%s but under %s/"
                            % (cid, want_arch, base_name))
    # --- roles _index ↔ files ---
    idx_paths = _orch_lint_index_role_paths(kit_dir)
    file_paths = _orch_lint_role_files(kit_dir)
    roles_root = os.path.join(
        kit_dir, "skills", "orchestration", "references", "roles")
    if not os.path.isfile(os.path.join(roles_root, "_index.md")):
        viols.append("roles: missing _index.md")
    for rel in sorted(idx_paths - file_paths):
        viols.append("roles: _index lists %s but file missing" % rel)
    for rel in sorted(file_paths - idx_paths):
        viols.append("roles: file %s not listed in _index" % rel)
    # --- SHA refs ---
    for c in cards:
        if not isinstance(c, dict) or not c.get("id"):
            continue
        for sha in _orch_lint_card_shas(c, kit_dir):
            if not _orch_lint_git_sha_alive(sha, kit_dir):
                viols.append(
                    "sha: card %s references dead sha %s" % (c.get("id"), sha))
    # --- deep: cause-cleared + born_at history ---
    if deep:
        bl = baseline if baseline is not None else ORCH_LINT_BASELINE_SHA
        try:
            viols.extend(
                _orch_lint_cause_cleared_violations(kit_dir, bl, state=state))
        except Exception as e:
            viols.append("cause-cleared: check error (%s)" % e)
        try:
            viols.extend(_orch_lint_born_at_violations(kit_dir, bl))
        except Exception as e:
            viols.append("born_at: check error (%s)" % e)
    return viols
