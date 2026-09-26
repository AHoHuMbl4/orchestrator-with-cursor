#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Облачный исполнитель: Cursor Cloud Agents API (https://api.cursor.com).

Сценарий «нулевая установка»: на компьютере НЕ нужен ни cursor-agent, ни что-либо
ещё — задачи уходят в облачные VM Cursor, результат (текст + ветка/PR + артефакты)
читается программно. Нужен только API-ключ (Cursor Dashboard → API Keys), paid-план.

Эндпоинты (public beta — сверять схему с
https://cursor.com/docs/cloud-agent/api/endpoints):
  POST /v1/agents                          — создать агента + первый run
  POST /v1/agents/{agentId}/runs           — follow-up в существующего агента
  GET  /v1/agents?limit=N                  — список агентов (id, name, status,
                                             createdAt, latestRunId) — list + recovery
  GET  /v1/agents/{agentId}/runs/{runId}   — статус/результат
  GET  /v1/agents/{agentId}/runs/{runId}/stream — SSE-прогресс
  GET  /v1/agents/{agentId}/artifacts      — артефакты (presigned download)

Тело create/follow-up: "prompt" — объект {"text": "<строка>"}, не голая строка
(см. Request Body → prompt.text в доках выше).

HTTP: --http-timeout (дефолт 300 с) на все вызовы call(). Create на сервере
часто ~61 с — таймаут 60 с у клиента убивал запрос за секунду до ответа, при
этом агент уже создавался. При URLError/timeout на POST /v1/agents клиент
делает GET /v1/agents?limit=20 и ищет свежего (createdAt ≤ ~5 мин) агента с
совпадающим name (если передавали) или текстом промта в name; при находке
восстанавливает agent.id / run.id (latestRunId), пишет в лог
«recovered after timeout: <id>» и продолжает как успех.

--wait: поллинг GET run до терминального status —
FINISHED / ERROR / CANCELLED / EXPIRED (не по подстрокам в сыром JSON).

Подкоманды:
  run       — создать агента (или follow-up) с промтом из файла
  status    — статус/результат run (опция --wait: поллить до готовности)
  list      — GET /v1/agents?limit=20; таблица id/name/status/latestRunId/createdAt
  artifacts — список артефактов агента

Файлы рядом с логом (--id нужен для имён; без --id → id=C1):
  <state>/cloud-<id>.log          — текстовый лог (и для list тоже; не list-<id>.log)
  <state>/cloud-<id>.result.json  — машиночитаемый итог run/status (обновляется
                                    при каждом poll): agent_id, run_id, status,
                                    updated, result (текст терминального ответа
                                    или null). Для синтеза/вердикт-пайплайна.
  <state>/agent-<id>.json         — {agent_id, run_id, created} после create/
                                    follow-up/recovery — follow-up без ре-парсинга лога.

Общие флаги (--id, --api-key, --http-timeout) работают ДО и ПОСЛЕ субкоманды:
  run-cloud.py --id w1 run --prompt-file P.md
  run-cloud.py run --id w1 --prompt-file P.md
  run-cloud.py list --api-key K
  run-cloud.py --id L1 list

Ключ (по приоритету): 1) --api-key; 2) env CURSOR_API_KEY; 3) файл
<state>/cursor.key (state — .orchestration, ищется от cwd вверх / ORCHESTRATION_DIR).
Схема beta: первый живой прогон калибрует парсинг id (ответ логируется целиком).
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

BASE = "https://api.cursor.com"
TERMINAL_RUN_STATUSES = frozenset(("FINISHED", "ERROR", "CANCELLED", "EXPIRED"))
RECOVERY_WINDOW_SEC = 300

# Паттерны секретов в промте — до HTTP create.
SECRET_PATTERNS = (
    re.compile(r"crsr_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"BEGIN [A-Z0-9 ]*PRIVATE KEY"),
    re.compile(r"ghp_[A-Za-z0-9]{30,}"),
)


def scan_secrets(text):
    """Первый совпавший паттерн (pattern.pattern) или None."""
    for rx in SECRET_PATTERNS:
        if rx.search(text or ""):
            return rx.pattern
    return None


def _append_gate_log(log_path, line):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def apply_launch_gates(prompt, prompt_file, front_id, log_path):
    """Гейты до HTTP create: секреты → 5; фронт closed → 6; hard-бюджет → 7.

    Бюджет: used>hard (hard>0) → BUDGET_HARD/exit 7; used>=warn →
    FRONT_BUDGET_WARN + pending_budget_warn, запуск продолжается. Иначе None.
    """
    hit = scan_secrets(prompt)
    if hit:
        _append_gate_log(log_path, "SECRETS_IN_PROMPT=%s, %s" % (hit, prompt_file))
        sys.stderr.write(
            "секрет в промте: вынеси в .orchestration/cursor.key / ENV; "
            "промт без секрета\n")
        return 5
    if not front_id:
        return None
    front_status = getattr(orchlib, "front_status", None)
    if callable(front_status):
        status = front_status(front_id)
        if status in ("cancelled", "rejected"):
            _append_gate_log(log_path, "FRONT_CLOSED=%s" % front_id)
            return 6
    bump = getattr(orchlib, "bump_front_runs", None)
    if callable(bump):
        used, warn, hard = bump(front_id)
        _append_gate_log(log_path, "FRONT_RUNS=%s %s warn=%s hard=%s" % (
            front_id, used, warn, hard))
        if hard > 0 and used > hard:
            _append_gate_log(log_path, "BUDGET_HARD=%s %s/%s" % (
                front_id, used, hard))
            return 7
        if used >= warn:
            _append_gate_log(log_path, "FRONT_BUDGET_WARN=%s %s/%s" % (
                front_id, used, warn))
            emit = getattr(orchlib, "emit_pending_budget_warn", None)
            if callable(emit):
                emit(front_id, used, warn)
    return None


def _add_common_args(parser):
    """Общие флаги на main и на субпарсерах (default=SUPPRESS → оба порядка)."""
    parser.add_argument("--api-key", default=argparse.SUPPRESS)
    parser.add_argument("--id", default=argparse.SUPPRESS)
    parser.add_argument("--front", default=argparse.SUPPRESS,
                        help="id фронта: статус cancelled/rejected; бюджет warn/hard "
                             "(warn=датчик, hard=стоп при hard>0)")
    parser.add_argument("--no-front", default=argparse.SUPPRESS, metavar="REASON",
                        help="запуск вне фронта с причиной (journal no_front_reason)")
    parser.add_argument("--role", default=argparse.SUPPRESS,
                        help="роль (приоритет над шапкой); иначе «роль: path.md» в первых 3 строках промта")
    parser.add_argument("--http-timeout", type=float, default=argparse.SUPPRESS,
                        help="HTTP socket timeout для всех API-вызовов, сек (дефолт 300)")


def build_parser():
    ap = argparse.ArgumentParser(description=__doc__)
    _add_common_args(ap)
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("run", help="создать агента / follow-up")
    _add_common_args(p)
    p.add_argument("--prompt-file", default=None)
    p.add_argument("--agent-id", default=None, help="follow-up в существующего агента")
    p.add_argument("--body-file", default=None,
                   help="json с доп. полями запроса (repo/config — по докам beta)")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--poll", type=int, default=15)

    p = sub.add_parser("status", help="статус/результат run")
    _add_common_args(p)
    p.add_argument("--agent-id", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--wait", action="store_true")
    p.add_argument("--timeout", type=int, default=1800)
    p.add_argument("--poll", type=int, default=15)

    p = sub.add_parser("list", help="список агентов (свежие первыми)")
    _add_common_args(p)

    p = sub.add_parser("artifacts", help="список артефактов агента")
    _add_common_args(p)
    p.add_argument("--agent-id", required=True)

    return ap


def normalize_args(a):
    """Дозаполнить общие флаги, если пришли только с одной стороны парсера."""
    if not hasattr(a, "api_key"):
        a.api_key = None
    if not hasattr(a, "id"):
        a.id = "C1"
    if not hasattr(a, "front"):
        a.front = None
    if not hasattr(a, "no_front"):
        a.no_front = None
    if not hasattr(a, "role"):
        a.role = None
    if not hasattr(a, "http_timeout"):
        a.http_timeout = 300.0
    return a


def journal_start(run_id, prompt_file, front, role, engine="cloud",
                  no_front_reason=None, auto=False):
    """Запись kind=start в journal (ошибки глотает orchlib)."""
    if not run_id:
        return
    parent = orchlib.resolve_journal_parent(run_id)
    role = orchlib.normalize_journal_role(role)
    entry = {
        "ts": time.time(),
        "kind": "start",
        "id": run_id,
        "parent": parent,
        "engine": engine,
        "prompt_file": os.path.abspath(prompt_file) if prompt_file else None,
        "front": front,
        "role": role,
    }
    if no_front_reason:
        entry["no_front_reason"] = no_front_reason
    if auto or os.environ.get("ORCH_RUN_AUTO") == "1":
        entry["auto"] = True
    orchlib.journal_append(entry)


def journal_end(run_id, log_path, exit_code):
    """Запись kind=end в journal (ошибки глотает orchlib)."""
    if not run_id:
        return
    verdict, gates = orchlib.journal_log_meta(log_path)
    orchlib.journal_append({
        "ts": time.time(),
        "kind": "end",
        "id": run_id,
        "exit": exit_code,
        "verdict": verdict,
        "gates": gates,
    })


def journal_gate_refuse(run_id, prompt_file, front, role, log_path, exit_code,
                        engine="cloud", no_front_reason=None):
    """start+end при отказе гейта (exit 5/6/7/8) — без дыры в journal."""
    journal_start(run_id, prompt_file, front, role, engine=engine,
                  no_front_reason=no_front_reason)
    journal_end(run_id, log_path, exit_code)


def api_key(a):
    key = a.api_key or os.environ.get("CURSOR_API_KEY")
    kf = os.path.abspath(os.path.join(orchlib.find_state_dir(), "cursor.key"))
    if not key:
        if os.path.exists(kf):
            with open(kf, "r", encoding="utf-8") as f:
                key = f.read().strip()
    if not key:
        sys.stderr.write("нужен --api-key, env CURSOR_API_KEY или файл %s "
                         "(вносится в панели; state-каталог ищется от cwd вверх)\n" % kf)
        sys.exit(2)
    return key


def http_error_message(body):
    """Краткое сообщение из тела 4xx/5xx (строка или JSON)."""
    body = body or ""
    try:
        j = json.loads(body)
    except ValueError:
        return body.strip() or "(пустое тело)"
    if isinstance(j, dict):
        for k in ("message", "error", "detail", "msg"):
            v = j.get(k)
            if isinstance(v, str) and v.strip():
                return v
            if isinstance(v, dict):
                return json.dumps(v, ensure_ascii=False)
        return json.dumps(j, ensure_ascii=False)
    return str(j)


def report_http_error(out):
    """Печать краткой диагностики HTTP-ошибки в stderr. True если это ошибка."""
    if not isinstance(out, dict) or "_http_error" not in out:
        return False
    code = out["_http_error"]
    msg = http_error_message(out.get("_body", ""))
    sys.stderr.write("HTTP %s: %s\n" % (code, msg))
    return True


def call(method, path, key, body=None, stream=False, timeout=300.0):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        if stream:
            return resp  # итерируем SSE-строки
        raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except ValueError:
            return {"_raw": raw}
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode("utf-8", "replace")}
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"_network_error": str(e)}


def utc_stamp():
    """UTC-штамп с суффиксом Z."""
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())


def logf(state, a):
    return open(os.path.join(state, "cloud-%s.log" % a.id), "a",
                encoding="utf-8", buffering=1)


def log_write(lf, text):
    lf.write(text)
    lf.flush()


def _log_trailing_incomplete(log_path):
    """Хвост файла после последнего \\n, либо None если файл пуст/кончается на \\n."""
    try:
        with open(log_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        if data.endswith(b"\n"):
            return None
        idx = data.rfind(b"\n")
        chunk = data if idx < 0 else data[idx + 1:]
        return chunk.decode("utf-8", "replace")
    except Exception:
        return None


def check_compass_overflow(log_path, session, noted, allow_log_write=True):
    """Скан compass: COMPASS_OVERFLOW= + pending-флаг. Ошибки глотаем.

    noted — set (path, size) уже отмеченных в этом прогоне; мутируется.
    allow_log_write=False: скан overflows + emit_pending; в лог не пишем;
      noted не трогаем (параллельная запись poll-строк в lf).
    allow_log_write=True: финальная запись недостающих маркеров + pending
      при записи. В present — только полные строки (с \\n).
    """
    try:
        p = orchlib.load_params()
        overflows = orchlib.compass_overflows(p)
        if not allow_log_write:
            if overflows:
                orchlib.emit_pending_compass_guard(session, overflows)
            return
        present = set()
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    # обрывок без \\n — не считать совпадением маркера
                    if not line.endswith("\n"):
                        continue
                    s = line.rstrip("\n").rstrip("\r").strip()
                    if s.startswith("COMPASS_OVERFLOW="):
                        present.add(s)
        except Exception:
            pass
        trailing = _log_trailing_incomplete(log_path)
        wrote = False
        for o in overflows:
            marker = "COMPASS_OVERFLOW=%s:%s/%s\n" % (
                o["path"], o["size"], o["limit"])
            marker_s = marker.rstrip("\n")
            key = (o.get("path"), o.get("size"))
            if key in noted:
                continue
            if marker_s in present:
                noted.add(key)
                continue
            noted.add(key)
            present.add(marker_s)
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    if trailing is not None and trailing.strip() == marker_s:
                        f.write("\n")
                        trailing = None
                    else:
                        if trailing is not None:
                            f.write("\n")
                            trailing = None
                        f.write(marker)
            except Exception:
                pass
            wrote = True
        if wrote:
            orchlib.emit_pending_compass_guard(session, overflows)
    except Exception:
        pass


def result_json_path(state, run_id_label):
    """Путь <state>/cloud-<id>.result.json (run_id_label = a.id из --id)."""
    return os.path.join(state, "cloud-%s.result.json" % run_id_label)


def agent_json_path(state, run_id_label):
    """Путь <state>/agent-<id>.json."""
    return os.path.join(state, "agent-%s.json" % run_id_label)


def _extract_result_text(status_obj):
    """Текст ответа из тела GET run (поле result / text / output), иначе None."""
    if not isinstance(status_obj, dict):
        return None
    for key in ("result", "text", "output"):
        v = status_obj.get(key)
        if isinstance(v, str) and v.strip():
            return v
    return None


def write_result_json(state, a, agent_id, run_id, status_obj=None):
    """Записать/обновить cloud-<id>.result.json рядом с логом."""
    status = None
    result = None
    if isinstance(status_obj, dict):
        st = status_obj.get("status")
        if isinstance(st, str):
            status = st
        if run_status_terminal(status_obj):
            result = _extract_result_text(status_obj)
    payload = {
        "agent_id": agent_id,
        "run_id": run_id,
        "status": status,
        "updated": utc_stamp(),
        "result": result,
    }
    path = result_json_path(state, a.id)
    os.makedirs(state, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def write_agent_json(state, a, agent_id, run_id):
    """Записать <state>/agent-<id>.json для follow-up без ре-парсинга лога."""
    payload = {
        "agent_id": agent_id,
        "run_id": run_id,
        "created": utc_stamp(),
    }
    path = agent_json_path(state, a.id)
    os.makedirs(state, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return path


def find_ids(obj):
    """Beta-схема: вытаскиваем вероятные id агента/рана, не полагаясь на имена."""
    ids = {}
    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                lk = k.lower()
                if isinstance(v, str) and ("agent" in lk and lk.endswith("id")):
                    ids.setdefault("agent_id", v)
                if isinstance(v, str) and ("run" in lk and lk.endswith("id")):
                    ids.setdefault("run_id", v)
                if isinstance(v, str) and lk in ("id",) and "agent_id" not in ids:
                    ids.setdefault("first_id", v)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(obj)
    return ids


def prompt_body(text):
    """Формат API: prompt — объект с полем text (не строка)."""
    return {"text": text}


def _parse_created_at(value):
    """ISO8601 createdAt → aware datetime UTC; None если не разобрали.

    Без datetime.fromisoformat (нужна совместимость с Python 3.6).
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip()
    if s.endswith("Z"):
        s = s[:-1]
    # оставить только дату/время, отбросить смещение +HH:MM / -HH:MM
    if "T" in s:
        date_part, rest = s.split("T", 1)
        cut = len(rest)
        for i, ch in enumerate(rest):
            if i >= 8 and ch in "+-":
                cut = i
                break
        rest = rest[:cut]
        s = date_part + "T" + rest
    try:
        if "." in s:
            main, frac = s.split(".", 1)
            frac = "".join(c for c in frac if c.isdigit())[:6].ljust(6, "0")
            dt = datetime.strptime(main + "." + frac, "%Y-%m-%dT%H:%M:%S.%f")
        else:
            dt = datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _agent_matches(item, sent_name, prompt_text):
    """Совпадение свежего агента: name если передавали, иначе текст промта в name."""
    name = item.get("name") or ""
    if sent_name:
        return name == sent_name
    pt = (prompt_text or "").strip()
    if not pt:
        return False
    if pt == name or pt in name or name in pt:
        return True
    # первая непустая строка промта часто становится авто-именем
    first = pt.splitlines()[0].strip() if pt else ""
    if first and (first == name or first in name or name in first):
        return True
    return False


def recover_create_after_timeout(key, body, prompt_text, http_timeout, lf):
    """GET /v1/agents?limit=20 → свежий агент с совпадающим name/prompt.

    Возвращает (out_dict, ids) или (None, None) если не нашли.
    """
    listing = call("GET", "/v1/agents?limit=20", key, timeout=http_timeout)
    log_write(lf, "recovery list:\n%s\n" % json.dumps(listing, ensure_ascii=False, indent=2))
    if report_http_error(listing) or not isinstance(listing, dict):
        return None, None
    if "_network_error" in listing:
        return None, None

    items = listing.get("items")
    if not isinstance(items, list):
        # на всякий случай — голый список
        items = listing if isinstance(listing, list) else []

    sent_name = body.get("name") if isinstance(body, dict) else None
    now = datetime.now(timezone.utc)
    for item in items:
        if not isinstance(item, dict):
            continue
        created = _parse_created_at(item.get("createdAt"))
        if created is None:
            continue
        age = (now - created).total_seconds()
        if age < 0 or age > RECOVERY_WINDOW_SEC:
            continue
        if not _agent_matches(item, sent_name, prompt_text):
            continue
        agent_id = item.get("id")
        run_id = item.get("latestRunId") or item.get("runId") or item.get("run_id")
        if not agent_id:
            continue
        out = {
            "id": agent_id,
            "agentId": agent_id,
            "name": item.get("name"),
            "createdAt": item.get("createdAt"),
            "latestRunId": run_id,
        }
        if run_id:
            out["runId"] = run_id
        ids = find_ids(out)
        if run_id:
            ids["agent_id"] = agent_id
            ids["run_id"] = run_id
        else:
            ids.setdefault("agent_id", agent_id)
        msg = "recovered after timeout: %s" % agent_id
        log_write(lf, msg + "\n")
        print(msg)
        return out, ids
    return None, None


def cmd_run(a):
    state = orchlib.find_state_dir()
    os.makedirs(state, exist_ok=True)
    prompt_file = a.prompt_file or os.path.join(state, "prompt-%s.md" % a.id)
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not prompt.strip():
        sys.stderr.write("промт пуст: %s\n" % prompt_file)
        return 2

    log_path = os.path.join(state, "cloud-%s.log" % a.id)
    role = orchlib.resolve_run_role(getattr(a, "role", None), prompt)
    front_raw = getattr(a, "front", None)
    no_front_raw = getattr(a, "no_front", None)
    front, no_front_reason, front_refuse = orchlib.resolve_front_launch(
        front_raw, no_front_raw)
    # Гейты до HTTP create / api_key: FRONT_REQUIRED; секрет; при --front — статус/бюджет.
    # Гейт-отказы: journal start+end до return (дыры нет).
    if front_refuse is not None:
        _append_gate_log(log_path, front_refuse)
        sys.stderr.write(front_refuse + "\n")
        journal_gate_refuse(
            a.id, prompt_file, None, role, log_path,
            orchlib.FRONT_REQUIRED_EXIT)
        return orchlib.FRONT_REQUIRED_EXIT
    gate_rc = apply_launch_gates(prompt, prompt_file, front, log_path)
    if gate_rc is not None:
        journal_gate_refuse(a.id, prompt_file, front, role, log_path, gate_rc,
                            no_front_reason=no_front_reason)
        return gate_rc

    # api_key до journal start — иначе sys.exit(2) оставляет orphan-start
    key = api_key(a)

    # летописец start (parent из OUR env; cloud-агент env не наследует)
    journal_start(a.id, prompt_file, front, role, engine="cloud",
                  no_front_reason=no_front_reason)

    extra = {}
    if a.body_file:
        with open(a.body_file, "r", encoding="utf-8") as f:
            extra = json.load(f)

    http_timeout = a.http_timeout
    lf = logf(state, a)
    log_write(lf, "=== %s run\n" % utc_stamp())
    body = dict(extra)
    body["prompt"] = prompt_body(prompt)
    ids = {}
    if a.agent_id:
        out = call("POST", "/v1/agents/%s/runs" % a.agent_id, key, body,
                   timeout=http_timeout)
        log_write(lf, "follow-up %s:\n%s\n" % (
            a.agent_id, json.dumps(out, ensure_ascii=False, indent=2)))
        ids = find_ids(out)
    else:
        out = call("POST", "/v1/agents", key, body, timeout=http_timeout)
        if isinstance(out, dict) and "_network_error" in out:
            log_write(lf, "create network/timeout: %s\n" % out["_network_error"])
            recovered, recovered_ids = recover_create_after_timeout(
                key, body, prompt, http_timeout, lf)
            if recovered is None:
                log_write(lf, "create recovery failed\n")
                lf.close()
                sys.stderr.write("create timeout/network error, recovery failed: %s\n"
                                 % out["_network_error"])
                print(json.dumps({"response": out, "ids": {}}, ensure_ascii=False, indent=2))
                check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                                       None, set())
                journal_end(a.id, log_path, 1)
                return 1
            out = recovered
            ids = recovered_ids
            log_write(lf, "create (recovered):\n%s\n" % json.dumps(
                out, ensure_ascii=False, indent=2))
        else:
            log_write(lf, "create:\n%s\n" % json.dumps(out, ensure_ascii=False, indent=2))
            ids = find_ids(out)

    # ids в лог до любого поллинга (flush через log_write)
    log_write(lf, "ids: %s\n" % ids)
    lf.close()

    if report_http_error(out):
        print(json.dumps({"response": out, "ids": ids}, ensure_ascii=False, indent=2))
        check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                               None, set())
        journal_end(a.id, log_path, 1)
        return 1

    agent = a.agent_id or ids.get("agent_id")
    run = ids.get("run_id") or ids.get("first_id")
    if agent and run:
        write_agent_json(state, a, agent, run)
        write_result_json(state, a, agent, run, status_obj={"status": "CREATED"})

    print(json.dumps({"response": out, "ids": ids}, ensure_ascii=False, indent=2))

    if a.wait:
        if agent and run:
            rc = wait_and_report(agent, run, key, state, a)
            # exit: статус терминала из result.json если есть, иначе rc
            exit_val = rc
            try:
                rpath = os.path.join(state, "cloud-%s.result.json" % a.id)
                with open(rpath, "r", encoding="utf-8") as rf:
                    rdata = json.load(rf)
                st = rdata.get("status") if isinstance(rdata, dict) else None
                if isinstance(st, str) and st:
                    exit_val = st
            except Exception:
                pass
            journal_end(a.id, log_path, exit_val)
            return rc
        sys.stderr.write("--wait: id не найдены в ответе, поллинг пропущен (см. лог)\n")
        check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                               None, set())
        journal_end(a.id, log_path, 0)
        return 0
    # без --wait: пост-проверка после create/follow-up
    check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                           None, set())
    journal_end(a.id, log_path, 0)
    return 0


def run_status_terminal(status_obj):
    """True если GET run вернул терминальный status."""
    if not isinstance(status_obj, dict):
        return False
    st = status_obj.get("status")
    return isinstance(st, str) and st in TERMINAL_RUN_STATUSES


def wait_and_report(agent, run, key, state, a):
    deadline = time.time() + (a.timeout or 1800)
    status = {}
    lf = logf(state, a)
    http_timeout = getattr(a, "http_timeout", 300.0)
    log_path = os.path.join(state, "cloud-%s.log" % a.id)
    noted = set()
    write_result_json(state, a, agent, run, status_obj={"status": "POLLING"})
    while time.time() < deadline:
        status = call("GET", "/v1/agents/%s/runs/%s" % (agent, run), key,
                      timeout=http_timeout)
        log_write(lf, "poll: %s\n" % json.dumps(status, ensure_ascii=False))
        write_result_json(state, a, agent, run, status_obj=status
                          if isinstance(status, dict) else None)
        # пока lf открыт и идут poll-строки — только pending, без маркера в лог
        check_compass_overflow(log_path, None, noted, allow_log_write=False)
        if report_http_error(status):
            lf.close()
            check_compass_overflow(log_path, None, noted, allow_log_write=True)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 1
        if run_status_terminal(status):
            break
        time.sleep(a.poll or 15)
    log_write(lf, "final: %s\n" % json.dumps(status, ensure_ascii=False, indent=2))
    write_result_json(state, a, agent, run,
                      status_obj=status if isinstance(status, dict) else None)
    lf.close()
    # маркер в лог — строго после flush/close lf (вне окна параллельной записи)
    check_compass_overflow(log_path, None, noted, allow_log_write=True)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    return 0


def cmd_status(a):
    key = api_key(a)
    state = orchlib.find_state_dir()
    if a.wait:
        return wait_and_report(a.agent_id, a.run_id, key, state, a)
    out = call("GET", "/v1/agents/%s/runs/%s" % (a.agent_id, a.run_id), key,
               timeout=a.http_timeout)
    write_result_json(state, a, a.agent_id, a.run_id,
                      status_obj=out if isinstance(out, dict) else None)
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                               None, set())
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with logf(state, a) as lf:
        log_write(lf, "status %s/%s: %s\n" % (a.agent_id, a.run_id,
                                              json.dumps(out, ensure_ascii=False)))
    # пост-проверка после получения статуса без --wait
    check_compass_overflow(os.path.join(state, "cloud-%s.log" % a.id),
                           None, set())
    return 0


def cmd_list(a):
    """GET /v1/agents?limit=20 → компактная таблица; лог cloud-<id>.log."""
    key = api_key(a)
    state = orchlib.find_state_dir()
    os.makedirs(state, exist_ok=True)
    out = call("GET", "/v1/agents?limit=20", key, timeout=a.http_timeout)
    with logf(state, a) as lf:
        log_write(lf, "=== %s list\n" % utc_stamp())
        log_write(lf, "%s\n" % json.dumps(out, ensure_ascii=False, indent=2))
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    if isinstance(out, dict) and "_network_error" in out:
        sys.stderr.write("network error: %s\n" % out["_network_error"])
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1

    items = []
    if isinstance(out, dict):
        raw = out.get("items")
        if isinstance(raw, list):
            items = raw
    elif isinstance(out, list):
        items = out

    # компактная таблица
    headers = ("id", "name", "status", "latestRunId", "createdAt")
    rows = []
    for it in items:
        if not isinstance(it, dict):
            continue
        rows.append((
            str(it.get("id") or ""),
            str(it.get("name") or ""),
            str(it.get("status") or ""),
            str(it.get("latestRunId") or it.get("runId") or ""),
            str(it.get("createdAt") or ""),
        ))
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if len(cell) > widths[i]:
                widths[i] = len(cell)
    # ограничить очень длинные ячейки для читаемости
    max_w = (36, 32, 12, 36, 28)
    widths = [min(widths[i], max_w[i]) for i in range(5)]

    def _cell(s, w):
        s = s.replace("\n", " ")
        if len(s) > w:
            return s[: max(0, w - 1)] + "…"
        return s.ljust(w)

    print("  ".join(_cell(headers[i], widths[i]) for i in range(5)))
    print("  ".join("-" * widths[i] for i in range(5)))
    for row in rows:
        print("  ".join(_cell(row[i], widths[i]) for i in range(5)))
    if not rows:
        print("(пусто)")
    return 0


def cmd_artifacts(a):
    key = api_key(a)
    out = call("GET", "/v1/agents/%s/artifacts" % a.agent_id, key,
               timeout=a.http_timeout)
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with logf(orchlib.find_state_dir(), a) as lf:
        log_write(lf, "artifacts %s: %s\n" % (a.agent_id, json.dumps(out, ensure_ascii=False)))
    return 0


def main():
    orchlib.utf8_stdio()
    note = orchlib.state_dir_note()
    if note:
        sys.stderr.write(note + "\n")
    ap = build_parser()
    a = normalize_args(ap.parse_args())
    if a.cmd == "run":
        return cmd_run(a)
    if a.cmd == "status":
        return cmd_status(a)
    if a.cmd == "list":
        return cmd_list(a)
    if a.cmd == "artifacts":
        return cmd_artifacts(a)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
