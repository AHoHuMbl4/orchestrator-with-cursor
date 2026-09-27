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


def waves_without_prosecutor(state=None, window_runs=30):
    """id active-фронтов, где в окне start-записей есть работа, но нет прокурора.

    Окно: последние window_runs kind==start с front==fid. Тихие ошибки → [].
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
            for e in starts:
                role = e.get("role")
                if _role_is_prosecutor(role):
                    has_pros = True
                if _role_is_wave_work(role):
                    worked = True
            if worked and not has_pros:
                out.append(fid)
        return out
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
RULES_KOMU = frozenset({
    "general", "colonel", "executor", "wrapper", "panel"})
RULES_KOGDA = frozenset({
    "decomposition", "launch", "acceptance", "retro",
    "prompt-submit", "post-tool"})
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


def load_manifest(kit_dir=None):
    """Загрузить rules/manifest.json; нет файла → пустой {cards, aliases}."""
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
    return {"cards": cards, "aliases": aliases}


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


def match_cards(komu, kogda, category=None, kit_dir=None, limit=3):
    """Список id по адресу (кому×когда×[категория]); hit desc, created asc.

    Алиас W0: match по старой категории включает подкатегории из aliases.
    Безадресный / нет совпадений → []. Потолок доставки — limit (дефолт 3).
    """
    if komu not in RULES_KOMU or kogda not in RULES_KOGDA:
        return []
    manifest = load_manifest(kit_dir)
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
    matched.sort(key=lambda x: (-int(x.get("hit") or 0),
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
    """Инкремент hit: journal card_injected + counters/rules-hits.json + manifest.

    Возвращает новый hit или None если id не найден.
    """
    if not card_id:
        return None
    if state is None:
        state = find_state_dir()
    manifest = load_manifest(kit_dir)
    card = _rules_card_by_id(manifest, card_id)
    if card is None:
        return None
    hit = int(card.get("hit") or 0) + 1
    card["hit"] = hit
    save_manifest(manifest, kit_dir)
    hits = _rules_load_hits(state)
    hits[card_id] = int(hits.get(card_id) or 0) + 1
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

    meta/front-general→general, meta/front-colonel→colonel, code/*→executor,
    обёртки/wrapper→wrapper, panel→panel; голый токен из RULES_KOMU — как есть.
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


def format_step_inject_lines(komu, kogda, kit_dir=None, state=None, limit=3,
                             extra=None):
    """Вклейки шага: 1–3 строк «[rules/<id>] <суть>≤80 → <path>» + record_hit.

    Безадресный komu/kogda или нет совпадений → [].
    """
    if komu not in RULES_KOMU or kogda not in RULES_KOGDA:
        return []
    try:
        limit = 3 if limit is None else max(0, int(limit))
    except Exception:
        limit = 3
    ids = match_cards(komu, kogda, category=None, kit_dir=kit_dir, limit=limit)
    lines = []
    for cid in ids:
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


def format_precedent_lines(komu, prompt_text=None, kit_dir=None, state=None,
                           limit=3, extra=None):
    """Прецеденты до старта (launch): DO/CASE «прецедент:» + DON'T «осторожно:».

    Адрес DO/CASE: komu×launch. DON'T: тот же адрес и/или ключевые файлы промта
    в теле карточки. Суммарно ≤limit (дефолт 3). Безадресный → [].
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


def archive_lru(kit_dir=None):
    """>25 активных → старейшие hit=0 в archive/; hit>0 не архивируется.

    Возвращает список id, ушедших в archive. Активные после вызова ≤25
    (если хватает hit=0 кандидатов).
    """
    manifest = load_manifest(kit_dir)
    active = _rules_active_cards(manifest)
    limit = RULES_ACTIVE_LIMIT
    if len(active) <= limit:
        return []
    # кандидаты: hit==0, старейшие created сначала
    zeros = [c for c in active if int(c.get("hit") or 0) == 0]
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

    Снимает archived; created=now. LRU не вызывается здесь (вызывающий
    может archive_lru отдельно) — иначе hit=0 сразу уходит обратно.
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
    card["created"] = time.time()
    save_manifest(manifest, kit_dir)
    return True


def _rules_end_is_problem(entry):
    """end с PROBLEMS/BLOCKED в verdict или непустым gates (гейт-отказ)."""
    if not isinstance(entry, dict) or entry.get("kind") != "end":
        return False
    gates = entry.get("gates") or []
    if isinstance(gates, list) and gates:
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


def _rules_dead_ids(entries, kit_dir=None):
    """Активные hit=0 при ≥N=10 волн в журнале."""
    waves = _rules_wave_ends(entries)
    if len(waves) < RULES_DEAD_WAVES:
        return []
    manifest = load_manifest(kit_dir)
    dead = []
    for c in _rules_active_cards(manifest):
        if int(c.get("hit") or 0) == 0 and c.get("id"):
            dead.append(c["id"])
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


def health_red_chips(state=None, scan_limit=None, kit_dir=None):
    """Красные чипы панели + списки id (вкл. F-RULES: rules_*).

    Скан journal — последние HEALTH_JOURNAL_SCAN_LIMIT строк (константа).

    runs_no_front: только после активации гейта FRONT_REQUIRED в окне скана.
    Активация = ts первой end-записи с маркером FRONT_REQUIRED в gates;
    если маркера в окне нет — список пуст (история до гейта не шум).
    Отказы самого гейта (FRONT_REQUIRED в gates) не считаются нарушением.

    wave_no_docs: mask-коммит (:(glob)bin/*.py, panel/server.py,
    skills/orchestration/**) новее последнего local docs-keeper end —
    список с %h mask-коммита. kit_dir=None → KIT_DIR (хук для /tmp-синтетики).

    rules_no_retro / rules_dead / manifest_category_oversize — база rules/
    в kit_dir (хук для /tmp-синтетики).
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
        # Отказы гейта (тот же маркер) — работа гейта, не нарушение.
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
                if isinstance(gates, list) and "FRONT_REQUIRED" in gates:
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
            needs_critic = [
                x for x in wave_ends
                if not _role_is_critic(x.get("role"))
                and not _role_is_gitwarden(x.get("role"))
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

        rules_no_retro = _rules_no_retro_ids(entries, kit_dir=kit_dir)
        rules_dead = _rules_dead_ids(entries, kit_dir=kit_dir)
        manifest_category_oversize = _rules_category_oversize(kit_dir=kit_dir)

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
