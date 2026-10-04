#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общая библиотека оркестрации: state-каталог, params.json, валидация.

Кроссплатформенно (Linux/macOS/Windows), python3.6+, только stdlib.
Единственный источник правды — .orchestration/params.json (+ compass.md).
"""
import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid

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
        "max_wall_s": 86400,        # жёсткий wall-clock лимит прогона; stall_s — optional (compat: timeout_s)
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
    "execution.stall_s": (30, 21600),
    "execution.max_wall_s": (3600, 604800),
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
                    f.flush()
                    os.fsync(f.fileno())
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            else:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())
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


_OVERSIGHT_ROLE_TAILS = (
    "meta/front-prosecutor.md",
    "meta/front-observer.md",
)


def role_is_oversight(role):
    """Прокурор/наблюдатель: не dual-writer; не путать с --mode plan."""
    r = normalize_journal_role(role)
    if not isinstance(r, str) or not r:
        return False
    for tail in _OVERSIGHT_ROLE_TAILS:
        if r == tail or r.endswith(tail):
            return True
    return False


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


# Process-local snapshots for 3-way RMW merge on save_* (load → patch → save).
_params_base = None
_fronts_base = None


def _leaf_paths(obj, prefix=()):
    """Yield (path_tuple, value) for nested dict leaves; lists treated as leaves."""
    if isinstance(obj, dict):
        if not obj:
            yield prefix, obj
            return
        for k, v in obj.items():
            for p, val in _leaf_paths(v, prefix + (k,)):
                yield p, val
    else:
        yield prefix, obj


def _get_at(obj, path):
    cur = obj
    for k in path:
        if not isinstance(cur, dict) or k not in cur:
            return None, False
        cur = cur[k]
    return cur, True


def _set_at(obj, path, value):
    cur = obj
    for k in path[:-1]:
        nxt = cur.get(k)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[k] = nxt
        cur = nxt
    cur[path[-1]] = value


def _three_way_dict_merge(base, ours, theirs):
    """3-way merge nested dicts by leaf paths; conflicts prefer ours."""
    if not isinstance(base, dict):
        base = {}
    if not isinstance(ours, dict):
        ours = {}
    if not isinstance(theirs, dict):
        theirs = {}
    result = copy.deepcopy(theirs)
    paths = set()
    for obj in (base, ours, theirs):
        for p, _v in _leaf_paths(obj):
            if p:
                paths.add(p)
    for path in paths:
        b, hb = _get_at(base, path)
        o, ho = _get_at(ours, path)
        t, ht = _get_at(theirs, path)
        if not ho:
            continue
        if hb and o == b:
            continue  # we didn't change this leaf
        # we changed: keep ours if they didn't change, or on conflict
        if (not ht) or (hb and t == b) or o == t:
            _set_at(result, path, copy.deepcopy(o))
        else:
            _set_at(result, path, copy.deepcopy(o))
    return result


def _fronts_by_id(f):
    out = {}
    for fr in (f.get("fronts") if isinstance(f, dict) else None) or []:
        if isinstance(fr, dict) and isinstance(fr.get("id"), str):
            out[fr["id"]] = fr
    return out


def _three_way_fronts_merge(base, ours, theirs):
    """3-way merge fronts.json: top-level fields + per-front leaf fields by id."""
    if not isinstance(base, dict):
        base = {"goal": "", "fronts": [], "notes": ""}
    if not isinstance(ours, dict):
        ours = {"goal": "", "fronts": [], "notes": ""}
    if not isinstance(theirs, dict):
        theirs = {"goal": "", "fronts": [], "notes": ""}
    result = {
        "goal": theirs.get("goal", "") if isinstance(theirs.get("goal"), str) else "",
        "notes": theirs.get("notes", "") if isinstance(theirs.get("notes"), str) else "",
        "fronts": [],
    }
    for field in ("goal", "notes"):
        b = base.get(field, "")
        o = ours.get(field, "")
        t = theirs.get(field, "")
        if o == b:
            result[field] = t if isinstance(t, str) else ""
        else:
            result[field] = o if isinstance(o, str) else ""
    bb, oo, tt = _fronts_by_id(base), _fronts_by_id(ours), _fronts_by_id(theirs)
    order = []
    for src in (theirs.get("fronts") or [], ours.get("fronts") or [], base.get("fronts") or []):
        for fr in src:
            if isinstance(fr, dict) and isinstance(fr.get("id"), str):
                if fr["id"] not in order:
                    order.append(fr["id"])
    for fid in order:
        bfr = bb.get(fid, {})
        ofr = oo.get(fid)
        tfr = tt.get(fid)
        if ofr is None and tfr is not None:
            merged = copy.deepcopy(tfr)
        elif tfr is None and ofr is not None:
            merged = copy.deepcopy(ofr)
        elif ofr is None and tfr is None:
            merged = copy.deepcopy(bfr)
        else:
            merged = _three_way_dict_merge(bfr, ofr, tfr)
            merged["id"] = fid
        result["fronts"].append(merged)
    return result


def _read_params_file(pf):
    """Read params.json without seeding or updating snapshot."""
    with open(pf, "r", encoding="utf-8-sig") as f:
        data = json.load(f)
    return _merge(DEFAULTS, data)


def _write_params_unlocked(p, pf):
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


def load_params():
    """Читает params.json; при первом запуске сеет из шаблона kit/params.json.

    First-boot: O_EXCL claim на params.json.seed.lock до записи. Кандидат,
    заставший seed in-progress или проигравший CAS → RuntimeError (не тихий
    read). seed.lock отделён от params.json.lock (save_params).
    """
    global _params_base
    pf = params_file()
    seed_lock = pf + ".seed.lock"
    d = os.path.dirname(pf)
    if d:
        os.makedirs(d, exist_ok=True)

    # params виден, но seed ещё в CS → concurrent loser, не early-read.
    if os.path.exists(pf):
        if os.path.isdir(seed_lock):
            raise RuntimeError("params.json seed lost race")
        try:
            result = _read_params_file(pf)
        except Exception as e:
            raise ValueError("params.json битый: %s (путь: %s)" % (e, pf))
        _params_base = copy.deepcopy(result)
        return result

    # First-boot кандидат (!exists на входе): ждём claim; появление pf → Raise.
    deadline = time.time() + 5.0
    while True:
        try:
            os.mkdir(seed_lock)
            break
        except FileExistsError:
            if os.path.exists(pf):
                raise RuntimeError("params.json seed lost race")
            if time.time() >= deadline:
                raise RuntimeError("params.json seed lock busy")
            time.sleep(0.01)
    try:
        if os.path.exists(pf):
            raise RuntimeError("params.json seed lost race")
        # Дать barrier-паре войти как !exists-кандидату до записи pf
        # (иначе опоздавший уходит в тихий read после полного release).
        time.sleep(0.05)
        if os.path.exists(pf):
            raise RuntimeError("params.json seed lost race")
        tpl = os.path.join(KIT_DIR, "params.json")
        seed = {}
        if os.path.exists(tpl):
            try:
                with open(tpl, "r", encoding="utf-8-sig") as f:
                    seed = json.load(f)
            except Exception:
                seed = {}
        merged = _merge(DEFAULTS, seed)
        errs = validate_params(merged)
        if errs:
            raise ValueError("; ".join(errs))
        _write_params_unlocked(merged, pf)
        _bootstrap_compass(merged)
        _params_base = copy.deepcopy(merged)
        return merged
    finally:
        try:
            os.rmdir(seed_lock)
        except Exception:
            try:
                for name in os.listdir(seed_lock):
                    try:
                        os.unlink(os.path.join(seed_lock, name))
                    except Exception:
                        pass
                os.rmdir(seed_lock)
            except Exception:
                pass


def validate_params(p):
    errs = []
    for key, (lo, hi) in RANGES.items():
        sec, _, field = key.partition(".")
        val = p.get(sec, {}).get(field)
        if val is None:
            continue  # optional: ключ из RANGES может отсутствовать в DEFAULTS
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


def save_params(p, timeout_s=5.0):
    """Атомарная запись params.json с 3-way merge под lockdir."""
    global _params_base
    errs = validate_params(p)
    if errs:
        raise ValueError("; ".join(errs))
    pf = params_file()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    lock_dir = pf + ".lock"
    held = _dir_lock_acquire(lock_dir, timeout_s=timeout_s)
    if not held:
        raise RuntimeError("params lock busy: %s" % lock_dir)
    try:
        if os.path.exists(pf):
            try:
                current = _read_params_file(pf)
            except Exception:
                current = copy.deepcopy(DEFAULTS)
        else:
            current = copy.deepcopy(DEFAULTS)
        base = _params_base if _params_base is not None else copy.deepcopy(current)
        merged = _three_way_dict_merge(base, p, current)
        errs = validate_params(merged)
        if errs:
            raise ValueError("; ".join(errs))
        _write_params_unlocked(merged, pf)
        _params_base = copy.deepcopy(merged)
    finally:
        _dir_lock_release(lock_dir)


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


def _safe_encode(raw):
    """Percent-encode session id: '%'→'%%', then non-[A-Za-z0-9._-] → %XX (UTF-8 bytes).

    No truncation — callers apply [:SESSION_ID_MAX] where needed.
    """
    s = str(raw or "default")
    out = []
    for ch in s:
        if ch == "%":
            out.append("%%")
        elif (
            ("A" <= ch <= "Z")
            or ("a" <= ch <= "z")
            or ("0" <= ch <= "9")
            or ch in "._-"
        ):
            out.append(ch)
        else:
            for b in ch.encode("utf-8"):
                out.append("%%%02X" % b)
    return "".join(out)


def safe_name(session_id):
    """Инъективное FS-имя: percent-кодирование (_safe_encode) + усечение SESSION_ID_MAX."""
    return _safe_encode(session_id)[:SESSION_ID_MAX] or "default"


def session_dir(session_id):
    orig = str(session_id or "default")
    # та же percent-схема, что safe_name (до усечения — legacy existing dirs)
    raw = _safe_encode(orig) or "default"
    # legacy ≤80 и имена с list_sessions — не режем, если каталог уже есть
    existing = os.path.join(sessions_dir(), raw)
    if os.path.isdir(existing):
        d = existing
    else:
        d = os.path.join(sessions_dir(), raw[:SESSION_ID_MAX])
        os.makedirs(d, exist_ok=True)
    # Алиас sessions/<orig-with-slashes> → safe, чтобы realpath совпадал с safe_name
    if ("/" in orig or "\\" in orig) and raw != orig:
        alias = os.path.join(sessions_dir(), orig)
        parent = os.path.dirname(alias)
        try:
            os.makedirs(parent, exist_ok=True)
            if not os.path.lexists(alias):
                os.symlink(os.path.relpath(d, parent), alias)
        except Exception:
            pass
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
    global _fronts_base
    pf = fronts_path()
    if not os.path.exists(pf):
        empty = {"goal": "", "fronts": [], "notes": ""}
        _fronts_base = copy.deepcopy(empty)
        return empty
    try:
        with open(pf, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
    except Exception:
        empty = {"goal": "", "fronts": [], "notes": ""}
        _fronts_base = copy.deepcopy(empty)
        return empty
    if not isinstance(data, dict):
        empty = {"goal": "", "fronts": [], "notes": ""}
        _fronts_base = copy.deepcopy(empty)
        return empty
    fronts = data.get("fronts") if isinstance(data.get("fronts"), list) else []
    _migrate_fronts_list(fronts)
    result = {
        "goal": data.get("goal", "") if isinstance(data.get("goal"), str) else "",
        "fronts": fronts,
        "notes": data.get("notes", "") if isinstance(data.get("notes"), str) else "",
    }
    _fronts_base = copy.deepcopy(result)
    return result


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


def check_activation_gate(fronts, fid, warn_stream=None):
    """Гейт активации: семантика owns.check_activation на in-memory fronts.

    Peers: status==\"active\" СТРОГО (legacy running/planned НЕ блокируют).
    Пустые/отсутствующие owns → stderr-warn и РАЗРЕШИТЬ.
    Отказ: «пересечение <свидетель> (<fid> ∩ <fid2>)».
    Возврат: (ok: bool, msg: str|None).
    """
    if warn_stream is None:
        warn_stream = sys.stderr
    try:
        import owns as _owns
    except ImportError:
        _owns = None
        bin_dir = os.path.dirname(os.path.abspath(__file__))
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        import owns as _owns  # noqa: E402
    rows = []
    for fr in fronts or []:
        if not isinstance(fr, dict):
            continue
        i = fr.get("id")
        if not i:
            continue
        owns_v = fr.get("owns")
        if owns_v is None:
            globs = []
        elif isinstance(owns_v, list):
            globs = [str(g) for g in owns_v]
        else:
            globs = []
        st = fr.get("status") or ""
        rows.append((i, globs, st))
    ok, msg = _owns.check_activation_rows(rows, fid)
    if ok and msg == "owns пуст":
        try:
            warn_stream.write("owns пуст\n")
            warn_stream.flush()
        except Exception:
            pass
        return True, None
    if ok:
        return True, None
    return False, msg


def active_owns_overlap_stats(fronts):
    """Ленивый счётчик пересечений owns среди status==active (через owns.witness).

    Возврат: (has_conflict: bool, pair_count: int, per_fid: {fid: n_peers}).
    """
    try:
        import owns as _owns
    except ImportError:
        bin_dir = os.path.dirname(os.path.abspath(__file__))
        if bin_dir not in sys.path:
            sys.path.insert(0, bin_dir)
        import owns as _owns  # noqa: E402
    active = []
    for fr in fronts or []:
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not fid or fr.get("status") != "active":
            continue
        owns_v = fr.get("owns")
        if not isinstance(owns_v, list) or not owns_v:
            continue
        globs = [str(g) for g in owns_v]
        active.append((fid, globs))
    per_fid = {fid: 0 for fid, _g in active}
    pair_count = 0
    for i in range(len(active)):
        for j in range(i + 1, len(active)):
            f1, g1s = active[i]
            f2, g2s = active[j]
            hit = False
            for g1 in g1s:
                for g2 in g2s:
                    if _owns.witness(g1, g2):
                        hit = True
                        break
                if hit:
                    break
            if hit:
                pair_count += 1
                per_fid[f1] = per_fid.get(f1, 0) + 1
                per_fid[f2] = per_fid.get(f2, 0) + 1
    return pair_count > 0, pair_count, per_fid


def _gate_activations_before_persist(out, raw_status_by_id):
    """ДО persist: гейт для фронтов, переходящих в active (не migrate running→active)."""
    fronts = out.get("fronts") if isinstance(out, dict) else []
    if not isinstance(fronts, list):
        return
    activating = []
    for fr in fronts:
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not fid or fr.get("status") != "active":
            continue
        prev = raw_status_by_id.get(fid)
        if prev == "active":
            continue
        if prev == "running":
            continue  # только migrate legacy → не гейтить
        activating.append(fid)
    for fid in activating:
        check_list = []
        for fr in fronts:
            if not isinstance(fr, dict) or not fr.get("id"):
                continue
            fr2 = dict(fr)
            oid = fr2["id"]
            if oid == fid or oid in activating:
                fr2["status"] = "active"
            elif oid in raw_status_by_id:
                # сырой статус пэера (running остаётся running → не блокирует)
                fr2["status"] = raw_status_by_id[oid]
            check_list.append(fr2)
        ok, msg = check_activation_gate(check_list, fid)
        if not ok:
            raise ValueError([msg] if msg else ["отказ активации"])


def save_fronts(f, timeout_s=5.0):
    """Атомарная запись fronts.json после валидации.

    Legacy-статусы принимаются и перед записью нормализуются в канон.
    Запись под каталог-замком fronts.json.lock с 3-way merge по id фронта;
    при busy — RuntimeError (не ValueError: панель ловит ValueError и пишет
    в обход замка).
    Гейт активации (owns) — ДО persist; отказ → ValueError.
    F-C5 K3 close-гейт: переход *→done при непустых close_blockers →
    ValueError с перечнем «класс:элемент» (ошибка скана = блокер
    close_scan_error — fail-closed). Блокеры считаются ДО захвата
    замка (кэш по mtime journal+fronts+order.md+kit); под замком — только
    сверка ключа (mtime изменился → отпустить замок и пересчитать вне
    замка; пересчёта под замком нет, после исчерпания ретраев — отказ).
    Чистый переход *→done помечается journal-записью front_closed_clean
    (граница «события» пост-чипа); после persist — скан-писатель чипа
    front_closed_red (vim-обходы/гонки мимо гейта краснеют чипом).
    """
    global _fronts_base
    pf = fronts_path()
    d = os.path.dirname(pf)
    os.makedirs(d, exist_ok=True)
    lock_dir = pf + ".lock"
    state = d
    retries = 0
    clean_closes = []
    while True:
        # close-гейт: тяжёлый скан ДО замка (lock-гигиена K3)
        gate_key, gate_blockers, gate_fids = _close_gate_preflight(
            f, pf, state=state)
        held = _dir_lock_acquire(lock_dir, timeout_s=timeout_s)
        if not held:
            raise RuntimeError("fronts lock busy: %s" % lock_dir)
        try:
            if (gate_key is not None and retries < 3
                    and _close_gate_key(state, pf, gate_fids) != gate_key):
                # journal/fronts изменились после preflight → пересчёт
                # ВНЕ замка (finally отпустит замок перед новой попыткой)
                retries += 1
                continue
            # re-read current under lock for 3-way merge
            raw_status_by_id = {}
            if os.path.exists(pf):
                try:
                    with open(pf, "r", encoding="utf-8-sig") as fh:
                        cur_data = json.load(fh)
                except Exception:
                    cur_data = {"goal": "", "fronts": [], "notes": ""}
                if not isinstance(cur_data, dict):
                    cur_data = {"goal": "", "fronts": [], "notes": ""}
                cur_fronts = cur_data.get("fronts") if isinstance(cur_data.get("fronts"), list) else []
                # сырые статусы ДО migrate — для гейта (running ≠ active)
                for fr in cur_fronts:
                    if isinstance(fr, dict) and fr.get("id"):
                        raw_status_by_id[fr["id"]] = fr.get("status")
                _migrate_fronts_list(cur_fronts)
                current = {
                    "goal": cur_data.get("goal", "") if isinstance(cur_data.get("goal"), str) else "",
                    "fronts": cur_fronts,
                    "notes": cur_data.get("notes", "") if isinstance(cur_data.get("notes"), str) else "",
                }
            else:
                current = {"goal": "", "fronts": [], "notes": ""}
            ours = f if isinstance(f, dict) else {"fronts": []}
            ours_fronts = ours.get("fronts") if isinstance(ours.get("fronts"), list) else []
            # новые фронты (нет в raw_status_by_id) → prev=None → гейт при status=active
            _migrate_fronts_list(ours_fronts)
            ours = {
                "goal": ours.get("goal", "") if isinstance(ours.get("goal"), str) else "",
                "fronts": ours_fronts,
                "notes": ours.get("notes", "") if isinstance(ours.get("notes"), str) else "",
            }
            base = _fronts_base if _fronts_base is not None else copy.deepcopy(current)
            out = _three_way_fronts_merge(base, ours, current)
            _migrate_fronts_list(out.get("fronts") or [])
            errs = validate_fronts(out)
            if errs:
                raise ValueError(errs)
            _gate_activations_before_persist(out, raw_status_by_id)
            # F-C5 K3: close-гейт (переход *→done при красных) — под замком
            # только сверка блокеров, посчитанных ДО замка; пересчёта ПОД
            # замком нет: unknown → отпустить замок и пересчитать вне замка
            unknown, clean_closes = _gate_close_before_persist(
                out, raw_status_by_id, gate_blockers)
            if unknown and retries < 3:
                retries += 1
                continue
            if unknown:
                raise ValueError([
                    "close-гейт: пересчёт блокеров не сошёлся (state менялся "
                    "под руками), повторите закрытие: %s"
                    % ", ".join(unknown)])
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
            _fronts_base = copy.deepcopy(out)
        finally:
            _dir_lock_release(lock_dir)
        break
    # F-C5 K3: маркер чистого закрытия — граница «события» пост-чипа
    # (чип front_closed_red погашен чистым пере-закрытием НОВЕЕ него;
    # второе красное закрытие пишет НОВЫЙ чип — дедуп «на событие»)
    for fid in clean_closes or ():
        journal_append({
            "ts": time.time(),
            "kind": "note",
            "note": FRONT_CLOSED_CLEAN_NOTE,
            "front": fid,
        })
    # F-C5 K3: пост-чип — скан done-фронтов ВНЕ замка (vim-обходы/гонки
    # мимо гейта получают journal-чип front_closed_red; дедуп в emit)
    try:
        front_closed_red_scan(state=state)
    except Exception:
        pass


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


# Holders of dir-locks in this process: lock_dir → {token, stop, thread}
_dir_lock_holders = {}


def _pid_alive(pid):
    try:
        pid = int(pid)
    except Exception:
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
    except Exception:
        return False


def _dir_lock_owner_path(lock_dir):
    return os.path.join(lock_dir, ".owner")


def _dir_lock_write_owner(lock_dir, token):
    path = _dir_lock_owner_path(lock_dir)
    data = {
        "pid": os.getpid(),
        "token": token,
        "heartbeat_ts": time.time(),
    }
    fd, tmp = tempfile.mkstemp(dir=lock_dir, prefix=".owner-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            if os.path.exists(tmp):
                os.unlink(tmp)
        except Exception:
            pass
        raise
    try:
        os.utime(lock_dir, None)
    except Exception:
        pass


def _dir_lock_read_owner(lock_dir):
    path = _dir_lock_owner_path(lock_dir)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _dir_lock_owner_live(owner, stale_s):
    """True если owner-файл есть, pid жив и heartbeat свежий."""
    if not owner:
        return False
    try:
        hb = float(owner.get("heartbeat_ts") or 0)
    except Exception:
        return False
    if time.time() - hb > float(stale_s):
        return False
    return _pid_alive(owner.get("pid"))


def _dir_lock_heartbeat_loop(lock_dir, token, stop_evt, stale_s):
    interval = max(0.02, min(0.2, float(stale_s) / 3.0 if float(stale_s) > 0 else 0.05))
    while not stop_evt.wait(interval):
        try:
            owner = _dir_lock_read_owner(lock_dir)
            if not owner or owner.get("token") != token:
                break
            _dir_lock_write_owner(lock_dir, token)
        except Exception:
            break


def _dir_lock_start_heartbeat(lock_dir, token, stale_s):
    stop = threading.Event()
    thr = threading.Thread(
        target=_dir_lock_heartbeat_loop,
        args=(lock_dir, token, stop, stale_s),
        daemon=True,
    )
    thr.start()
    _dir_lock_holders[lock_dir] = {
        "token": token,
        "stop": stop,
        "thread": thr,
    }


def _dir_lock_stop_heartbeat(lock_dir):
    info = _dir_lock_holders.pop(lock_dir, None)
    if not info:
        return None
    try:
        info["stop"].set()
    except Exception:
        pass
    try:
        info["thread"].join(timeout=1.0)
    except Exception:
        pass
    return info.get("token")


def _dir_lock_cleanup_stolen(stolen):
    """Удалить stale rename-жертву (owner + прочие файлы + каталог)."""
    try:
        for name in os.listdir(stolen):
            try:
                os.unlink(os.path.join(stolen, name))
            except Exception:
                pass
    except Exception:
        pass
    try:
        os.rmdir(stolen)
    except Exception:
        try:
            sys.stderr.write(
                "orchlib: stale-lock cleanup failed: %s\n" % stolen)
        except Exception:
            pass


def _dir_lock_acquire(lock_dir, timeout_s=5.0, stale_s=30.0):
    """Каталог-замок через os.mkdir + owner token/pid/heartbeat.

    True — замок взят; False — не удалось.
    Reclaim: atomic rename с inode revalidate; live owner не крадётся.
    Tokenless aged (нет owner) = abandoned → ровно один winner.
    """
    deadline = time.time() + float(timeout_s)
    sleep_s = 0.05
    token = uuid.uuid4().hex
    while True:
        try:
            os.mkdir(lock_dir)
            try:
                _dir_lock_write_owner(lock_dir, token)
            except Exception as exc:
                try:
                    os.rmdir(lock_dir)
                except Exception:
                    pass
                try:
                    sys.stderr.write(
                        "orchlib: lock owner write failed: %s\n" % exc)
                except Exception:
                    pass
                return False
            _dir_lock_start_heartbeat(lock_dir, token, stale_s)
            return True
        except FileExistsError:
            try:
                st = os.stat(lock_dir)
                ino = st.st_ino
            except FileNotFoundError:
                continue
            except Exception as exc:
                try:
                    sys.stderr.write(
                        "orchlib: lock mtime check failed: %s\n" % exc)
                except Exception:
                    pass
                ino = None
            owner = _dir_lock_read_owner(lock_dir)
            if _dir_lock_owner_live(owner, stale_s):
                # живой holder — ждать, не steal
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
                continue
            # нет live owner: tokenless → только если aged; stale owner → reclaim
            if owner is None:
                try:
                    age = time.time() - os.path.getmtime(lock_dir)
                except Exception:
                    age = 0.0
                if age <= float(stale_s):
                    # свежий mkdir без owner (окно записи) — ждать
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
                    continue
            # reclaim: exclusive gate + rename; inode revalidate; один winner gate
            gate = lock_dir + ".reclaim-gate"
            try:
                os.mkdir(gate)
            except FileExistsError:
                # другой процесс reclaim'ит — ждать появления live owner / timeout
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
                continue
            except Exception:
                return False
            stolen = "%s.stale-%d-%.6f" % (
                lock_dir, os.getpid(), time.time())
            try:
                if ino is not None:
                    st2 = os.stat(lock_dir)
                    if st2.st_ino != ino:
                        raise FileNotFoundError("inode changed")
                os.rename(lock_dir, stolen)
            except Exception:
                try:
                    os.rmdir(gate)
                except Exception:
                    pass
                if time.time() >= deadline:
                    return False
                time.sleep(sleep_s)
                sleep_s = min(sleep_s * 1.5, 0.5)
                continue
            # Claim new lock WHILE holding gate — иначе окно rename→mkdir
            # даёт второму вору mkdir до owner/heartbeat.
            claimed = False
            try:
                os.mkdir(lock_dir)
                try:
                    _dir_lock_write_owner(lock_dir, token)
                except Exception as exc:
                    try:
                        os.rmdir(lock_dir)
                    except Exception:
                        pass
                    try:
                        sys.stderr.write(
                            "orchlib: lock owner write failed: %s\n" % exc)
                    except Exception:
                        pass
                else:
                    _dir_lock_start_heartbeat(lock_dir, token, stale_s)
                    claimed = True
            except Exception:
                claimed = False
            _dir_lock_cleanup_stolen(stolen)
            try:
                os.rmdir(gate)
            except Exception:
                pass
            if claimed:
                return True
            if time.time() >= deadline:
                return False
            time.sleep(sleep_s)
            sleep_s = min(sleep_s * 1.5, 0.5)
            continue
        except Exception as exc:
            try:
                sys.stderr.write(
                    "orchlib: lock acquire failed: %s\n" % exc)
            except Exception:
                pass
            return False


def _dir_lock_release(lock_dir):
    """Снять каталог-замок (только свой token); ошибки — тихий stderr."""
    token = _dir_lock_stop_heartbeat(lock_dir)
    owner = _dir_lock_read_owner(lock_dir)
    if token is not None and owner is not None:
        if owner.get("token") != token:
            return
    try:
        op = _dir_lock_owner_path(lock_dir)
        if os.path.isfile(op):
            os.unlink(op)
    except Exception:
        pass
    try:
        # убрать возможные tmp-файлы owner
        for name in os.listdir(lock_dir):
            try:
                os.unlink(os.path.join(lock_dir, name))
            except Exception:
                pass
    except Exception:
        pass
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
    Под lock: re-check status; cancelled|rejected → RuntimeError front-runs closed.
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
        # TOCTOU status→bump: re-check under lock
        st = front_status(fid)
        canon = normalize_front_status(st) if st is not None else None
        if canon in ("cancelled", "rejected") or st in ("cancelled", "rejected"):
            raise RuntimeError("front-runs closed: %s" % fid)
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


# --- F-ADVERSARIAL ADV-C1: A2 order_no_mechanics (V2) ---------------------
# Якоря механики запуска — ТОЛЬКО точные литералы-подстроки (без regex).
# Расширение списка = отдельная волна.
ORDER_MECHANICS_ANCHORS = (
    "run-exec",
    "run-cloud",
    "ORCHESTRATION_DIR",
    "--session",
    "--front",
    "--prompt-file",
    "--probe",
    "bin/",
    "python3",
    "cursor-agent",
    "PATH=",
)


def _order_has_mechanics(text):
    """True, если в тексте есть ≥1 якорь механики (точная подстрока).

    Якоря — ORDER_MECHANICS_ANCHORS (литералы, без regex).
    """
    if not text:
        return False
    for anchor in ORDER_MECHANICS_ANCHORS:
        if anchor in text:
            return True
    return False


def orders_without_mechanics(state=None):
    """Относительные пути order.md с basis=true без якорей механики (posix).

    Walk как у orders_without_basis: fronts/<id>/order.md и
    fronts/<id>/colonels/<cid>/order.md.
    ЛОВИТ: _order_has_basis True И якоря нет.
    МОЛЧИТ (не в списке): basis=false ИЛИ якорь есть.
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
            if not _order_has_basis(text):
                continue
            if _order_has_mechanics(text):
                continue
            rel = os.path.relpath(path, state)
            out.append(rel.replace(os.sep, "/"))
        return out
    except Exception:
        return []


# --- F-ORDERTRUTH: «без советников» + маркеры допущения --------------------
# Маркеры Цель-1 приказа фронта (дословно); публичный предикат — для reground W2.
_ORDER_ASSUMPTION_MARKERS_RE = re.compile(
    r"(вероятно|похоже|должно быть|наверное|предполагаю|скорее всего|кажется)",
    re.IGNORECASE | re.UNICODE,
)
_ORDER_BEZ_SOVETNIKOV_RE = re.compile(
    r"без советников", re.IGNORECASE | re.UNICODE)
# Статусы «живых» фронтов: канон + legacy (не cancelled/rejected/done).
_ORDERS_SUSPECT_LIVE = frozenset((
    "active", "stalled", "proposed",
    "running", "planned", "blocked",
))
# A4-FIX: причины серой зоны отдельно от значений чипа (чип = список id).
_ORDERS_SUSPECT_REASONS = {}
# Малый таймаут: health-скан не должен висеть на jev.
ORDERS_ADVISOR_NEED_TIMEOUT_S = 3.0
_ORDERS_ALLOWLIST_CACHE = None  # (mtime|path, entries)


def order_suspect_facts(text):
    """Чистый текст-предикат: (без_советников, markers). Без ФС.

    без_советников — True, если в тексте есть «без советников» (case-insensitive).
    markers — найденные маркеры допущения Цель-1 (dedup, порядок появления).
    Публичное API для сканера и reground W2.
    """
    if not isinstance(text, str) or not text:
        return False, []
    bez = bool(_ORDER_BEZ_SOVETNIKOV_RE.search(text))
    markers = []
    seen = set()
    for m in _ORDER_ASSUMPTION_MARKERS_RE.finditer(text):
        raw = m.group(1)
        key = raw.lower()
        if key not in seen:
            seen.add(key)
            markers.append(raw)
    return bez, markers


def _order_assumption_verified(text):
    """True, если строка начинается с «допущение проверено замером:» + ссылка.

    Как _order_has_basis: только начало строки после lstrip (цитата в теле
    приказа не считается пометкой-снятием).
    """
    if not isinstance(text, str) or not text:
        return False
    needle = "допущение проверено замером:"
    for line in text.splitlines():
        s = line.lstrip()
        if not s.startswith(needle):
            continue
        rest = s[len(needle):].strip()
        if rest:
            return True
    return False


def _role_is_opportunity_advisor(role):
    role = normalize_journal_role(role)
    if not isinstance(role, str) or not role:
        return False
    return (role == "meta/opportunity-advisor.md"
            or role == "opportunity-advisor"
            or role.endswith("opportunity-advisor.md"))


def orders_suspect_reasons():
    """Side-map причин последнего orders_suspect: {front_id: reason}."""
    return dict(_ORDERS_SUSPECT_REASONS)


def _orders_allowlist_path(kit_dir=None):
    override = (os.environ.get("ORCH_ORDERS_ALLOWLIST") or "").strip()
    if override:
        return override
    return os.path.join(
        kit_dir or KIT_DIR, "tests", "adversarial", "allowlist.json")


def _orders_allowlist_entries(kit_dir=None):
    """Загрузить entries allowlist; fail-open → []."""
    global _ORDERS_ALLOWLIST_CACHE
    path = _orders_allowlist_path(kit_dir)
    try:
        mtime = os.path.getmtime(path)
    except Exception:
        _ORDERS_ALLOWLIST_CACHE = None
        return []
    if (
        _ORDERS_ALLOWLIST_CACHE is not None
        and _ORDERS_ALLOWLIST_CACHE[0] == (path, mtime)
    ):
        return _ORDERS_ALLOWLIST_CACHE[1]
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        entries = data.get("entries") if isinstance(data, dict) else data
        if not isinstance(entries, list):
            entries = []
        out = [e for e in entries if isinstance(e, dict)]
        _ORDERS_ALLOWLIST_CACHE = ((path, mtime), out)
        return out
    except Exception:
        _ORDERS_ALLOWLIST_CACHE = None
        return []


def _orders_allowlist_pattern_hit(pattern, text):
    """pattern: точная подстрока ИЛИ regex (префикс re:/regex:)."""
    if not isinstance(pattern, str) or not pattern or not isinstance(text, str):
        return False
    if pattern.startswith("re:") or pattern.startswith("regex:"):
        body = pattern.split(":", 1)[1]
        try:
            return bool(re.search(body, text, re.IGNORECASE | re.UNICODE))
        except Exception:
            return False
    return pattern in text


def _orders_allowlist_entry_active(entry, today=None):
    """True, если expires_on ещё не просрочен (дата включительно)."""
    exp = entry.get("expires_on")
    if not isinstance(exp, str) or not exp.strip():
        return False
    try:
        y, m, d = [int(x) for x in exp.strip().split("-", 2)]
        from datetime import date as _date
        exp_d = _date(y, m, d)
        if today is None:
            today = _date.today()
        elif not isinstance(today, _date):
            today = _date.today()
        return today <= exp_d
    except Exception:
        return False


def orders_allowlist_hit(text, kit_dir=None, today=None):
    """True, если текст бьёт неистёкший pattern allowlist (без jev)."""
    if not isinstance(text, str) or not text:
        return False
    for entry in _orders_allowlist_entries(kit_dir):
        if not _orders_allowlist_entry_active(entry, today=today):
            continue
        if _orders_allowlist_pattern_hit(entry.get("pattern"), text):
            return True
    return False


def _orders_grey_zone_decision(fid, text):
    """Серая зона bez без маркеров вне allowlist → (flagged, reason).

    mechanical (любой band) → молчание; fork mid/high → jev=fork;
    fork+low → jev=low-confidence; defer/absent/API-fail/unknown → jev=unavailable.
    """
    questions = {
        "advisor-need-check": {
            "type": "choice",
            "instructions": (
                "чистый «без советников» без маркеров допущения: "
                "легитимная механика или развилка (fork)?"
            ),
            "criteria": {
                "mechanical": "легитимная механика, выбора нет",
                "fork": "есть развилка/выбор — нужен advisor",
            },
        }
    }
    data, err = _rules_run_jev_advise(
        "advisor-need-check",
        fid or "orders_suspect",
        (text or "")[:2000],
        questions,
        timeout_s=ORDERS_ADVISOR_NEED_TIMEOUT_S,
    )
    if err or not data:
        return True, "advisor-need-check: jev=unavailable"
    advice = (data.get("advice") or {}).get("advisor-need-check")
    if not isinstance(advice, dict):
        # stdout мог быть primary advice без обёртки advice{}
        if isinstance(data.get("choice"), (str, list)) or data.get("band"):
            advice = data
        else:
            return True, "advisor-need-check: jev=unavailable"
    choice = advice.get("choice")
    if isinstance(choice, list):
        choice = choice[0] if choice else None
    if isinstance(choice, str):
        choice = choice.strip().lower()
    else:
        choice = ""
    band = advice.get("band")
    band = band.strip().lower() if isinstance(band, str) else ""
    # тихое OK только при явном mechanical (любой band)
    if choice == "mechanical":
        return False, None
    if choice == "fork" and band in ("mid", "high"):
        return True, "advisor-need-check: jev=fork"
    if choice == "fork" and band == "low":
        return True, "advisor-need-check: jev=low-confidence"
    # defer / absent / неизвестная форма → fail-safe suspect
    return True, "advisor-need-check: jev=unavailable"


def orders_suspect(state=None):
    """id фронтов с подозрительным «без советников» в order.md.

    Скан fronts/<id>/order.md и fronts/<id>/colonels/*/order.md.
    Статусы: active/stalled/proposed (+legacy running/planned/blocked);
    cancelled/rejected/done вне.
    Флаг: (а) bez И markers≥1; ИЛИ (б) bez без маркеров вне allowlist
    и jev advisor-need-check = fork mid/high / jev=unavailable.
    Снятие: нет маркеров+allowlist/mechanical-low / advisor journal /
    «допущение проверено замером:». Dedup; fail-open → [].
    Причины — orders_suspect_reasons() (не в чипе).
    """
    global _ORDERS_SUSPECT_REASONS
    _ORDERS_SUSPECT_REASONS = {}
    try:
        if state is None:
            state = find_state_dir()
        data = _load_fronts_at(state)
        journal = _journal_entries_at(state)
        advisor_fronts = set()
        for e in journal:
            if not _role_is_opportunity_advisor(e.get("role")):
                continue
            af = e.get("front")
            if isinstance(af, str) and af:
                advisor_fronts.add(af)
        out = []
        seen = set()
        for fr in data.get("fronts") or []:
            if not isinstance(fr, dict):
                continue
            fid = fr.get("id")
            if not isinstance(fid, str) or not fid:
                continue
            if fr.get("status") not in _ORDERS_SUSPECT_LIVE:
                continue
            if fid in advisor_fronts:
                continue
            paths = [os.path.join(state, "fronts", fid, "order.md")]
            col_root = os.path.join(state, "fronts", fid, "colonels")
            if os.path.isdir(col_root):
                try:
                    for cid in sorted(os.listdir(col_root)):
                        p = os.path.join(col_root, cid, "order.md")
                        if os.path.isfile(p):
                            paths.append(p)
                except Exception:
                    pass
            flagged = False
            reason = None
            for path in paths:
                try:
                    with open(path, "r", encoding="utf-8-sig") as f:
                        text = f.read()
                except Exception:
                    continue
                if not text or not text.strip():
                    continue
                if _order_assumption_verified(text):
                    continue
                bez, markers = order_suspect_facts(text)
                if not bez:
                    continue
                if markers:
                    flagged = True
                    reason = "markers"
                    break
                # серая зона: bez без маркеров
                if orders_allowlist_hit(text):
                    continue  # молчание (детерминированно)
                flagged, reason = _orders_grey_zone_decision(fid, text)
                if flagged:
                    break
            if flagged and fid not in seen:
                seen.add(fid)
                out.append(fid)
                if reason:
                    _ORDERS_SUSPECT_REASONS[fid] = reason
        return out
    except Exception:
        return []


# --- F-MUSTMAP MM-C2: handoff_oversize / project_md_missing / mustmap_stale ---
HANDOFF_MAX_CHARS = 2000


def _hierarchy_graph_active(state):
    """True, если в fronts.json есть хотя бы один фронт (иерархия стартовала)."""
    try:
        fronts = (_load_fronts_at(state).get("fronts") or [])
        return isinstance(fronts, list) and len(fronts) > 0
    except Exception:
        return False


def handoff_oversize(state=None):
    """handoff.md >2000 символов при непустом графе фронтов.

    Нет файла / иерархия не стартовала / ≤2000 → []. Fail-open → [].
    Значение: «handoff.md:<len>».
    """
    try:
        if state is None:
            state = find_state_dir()
        if not _hierarchy_graph_active(state):
            return []
        path = os.path.join(state, "handoff.md")
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                text = f.read()
        except Exception:
            return []
        n = len(text)
        if n > HANDOFF_MAX_CHARS:
            return ["handoff.md:%d" % n]
        return []
    except Exception:
        return []


def project_md_missing(state=None):
    """Нет PROJECT.md рядом со state при непустом графе фронтов.

    PROJECT.md = dirname(state)/PROJECT.md. Fail-open → [].
    """
    try:
        if state is None:
            state = find_state_dir()
        if not _hierarchy_graph_active(state):
            return []
        project_md = os.path.join(os.path.dirname(state), "PROJECT.md")
        if os.path.isfile(project_md):
            return []
        return ["PROJECT.md"]
    except Exception:
        return []


def mustmap_stale(kit_dir=None):
    """audit/mustmap/mustmap.json устарел vs doctrine_files (mtime).

    Нет файла → [] (fail-open). Битый JSON / нет doctrine_files → [\"invalid\"].
    mtime(doctrine) > mtime(mustmap) → список устаревших rel-путей.
    """
    try:
        if kit_dir is None:
            kit_dir = KIT_DIR
        path = os.path.join(kit_dir, "audit", "mustmap", "mustmap.json")
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            return ["invalid"]
        if not isinstance(data, dict):
            return ["invalid"]
        doctrine = data.get("doctrine_files")
        if not isinstance(doctrine, list) or not doctrine:
            return ["invalid"]
        try:
            mm_mtime = os.path.getmtime(path)
        except Exception:
            return ["invalid"]
        stale = []
        for rel in doctrine:
            if not isinstance(rel, str) or not rel:
                continue
            fp = os.path.join(kit_dir, rel)
            try:
                if os.path.isfile(fp) and os.path.getmtime(fp) > mm_mtime:
                    stale.append(rel.replace("\\", "/"))
            except Exception:
                continue
        return stale
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
    """Все валидные записи journal.jsonl из state (хронологический порядок).

    Чтение bytes + decode("utf-8", "replace") — оборванный мультибайтный
    хвост писателя не глушит читателя в пустоту (K1-паттерн сторожа,
    приказ K3 п.8): битые строки пропускаются, остальные читаются.
    Отсутствие файла — легитимная тишина ([]); прочие ошибки чтения
    (EACCES/EIO/каталог на месте journal.jsonl) ПРОКИДЫВАЮТСЯ вверх —
    close-гейт обязан отказать (fail-closed, close_scan_error), а не
    слепнуть до «пусто» (KR1 fix-ревью).
    """
    path = os.path.join(state, "journal.jsonl")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return []
    out = []
    for raw_line in raw.decode("utf-8", "replace").splitlines():
        s = raw_line.strip()
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

# ADV-TC4: tracked-dirty вне волны. Роли открытого start, гасящие FP код-волны.
KIT_DIRTY_OUTSIDE_WAVE_ROLES = frozenset((
    "code/coder.md",
    "code/git-warden.md",
    "code/docs-keeper.md",
    "code/simplicity-warden.md",
))


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


def _kit_dirty_mask_pathspecs(kit_dir):
    """Маска kit_dirty_outside_wave: wave_no_docs + README.md (A5)."""
    specs = list(_wave_docs_mask_pathspecs(kit_dir))
    if "README.md" not in specs:
        specs.append("README.md")
    return specs


def _kit_dirty_posix_path(raw):
    """Путь из porcelain → posix relative без кавычек git."""
    p = (raw or "").strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _kit_dirty_path_excluded(path):
    if not path:
        return True
    if path == "SHA256SUMS":
        return True
    if path == ".orchestration" or path.startswith(".orchestration/"):
        return True
    return False


def _parse_kit_dirty_porcelain(stdout):
    """Tracked paths из `git status --porcelain` (без untracked). Детерминированный порядок."""
    found = []
    seen = set()
    for raw in (stdout or "").splitlines():
        if not raw or len(raw) < 3:
            continue
        xy = raw[:2]
        if xy == "??":
            continue
        rest = raw[3:] if raw[2:3] == " " else raw[2:].lstrip()
        path = rest
        if " -> " in rest and (xy[0] in "RC" or xy[1] in "RC"):
            path = rest.split(" -> ", 1)[1]
        rel = _kit_dirty_posix_path(path)
        if _kit_dirty_path_excluded(rel):
            continue
        if rel not in seen:
            seen.add(rel)
            found.append(rel)
    found.sort()
    return found


def _code_wave_fixpoint_open(entries):
    """Открытый start код-волны (роли KIT_DIRTY_OUTSIDE_WAVE_ROLES).

    Закрытый end (в т.ч. git-warden) не гасит: после конца волны dirty снова красный.
    writable+front без роли из списка не гасит.
    """
    open_starts = {}
    for e in entries or []:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        rid = e.get("id")
        if kind == "start" and rid:
            open_starts[rid] = e
        elif kind == "end" and rid:
            open_starts.pop(rid, None)
    for st in open_starts.values():
        role = normalize_journal_role(st.get("role"))
        if role in KIT_DIRTY_OUTSIDE_WAVE_ROLES:
            return True
    return False


def _kit_dirty_outside_wave(entries, kit_dir):
    """Список posix-путей tracked-dirty по маске, если нет фикс-точки волны."""
    if not kit_dir:
        return []
    if _code_wave_fixpoint_open(entries):
        return []
    try:
        env = os.environ.copy()
        env.pop("GIT_DIR", None)
        env.pop("GIT_WORK_TREE", None)
        env.pop("GIT_INDEX_FILE", None)
        r = subprocess.run(
            [
                "git", "-C", kit_dir, "status",
                "--porcelain", "--untracked-files=no", "--",
            ] + _kit_dirty_mask_pathspecs(kit_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            universal_newlines=True,
            timeout=15,
            env=env,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return _parse_kit_dirty_porcelain(r.stdout)


# --- rules cards (F-RULES R1) -----------------------------------------------

RULES_ACTIVE_LIMIT = 25
RULES_CATEGORY_LIMIT = 20
# Age-grace C3-DETECT: hit=0 старше N часов без доставки → undelivered/dead.
RULES_DEAD_AGE_HOURS = 24.0
# Сироты wave-age (заменены RULES_DEAD_AGE_HOURS); оставлены для lint-имён.
RULES_DEAD_WAVES = 10
RULES_DEAD_AGE_WAVES = 10
RULES_JEV_TIMEOUT_S = 35.0
RULES_JEV_SHORTLIST_MAX = 10
RULES_KOMU = frozenset({
    "commander", "general", "colonel", "executor", "wrapper", "panel"})
RULES_KOGDA = frozenset({
    "decomposition", "launch", "acceptance", "retro",
    "prompt-submit", "post-tool", "task-active", "chip-red"})
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
    Исключает undelivered + патологический dead + hit=0∧deliveries=0∧opportunity
    (без порога возраста) — иначе LRU скроет сигнал доставки.
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
    protect = set()
    chips = {}
    try:
        chips = health_red_chips(state=state, kit_dir=kit_dir)
        protect |= set(chips.get("rules_undelivered") or [])
        protect |= set(chips.get("rules_dead") or [])
    except Exception:
        chips = {}
    try:
        st = state if state is not None else find_state_dir()
        full = _journal_entries_at(st)
        dcounts = _rules_delivery_counts(full)
        for c in zeros:
            cid = c.get("id")
            if not cid or cid in protect:
                continue
            if int(dcounts.get(cid) or 0) != 0:
                protect.add(cid)
                continue
            if _rules_has_opportunity(
                    c, full, manifest, other_chips=chips):
                protect.add(cid)
    except Exception:
        pass
    need = len(active) - limit
    moved = []
    arch_root = rules_archive_dir(kit_dir)
    os.makedirs(arch_root, exist_ok=True)
    for c in zeros:
        if need <= 0:
            break
        cid = c.get("id")
        if not cid or cid in protect:
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
    born_at — штамп рождения (ts); age-grace undelivered/dead: now−(born_at||created).
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
# 13 = FRONT_DUAL_WRITER (шов MW2-B: второй пишущий ран того же фронта)
_GATE_REFUSE_EXITS = frozenset({5, 6, 7, 8, 9, 11, 12, 13})


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
    if session:
        sid = safe_name(session)
        p = os.path.join(state, "sessions", sid, "runs", run_id, "run.log")
        if os.path.isfile(p):
            return p
        return p
    # без session: не выбирать молча один sid при коллизии run_id
    sess_hits = []
    sess_root = os.path.join(state, "sessions")
    if os.path.isdir(sess_root):
        try:
            for sid in os.listdir(sess_root):
                p = os.path.join(sess_root, sid, "runs", run_id, "run.log")
                if os.path.isfile(p):
                    sess_hits.append(p)
        except Exception:
            pass
    if len(sess_hits) > 1:
        return None
    if len(sess_hits) == 1:
        return sess_hits[0]
    candidates = [
        os.path.join(state, "cursor-run-%s.log" % run_id),
        os.path.join(state, "runs", run_id, "run.log"),
    ]
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
    markers = ("prompt.run.md", "artifact.md", "run.log", "probe-receipt.md")

    def _has_marker(d):
        if not d or not os.path.isdir(d):
            return False
        for m in markers:
            if os.path.isfile(os.path.join(d, m)):
                return True
        return False

    if session:
        sid = safe_name(session)
        d = os.path.join(state, "sessions", sid, "runs", run_id)
        return d if _has_marker(d) else None
    sess_hits = []
    sess_root = os.path.join(state, "sessions")
    if os.path.isdir(sess_root):
        try:
            for sid in os.listdir(sess_root):
                d = os.path.join(sess_root, sid, "runs", run_id)
                if _has_marker(d):
                    sess_hits.append(d)
        except Exception:
            pass
    if len(sess_hits) > 1:
        return None
    if len(sess_hits) == 1:
        return sess_hits[0]
    d = os.path.join(state, "runs", run_id)
    return d if _has_marker(d) else None


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
# ADDITIVE MARKER: RCPT-A generator/cmd_sha256 fields (compat dual-read)
_RECEIPT_FIELD_RE = re.compile(
    r"(?m)^\s*(probe|cmd|exit|oracle_match|ts|critic_id|artifact"
    r"|generator|cmd_sha256)\s*:\s*(.*\S)\s*$")
_RECEIPT_INLINE_RE = re.compile(
    r"\b(probe|cmd|exit|oracle_match|ts|critic_id|artifact"
    r"|generator|cmd_sha256)\s*:\s*([^;]+)")


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


def _parse_receipt_ts(raw):
    """ts квитанции §3: epoch-число или ISO-8601 → float UTC epoch; иначе None.

    ISO формы: «...Z» и «...+03:00» (и аналоги). Нормализация через
    stdlib datetime (см. _rules_iso_to_ts). Мусор → None (= ts_not_number).
    """
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    try:
        return float(s)
    except (TypeError, ValueError):
        pass
    return _rules_iso_to_ts(s)


def parse_probe_receipt(text, artifact_path=None, run_id=None):
    """Валидация квитанции §3 → (ok: bool, reason: str).

    ok только если: все поля; exit числом; exit==оракулу (если числовой);
    oracle_match true; ts≥mtime(артефакта); artifact ссылается на волну;
    проза без cmd/exit = violation.
    ts: epoch-число или ISO-8601 (Z / ±offset) → epoch UTC.
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
        ts = _parse_receipt_ts(rec["ts"])
        if ts is None:
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


def _receipt_require_generator(state=None):
    """params receipt.require_generator; отсутствует/ошибка → False (fail-open)."""
    try:
        if state is None:
            state = find_state_dir()
        pf = os.path.join(state, "params.json")
        if not os.path.isfile(pf):
            return False
        with open(pf, "r", encoding="utf-8-sig") as f:
            p = json.load(f)
        if not isinstance(p, dict):
            return False
        receipt = p.get("receipt")
        if not isinstance(receipt, dict):
            return False
        return bool(receipt.get("require_generator", False))
    except Exception:
        return False


def _receipt_records_have_generator(text):
    """True если ≥1 запись квитанции несёт непустой generator."""
    for rec in _parse_receipt_records(text):
        gen = str(rec.get("generator") or "").strip()
        if gen:
            return True
    return False


def write_probe_receipt(
        probe, cmd, exit_code, oracle_match, critic_id, artifact,
        run_id=None, path=None, state=None, **kwargs):
    """Единственный writer квитанций §3 → (ok, path|reason).

    ts=time.time() только внутри; параметр ts извне отвергается.
    generator=orch-probe-receipt/<kit_version()>; cmd_sha256=sha256(cmd utf-8).
    Multi-record append; после записи — parse_probe_receipt; fail → откат.
    """
    if "ts" in kwargs:
        return False, "ts_rejected"
    if kwargs:
        return False, "unexpected_kwargs:%s" % ",".join(sorted(kwargs))
    if state is None:
        state = find_state_dir()
    if path is None:
        if not run_id:
            return False, "path_or_run_id_required"
        d = find_run_dir(run_id, state=state)
        if not d:
            d = os.path.join(state, "runs", str(run_id))
            try:
                os.makedirs(d, exist_ok=True)
            except Exception as e:
                return False, "mkdir_failed:%s" % e
        path = os.path.join(d, "probe-receipt.md")
    try:
        om = oracle_match
        if isinstance(om, bool):
            om_s = "true" if om else "false"
        else:
            om_s = str(om).strip().lower()
            if om_s not in ("true", "false"):
                return False, "oracle_match_bad"
        exit_s = str(int(exit_code))
    except (TypeError, ValueError):
        return False, "exit_not_int"
    cmd_s = "" if cmd is None else str(cmd)
    ts = time.time()
    generator = "orch-probe-receipt/%s" % kit_version()
    cmd_sha = hashlib.sha256(cmd_s.encode("utf-8")).hexdigest()
    block = (
        "probe: %s\n"
        "cmd: %s\n"
        "exit: %s\n"
        "oracle_match: %s\n"
        "ts: %s\n"
        "critic_id: %s\n"
        "artifact: %s\n"
        "generator: %s\n"
        "cmd_sha256: %s\n"
    ) % (
        probe, cmd_s, exit_s, om_s, ts, critic_id, artifact,
        generator, cmd_sha,
    )
    prev = None
    existed = os.path.isfile(path)
    if existed:
        try:
            with open(path, "r", encoding="utf-8") as f:
                prev = f.read()
        except Exception as e:
            return False, "read_failed:%s" % e
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            if existed and prev and not prev.endswith("\n"):
                f.write("\n")
            if existed and prev and prev.strip():
                f.write("\n")
            f.write(block)
        with open(path, "r", encoding="utf-8") as f:
            full = f.read()
    except Exception as e:
        return False, "write_failed:%s" % e
    art_path = None
    if run_id:
        art_path = wave_probe_artifact_path(run_id, state=state)
    if art_path is None and artifact and os.path.isfile(str(artifact)):
        art_path = str(artifact)
    ok, reason = parse_probe_receipt(
        full, artifact_path=art_path, run_id=run_id)
    if not ok:
        try:
            if not existed:
                os.unlink(path)
            else:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(prev if prev is not None else "")
        except Exception:
            pass
        return False, "validate_failed:%s" % reason
    return True, path


def wave_has_valid_probe_receipt(run_id, state=None):
    """True если есть ≥1 валидная квитанция §3 на артефакт волны.

    ADDITIVE: при params receipt.require_generator=true квитанции без
    generator не снимают probes_missing (fail-open: ключ отсутствует → false).
    parse_probe_receipt без params-ветки.
    """
    if state is None:
        state = find_state_dir()
    art = wave_probe_artifact_path(run_id, state=state)
    require_gen = _receipt_require_generator(state=state)
    for path in find_probe_receipts(run_id, state=state):
        text = _read_text_silent(path)
        ok, _reason = parse_probe_receipt(
            text, artifact_path=art, run_id=run_id)
        if not ok:
            continue
        if require_gen and not _receipt_records_have_generator(text):
            continue
        return True
    return False


# ADDITIVE MARKER: RCPT-A receipt_handmade begin
def receipt_handmade(state=None, scan_limit=None):
    """run_ids волн кода/фикса active-фронтов с квитанцией без generator.

    WARN-скоп (не влияет на probes_missing). Тихие ошибки → [].
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
            handmade = False
            for rpath in find_probe_receipts(rid, state=state):
                text = _read_text_silent(rpath)
                records = _parse_receipt_records(text)
                if not records:
                    continue
                if not _receipt_records_have_generator(text):
                    handmade = True
                    break
            if handmade:
                out.append(rid)
                seen.add(rid)
        return out
    except Exception:
        return []
# ADDITIVE MARKER: RCPT-A receipt_handmade end


def probes_missing(state=None, scan_limit=None, front_ids=None):
    """id волн кода/фикса active-фронтов без валидной квитанции §3.

    Скоп: только active-фронты (DON'T all-chips-green — чужие фронты не
    критерий приёмки текущего). Снятие только валидной квитанцией §3
    (probe-ран run-exec --probe может не иметь artifact.md/§1-блока).
    front_ids — синтетический скоуп (K3 close-гейт: список из одного fid
    независимо от статуса; K2-паттерн invariants_not_run). Тихие
    ошибки → [].
    """
    try:
        if state is None:
            state = find_state_dir()
        if scan_limit is None:
            scan_limit = HEALTH_JOURNAL_SCAN_LIMIT
        path = os.path.join(state, "journal.jsonl")
        # bytes + decode(replace): оборванный мультибайтный хвост писателя
        # не глушит детектор в пустоту (K3-паттерн устойчивого читателя)
        try:
            with open(path, "rb") as f:
                raw = f.read()
        except FileNotFoundError:
            return []
        except Exception:
            return []
        lines = raw.decode("utf-8", "replace").splitlines()
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
        if front_ids is None:
            active_fids = set()
            for fr in data.get("fronts") or []:
                if not isinstance(fr, dict):
                    continue
                if fr.get("status") != "active":
                    continue
                fid = fr.get("id")
                if isinstance(fid, str) and fid:
                    active_fids.add(fid)
        else:
            active_fids = set(
                f for f in front_ids if isinstance(f, str) and f)
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
            has_receipt = wave_has_valid_probe_receipt(rid, state=state)
            # F-C5 K5: spawn-ран (движковый пул) может нести receipts —
            # список id квитанций §3 волны (spawn-finish/backfill). Рана
            # прикрыта, только если ВСЕ перечисленные квитанции валидны
            # (fail-closed); без поля у код-раны — красный, как раньше.
            if (not has_receipt and st.get("spawn")
                    and isinstance(e.get("receipts"), list)):
                has_receipt = _spawn_receipts_valid(e.get("receipts"),
                                                    state=state)
            # нет валидной квитанции → красный. §1-блок не обязателен:
            # run-exec --probe пишет probe-receipt.md без artifact.md
            # (ложный probes_missing при parse=ok, кейс ADV-PROBE-A4FIX2).
            if not has_receipt:
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


def chip_silenced_ids(state=None, kit_dir=None, reported_probes=None,
                      front_ids=None, scan_limit=None):
    """id волн, где probes_missing погашен фильтром/UI без устранения причины.

    Срабатывает если сырой probes_missing непуст, а (a) ключ не в reported,
    или reported пуст при непустом raw, или (b) panel UI не объявляет чип.
    front_ids — синтетический скоуп (K3 close-гейт; K2-паттерн);
    scan_limit — сквозной в probes_missing (close-путь читает ПОЛНЫЙ
    журнал: scan_limit=False; дефолт None — окно health, поведение
    панели не меняется).
    """
    try:
        if state is None:
            state = find_state_dir()
        if kit_dir is None:
            kit_dir = KIT_DIR
        raw = probes_missing(state=state, front_ids=front_ids,
                             scan_limit=scan_limit)
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
    """Сирота wave-age (C3-DETECT → hours); число wave_ends с ts > created."""
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


def _rules_card_age_ts(card):
    """born_at если есть, иначе created."""
    if not isinstance(card, dict):
        return 0
    if card.get("born_at") is not None:
        return card.get("born_at")
    return card.get("created")


def _rules_age_hours(card, now=None):
    """Возраст карточки в часах: now − (born_at||created)."""
    if now is None:
        now = time.time()
    try:
        age_ts = float(_rules_card_age_ts(card) or 0)
    except (TypeError, ValueError):
        age_ts = 0.0
    try:
        now_f = float(now)
    except (TypeError, ValueError):
        now_f = time.time()
    return (now_f - age_ts) / 3600.0


def _rules_delivery_counts(entries):
    """id → число card_injected в полном журнале."""
    counts = {}
    for e in entries or []:
        if not isinstance(e, dict) or e.get("kind") != "card_injected":
            continue
        cid = e.get("card")
        if not cid:
            continue
        counts[cid] = counts.get(cid, 0) + 1
    return counts


def _rules_other_red_active(other_chips):
    """Есть ли непустой чип кроме rules_undelivered/rules_dead (для chip-red)."""
    if not isinstance(other_chips, dict):
        return False
    for k, v in other_chips.items():
        if k in ("rules_undelivered", "rules_dead"):
            continue
        if v:
            return True
    return False


def _rules_has_opportunity(card, full_entries, manifest, other_chips=None):
    """JOIN-контракт возможности доставки (i)/(ii)/(iii).

    (i) после born_at есть card_injected card≠id с journal.kogda == когда
        кандидата и (manifest[card].кому == кому ИЛИ role_to_komu(role) == кому);
        совпадение адресов двух карточек в манифесте НЕДОСТАТОЧНО.
    (ii) когда=launch — start роли→кому после born_at.
    (iii) когда=chip-red — сейчас есть непустой чип кроме undelivered/dead.
    """
    if not isinstance(card, dict):
        return False
    cid = card.get("id")
    komu = card.get("кому")
    kogda = card.get("когда")
    if not cid or not komu or not kogda:
        return False
    try:
        born = float(_rules_card_age_ts(card) or 0)
    except (TypeError, ValueError):
        born = 0.0
    # (i) sibling inject with matching journal.kogda × address
    for e in full_entries or []:
        if not isinstance(e, dict) or e.get("kind") != "card_injected":
            continue
        other_id = e.get("card")
        if not other_id or other_id == cid:
            continue
        try:
            ets = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ets <= born:
            continue
        if e.get("kogda") != kogda:
            continue
        other_card = _rules_card_by_id(manifest, other_id)
        other_komu = other_card.get("кому") if other_card else None
        role_komu = role_to_komu(e.get("role")) if e.get("role") else None
        if other_komu == komu or role_komu == komu:
            return True
    # (ii) launch start
    if kogda == "launch":
        for e in full_entries or []:
            if not isinstance(e, dict) or e.get("kind") != "start":
                continue
            try:
                ets = float(e.get("ts") or 0)
            except (TypeError, ValueError):
                continue
            if ets <= born:
                continue
            if role_to_komu(e.get("role")) == komu:
                return True
    # (iii) chip-red trigger сейчас
    if kogda == "chip-red" and _rules_other_red_active(other_chips):
        return True
    return False


def _rules_classify_hit0(kit_dir=None, state=None, other_chips=None, now=None):
    """Ветвление hit=0: (undelivered_ids, dead_ids).

    Кандидат = active hit=0 и возраст > RULES_DEAD_AGE_HOURS.
    (а) доставок 0 и была возможность → undelivered;
    (б) доставок ≥1 → dead;
    (в) доставок 0 и возможностей 0 → тишина.
    Доставки/возможности — полный journal.jsonl (не окно HEALTH_JOURNAL_SCAN_LIMIT);
    полный проход только если есть hit=0-кандидаты по counters+manifest.
    """
    if kit_dir is None:
        kit_dir = KIT_DIR
    if state is None:
        state = find_state_dir()
    if now is None:
        now = time.time()
    manifest = load_manifest(kit_dir)
    hits = _rules_load_hits(state)
    hit0 = []
    for c in _rules_active_cards(manifest):
        cid = c.get("id")
        if not cid:
            continue
        if int(hits.get(cid) or 0) != 0:
            continue
        hit0.append(c)
    if not hit0:
        return [], []
    full = _journal_entries_at(state)
    deliveries = _rules_delivery_counts(full)
    undelivered = []
    dead = []
    grace = float(RULES_DEAD_AGE_HOURS)
    for c in hit0:
        cid = c.get("id")
        if _rules_age_hours(c, now) <= grace:
            continue
        d = int(deliveries.get(cid) or 0)
        if d >= 1:
            dead.append(cid)
        elif _rules_has_opportunity(
                c, full, manifest, other_chips=other_chips):
            undelivered.append(cid)
    return undelivered, dead


def _rules_undelivered_ids(entries, kit_dir=None, state=None, other_chips=None):
    """Активные hit=0 age>grace без доставок при наличии возможности."""
    undelivered, _dead = _rules_classify_hit0(
        kit_dir=kit_dir, state=state, other_chips=other_chips)
    return undelivered


def _rules_dead_ids(entries, kit_dir=None, state=None, other_chips=None):
    """Активные hit=0 age>grace с ≥1 card_injected (патология счётчика).

    hit — из counters/rules-hits.json (стейт); manifest.hit игнорируется.
    Age-grace: now − (born_at||created) > RULES_DEAD_AGE_HOURS.
    entries — совместимость API; доставки читаются из полного journal.
    """
    _undelivered, dead = _rules_classify_hit0(
        kit_dir=kit_dir, state=state, other_chips=other_chips)
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
# Горизонт «живого» resume-чипа; как SUBAGENT_RESUME_WINDOW_MIN в reground.py.
GENERAL_RESUME_LIVE_WINDOW_S = 1800
_RESUME_CLOSED_STATUSES = frozenset(("done", "cancelled", "rejected"))


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


def _wire_ts_unix_s(ts):
    """wire time/ts → unix-секунды; ms (>1e12) делим на 1000. None/битое → None."""
    if ts is None:
        return None
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if t > 1e12:
        t = t / 1000.0
    return t


def general_resume_chain(state=None):
    """Цепочки resume: агент с >1 distinct фронтом генерала в wire.

    Источник истины — agents/agent-*/wire.jsonl сессий движка (корни из
    find_engine_home / ORCH_AGENTS_ROOT / state/agents). Недоступно/пусто → [].
    Элемент: «agent-N:[F-A,F-B] ts=first..last». Fail-open.
    В красный чип только «активная» цепочка: незакрытый фронт в fronts.json
    или последний промт генерала моложе GENERAL_RESUME_LIVE_WINDOW_S.
    """
    try:
        if state is None:
            state = find_state_dir()
        status_by_id = {}
        for fr in (_load_fronts_at(state).get("fronts") or []):
            if isinstance(fr, dict):
                fid = fr.get("id")
                if isinstance(fid, str) and fid:
                    status_by_id[fid] = fr.get("status")
        now = time.time()
        out = []
        for agent, wire in _iter_agent_wire_paths(state):
            fronts, ts_first, ts_last = _scan_agent_general_fronts(wire)
            if len(fronts) <= 1:
                continue
            # (а) хотя бы один фронт цепочки в fronts.json не закрыт
            active_front = any(
                fid in status_by_id
                and status_by_id[fid] not in _RESUME_CLOSED_STATUSES
                for fid in fronts
            )
            # (б) последний промт генерала свежее окна
            last_s = _wire_ts_unix_s(ts_last)
            live = (
                last_s is not None
                and (now - last_s) <= GENERAL_RESUME_LIVE_WINDOW_S
            )
            if not active_front and not live:
                continue  # история — не красный чип
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


def _run_ts_from_journal_entries(entries, run_id, starts_by_id=None):
    """ts прогона: start этого id, иначе end; нет записи → None."""
    if not run_id:
        return None
    if starts_by_id:
        st = starts_by_id.get(run_id)
        if st and st.get("ts") is not None:
            return st.get("ts")
    start_ts = None
    end_ts = None
    for e in entries or []:
        if e.get("id") != run_id:
            continue
        ts = e.get("ts")
        if ts is None:
            continue
        kind = e.get("kind")
        if kind == "start":
            start_ts = ts
        elif kind == "end":
            end_ts = ts
    if start_ts is not None:
        return start_ts
    return end_ts


def _apply_legacy_waivers(chips, entries, kit_dir, starts_by_id=None):
    """Вычеркнуть pre-canon пары из красных списков; видимость → chips['legacy'].

    Реестр kit_dir/tests/adversarial/legacy-waivers.json; нет файла → no-op.
    Не трогает chip_silenced (не гашение детектора).
    """
    if not isinstance(chips, dict):
        return chips
    if "legacy" not in chips or not isinstance(chips.get("legacy"), list):
        chips["legacy"] = []
    if not kit_dir:
        return chips
    path = os.path.join(kit_dir, "tests", "adversarial", "legacy-waivers.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            reg = json.load(f)
    except FileNotFoundError:
        return chips
    except Exception:
        return chips
    if not isinstance(reg, dict):
        return chips
    try:
        canon_f = float(reg.get("canon_ts_epoch"))
    except (TypeError, ValueError):
        return chips
    waivers = reg.get("waivers") or []
    if not isinstance(waivers, list):
        return chips
    skip_chips = frozenset(("chip_silenced", "legacy"))
    for w in waivers:
        if not isinstance(w, dict):
            continue
        if w.get("reason") != "pre-canon-v1":
            continue
        rid = w.get("run_id")
        chip = w.get("chip")
        if not isinstance(rid, str) or not rid:
            continue
        if not isinstance(chip, str) or not chip:
            continue
        if chip in skip_chips:
            continue
        red = chips.get(chip)
        if not isinstance(red, list) or rid not in red:
            continue
        rts = _run_ts_from_journal_entries(
            entries, rid, starts_by_id=starts_by_id)
        if rts is None:
            continue
        try:
            rts_f = float(rts)
        except (TypeError, ValueError):
            continue
        if rts_f >= canon_f:
            continue
        chips[chip] = [x for x in red if x != rid]
        token = "%s:%s" % (rid, chip)
        if token not in chips["legacy"]:
            chips["legacy"].append(token)
    return chips


# --- F-C5 K1: живой надзор (чип supervision_dead, Инвариант 1) -------------

SUPERVISION_DEAD_CHIP = "supervision_dead"
# Смерть СО end: итоговый exit надзорного рана (end пишется один раз после
# retry — «без успешного retry» выполняется автоматически).
SUPERVISION_DEATH_EXITS = frozenset(("1", "3", "4", "124", "125"))
# Нестарт: ранние отказы обёртки до Popen (пара start+end писалась только
# для 5–9,12,13; для надзора 2/3/10/11/12 теперь тоже пара + чип).
SUPERVISION_NONSTART_EXITS = frozenset(("2", "3", "10", "11", "12"))
# «нет pid + свежий start ≤60 с» = жив (семантика _writer_still_alive).
SUPERVISION_GRACE_S = 60.0


def _supervision_role_class(role):
    """Класс надзора prosecutor|observer|None (снятие — только свой класс)."""
    r = normalize_journal_role(role)
    if not isinstance(r, str) or not r:
        return None
    if r == "meta/front-prosecutor.md" or r.endswith("meta/front-prosecutor.md"):
        return "prosecutor"
    if r == "meta/front-observer.md" or r.endswith("meta/front-observer.md"):
        return "observer"
    return None


def _proc_starttime_field(pid):
    """starttime из /proc/<pid>/stat (поле 22); нет /proc → None."""
    try:
        with open("/proc/%d/stat" % int(pid), "r", encoding="utf-8") as f:
            data = f.read()
        rparen = data.rfind(")")
        if rparen < 0:
            return None
        fields = data[rparen + 2:].split()
        if len(fields) < 20:
            return None
        return fields[19]
    except Exception:
        return None


def _run_pid_alive_at(pid_path):
    """Живость по pid-файлу: pid жив ∧ starttime совпадает (зеркало run-exec)."""
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            return False
        pid = int(lines[0])
        recorded = lines[1] if len(lines) > 1 else None
    except Exception:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as e:
        errno = getattr(e, "errno", None)
        if errno == 1:
            return True
        return False
    if recorded is None:
        return True
    current = _proc_starttime_field(pid)
    if current is None:
        return True  # нет /proc — деградация до pid-only
    return str(current) == str(recorded)


def _supervision_session_root(state, session):
    """sessions/<sid> с той же кодировкой, что session_dir (без mkdir).

    percent-кодирование _safe_encode + усечение SESSION_ID_MAX + алиас
    sessions/<orig>: экзотичные/длинные session id не должны давать скану
    ложный runtime_no_pid по несуществующему пути.
    """
    orig = str(session or "default")
    raw = _safe_encode(orig) or "default"
    base = os.path.join(state, "sessions")
    for cand in (os.path.join(base, raw),
                 os.path.join(base, raw[:SESSION_ID_MAX]),
                 os.path.join(base, orig)):
        if os.path.isdir(cand):
            return cand
    return os.path.join(base, raw[:SESSION_ID_MAX])


def _supervision_run_paths(state, run_id, session=None):
    """(log, pid) рана: state/cursor-run-<id>.* или sessions/<sid>/runs/<id>/."""
    if session:
        run_dir = os.path.join(_supervision_session_root(state, session),
                               "runs", run_id)
        return (os.path.join(run_dir, "run.log"),
                os.path.join(run_dir, "run.pid"))
    return (os.path.join(state, "cursor-run-%s.log" % run_id),
            os.path.join(state, "cursor-run-%s.pid" % run_id))


def _log_last_marker_tail(log_path, tail_bytes=65536):
    """Последний маркер EXIT=/RETRY= в хвосте лога (без чтения всего файла)."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - tail_bytes))
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return None
    marker = None
    for line in data.splitlines():
        s = line.strip()
        if s.startswith("EXIT=") or s.startswith("RETRY="):
            marker = s
    return marker


def supervision_silence_window(state, run_id, session=None, log_path=None,
                               now=None):
    """Окно тишины «смерть ещё обрабатывается»: True, пока обработки ≤60 с.

    Маркер RETRY=/EXIT= — не бессрочная индульгенция: глушит смерть только
    пока свеж (mtime лога ≤60 с — retry/end в полёте); замороженный лог
    (гибель обёртки в retry-фазе или в зазоре EXIT=→journal_end) по
    истечении окна даёт чип. Свежий TOMBSTONE (--kill в обработке, watcher
    вот-вот допишет end) — тот же класс тишины.
    """
    now = time.time() if now is None else float(now)
    if log_path:
        marker = _log_last_marker_tail(log_path)
        if marker is not None and marker.startswith(("RETRY=", "EXIT=")):
            try:
                if (now - os.path.getmtime(log_path)) < SUPERVISION_GRACE_S:
                    return True
            except OSError:
                pass
    # TOMBSTONE: state-level cursor-run-<id>.TOMBSTONE и session runs/<id>/
    run_dir = os.path.dirname(
        _supervision_run_paths(state, run_id, session)[0])
    for ts_path in (
            os.path.join(run_dir, "TOMBSTONE"),
            os.path.join(state, "cursor-run-%s.TOMBSTONE" % run_id)):
        try:
            if (now - os.path.getmtime(ts_path)) < SUPERVISION_GRACE_S:
                return True
        except OSError:
            continue
    return False


def _supervision_event_class(cause):
    """Класс события чипа: nonstart (нестарт) | death (смерть рана)."""
    return "nonstart" if str(cause or "").startswith("nonstart") else "death"


def _supervision_walk(entries):
    """Инкарнации ранов по хронологии journal: [(start, end|None)].

    start кладётся в очередь своего id; end снимает ПОСЛЕДНЮЮ открытую
    инкарнацию того же id (дубль-id не гасит чужую смерть — матч инкарнаций
    по хронологии пары, не «последняя start»). killed не снимает: после
    --kill смерть без end — тоже сигнал (runtime-ветвь/скан).
    """
    open_by_id = {}
    order = []
    for e in entries:
        rid = e.get("id")
        if not isinstance(rid, str) or not rid:
            continue
        kind = e.get("kind")
        if kind == "start":
            node = [e, None]
            open_by_id.setdefault(rid, []).append(node)
            order.append(node)
        elif kind == "end":
            q = open_by_id.get(rid)
            if q:
                q[-1][1] = e
                q.pop()
    return order


def _supervision_ok_ends(entries):
    """[(start_ts, class)] успешных надзоров: end 0 у инкарнации надзора."""
    out = []
    for st, en in _supervision_walk(entries):
        if en is None or str(en.get("exit")) != "0":
            continue
        cls = _supervision_role_class(st.get("role"))
        if cls is None:
            continue
        try:
            out.append((float(st.get("ts") or 0), cls))
        except (TypeError, ValueError):
            continue
    return out


def supervision_dead_events(state=None, now=None, entries=None):
    """Предикт событий смерти/нестарта надзора (полный журнал, не окно 5000).

    Дизъюнкция приказа (а): (start∖end ∧ мёртвый pid) ∪ (end ∈ death-set
    {1,3,4,124,125}) ∪ (нестарт-пара 2/3/10/11/12); только role_is_oversight,
    runtime-ветвь — engine==local. Инкарнации по _supervision_walk: дубль-id
    (нестарт-пара exit 11) не гасит смерть живой инкарнации того же id;
    runtime — только новейшая открытая инкарнация id (pid-файл один на id).
    Ложные окна — тишина: живой pid, СВЕЖИЙ (≤60 с) RETRY/EXIT-маркер или
    TOMBSTONE (замороженный маркер — чип), «нет pid + свежий start ≤60 с».
    Возвращает [{"id","front","role","ts","cause"}] — по одному на ран.
    """
    try:
        if state is None:
            state = find_state_dir()
        now = time.time() if now is None else float(now)
        if entries is None:
            entries = _journal_entries_at(state)
        out = []
        open_latest = {}  # id → (start, role) — новейшая открытая инкарнация
        for st, en in _supervision_walk(entries):
            rid = st.get("id")
            role = normalize_journal_role(st.get("role"))
            if en is not None:
                if not role_is_oversight(role):
                    continue
                exit_s = str(en.get("exit"))
                if exit_s in SUPERVISION_NONSTART_EXITS:
                    cause = "nonstart_exit_%s" % exit_s
                elif exit_s in SUPERVISION_DEATH_EXITS:
                    cause = "death_exit_%s" % exit_s
                else:
                    continue  # end 0 / легитимные гейт-отказы 5–9,13 — тишина
                try:
                    ev_ts = float(en.get("ts") or 0)
                except (TypeError, ValueError):
                    ev_ts = 0.0
                out.append({"id": rid, "front": st.get("front"),
                            "role": role, "ts": ev_ts, "cause": cause})
                continue
            if role_is_oversight(role):
                open_latest[rid] = (st, role)
        for rid, (st, role) in open_latest.items():
            if (st.get("engine") or "local") != "local":
                continue
            session = st.get("session")
            log_path, pid_path = _supervision_run_paths(state, rid, session)
            if os.path.isfile(pid_path):
                if _run_pid_alive_at(pid_path):
                    continue
                if supervision_silence_window(
                        state, rid, session=session, log_path=log_path,
                        now=now):
                    continue
                out.append({"id": rid, "front": st.get("front"),
                            "role": role, "ts": now,
                            "cause": "runtime_pid_dead"})
                continue
            try:
                start_ts = float(st.get("ts") or 0)
            except (TypeError, ValueError):
                start_ts = 0.0
            if (now - start_ts) < SUPERVISION_GRACE_S:
                continue  # нет pid + свежий start ≤60 с = жив
            if supervision_silence_window(
                    state, rid, session=session, log_path=log_path, now=now):
                continue
            out.append({"id": rid, "front": st.get("front"), "role": role,
                        "ts": now, "cause": "runtime_no_pid"})
        return out
    except Exception:
        return []


def _supervision_chip_outstanding(entries, front, run_id, cause):
    """True, если есть НЕпогашенный чип с тем же (name, front, id, класс).

    Погашение — успешный end 0 надзора ТОГО ЖЕ класса со start новее чипа
    (зеркало снятия в health): погашенный чип не глушит новую смерть того
    же id (снятый чип может писаться заново).
    """
    want_cls = _supervision_event_class(cause)
    ok_ends = _supervision_ok_ends(entries)
    outstanding = False
    for e in entries:
        if (e.get("kind") != "chip"
                or e.get("name") != SUPERVISION_DEAD_CHIP
                or e.get("id") != run_id):
            continue
        if (e.get("front") or None) != (front or None):
            continue
        if _supervision_event_class(e.get("cause")) != want_cls:
            continue
        try:
            cts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            cts = 0.0
        cls = _supervision_role_class(e.get("role"))
        cleared = cls is not None and any(
            ocls == cls and sts > cts for sts, ocls in ok_ends)
        if not cleared:
            outstanding = True
    return outstanding


def emit_supervision_dead_chip(front, run_id, cause=None, role=None,
                               state=None):
    """journal-чип supervision_dead с ДЕДУПОМ писателей (один чип на событие).

    Дедуп-ключ — (name, front, id) + класс события (nonstart|death); чип
    погашен успешным end 0 того же класса новее — тогда не глушит новую
    запись. Проверка и запись — под одним flock journal.jsonl (атомарность
    гонки сторож/скан; деградация без fcntl — как journal_append).
    Возвращает True, если чип записан.
    """
    if not run_id:
        return False
    if state is None:
        state = find_state_dir()
    path = os.path.join(state, "journal.jsonl")
    entry = {
        "ts": time.time(),
        "kind": "chip",
        "name": SUPERVISION_DEAD_CHIP,
        "id": run_id,
    }
    if front:
        entry["front"] = front
    if cause:
        entry["cause"] = cause
    if role:
        entry["role"] = normalize_journal_role(role)
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    f = None
    locked = False
    try:
        try:
            os.makedirs(state, exist_ok=True)
        except Exception:
            pass
        f = open(path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            locked = True
        f.seek(0)
        entries = []
        for raw in f.read().splitlines():
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
        if _supervision_chip_outstanding(entries, front, run_id, cause):
            return False
        f.seek(0, os.SEEK_END)
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
        return True
    except Exception:
        return False
    finally:
        if f is not None:
            if locked:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                f.close()
            except Exception:
                pass


def supervision_dead_scan(state=None):
    """Скан-писатель чипа supervision_dead (второй писатель после сторожа).

    Предикт по ПОЛНОМУ журналу (_journal_entries_at, не окно
    HEALTH_JOURNAL_SCAN_LIMIT) и запись journal-чипа при обнаружении
    (дедуп в emit); покрывает гибель сторожа (ребут/OOM/сбой спавна).
    Возвращает список обнаруженных событий.
    """
    try:
        if state is None:
            state = find_state_dir()
        events = supervision_dead_events(state=state)
        for ev in events:
            emit_supervision_dead_chip(
                ev.get("front"), ev.get("id"), cause=ev.get("cause"),
                role=ev.get("role"), state=state)
        return events
    except Exception:
        return []


def supervision_dead_ids(state=None):
    """id для health-чипа supervision_dead: journal-чипы ∪ предикт − снятые.

    Снятие — ТОЛЬКО health-семантика (close-гейт K3 чип не гасит):
    успешный end 0 надзора ТОГО ЖЕ класса (prosecutor↔prosecutor,
    observer↔observer) со start новее смерти.
    """
    try:
        if state is None:
            state = find_state_dir()
        entries = _journal_entries_at(state)
        dead_ts = {}
        dead_cls = {}
        order = []
        for e in entries:
            if (e.get("kind") != "chip"
                    or e.get("name") != SUPERVISION_DEAD_CHIP):
                continue
            rid = e.get("id") or e.get("front")
            if not rid:
                continue
            try:
                cts = float(e.get("ts") or 0)
            except (TypeError, ValueError):
                cts = 0.0
            if rid not in dead_ts:
                order.append(rid)
                dead_ts[rid] = cts
                dead_cls[rid] = _supervision_role_class(e.get("role"))
            elif cts > dead_ts[rid]:
                # несколько чипов id (нестарт + смерть / переписан после
                # погашения) — снятие сверяем с НОВЕЙШИМ
                dead_ts[rid] = cts
                dead_cls[rid] = _supervision_role_class(e.get("role"))
        for ev in supervision_dead_events(state=state, entries=entries):
            rid = ev.get("id")
            if not rid or rid in dead_ts:
                continue
            try:
                dead_ts[rid] = float(ev.get("ts") or 0)
            except (TypeError, ValueError):
                dead_ts[rid] = 0.0
            dead_cls[rid] = _supervision_role_class(ev.get("role"))
            order.append(rid)
        ok_ends = _supervision_ok_ends(entries)
        out = []
        for rid in order:
            cls = dead_cls.get(rid)
            dts = dead_ts.get(rid) or 0.0
            cleared = False
            if cls is not None:
                for sts, ocls in ok_ends:
                    if ocls == cls and sts > dts:
                        cleared = True
                        break
            if not cleared:
                out.append(rid)
        return out
    except Exception:
        return []


# --- F-C5 K2: мёртвые инварианты приказа (чип invariants_not_run, И2) ------

INVARIANTS_NOT_RUN_CHIP = "invariants_not_run"
# Заголовок машинной секции инвариантов приказа фронта («## Инварианты
# (машиночитаемые…»); прочие «## Инварианты» — проза, вне скопа детектора.
_INVARIANTS_SECTION_RE = re.compile(r"^#{1,6}\s*Инварианты\s*\(машиночитаемые")
# Строгий формат строки секции: «- Инвариант N: <cmd> → <оракул>»; N —
# каноническое целое без ведущих нулей; cmd хешируется байт-в-байт (без
# нормализации пробелов) и может содержать «→» — рез по ПОСЛЕДНЕЙ стрелке
# строки (оракул стрелки не содержит).
_INVARIANT_ROW_RE = re.compile(r"^- Инвариант ([1-9]\d*): (.+) → (.+)$")
# tool-only генератор квитанций §3 (рукописная квитанция не гасит)
_RECEIPT_TOOL_GENERATOR = "orch-probe-receipt/"


def front_order_path(fid, state=None):
    """Путь приказа фронта: <state>/fronts/<safe_id>/order.md (паттерн front_compass_path)."""
    if state is None:
        state = find_state_dir()
    return os.path.join(state, "fronts", safe_name(fid), "order.md")


def parse_front_invariants(text):
    """Строгий парсер машинной секции инвариантов → (invariants, parse_error).

    Скоуп — ТОЛЬКО секция «## Инварианты (машиночитаемые…» до следующего
    «##»-заголовка или EOF; «#»-строки внутри секции — комментарии
    (пропуск, не терминатор). Колонельские приказы сюда не попадают в
    принципе (парсится только fronts/<fid>/order.md). Нет секции →
    (None, None): инвариантов нет, чип не краснеет; проза вне секции —
    тишина. Вторая машинная секция (затенение первой) → parse-отказ
    parse:duplicate_section. Внутри секции каждая строка-список обязана
    совпадать с «- Инвариант N: <cmd> → <оракул>» (N без ведущих нулей;
    cmd байт-в-байт, стрелка режет по последней), номера — ровно 1..K без
    пропусков/дублей. Отклонение формата/нумерации → parse_error с номером
    строки (не fail-open); заголовок без строк формата → parse_error
    no_rows. invariants = [{"num", "cmd", "oracle"}].
    """
    if not text or not isinstance(text, str):
        return None, None
    lines = text.splitlines()
    heads = [i for i, ln in enumerate(lines)
             if _INVARIANTS_SECTION_RE.match(ln)]
    if not heads:
        return None, None
    if len(heads) > 1:
        return None, "parse:duplicate_section"
    out = []
    for j in range(heads[0] + 1, len(lines)):
        ln = lines[j]
        if ln.startswith("##"):
            break  # следующий ##-заголовок — конец секции
        s = ln.strip()
        if not s or not s.startswith("-"):
            continue  # пустые строки, #-комментарии и проза — не формат
        m = _INVARIANT_ROW_RE.match(ln)
        if not m:
            return None, "parse:line=%d:format" % (j + 1)
        num = int(m.group(1))
        if num != len(out) + 1:
            return None, "parse:line=%d:numbering" % (j + 1)
        out.append({"num": num, "cmd": m.group(2), "oracle": m.group(3)})
    if not out:
        return None, "parse:no_rows"
    return out, None


def _read_order_text(path):
    """Чтение приказа с errors=replace (K1-паттерн устойчивого читателя).

    Битые байты не глушат парсер в тишину: мусор доходит до
    parse_front_invariants и даёт parse-причину (или расходящийся sha).
    Нет файла → "" (инвариантов нет).
    """
    try:
        with open(path, "r", encoding="utf-8-sig", errors="replace") as f:
            return f.read()
    except Exception:
        return ""


def front_invariants_state(fid, state=None):
    """(invariants, parse_error) машинной секции приказа фронта fid."""
    if state is None:
        state = find_state_dir()
    return parse_front_invariants(
        _read_order_text(front_order_path(fid, state=state)))


def _iter_probe_receipt_paths(state):
    """Все probe-receipt.md полигона: runs/*/ и sessions/*/runs/*/."""
    out = []
    try:
        root = os.path.join(state, "runs")
        if os.path.isdir(root):
            for name in sorted(os.listdir(root)):
                p = os.path.join(root, name, "probe-receipt.md")
                if os.path.isfile(p):
                    out.append(p)
    except Exception:
        pass
    try:
        sess = os.path.join(state, "sessions")
        if os.path.isdir(sess):
            for sid in sorted(os.listdir(sess)):
                runs = os.path.join(sess, sid, "runs")
                if not os.path.isdir(runs):
                    continue
                for rid in sorted(os.listdir(runs)):
                    p = os.path.join(runs, rid, "probe-receipt.md")
                    if os.path.isfile(p):
                        out.append(p)
    except Exception:
        pass
    return out


def _invariant_receipt_fronts(state=None, entries=None):
    """cmd_sha256 → множество front_id валидных tool-only квитанций §3.

    Резолв фронта — journal-ран квитанции (start.front; --probe пишет
    start с front) по ПОЛНОМУ журналу (_journal_entries_at, не окно
    HEALTH_JOURNAL_SCAN_LIMIT). Гасит только generator=orch-probe-receipt/*
    (tool-only канон §3: рукописная квитанция с подсчитанным sha не гасит).
    """
    if state is None:
        state = find_state_dir()
    if entries is None:
        entries = _journal_entries_at(state)
    starts_by_id = _journal_start_index(entries)
    covered = {}
    for path in _iter_probe_receipt_paths(state):
        rid = os.path.basename(os.path.dirname(path))
        st = starts_by_id.get(rid)
        front = st.get("front") if st else None
        if not isinstance(front, str) or not front:
            continue  # ран без front не резолвится — не гасит ничей инвариант
        text = _read_text_silent(path)
        if not text:
            continue
        ok, _reason = parse_probe_receipt(
            text, artifact_path=wave_probe_artifact_path(rid, state=state),
            run_id=rid)
        if not ok:
            continue
        for rec in _parse_receipt_records(text):
            gen = str(rec.get("generator") or "").strip()
            if not gen.startswith(_RECEIPT_TOOL_GENERATOR):
                continue
            sha = str(rec.get("cmd_sha256") or "").strip().lower()
            if sha:
                covered.setdefault(sha, set()).add(front)
    return covered


def invariants_not_run(state=None, front_ids=None):
    """ids чипа invariants_not_run: непогашенные инварианты приказов фронтов.

    id = «<fid>:<N>» для каждого инварианта N без валидной квитанции §3
    (cmd_sha256 == sha256(cmd) байт-в-байт + front-резолв по полному
    журналу + generator tool-only); при parse-отказе машинной секции —
    «<fid>:parse:<причина>» (с номером строки). Скоуп по умолчанию —
    active-фронты (как probes_missing); front_ids — синтетический список
    для K3 (один fid независимо от статуса фронта). Нет секции → тишина.
    Тихие ошибки → [].
    """
    try:
        if state is None:
            state = find_state_dir()
        if front_ids is None:
            front_ids = [
                fr.get("id")
                for fr in (_load_fronts_at(state).get("fronts") or [])
                if isinstance(fr, dict)
                and fr.get("status") == "active"
                and isinstance(fr.get("id"), str) and fr.get("id")
            ]
        else:
            front_ids = [f for f in front_ids if isinstance(f, str) and f]
        if not front_ids:
            return []
        covered = _invariant_receipt_fronts(state=state)
        out = []
        for fid in front_ids:
            invariants, parse_error = front_invariants_state(fid, state=state)
            if parse_error:
                out.append("%s:%s" % (fid, parse_error))
                continue
            if not invariants:
                continue  # нет секции — инвариантов нет, тишина
            for inv in invariants:
                sha = hashlib.sha256(inv["cmd"].encode("utf-8")).hexdigest()
                if fid not in covered.get(sha, ()):
                    out.append("%s:%d" % (fid, inv["num"]))
        return out
    except Exception:
        return []


# --- F-C5 K3: front-close гейт (отказ done при красных чипах волн, И3) -----

# Пост-чип закрытия с красными (писатель — orchlib: health-скан/сохранение).
FRONT_CLOSED_RED_CHIP = "front_closed_red"
# Journal-маркер чистого закрытия (гейт пропустил *→done с пустыми
# блокерами): граница «события» пост-чипа — чип front_closed_red погашен
# чистым пере-закрытием НОВЕЕ него (дедуп «один чип на событие», K3 FU).
FRONT_CLOSED_CLEAN_NOTE = "front_closed_clean"
# ts корабля гейта (grandfathering): блокеры — только события ПОСЛЕ cutoff;
# 25 существующих done-фронтов легаси (события до cutoff) — тишина.
# Тесты передают cutoff параметром close_blockers(cutoff_ts=…), константу
# не патчат.
CLOSE_GATE_SHIP_TS = 1791065778.0
# Решение командующего (план v5, раздел (в)): allowlist v1 = РОВНО 8;
# commit_no_verify исключён (шумный текст-матчер — нет пути разбора).
CLOSE_GATE_ALLOWLIST = (
    "probes_missing",
    "chip_silenced",
    "invariants_not_run",
    "supervision_dead",
    "multi_write_front",
    "fronts_no_prosecutor",
    "waves_no_critic",
    "code_waves_no_gitwarden",
)
# Источник (i): journal kind=chip с front==fid (элементы этих классов
# живут в самом журнале; supervision_dead НЕ гасится позднейшим
# возрождением — асимметрия с health, решение круга 1).
_CLOSE_GATE_JOURNAL_CHIP_CLASSES = frozenset((
    "supervision_dead", "multi_write_front"))
# Кэш close_blockers: (state, cutoff, fids[, kit_dir]) → (mtime-ключ,
# {fid: блокеры}). Ключ = stat(mtime_ns, size) journal+fronts+order.md
# каждого fid + kit-файлы (panel/index.html — label chip_silenced;
# tests/adversarial/legacy-waivers.json — прецедент _health_mtime_key
# панели); любая запись инвалидирует.
_CLOSE_BLOCKERS_CACHE = {}


def _meta_end_ts(x):
    """ts end-записи из ends_with_meta-элемента (0.0 при мусоре)."""
    try:
        return float(x["entry"].get("ts") or 0)
    except (TypeError, ValueError, KeyError):
        return 0.0


def _front_scope_chips(data, entries, front_ids=None):
    """fid-классы красных чипов для скоупа (K3: ядро с параметром скоупа).

    front_ids=None — health-режим: все фронты, прежние условия статусов
    (fronts_no_prosecutor/waves_no_critic — только active;
    code_waves_no_gitwarden — любой статус, как в health до K3).
    front_ids=[fid…] — close-режим: синтетический скоуп, active-фильтр НЕ
    применяется (после done computed-блокеры не пустеют — иначе vim-обход
    опустошал бы пост-чип).

    Возвращает (chips, anchors): chips = {класс: [fid]}, anchors[fid] =
    ts события нарушения (grandfathering-якорь close-гейта).
    """
    close_mode = front_ids is not None
    scope = set(
        f for f in front_ids if isinstance(f, str) and f) if close_mode else None
    starts_by_id = _journal_start_index(entries)
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
    chips = {
        "fronts_no_prosecutor": [],
        "waves_no_critic": [],
        "code_waves_no_gitwarden": [],
    }
    anchors = {}
    for fr in data.get("fronts") or []:
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not isinstance(fid, str) or not fid:
            continue
        if close_mode and fid not in scope:
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
        idle = not [rid for rid, alive in started.items() if alive]

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
        if wave_ends and not pros_ends and (close_mode or status == "active"):
            chips["fronts_no_prosecutor"].append(fid)
            anchors[fid] = max(anchors.get(fid, 0.0),
                               max(_meta_end_ts(x) for x in wave_ends))

        if idle and wave_ends:
            # Критик того же front (role fact-checker/code-reviewer).
            # Сравниваем start_ts исполнителя с end_ts последнего критика:
            # длинный colonel, стартовавший до критиков, не ложный плюс.
            if close_mode or status == "active":
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
                    chips["waves_no_critic"].append(fid)
                    anchors[fid] = max(
                        anchors.get(fid, 0.0),
                        max(_meta_end_ts(x) for x in after_critic))

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
                chips["code_waves_no_gitwarden"].append(fid)
                anchors[fid] = max(anchors.get(fid, 0.0),
                                   max(_meta_end_ts(x) for x in coder_after))
    return chips, anchors


def _close_gate_stat(path):
    """(mtime_ns, size) файла; нет файла → (0, -1) (появление меняет ключ)."""
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return (0, -1)


def _close_gate_key(state, pf, fids=(), kit_dir=None):
    """Ключ кэша/сверки: stat journal+fronts (+order.md fid) + kit-файлы.

    kit в ключе — прецедент _health_mtime_key: chip_silenced зависит от
    label panel/index.html кита, waivers — от legacy-waivers.json; смена
    кита без изменения journal/fronts обязана инвалидировать кэш.
    """
    if kit_dir is None:
        kit_dir = KIT_DIR
    key = _close_gate_stat(os.path.join(state, "journal.jsonl"))
    key += _close_gate_stat(pf)
    for fid in fids:
        key += _close_gate_stat(front_order_path(fid, state=state))
    key += _close_gate_stat(os.path.join(kit_dir, "panel", "index.html"))
    key += _close_gate_stat(
        os.path.join(kit_dir, "tests", "adversarial", "legacy-waivers.json"))
    return key


def _run_event_ts(entries, rid, starts_by_id=None):
    """ts события волны rid: последний end («волна закончилась»), иначе start."""
    if not rid:
        return None
    start_ts = None
    end_ts = None
    if starts_by_id:
        st = starts_by_id.get(rid)
        if st is not None and st.get("ts") is not None:
            start_ts = st.get("ts")
    for e in entries or []:
        if e.get("id") != rid:
            continue
        kind = e.get("kind")
        ts = e.get("ts")
        if ts is None:
            continue
        if kind == "start" and start_ts is None:
            start_ts = ts
        elif kind == "end":
            end_ts = ts
    v = end_ts if end_ts is not None else start_ts
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _front_activity_ts(entries, fid):
    """Новый ts journal-записи с front==fid (якорь invariants-блокеров).

    Бухгалтерия самого гейта НЕ якорит (иначе самозапирание): все
    kind=note (вкл. маркер front_closed_clean) и чипы front_closed_red
    исключены — легаси-инвариант не становится красным от факта закрытия.
    Записи с нечисловым ts пропускаются (не якорят ничего — край
    fail-closed отдельно: целиком нечитаемый журнал → close_scan_error).
    """
    out = 0.0
    for e in entries or []:
        if e.get("front") != fid:
            continue
        kind = e.get("kind")
        if kind == "note":
            continue
        if (kind == "chip"
                and e.get("name") == FRONT_CLOSED_RED_CHIP):
            continue
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts > out:
            out = ts
    return out


def _close_blockers_core(fids, state, kit_dir, cutoff_ts):
    """Блокеры закрытия для списка fids одним проходом (общие чтения).

    Два источника (решение командующего, план v5 (в)):
    (i) journal kind=chip с front==fid: supervision_dead, multi_write_front
        (полный журнал _journal_entries_at, не окно 5000; supervision_dead
        НЕ гасится позднейшим возрождением — асимметрия с health);
    (ii) computed-детекторы со скоупом fids (независимо от статуса фронта):
        (ii-1) run_id-классы (probes_missing/chip_silenced/invariants_not_run)
        — элемент резолвится на start.front==fid по ПОЛНОМУ журналу
        (scan_limit=False: волна за окном 5000 тоже блокирует);
        (ii-2) fid-классы (fronts_no_prosecutor/waves_no_critic/
        code_waves_no_gitwarden) — прямое сравнение, БЕЗ резолва.
    Grandfathering: блокеры только из событий с ts > cutoff (записи с
    нечисловым ts не якорят — пропуск); legacy-waivers уважаются
    (_apply_legacy_waivers). FAIL-CLOSED: исключение скана = блокер
    close_scan_error (отказ закрытия), целиком нечитаемый журнал — тоже.
    Возвращает {fid: ["класс:элемент", …]} в порядке CLOSE_GATE_ALLOWLIST.
    """
    try:
        if state is None:
            state = find_state_dir()
        if kit_dir is None:
            kit_dir = KIT_DIR
        cutoff = _close_gate_cutoff(cutoff_ts)
        fids = [f for f in fids if isinstance(f, str) and f]
        if not fids:
            return {}
        fid_set = set(fids)
        entries = _journal_entries_at(state)
        if not entries:
            # край fail-closed (нит 6): файл непуст, но ни одной валидной
            # записи — «не читается» ≠ «чисто»; пустой/отсутствующий файл —
            # штатная тишина (новый state)
            jpath = os.path.join(state, "journal.jsonl")
            try:
                with open(jpath, "rb") as f:
                    jraw = f.read()
            except FileNotFoundError:
                jraw = b""
            # прочие OSError (EACCES/EIO/каталог) не глушатся: пусть
            # поднимутся до fail-closed-ветки close_scan_error (KR1)
            if jraw.strip():
                raise RuntimeError(
                    "journal_unparseable:%d bytes" % len(jraw))
        starts_by_id = _journal_start_index(entries)
        data = _load_fronts_at(state)
        per_fid = {fid: {name: [] for name in CLOSE_GATE_ALLOWLIST}
                   for fid in fids}

        def _add(fid, name, el):
            red = per_fid[fid][name]
            if el not in red:
                red.append(el)

        # (i) journal kind=chip, front==fid
        for e in entries:
            if e.get("kind") != "chip":
                continue
            name = e.get("name")
            if name not in _CLOSE_GATE_JOURNAL_CHIP_CLASSES:
                continue
            fid = e.get("front")
            if fid not in fid_set:
                continue
            try:
                cts = float(e.get("ts") or 0)
            except (TypeError, ValueError):
                cts = 0.0
            if not cts > cutoff:
                continue
            if name == "multi_write_front":
                el = e.get("front") or e.get("id") or e.get("run_id") or name
            else:
                el = e.get("id") or fid
            _add(fid, name, el)

        # (ii-1) run_id-классы: скоуп-параметр детекторов + резолв rid→front.
        # ПОЛНЫЙ журнал (scan_limit=False, не окно HEALTH_JOURNAL_SCAN_LIMIT):
        # волна, вытесненная из окна 5000, обязана блокировать close и
        # краснеть пост-чипом (блокер ревью ×3)
        for name, els in (
            ("probes_missing",
             probes_missing(state=state, front_ids=list(fids),
                            scan_limit=False)),
            ("chip_silenced",
             chip_silenced_ids(state=state, kit_dir=kit_dir,
                               front_ids=list(fids), scan_limit=False)),
        ):
            for el in els:
                st = starts_by_id.get(el)
                fid = st.get("front") if st else None
                if fid not in fid_set:
                    continue
                ts = _run_event_ts(entries, el, starts_by_id=starts_by_id)
                if ts is None or not ts > cutoff:
                    continue
                _add(fid, name, el)

        # invariants_not_run: элементы «<fid>:<N>» / «<fid>:parse:…» —
        # якорь = последняя активность фронта в журнале
        for el in invariants_not_run(state=state, front_ids=list(fids)):
            fid = el.split(":", 1)[0]
            if fid not in fid_set:
                continue
            if _front_activity_ts(entries, fid) > cutoff:
                _add(fid, "invariants_not_run", el)

        # (ii-2) fid-классы: прямое сравнение, БЕЗ резолва run_id
        scope_chips, anchors = _front_scope_chips(
            data, entries, front_ids=list(fids))
        for name in ("fronts_no_prosecutor", "waves_no_critic",
                     "code_waves_no_gitwarden"):
            for fid in scope_chips.get(name) or []:
                if fid not in fid_set:
                    continue
                if (anchors.get(fid) or 0.0) > cutoff:
                    _add(fid, name, fid)

        out = {}
        for fid in fids:
            chips = per_fid[fid]
            _apply_legacy_waivers(
                chips, entries, kit_dir, starts_by_id=starts_by_id)
            out[fid] = [
                "%s:%s" % (name, el)
                for name in CLOSE_GATE_ALLOWLIST
                for el in chips.get(name) or []
            ]
        return out
    except Exception as e:
        # fail-closed (консервативный полный путь): ошибка скана = «блокеры
        # неизвестны» → блокер close_scan_error → отказ закрытия с причиной
        # (гейт/CLI); пост-чип и health-зеркало при этом событие НЕ
        # утверждают (чип не пишется — см. front_closed_red_scan)
        reason = "close_scan_error:%s" % str(e)[:160]
        return {fid: [reason] for fid in fids}


def _close_scan_error_of(blockers):
    """Строка close_scan_error:… в списке блокеров или None (fail-closed)."""
    for b in blockers or []:
        if isinstance(b, str) and b.startswith("close_scan_error"):
            return b
    return None


def _close_gate_cutoff(cutoff_ts=None):
    """cutoff close-гейта: явный параметр, иначе константа корабля."""
    try:
        if cutoff_ts is None:
            return float(CLOSE_GATE_SHIP_TS)
        return float(cutoff_ts)
    except (TypeError, ValueError):
        return float(CLOSE_GATE_SHIP_TS)


def close_blockers(fid, state=None, kit_dir=None, cutoff_ts=None,
                   use_cache=True):
    """Блокеры закрытия фронта fid — список «класс:элемент» (allowlist 8).

    Полный журнал; скоуп-независимое вычисление (active-фильтр не
    применяется); grandfathering по CLOSE_GATE_SHIP_TS (тесты — параметром
    cutoff_ts). Кэш по mtime journal+fronts+order.md(fid)+kit-файлов
    (panel/index.html — label chip_silenced; legacy-waivers.json).
    FAIL-CLOSED: ошибка/нечитаемость скана — блокер close_scan_error
    (отказ закрытия); отсутствие файла журнала — легитимная тишина
    нового state.
    """
    if not isinstance(fid, str) or not fid:
        return []
    if state is None:
        state = find_state_dir()
    ck = None
    key = None
    if use_cache:
        pf = os.path.join(state, "fronts.json")
        ck = (state, _close_gate_cutoff(cutoff_ts), fid, kit_dir)
        cached = _CLOSE_BLOCKERS_CACHE.get(ck)
        key = _close_gate_key(state, pf, (fid,), kit_dir)
        if cached is not None and cached[0] == key:
            return list(cached[1][fid])
    out = _close_blockers_core((fid,), state, kit_dir, cutoff_ts)
    blockers = list(out.get(fid) or [])
    if use_cache and ck is not None:
        _CLOSE_BLOCKERS_CACHE[ck] = (key, {fid: blockers})
        while len(_CLOSE_BLOCKERS_CACHE) > 256:
            _CLOSE_BLOCKERS_CACHE.pop(next(iter(_CLOSE_BLOCKERS_CACHE)))
    return blockers


def _close_gate_preflight(f, pf, state=None, kit_dir=None, cutoff_ts=None):
    """Кандидаты *→done и их блокеры ДО захвата fronts.json.lock (K3).

    Тяжёлый скан — здесь, ВНЕ замка (lock-гигиена: никакого тяжёлого под
    замком). Возвращает (ключ, {fid: блокеры}, [fid…]) или (None, {}, [])
    при отсутствии переходов в done.
    """
    if state is None:
        state = os.path.dirname(os.path.abspath(pf))
    prev = {}
    try:
        with open(pf, "r", encoding="utf-8-sig") as fh:
            cur = json.load(fh)
        if isinstance(cur, dict):
            for fr in cur.get("fronts") or []:
                if isinstance(fr, dict) and fr.get("id"):
                    prev[fr["id"]] = fr.get("status")
    except Exception:
        pass
    fronts = f.get("fronts") if isinstance(f, dict) else None
    candidates = []
    if isinstance(fronts, list):
        for fr in fronts:
            if not isinstance(fr, dict):
                continue
            fid = fr.get("id")
            if not fid or fr.get("status") != "done":
                continue
            if prev.get(fid) == "done":
                continue
            candidates.append(fid)
    if not candidates:
        return None, {}, []
    blockers = _close_blockers_core(
        candidates, state, kit_dir, cutoff_ts)
    blockers = {fid: list(blockers.get(fid) or []) for fid in candidates}
    key = _close_gate_key(state, pf, candidates, kit_dir)
    return key, blockers, candidates


def _gate_close_before_persist(out, raw_status_by_id, preflight_blockers):
    """ДО persist: переход *→done при непустых close_blockers → ValueError.

    Прецедент _gate_activations_before_persist (отказ с перечнем). Блокеры
    посчитаны ДО замка (preflight); пересчёта ПОД замком нет (нит ревью):
    fid без preflight-записи возвращается в unknown — вызывающий отпускает
    замок и пересчитывает вне замка; после исчерпания ретраев — отказ
    (fail-closed). Возвращает (unknown_fids, clean_close_fids): чистые
    переходы — для journal-маркера front_closed_clean (граница «события»
    пост-чипа: чип front_closed_red погашен чистым пере-закрытием новее).
    """
    fronts = out.get("fronts") if isinstance(out, dict) else []
    if not isinstance(fronts, list):
        return [], []
    unknown = []
    clean = []
    for fr in fronts:
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not fid or fr.get("status") != "done":
            continue
        if raw_status_by_id.get(fid) == "done":
            continue
        blockers = preflight_blockers.get(fid)
        if blockers is None:
            unknown.append(fid)
            continue
        if blockers:
            raise ValueError([
                "close-гейт: фронт %s не закрывается при красных чипах "
                "волн: %s" % (fid, ", ".join(blockers))])
        clean.append(fid)
    return unknown, clean


def _front_closed_red_chip_outstanding(entries, fid):
    """ts последнего НЕпогашенного чипа front_closed_red (front==fid) или None.

    Дедуп «один чип на СОБЫТИЕ», не «на фронт» (нит ревью, K1-паттерн
    ключа события): чип погашен чистым пере-закрытием НОВЕЕ него —
    journal-маркер kind=note note=front_closed_clean с front==fid и
    ts > ts чипа. Погашенный чип не глушит новое красное закрытие.
    """
    last_chip_ts = None
    for e in entries:
        if (e.get("kind") != "chip"
                or e.get("name") != FRONT_CLOSED_RED_CHIP
                or e.get("front") != fid):
            continue
        try:
            cts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            cts = 0.0
        if last_chip_ts is None or cts > last_chip_ts:
            last_chip_ts = cts
    if last_chip_ts is None:
        return None
    for e in entries:
        if (e.get("kind") != "note"
                or e.get("note") != FRONT_CLOSED_CLEAN_NOTE
                or e.get("front") != fid):
            continue
        try:
            nts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if nts > last_chip_ts:
            return None  # погашен чистым пере-закрытием новее чипа
    return last_chip_ts


def emit_front_closed_red_chip(fid, state=None):
    """journal-чип front_closed_red с ДЕДУПОМ писателей (паттерн K1 emit).

    Вызывается ТОЛЬКО для done-фронта с непустыми блокерами: непогашенный
    чип (без чистого пере-закрытия новее — см.
    _front_closed_red_chip_outstanding) подавляет запись — «один чип на
    СОБЫТИЕ»; погашенный — нет (второе красное закрытие пишет НОВЫЙ чип).
    Проверка и запись — под одним flock journal.jsonl; чтение журнала
    устойчиво к битым байтам (errors=replace).
    Возвращает True, если чип записан.
    """
    if not fid:
        return False
    if state is None:
        state = find_state_dir()
    path = os.path.join(state, "journal.jsonl")
    entry = {
        "ts": time.time(),
        "kind": "chip",
        "name": FRONT_CLOSED_RED_CHIP,
        "front": fid,
    }
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    f = None
    locked = False
    try:
        try:
            os.makedirs(state, exist_ok=True)
        except Exception:
            pass
        f = open(path, "a+", encoding="utf-8", errors="replace")
        if fcntl is not None:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            locked = True
        f.seek(0)
        entries = []
        for raw in f.read().splitlines():
            s = raw.strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if isinstance(obj, dict):
                entries.append(obj)
        if _front_closed_red_chip_outstanding(entries, fid) is not None:
            return False
        f.seek(0, os.SEEK_END)
        f.write(line)
        f.flush()
        os.fsync(f.fileno())
        return True
    except Exception:
        return False
    finally:
        if f is not None:
            if locked:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
            try:
                f.close()
            except Exception:
                pass


def front_closed_red_scan(state=None, kit_dir=None, cutoff_ts=None):
    """Скан-писатель чипа front_closed_red (писатель — orchlib, K3).

    Для каждого done-фронта с непустыми close_blockers — journal-чип
    (дедуп в emit). Вызывается из health_red_chips и после persist в
    save_fronts: прямая правка fronts.json (vim) мимо save_fronts краснеет
    при первом же скане/сохранении (TOCTOU-гонки мимо гейта — тоже).
    Блокеры считаются одним проходом по всем done-фронтам (кэш mtime).
    Возвращает [(fid, blockers)] по факту обнаружения.
    """
    if state is None:
        state = find_state_dir()
    data = _load_fronts_at(state)
    done_fids = []
    for fr in data.get("fronts") or []:
        if not isinstance(fr, dict) or fr.get("status") != "done":
            continue
        fid = fr.get("id")
        if isinstance(fid, str) and fid:
            done_fids.append(fid)
    if not done_fids:
        return []
    pf = os.path.join(state, "fronts.json")
    cutoff = _close_gate_cutoff(cutoff_ts)
    ck = (state, cutoff, tuple(done_fids), kit_dir)
    key = _close_gate_key(state, pf, done_fids, kit_dir)
    cached = _CLOSE_BLOCKERS_CACHE.get(ck)
    if cached is not None and cached[0] == key:
        blockers_by_fid = cached[1]
    else:
        blockers_by_fid = _close_blockers_core(
            done_fids, state, kit_dir, cutoff_ts)
        blockers_by_fid = {fid: list(blockers_by_fid.get(fid) or [])
                           for fid in done_fids}
        _CLOSE_BLOCKERS_CACHE[ck] = (key, blockers_by_fid)
        while len(_CLOSE_BLOCKERS_CACHE) > 256:
            _CLOSE_BLOCKERS_CACHE.pop(next(iter(_CLOSE_BLOCKERS_CACHE)))
    found = []
    for fid in done_fids:
        blockers = blockers_by_fid.get(fid) or []
        if not blockers:
            continue
        if _close_scan_error_of(blockers) is not None:
            # fail-closed — ошибка скана: событие НЕ подтверждено, чип не
            # пишется и не утверждается зеркалом; отказ даёт сам гейт
            # (close_scan_error в блокерах) и CLI
            continue
        found.append((fid, list(blockers)))
        emit_front_closed_red_chip(fid, state=state)
    return found


def front_closed_red_ids(state=None, kit_dir=None, cutoff_ts=None):
    """ids для health-чипа front_closed_red: чип ∩ «событие живо».

    Событие погашено, только если фронт СЕЙЧАС done И close_blockers пусты
    (пере-закрыт чистым); ре-открытие чип НЕ гасит — живёт до исправления.
    Тихие ошибки → [].
    """
    try:
        if state is None:
            state = find_state_dir()
        entries = _journal_entries_at(state)
        fids = []
        for e in entries:
            if (e.get("kind") == "chip"
                    and e.get("name") == FRONT_CLOSED_RED_CHIP
                    and isinstance(e.get("front"), str) and e.get("front")
                    and e.get("front") not in fids):
                fids.append(e.get("front"))
        if not fids:
            return []
        data = _load_fronts_at(state)
        status = {}
        for fr in data.get("fronts") or []:
            if isinstance(fr, dict) and fr.get("id"):
                status[fr["id"]] = fr.get("status")
        out = []
        for fid in fids:
            if _front_closed_red_chip_outstanding(entries, fid) is None:
                continue  # погашен чистым пере-закрытием новее чипа
            if (status.get(fid) == "done"
                    and not close_blockers(
                        fid, state=state, kit_dir=kit_dir,
                        cutoff_ts=cutoff_ts)):
                continue  # сейчас done и чисто — событие погашено
            out.append(fid)
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

    kit_dirty_outside_wave: tracked porcelain по маске wave_no_docs+README.md
    при отсутствии открытого journal start код-волны (роли KIT_DIRTY_OUTSIDE_WAVE_ROLES).
    writable+front чужой роли не гасит. Не mtime;
    не путать с commander_hands_active. Закрытый git-warden end не гасит.

    rules_no_retro / rules_dead / manifest_category_oversize — база rules/
    в kit_dir (хук для /tmp-синтетики).

    general_resume_chain: красный по agents-wire (>1 фронт генерала на агента).
    general_resume_chain_warn: WARN-журнал-эвристика только если wire недоступен.

    front_closed_red (F-C5 K3): пост-чип закрытия с красными — писатель
    скан orchlib (journal-чип, дедуп), health-зеркало гаснет только при
    пере-закрытии чистым. fid-классы (fronts_no_prosecutor/waves_no_critic/
    code_waves_no_gitwarden) — ядро _front_scope_chips (K3, параметр скоупа;
    здесь health-режим без изменения поведения).
    """
    empty = {
        "runs_no_front": [],
        "orders_without_basis": [],
        "orders_suspect": [],
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
        "rules_undelivered": [],
        "probes_missing": [],
        "chip_silenced": [],
        "general_resume_chain": [],
        "general_resume_chain_warn": [],
        # MW2-A: journal kind=chip (входы пишет MW2-B)
        "multi_write_front": [],
        "commit_no_verify": [],
        # ADDITIVE MARKER: F-C5 K1 живой надзор (supervision_dead)
        "supervision_dead": [],
        # ADDITIVE MARKER: F-C5 K2 мёртвые инварианты приказа (invariants_not_run)
        "invariants_not_run": [],
        # ADDITIVE MARKER: F-C5 K3 front-close гейт (front_closed_red)
        "front_closed_red": [],
        # ADDITIVE MARKER: RCPT-A receipt_handmade
        "receipt_handmade": [],
        # ADDITIVE MARKER: F-MUSTMAP MM-C2 chips
        "handoff_oversize": [],
        "project_md_missing": [],
        "mustmap_stale": [],
        # ADDITIVE MARKER: F-ADVERSARIAL ADV-C1 order_no_mechanics
        "order_no_mechanics": [],
        # ADDITIVE MARKER: ADV-TC3 legacy waivers (visible, not chip_silenced)
        "legacy": [],
        # ADDITIVE MARKER: ADV-TC4 kit_dirty_outside_wave
        "kit_dirty_outside_wave": [],
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
        order_no_mechanics_ids = orders_without_mechanics(state)
        orders_suspect_ids = orders_suspect(state)
        data = _load_fronts_at(state)
        # F-C5 K3: fid-классы вынесены в ядро с параметром скоупа
        # (_front_scope_chips, health-режим: прежние условия статусов —
        # поведение активных детекторов/панели НЕ меняется)
        _fid_scope, _fid_anchors = _front_scope_chips(data, entries)
        fronts_no_prosecutor = _fid_scope["fronts_no_prosecutor"]
        waves_no_critic = _fid_scope["waves_no_critic"]
        code_waves_no_gitwarden = _fid_scope["code_waves_no_gitwarden"]
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

        try:
            kit_dirty_outside_wave = _kit_dirty_outside_wave(entries, kit_dir)
        except Exception:
            kit_dirty_outside_wave = []

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
                # same front ИЛИ scout без фронта (--no-front); чужой front — нет
                sc_front = sc.get("front")
                if sc_front is not None and sc_front != afront:
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
        # ADDITIVE MARKER: RCPT-A receipt_handmade
        try:
            handmade = receipt_handmade(state=state, scan_limit=scan_limit)
        except Exception:
            handmade = []

        # ADDITIVE MARKER: F-MUSTMAP MM-C2 chips
        try:
            handoff_over = handoff_oversize(state=state)
        except Exception:
            handoff_over = []
        try:
            project_missing = project_md_missing(state=state)
        except Exception:
            project_missing = []
        try:
            mustmap_stale_ids = mustmap_stale(kit_dir=kit_dir)
        except Exception:
            mustmap_stale_ids = []

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

        # MW2-A: journal kind="chip" name∈{multi_write_front, commit_no_verify}
        multi_write_front = []
        commit_no_verify = []
        for e in entries:
            if e.get("kind") != "chip":
                continue
            name = e.get("name")
            if name not in ("multi_write_front", "commit_no_verify"):
                continue
            token = e.get("front") or e.get("id") or e.get("run_id") or name
            if name == "multi_write_front":
                if token not in multi_write_front:
                    multi_write_front.append(token)
            else:
                if token not in commit_no_verify:
                    commit_no_verify.append(token)

        # F-C5 K1: скан-писатель supervision_dead (полный журнал, дедуп в
        # emit) + предикт health со снятием end 0 того же класса новее смерти
        try:
            supervision_dead_scan(state=state)
        except Exception:
            pass
        try:
            supervision_dead = supervision_dead_ids(state=state)
        except Exception:
            supervision_dead = []

        # F-C5 K2: computed-детектор непогашенных инвариантов приказа
        # (active-скоуп; квитанции резолвятся по ПОЛНОМУ журналу — внутри
        # детектора; journal-писатель для K2 не нужен)
        try:
            inv_not_run = invariants_not_run(state=state)
        except Exception:
            inv_not_run = []

        # F-C5 K3: скан-писатель front_closed_red (vim-обходы и TOCTOU-гонки
        # мимо гейта краснеют journal-чипом) + health-зеркало
        try:
            front_closed_red_scan(state=state, kit_dir=kit_dir)
        except Exception:
            pass
        try:
            front_closed_red = front_closed_red_ids(
                state=state, kit_dir=kit_dir)
        except Exception:
            front_closed_red = []

        # undelivered/dead после прочих чипов — (iii) chip-red без самозавода
        other_chips = {
            "runs_no_front": runs_no_front,
            "orders_without_basis": orders,
            "orders_suspect": orders_suspect_ids,
            "fronts_no_prosecutor": fronts_no_prosecutor,
            "waves_no_critic": waves_no_critic,
            "code_waves_no_gitwarden": code_waves_no_gitwarden,
            "budget_warn": budget_warn,
            "advisors_without_scouts": advisors_without_scouts,
            "commander_no_children": commander_no_children,
            "wave_no_docs": wave_no_docs,
            "rules_no_retro": rules_no_retro,
            "manifest_category_oversize": manifest_category_oversize,
            "lint_failures": lint_failures,
            "probes_missing": probes,
            "chip_silenced": silenced,
            "general_resume_chain": resume_chain,
            "general_resume_chain_warn": resume_warn,
            "multi_write_front": multi_write_front,
            "commit_no_verify": commit_no_verify,
            # ADDITIVE MARKER: RCPT-A receipt_handmade
            "receipt_handmade": handmade,
            # ADDITIVE MARKER: F-C5 K2 мёртвые инварианты (invariants_not_run)
            "invariants_not_run": inv_not_run,
            # ADDITIVE MARKER: F-MUSTMAP MM-C2 chips
            "handoff_oversize": handoff_over,
            "project_md_missing": project_missing,
            "mustmap_stale": mustmap_stale_ids,
            # ADDITIVE MARKER: F-ADVERSARIAL ADV-C1 order_no_mechanics
            "order_no_mechanics": order_no_mechanics_ids,
            # ADDITIVE MARKER: ADV-TC4 kit_dirty_outside_wave
            "kit_dirty_outside_wave": kit_dirty_outside_wave,
        }
        _prev_no_mig2 = os.environ.get("ORCH_RULES_NO_MIGRATE")
        os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
        try:
            rules_undelivered, rules_dead = _rules_classify_hit0(
                kit_dir=kit_dir, state=state, other_chips=other_chips)
        except Exception:
            rules_undelivered, rules_dead = [], []
        finally:
            if _prev_no_mig2 is None:
                os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
            else:
                os.environ["ORCH_RULES_NO_MIGRATE"] = _prev_no_mig2

        chips = {
            "runs_no_front": runs_no_front,
            "orders_without_basis": orders,
            "orders_suspect": orders_suspect_ids,
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
            "rules_undelivered": rules_undelivered,
            "probes_missing": probes,
            "chip_silenced": silenced,
            "general_resume_chain": resume_chain,
            "general_resume_chain_warn": resume_warn,
            "multi_write_front": multi_write_front,
            "commit_no_verify": commit_no_verify,
            # ADDITIVE MARKER: F-C5 K1 живой надзор (supervision_dead)
            "supervision_dead": supervision_dead,
            # ADDITIVE MARKER: F-C5 K2 мёртвые инварианты (invariants_not_run)
            "invariants_not_run": inv_not_run,
            # ADDITIVE MARKER: F-C5 K3 front-close гейт (front_closed_red)
            "front_closed_red": front_closed_red,
            # ADDITIVE MARKER: RCPT-A receipt_handmade
            "receipt_handmade": handmade,
            # ADDITIVE MARKER: F-MUSTMAP MM-C2 chips
            "handoff_oversize": handoff_over,
            "project_md_missing": project_missing,
            "mustmap_stale": mustmap_stale_ids,
            # ADDITIVE MARKER: F-ADVERSARIAL ADV-C1 order_no_mechanics
            "order_no_mechanics": order_no_mechanics_ids,
            # ADDITIVE MARKER: ADV-TC3 legacy waivers (visible, not chip_silenced)
            "legacy": [],
            # ADDITIVE MARKER: ADV-TC4 kit_dirty_outside_wave
            "kit_dirty_outside_wave": kit_dirty_outside_wave,
        }
        _apply_legacy_waivers(
            chips, entries, kit_dir, starts_by_id=starts_by_id)
        return chips
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


def _orch_lint_cause_text_has_measure(text):
    """True если текст содержит «CAUSE-CLEARED:» + непустой замер."""
    for ln in (text or "").splitlines():
        if "CAUSE-CLEARED:" in ln:
            rest = ln.split("CAUSE-CLEARED:", 1)[1].strip()
            if rest:
                return True
    return False


def _orch_lint_cause_cleared_ok(kit_dir, sha, state=None):
    """True если у коммита есть доказательство CAUSE-CLEARED.

    Порядок: (1) артефакт прогона в state; (2) квитанция rules/causes/<run-id>.md;
    (3) инлайн «CAUSE-CLEARED: <замер>» в сообщении коммита.
    """
    msg = _orch_lint_commit_msg(kit_dir, sha) or ""
    # run-id: «CAUSE-CLEARED:<run-id>» + (1) state artifact | (2) kit receipt
    m = _ORCH_LINT_CAUSE_RUN_RE.search(msg)
    if m:
        run_id = m.group(1).strip()
        if run_id and (
                _orch_lint_cause_artifact_has_measure(run_id, state=state)
                or _orch_lint_cause_kit_receipt_has_measure(kit_dir, run_id)):
            return True
    # (3) Fallback: «CAUSE-CLEARED: <команда + результат>» (пробел после ':') в msg
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
        if _orch_lint_cause_text_has_measure(text):
            return True
    return False


def _orch_lint_cause_kit_receipt_has_measure(kit_dir, run_id):
    """rules/causes/<run-id>.md содержит «CAUSE-CLEARED:» + непустой замер."""
    if not kit_dir or not run_id:
        return False
    if "/" in run_id or "\\" in run_id or run_id in (".", ".."):
        return False
    path = os.path.join(kit_dir, "rules", "causes", "%s.md" % run_id)
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return False
    return _orch_lint_cause_text_has_measure(text)


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


# --- F-C5 K5: регистратор движковых спавн-ранов (spawn-*) -------------------
# Движковый спавн волны (мимо обёрток run-exec/run-cloud) обязан писать
# journal-пару start/end — иначе волна невидима детекторам и close-гейту.
# Запись лишь отражает существующий артефакт с вердиктом: ts = момент
# регистрации, никакого ретро-творчества (журнал = истина, приведение к
# факту ≠ подделка).

# Отказ гарда регистратора (usage-ошибка — 2, блокеры close — 1).
SPAWN_REFUSE_EXIT = 3

# «Вердикт: …» с md-декором и суффиксом (follow-up / микро-ремонта №3).
_SPAWN_VERDICT_INLINE_RE = re.compile(
    r"^[\s>#*]{0,8}Вердикт(?:\s+[^\n:]{1,40})?:\s*(\S.*?)(?:\*\*)?\s*$")
# Голый заголовок «## Вердикт» — вердикт = первая непустая строка ниже.
_SPAWN_VERDICT_BARE_RE = re.compile(r"^[\s>#*]{0,8}Вердикт\s*$")
# Итог-строка — запасной источник вердикта артефакта.
_SPAWN_ITOG_RE = re.compile(r"^[\s>#*]{0,8}Итог:\s*(\S.*?)\s*$")


def spawn_verdict_from_text(text):
    """Вердикт-строка артефакта («Вердикт: …» / итог-строки) или None.

    Источники (по убыванию приоритета): последнее «Вердикт …: …» (строгий
    «Вердикт: OK» — частный случай), первый непустой текст под последним
    голым заголовком «Вердикт», последнее «Итог: …». None = вердикт-строки
    нет — гард регистратора обязан отказать (никакого ретро-творчества).
    Результат — «Вердикт: <текст>» одной строкой, ≤ _VERDICT_MAX_LEN.
    """
    if not text or not isinstance(text, str):
        return None
    lines = text.splitlines()
    inline = None
    bare_idx = None
    itog = None
    for i, line in enumerate(lines):
        m = _SPAWN_VERDICT_INLINE_RE.match(line)
        if m and m.group(1).strip():
            # «**Вердикт: OK** — пояснение»: ядро вердикта — до первого
            # возобновления bold-разметки, не вклейка «OK**»
            core = m.group(1).strip().split("**")[0].strip()
            inline = core or m.group(1).strip()
            continue
        if _SPAWN_VERDICT_BARE_RE.match(line):
            bare_idx = i
            continue
        m2 = _SPAWN_ITOG_RE.match(line)
        if m2 and m2.group(1).strip():
            itog = m2.group(1).strip()
    if inline is not None:
        v = inline
    elif bare_idx is not None:
        v = None
        for line in lines[bare_idx + 1:]:
            s = line.strip().strip("*").strip()
            if s:
                v = s
                break
        if v is None:
            return None
    elif itog is not None:
        v = itog
    else:
        return None
    v = "Вердикт: %s" % re.sub(r"\s+", " ", v).strip()
    if len(v) > _VERDICT_MAX_LEN:
        v = v[:_VERDICT_MAX_LEN]
    return v


def _spawn_split_receipts(raw):
    """--receipts «id1,id2» → список непустых id без дублей."""
    if not raw:
        return []
    out = []
    for part in str(raw).replace(";", ",").split(","):
        p = part.strip()
        if p and p not in out:
            out.append(p)
    return out


def _spawn_receipts_valid(receipts, state=None):
    """True если список id квитанций §3 непуст и ВСЕ валидны (fail-closed)."""
    if not isinstance(receipts, list) or not receipts:
        return False
    for rid in receipts:
        if not isinstance(rid, str) or not rid:
            return False
        if not wave_has_valid_probe_receipt(rid, state=state):
            return False
    return True


def _spawn_locked_write(state, builder):
    """Критическая секция регистрации: flock journal → гарды по существующим
    записям → запись всех записей сразу (отказ — без единой строки).

    builder(starts_by_id, start_ids, end_ids, art_by_id) → (ok, reason,
    entries): журнальные гарды вызывающего (дедуп id, требуемый
    spawn-start, дедуп source_artifact). art_by_id — source_artifact → id
    уже зарегистрированных ранов (kind=end/backfill).
    """
    path = os.path.join(state, "journal.jsonl")
    try:
        os.makedirs(state, exist_ok=True)
        with open(path, "a+", encoding="utf-8", errors="replace") as f:
            locked = False
            if fcntl is not None:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                locked = True
            try:
                f.seek(0)
                raw = f.read()
                starts_by_id = {}
                start_ids = set()
                end_ids = set()
                art_by_id = {}
                for line in raw.splitlines():
                    s = line.strip()
                    if not s:
                        continue
                    try:
                        obj = json.loads(s)
                    except Exception:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("kind") == "start" and obj.get("id"):
                        starts_by_id[obj["id"]] = obj
                        start_ids.add(obj["id"])
                    elif obj.get("kind") == "end" and obj.get("id"):
                        end_ids.add(obj["id"])
                    if (obj.get("kind") in ("end", "backfill")
                            and obj.get("id")
                            and isinstance(obj.get("source_artifact"), str)):
                        art_by_id.setdefault(obj["source_artifact"], obj["id"])
                ok, reason, entries = builder(
                    starts_by_id, start_ids, end_ids, art_by_id)
                if not ok:
                    return False, reason
                for entry in entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
                f.flush()
                os.fsync(f.fileno())
                return True, "ok"
            finally:
                if locked:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except Exception as e:
        return False, "io_error:%s" % e


def spawn_register(mode, run_id, role=None, front=None, artifact=None,
                   receipts=None, reason=None, state=None):
    """Регистрация движкового спавн-рана в journal (K5; журнал = истина).

    mode="start" — kind=start (spawn:true); mode="finish" — kind=end по
    существующему spawn-start (verdict/source_artifact/artifact_mtime из
    артефакта-факта, читаемого ПОД замком — без TOCTOU-окна); 
    mode="backfill" — пара + сводка kind=backfill одним вызовом для уже
    завершённого рана (ts = момент регистрации, НЕ исторический);
    mode="abandon" — kind=end с verdict="ABANDONED: <reason>" для
    мёртвого рана без артефакта (честная пометка, НЕ успех: ран перестаёт
    висеть и снова виден idle-детекторам; код-рана без квитанций остаётся
    красной в probes_missing — fail-closed). Истина завершения — verdict
    (строка артефакта / ABANDONED), поле exit в spawn-записи отсутствует.
    Гарды: front обязателен (start/backfill); артефакт существует/непустой/
    с вердикт-строкой БЕЗ секретов; для код-ролей (coder/fix) — обязательные
    --receipts (все id — валидные квитанции §3, кроме abandon); дедуп id И
    source_artifact (realpath) под flock; секретов в argv/reason нет.
    Возвращает (ok, message); отказ — журнал не тронут.
    """
    if mode not in ("start", "finish", "backfill", "abandon"):
        return False, "bad_mode:%s" % mode
    if state is None:
        state = find_state_dir()
    run_id = run_id.strip() if isinstance(run_id, str) else ""
    if not run_id:
        return False, "id_required"
    front = front.strip() if isinstance(front, str) and front.strip() else None
    if mode in ("start", "backfill") and not front:
        return False, "front_required"
    reg_role = normalize_journal_role(role) if role else None
    if mode in ("start", "backfill") and not reg_role:
        return False, "role_required"
    reason = re.sub(r"\s+", " ", str(reason or "")).strip()
    if mode == "abandon" and not reason:
        return False, "reason_required"
    # секреты не в argv (единый сканер кита; reason — тоже argv)
    argv_blob = " ".join(x for x in (
        run_id, reg_role or "", front or "", artifact or "",
        ",".join(receipts or []), reason or "") if x)
    secret = scan_secrets(argv_blob)
    if secret:
        return False, "secrets_in_argv:%s" % secret

    receipts = [r for r in (receipts or []) if isinstance(r, str)]
    artifact = artifact if isinstance(artifact, str) and artifact else None
    if mode in ("finish", "backfill") and not artifact:
        return False, "artifact_required"

    # роль finish/abandon-рана — из существующего start (дочитываем до
    # замка; под замком сверяем, что start не изменился)
    if mode in ("finish", "abandon"):
        pre = _journal_entries_at(state)
        pre_starts = _journal_start_index(pre)
        st = pre_starts.get(run_id)
        if st is None:
            return False, "no_spawn_start:%s" % run_id
        if not st.get("spawn"):
            return False, "not_spawn_start:%s" % run_id
        reg_role = normalize_journal_role(st.get("role"))

    # квитанции §3: для код-ранов обязательны и валидны (закрывают рану
    # для probes_missing); некод-ранам не требуются, но данные — валидируем
    if mode in ("finish", "backfill") and reg_role:
        if _role_is_code_or_fix_wave(reg_role) and not receipts:
            return False, "receipts_required:%s" % reg_role
        bad = [r for r in receipts
               if not wave_has_valid_probe_receipt(r, state=state)]
        if bad:
            return False, "receipts_invalid:%s" % ",".join(bad)

    def _read_artifact_under_lock():
        """Артефакт-факт под замком: (verdict, mtime, realpath, None) или
        (None, None, None, reason-отказа).

        Чтение текста и mtime в одной критсекции с append в journal —
        verdict/mtime соответствуют моменту записи (KR3: TOCTOU-окна нет).
        Вердикт-строка проходит scan_secrets: секрет из артефакта не
        переезжает в журнал (KR1).
        """
        real = os.path.realpath(artifact)
        if not os.path.isfile(real):
            return None, None, None, "artifact_missing:%s" % real
        try:
            if os.path.getsize(real) <= 0:
                return None, None, None, "artifact_empty:%s" % real
            with open(real, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
            mtime = float(os.path.getmtime(real))
        except OSError as e:
            return None, None, None, "artifact_unreadable:%s" % e
        verdict = spawn_verdict_from_text(text)
        if not verdict:
            return None, None, None, "artifact_no_verdict:%s" % real
        secret = scan_secrets(verdict)
        if secret:
            return None, None, None, "secrets_in_verdict:%s" % secret
        return verdict, mtime, real, None

    def _builder(starts_by_id, start_ids, end_ids, art_by_id):
        if mode in ("finish", "abandon"):
            st = starts_by_id.get(run_id)
            if st is None:
                return False, "no_spawn_start:%s" % run_id, []
            if not st.get("spawn"):
                return False, "not_spawn_start:%s" % run_id, []
            if mode == "finish" and normalize_journal_role(
                    st.get("role")) != reg_role:
                return False, "start_role_changed:%s" % run_id, []
            if run_id in end_ids:
                return False, "duplicate_end:%s" % run_id, []
        else:
            if run_id in start_ids:
                return False, "duplicate_start:%s" % run_id, []
            if mode == "backfill" and run_id in end_ids:
                return False, "duplicate_end:%s" % run_id, []
        verdict = None
        mtime = None
        artifact_real = None
        if mode in ("finish", "backfill"):
            verdict, mtime, artifact_real, problem = (
                _read_artifact_under_lock())
            if problem:
                return False, problem, []
            # дедуп источника (realpath): один артефакт — один ран (симлинк
            # на тот же файл — тот же источник); id-дедуп остаётся выше
            other = art_by_id.get(artifact_real)
            if other is not None and other != run_id:
                return False, "duplicate_artifact:%s already %s" % (
                    artifact_real, other), []
        elif mode == "abandon":
            verdict = ("ABANDONED: %s" % reason).strip()
            if len(verdict) > _VERDICT_MAX_LEN:
                verdict = verdict[:_VERDICT_MAX_LEN]
        ts = time.time()
        entries = []
        if mode in ("start", "backfill"):
            entries.append({
                "ts": ts, "kind": "start", "id": run_id,
                "parent": resolve_journal_parent(run_id),
                "engine": "local", "front": front, "role": reg_role,
                "spawn": True,
            })
        if mode in ("finish", "backfill", "abandon"):
            # поле exit отсутствует: истина завершения — verdict (строка
            # артефакта / ABANDONED), исторический код рана неизвестен
            end_entry = {
                "ts": ts, "kind": "end", "id": run_id,
                "verdict": verdict, "gates": [], "spawn": True,
            }
            if mode in ("finish", "backfill"):
                end_entry["source_artifact"] = artifact_real
                end_entry["artifact_mtime"] = mtime
                if receipts:
                    end_entry["receipts"] = list(receipts)
            entries.append(end_entry)
        if mode == "backfill":
            entries.append({
                "ts": ts, "kind": "backfill", "id": run_id,
                "role": reg_role, "front": front,
                "source_artifact": artifact_real, "verdict": verdict,
                "artifact_mtime": mtime, "registered_ts": ts,
            })
        return True, "ok", entries

    return _spawn_locked_write(state, _builder)


# Допустимые флаги по режиму (строгость argv, KR3): неприменимый к режиму
# флаг — usage-отказ exit 2, а не молчаливое игнорирование.
_SPAWN_MODE_FLAGS = {
    "start": ("--id", "--role", "--front"),
    "finish": ("--id", "--artifact", "--receipts"),
    "backfill": ("--id", "--role", "--front", "--artifact", "--receipts"),
    "abandon": ("--id", "--reason"),
}


def _cli_spawn(argv):
    """spawn-start / spawn-finish / spawn-backfill / spawn-abandon (K5).

    exit 0 — записано; 2 — usage (в т.ч. неприменимый к режиму флаг);
    SPAWN_REFUSE_EXIT — гард (журнал не тронут). Повторная регистрация
    того же id/артефакта — отказ (дедуп под flock).
    """
    mode = argv[0][len("spawn-"):]
    allowed_flags = _SPAWN_MODE_FLAGS.get(mode, ())
    known = ("--id", "--role", "--front", "--artifact", "--receipts",
             "--reason")
    flags = {}
    i = 1
    while i < len(argv):
        a = argv[i]
        if a in known and i + 1 < len(argv):
            flags[a] = argv[i + 1]
            i += 2
            continue
        sys.stderr.write("spawn: неизвестный или неполный аргумент: %s\n" % a)
        return 2
    for flag in flags:
        if flag not in allowed_flags:
            sys.stderr.write(
                "spawn %s: флаг %s неприменим к режиму (допустимо: %s)\n"
                % (mode, flag, " ".join(allowed_flags)))
            return 2
    run_id = (flags.get("--id") or "").strip()
    if not run_id:
        sys.stderr.write("spawn %s: --id обязателен\n" % mode)
        return 2
    required = {
        "start": ("--role", "--front"),
        "finish": ("--artifact",),
        "backfill": ("--role", "--front", "--artifact"),
        "abandon": ("--reason",),
    }.get(mode, ())
    for flag in required:
        if not (flags.get(flag) or "").strip():
            sys.stderr.write("spawn %s: %s обязателен\n" % (mode, flag))
            return 2
    ok, msg = spawn_register(
        mode, run_id,
        role=flags.get("--role"),
        front=flags.get("--front"),
        artifact=flags.get("--artifact"),
        receipts=_spawn_split_receipts(flags.get("--receipts")),
        reason=flags.get("--reason"),
    )
    if not ok:
        sys.stderr.write("spawn %s refuse: %s\n" % (mode, msg))
        return SPAWN_REFUSE_EXIT
    sys.stdout.write("spawn %s ok: %s\n" % (mode, run_id))
    return 0


# --- F-C5 K3: CLI-точка --check-close (гейт И3; owns.py не трогаем) --------

def _cli_check_close(fid):
    """exit 0 — блокеров нет; ненулевой + перечень в stderr — есть."""
    blockers = close_blockers(fid)
    if blockers:
        sys.stderr.write(
            "close-гейт: фронт %s не закрывается при красных чипах волн:\n"
            % fid)
        for b in blockers:
            sys.stderr.write("  - %s\n" % b)
        return 1
    sys.stdout.write("close OK: блокеров у %s нет\n" % fid)
    return 0


def main(argv=None):
    """CLI: --check-close <front-id>; spawn-start/finish/backfill/abandon."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("spawn-start", "spawn-finish", "spawn-backfill",
                            "spawn-abandon"):
        return _cli_spawn(argv)
    if len(argv) == 2 and argv[0] == "--check-close" and argv[1].strip():
        return _cli_check_close(argv[1].strip())
    sys.stderr.write(
        "использование: orchlib.py --check-close <front-id> | "
        "orchlib.py spawn-start --id ID --role R --front F | "
        "orchlib.py spawn-finish --id ID --artifact FILE "
        "[--receipts ID[,ID…]] | "
        "orchlib.py spawn-backfill --id ID --role R --front F "
        "--artifact FILE [--receipts ID[,ID…]] | "
        "orchlib.py spawn-abandon --id ID --reason \"<факт>\"\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
