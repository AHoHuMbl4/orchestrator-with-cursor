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

--wait: dual-timer до терминального status (FINISHED / ERROR / CANCELLED /
EXPIRED): stall по прикладным SSE/fallback; fuse --max-wall; не по подстрокам
в сыром JSON.

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

Коды EXIT (гейты до HTTP create; едины с run-exec):
  5       — секрет в промте (SECRETS_IN_PROMPT); процесс не стартовал
  6       — фронт закрыт (cancelled/rejected); FRONT_CLOSED
  7       — жёсткий бюджет фронта (hard>0 и used>hard); BUDGET_HARD
  8       — нет --front/--no-front при hierarchy≠off; FRONT_REQUIRED
  9       — замок front-runs занят; FRONT_LOCK_BUSY
  10      — params.json битый
  11      — id занят живым cloud-прогоном (CREATED/POLLING/RUNNING); --force
  12      — роль не найдена в каталоге кита; --allow-unknown-role для смоука
  13      — FRONT_DUAL_WRITER: фронт уже имеет другой живой пишущий ран
  124     — STALL: нет прикладного прогресса (SSE/fallback) дольше stall_s
  125     — WALL: сработал fuse --max-wall (не retryable)

--writable: journal start "writable": true — cloud-ран считается пишущим для
детектора dual-writer (без флага — аналитик, не пишущий). ORCH_FRONT в
journal/meta; cloud child env НЕ наследует (факт кита).

--wait dual-timer (B4): stall сброс только по SSE assistant|tool_call|status|result
или (если SSE недоступен) дельтам run.updatedAt / artifacts fingerprint;
строки «poll: …» и SSE heartbeat/keepalive stall НЕ сбрасывают.
HTTP 429 в --wait — backoff (Retry-After / экспонента), stall-часы на паузе стоят.
"""
import argparse
import hashlib
import json
import os
import select
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

DEFAULT_API_BASE = "https://api.cursor.com"
TERMINAL_RUN_STATUSES = frozenset(("FINISHED", "ERROR", "CANCELLED", "EXPIRED"))
LIVE_CLOUD_STATUSES = frozenset(("CREATED", "POLLING", "RUNNING"))
PROGRESS_SSE_EVENTS = frozenset(("assistant", "tool_call", "status", "result"))
RECOVERY_WINDOW_SEC = 300
BUILTIN_STALL_S = 600
BUILTIN_MAX_WALL_S = 86400
FRONT_DUAL_WRITER_EXIT = 13
CLOUD_WRITER_TTL_S = 6 * 3600
TOMBSTONE_TTL_S = 24 * 3600


def _load_run_exec_helpers():
    """Общий хелпер A2/dual/no-verify из run-exec.py (без нового пути модуля)."""
    import importlib.util
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "run-exec.py")
    spec = importlib.util.spec_from_file_location("orch_mw2b_run_exec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_REX = None


def rex():
    global _REX
    if _REX is None:
        _REX = _load_run_exec_helpers()
    return _REX


def inject_a2_ownership(prompt, front_id, state_dir=None):
    """A2-секция после «роль:»; делегирует общему хелперу run-exec."""
    return rex().inject_a2_ownership(prompt, front_id, state_dir=state_dir)


def detect_commit_no_verify(log_path):
    return rex().detect_commit_no_verify(log_path)


def journal_chip(name, front=None, run_id=None, extra=None):
    return rex().journal_chip(name, front=front, run_id=run_id, extra=extra)


def api_base():
    """База API: env CURSOR_API_BASE если задан, иначе https://api.cursor.com."""
    return (os.environ.get("CURSOR_API_BASE") or DEFAULT_API_BASE).rstrip("/")


def _append_gate_log(log_path, line):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def apply_launch_gates(prompt, prompt_file, front_id, log_path):
    """Гейты до HTTP create: секреты → 5; closed → 6; hard → 7; lock busy → 9.

    Бюджет: used>hard (hard>0) → BUDGET_HARD/exit 7; used>=warn →
    FRONT_BUDGET_WARN + pending_budget_warn, запуск продолжается.
    bump_front_runs RuntimeError(front-runs lock busy) → FRONT_LOCK_BUSY/exit 9.
    Иначе None.
    """
    hit = orchlib.scan_secrets(prompt)
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
        try:
            used, warn, hard = bump(front_id)
        except RuntimeError as exc:
            msg = str(exc)
            if "front-runs lock busy" in msg:
                _append_gate_log(log_path, "FRONT_LOCK_BUSY=%s" % front_id)
                return 9
            if "front-runs closed" in msg:
                _append_gate_log(log_path, "FRONT_CLOSED=%s" % front_id)
                return 6
            raise
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
    parser.add_argument("--session", default=argparse.SUPPRESS,
                        help="id сессии: cloud-логи под sessions/<sid>/runs/")
    parser.add_argument("--front", default=argparse.SUPPRESS,
                        help="id фронта: статус cancelled/rejected; бюджет warn/hard "
                             "(warn=датчик, hard=стоп при hard>0)")
    parser.add_argument("--no-front", default=argparse.SUPPRESS, metavar="REASON",
                        help="запуск вне фронта с причиной (journal no_front_reason)")
    parser.add_argument("--role", default=argparse.SUPPRESS,
                        help="роль (приоритет над шапкой); иначе «роль: path.md» в первых 3 строках промта")
    parser.add_argument("--http-timeout", type=float, default=argparse.SUPPRESS,
                        help="HTTP socket timeout для всех API-вызовов, сек (дефолт 300)")
    parser.add_argument("--force", action="store_true", default=argparse.SUPPRESS,
                        help="обойти гард дубль-id (exit 11); гейты 5–9 не затрагивает")
    parser.add_argument("--allow-unknown-role", action="store_true",
                        default=argparse.SUPPRESS,
                        help="обойти валидацию роли по каталогу кита (exit 12); "
                             "для технического смоука")


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
    p.add_argument("--stall-after", type=int, default=None,
                   help="сек без прикладного прогресса (SSE/fallback); канон stall")
    p.add_argument("--timeout", type=int, default=None,
                   help="алиас --stall-after: stall/no-output, не wall-clock")
    p.add_argument("--max-wall", type=int, default=None,
                   help="fuse: абсолютный потолок wall-clock от старта ожидания, сек")
    p.add_argument("--poll", type=int, default=15)
    p.add_argument("--writable", action="store_true",
                   help="пишущий cloud-ран: journal start writable=true "
                        "(dual-writer); без флага — аналитик, не пишущий")

    p = sub.add_parser("status", help="статус/результат run")
    _add_common_args(p)
    p.add_argument("--agent-id", default=None,
                   help="явный agent id (иначе из agent-<id>.json)")
    p.add_argument("--run-id", default=None,
                   help="явный run id (иначе из agent-<id>.json)")
    p.add_argument("--wait", action="store_true")
    p.add_argument("--stall-after", type=int, default=None,
                   help="сек без прикладного прогресса (SSE/fallback); канон stall")
    p.add_argument("--timeout", type=int, default=None,
                   help="алиас --stall-after: stall/no-output, не wall-clock")
    p.add_argument("--max-wall", type=int, default=None,
                   help="fuse: абсолютный потолок wall-clock от старта ожидания, сек")
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
    if not hasattr(a, "session"):
        a.session = None
    if not hasattr(a, "front"):
        a.front = None
    if not hasattr(a, "no_front"):
        a.no_front = None
    if not hasattr(a, "role"):
        a.role = None
    if not hasattr(a, "http_timeout"):
        a.http_timeout = 300.0
    if not hasattr(a, "force"):
        a.force = False
    if not hasattr(a, "allow_unknown_role"):
        a.allow_unknown_role = False
    if not hasattr(a, "writable"):
        a.writable = False
    return a


def journal_start(run_id, prompt_file, front, role, engine="cloud",
                  no_front_reason=None, auto=False, session=None,
                  writable=False):
    """Запись kind=start в journal (ошибки глотает orchlib).

    ORCH_FRONT только в journal/meta (front=...); cloud child env НЕ обещать.
    """
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
    if session is not None:
        entry["session"] = session
    if no_front_reason:
        entry["no_front_reason"] = no_front_reason
    if writable:
        entry["writable"] = True
    if front:
        entry["ORCH_FRONT"] = front  # meta only; child env не наследует
    if auto or os.environ.get("ORCH_RUN_AUTO") == "1":
        entry["auto"] = True
    orchlib.journal_append(entry)


def journal_end(run_id, log_path, exit_code, session=None, front=None,
                no_verify_hit=None):
    """Запись kind=end в journal (ошибки глотает orchlib)."""
    if not run_id:
        return
    verdict, gates = orchlib.journal_log_meta(log_path)
    hit = no_verify_hit
    if hit is None and front:
        hit = detect_commit_no_verify(log_path)
    if hit:
        journal_chip("commit_no_verify", front=front, run_id=run_id)
    entry = {
        "ts": time.time(),
        "kind": "end",
        "id": run_id,
        "exit": exit_code,
        "verdict": verdict,
        "gates": gates,
    }
    if session is not None:
        entry["session"] = session
    if hit:
        entry["commit_no_verify"] = True
    orchlib.journal_append(entry)
    # Снять lock автопрокурора, если это был он.
    orchlib.release_auto_prosecutor_lock_if_any(run_id)
    # автопрокурор на волну (после end).
    orchlib.maybe_auto_prosecutor_after_end(run_id)


def journal_gate_refuse(run_id, prompt_file, front, role, log_path, exit_code,
                        engine="cloud", no_front_reason=None, session=None,
                        writable=False):
    """start+end при отказе гейта (exit 5/6/7/8/9/13) — без дыры в journal."""
    journal_start(run_id, prompt_file, front, role, engine=engine,
                  no_front_reason=no_front_reason, session=session,
                  writable=writable)
    # gate-refuse: не триггерим автопрокурора — пишем end напрямую
    verdict, gates = orchlib.journal_log_meta(log_path)
    entry = {
        "ts": time.time(),
        "kind": "end",
        "id": run_id,
        "exit": exit_code,
        "verdict": verdict,
        "gates": gates,
    }
    if session is not None:
        entry["session"] = session
    orchlib.journal_append(entry)
    # finally-семантика: снять lockdir на любом завершении; спавн — только journal_end.
    orchlib.release_auto_prosecutor_lock_if_any(run_id)


def resolve_api_key(a):
    """Ключ из --api-key / CURSOR_API_KEY / <state>/cursor.key; иначе None."""
    key = a.api_key or os.environ.get("CURSOR_API_KEY")
    if not key:
        kf = os.path.abspath(os.path.join(orchlib.find_state_dir(), "cursor.key"))
        if os.path.exists(kf):
            with open(kf, "r", encoding="utf-8") as f:
                key = f.read().strip()
    return key or None


def api_key_missing_msg():
    kf = os.path.abspath(os.path.join(orchlib.find_state_dir(), "cursor.key"))
    return ("нужен --api-key, env CURSOR_API_KEY или файл %s "
            "(вносится в панели; state-каталог ищется от cwd вверх)" % kf)


def api_key(a):
    key = resolve_api_key(a)
    if not key:
        sys.stderr.write(api_key_missing_msg() + "\n")
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
    url = api_base() + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", "Bearer " + key)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if stream:
        req.add_header("Accept", "text/event-stream")
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
        body_txt = e.read().decode("utf-8", "replace")
        out = {"_http_error": e.code, "_body": body_txt}
        ra = None
        try:
            ra = e.headers.get("Retry-After") if e.headers else None
        except Exception:
            ra = None
        if ra is not None and str(ra).strip():
            try:
                out["retry_after"] = float(str(ra).strip())
            except ValueError:
                pass
        return out
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return {"_network_error": str(e)}


def read_params_json(state):
    """Локально json из state/params.json → dict; orchlib не трогаем."""
    path = os.path.join(state, "params.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def resolve_timers(params, stall_after=None, timeout=None, max_wall=None):
    """Приоритеты A3/B4: stall и max_wall. Читает keys локально из params dict.

    stall: --stall-after > --timeout > execution.stall_s > execution.timeout_s
           > builtin 600.
    max_wall: --max-wall > execution.max_wall_s > builtin 86400.
    """
    ex = (params or {}).get("execution", {}) or {}
    if stall_after is not None:
        stall_s = int(stall_after)
    elif timeout is not None:
        stall_s = int(timeout)
    elif ex.get("stall_s") is not None:
        stall_s = int(ex["stall_s"])
    elif ex.get("timeout_s") is not None:
        stall_s = int(ex["timeout_s"])
    else:
        stall_s = BUILTIN_STALL_S
    if max_wall is not None:
        max_wall_s = int(max_wall)
    elif ex.get("max_wall_s") is not None:
        max_wall_s = int(ex["max_wall_s"])
    else:
        max_wall_s = BUILTIN_MAX_WALL_S
    return stall_s, max_wall_s


def artifacts_fingerprint(artifacts_obj):
    """Стабильный fingerprint списка артефактов (path|updatedAt)."""
    if isinstance(artifacts_obj, dict):
        raw = artifacts_obj.get("artifacts") or []
    elif isinstance(artifacts_obj, list):
        raw = artifacts_obj
    else:
        raw = []
    lines = []
    for it in raw:
        if isinstance(it, dict):
            lines.append("%s|%s" % (it.get("path") or "",
                                    it.get("updatedAt") or ""))
    lines.sort()
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def _sse_clear_sock_timeout(resp):
    """Снять socket-timeout с SSE-ответа.

    urlopen() ставит timeout на сокет; при TimeoutError BufferedReader
    http.client переходит в состояние «cannot read from timed out object»
    и дальнейший drain невозможен. Неблокируемость даёт select() в
    drain_sse_progress, а не короткий sock.settimeout.
    """
    sock = None
    try:
        sock = resp.fp.raw._sock  # noqa: SLF001
    except Exception:
        try:
            sock = resp.fp.fp.raw._sock  # noqa: SLF001
        except Exception:
            sock = None
    if sock is not None:
        try:
            sock.settimeout(None)
        except Exception:
            pass


def open_sse_stream(agent, run, key, http_timeout):
    """Открыть SSE stream run; None если недоступен."""
    out = call("GET", "/v1/agents/%s/runs/%s/stream" % (agent, run), key,
               stream=True, timeout=min(float(http_timeout), 30.0))
    if isinstance(out, dict):
        return None
    _sse_clear_sock_timeout(out)
    return out


def drain_sse_progress(resp, buf_state, window_s=0.4):
    """Слить доступные SSE; True если прикладное событие assistant|tool_call|status|result.

    heartbeat/keepalive и comment-строки «:» прогрессом НЕ считаются.
    buf_state — dict с ключом 'buf' (накопленный текст) и 'alive' (False при EOF).
    """
    if resp is None or not buf_state.get("alive", True):
        return False
    progressed = False
    deadline = time.time() + float(window_s)
    while time.time() < deadline:
        fd = None
        try:
            fd = resp.fileno()
        except Exception:
            fd = None
        if fd is not None:
            try:
                ready, _, _ = select.select([fd], [], [], max(0.0, deadline - time.time()))
            except Exception:
                ready = []
            if not ready:
                break
        try:
            # read1: один syscall — не блокирует до заполнения 4096 на
            # keep-alive SSE (read(4096) на blocking sock ждал бы полный буфер).
            if hasattr(resp, "read1"):
                chunk = resp.read1(4096)
            else:
                chunk = resp.read(4096)
        except Exception:
            break
        if not chunk:
            buf_state["alive"] = False
            break
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        buf_state["buf"] = buf_state.get("buf", "") + chunk
        while "\n\n" in buf_state["buf"]:
            block, buf_state["buf"] = buf_state["buf"].split("\n\n", 1)
            event = "message"
            for line in block.split("\n"):
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith(":"):
                    event = "keepalive"
            if event in PROGRESS_SSE_EVENTS:
                progressed = True
        if fd is None:
            # без select — один read за вызов
            break
    return progressed


def cancel_run(agent, run, key, http_timeout, lf):
    """Попытка cancel run через API. True при успехе; лог при недоступности."""
    out = call("POST", "/v1/agents/%s/runs/%s/cancel" % (agent, run), key,
               timeout=http_timeout)
    if isinstance(out, dict) and ("_http_error" in out or "_network_error" in out):
        detail = out.get("_http_error") or out.get("_network_error") or "?"
        log_write(lf, "cancel unavailable: %s\n" % detail)
        return False
    log_write(lf, "cancel ok\n")
    return True


def _wait_backoff_sleep(seconds, wall_deadline, last_progress_at):
    """Сон 429-паузы: stall-часы не инкрементируются; упираемся в max_wall."""
    now = time.time()
    remain = max(0.0, wall_deadline - now)
    sleep_s = min(float(seconds), remain)
    if sleep_s <= 0:
        return last_progress_at
    t_pause = time.time()
    time.sleep(sleep_s)
    # заморозить stall-часы на длительность паузы
    return last_progress_at + (time.time() - t_pause)


def cloud_run_dir(state, run_id, session=None):
    """Каталог sessions/<sid>/runs/<id>/ для cloud-артефактов."""
    sid = orchlib.safe_name(session) if session else "default"
    d = os.path.join(state, "sessions", sid, "runs", run_id)
    os.makedirs(d, exist_ok=True)
    return d


def cloud_log_path(state, run_id, session=None):
    """Путь sessions/<sid>/runs/<id>/cloud-<id>.log."""
    return os.path.join(
        cloud_run_dir(state, run_id, session), "cloud-%s.log" % run_id)


def utc_stamp():
    """UTC-штамп с суффиксом Z."""
    return time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime())


def logf(state, a):
    return open(cloud_log_path(state, a.id, getattr(a, "session", None)), "a",
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
        try:
            p = orchlib.load_params()
        except ValueError as e:
            sys.stderr.write("%s\n" % e)
            return
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


def result_json_path(state, run_id_label, session=None):
    """Путь sessions/<sid>/runs/<id>/cloud-<id>.result.json."""
    return os.path.join(
        cloud_run_dir(state, run_id_label, session),
        "cloud-%s.result.json" % run_id_label)


def agent_json_path(state, run_id_label):
    """Путь <state>/agent-<id>.json."""
    return os.path.join(state, "agent-%s.json" % run_id_label)


def cloud_live_status(state, run_id, session=None):
    """Живой cloud-id → status-строка; иначе None. Не смотрит journal.

    Приоритет: (а) result.json status ∈ LIVE → жив; (б) result нет/нечитаем,
    но есть agent-<id>.json → CREATED; (в) result терминальный → нежив.

    Ищет result под явным session, sessions/default и sessions/*/runs/<id>/.
    """
    candidates = []
    if session is not None:
        candidates.append(result_json_path(state, run_id, session))
    candidates.append(result_json_path(state, run_id, None))
    sessions_root = os.path.join(state, "sessions")
    if os.path.isdir(sessions_root):
        try:
            for sid in os.listdir(sessions_root):
                p = result_json_path(state, run_id, sid)
                if p not in candidates:
                    candidates.append(p)
        except Exception:
            pass

    saw_readable_result = False
    for result_path in candidates:
        if not os.path.isfile(result_path):
            continue
        try:
            with open(result_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        saw_readable_result = True
        st = data.get("status")
        if isinstance(st, str) and st in LIVE_CLOUD_STATUSES:
            return st
        if isinstance(st, str) and st in TERMINAL_RUN_STATUSES:
            continue
        # result есть, status неизвестен/пуст — не жив по этому result
        continue

    if saw_readable_result:
        return None

    agent_path = agent_json_path(state, run_id)
    if os.path.isfile(agent_path):
        return "CREATED"
    return None


def check_cloud_duplicate_id_guard(state, run_id, force=False, session=None):
    """Отказ exit 11 при живом cloud-прогоне. --force обходит только этот гард."""
    if force:
        return None
    status = cloud_live_status(state, run_id, session=session)
    if status is None:
        return None
    sys.stderr.write(
        "id %s занят живым cloud-прогоном (status %s); --force для явного\n"
        % (run_id, status))
    return 11


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
    path = result_json_path(state, a.id, getattr(a, "session", None))
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


def resolve_status_ids(state, a):
    """Резолв agent_id/run_id для status ДО api_key/HTTP.

    Явные --agent-id/--run-id перекрывают; иначе --id → agent-<id>.json.
    Нет/битый файл → stderr + None (caller → exit 2).
    """
    agent_id = getattr(a, "agent_id", None) or None
    run_id = getattr(a, "run_id", None) or None
    if agent_id and run_id:
        return agent_id, run_id
    path = agent_json_path(state, a.id)

    def _fail():
        sys.stderr.write(
            "status: нет %s; укажите --agent-id/--run-id или выполните run "
            "с --id %s\n" % (path, a.id))
        return None

    if not os.path.isfile(path):
        return _fail()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return _fail()
    if not isinstance(data, dict):
        return _fail()
    file_agent = data.get("agent_id")
    file_run = data.get("run_id")
    if not (isinstance(file_agent, str) and file_agent
            and isinstance(file_run, str) and file_run):
        return _fail()
    if not agent_id:
        agent_id = file_agent
    if not run_id:
        run_id = file_run
    return agent_id, run_id


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
    # TTL/GC tombstones при старте любого прогона
    try:
        rex().sweep_tombstones(state)
    except Exception:
        pass
    # Гард дубль-id: только subcommand run, до HTTP / записи cloud|agent файлов.
    dup_rc = check_cloud_duplicate_id_guard(
        state, a.id, force=bool(a.force),
        session=getattr(a, "session", None))
    if dup_rc is not None:
        return dup_rc
    os.makedirs(state, exist_ok=True)
    prompt_file = a.prompt_file or os.path.join(state, "prompt-%s.md" % a.id)
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not prompt.strip():
        sys.stderr.write("промт пуст: %s\n" % prompt_file)
        return 2

    log_path = cloud_log_path(state, a.id, getattr(a, "session", None))
    role = orchlib.resolve_run_role(getattr(a, "role", None), prompt)
    # Валидация роли до FRONT/API_KEY/HTTP: неизвестная → exit 12.
    if role is not None and not getattr(a, "allow_unknown_role", False):
        role_path = os.path.join(
            orchlib.KIT_DIR, "skills", "orchestration", "references", "roles",
            role)
        if not os.path.isfile(role_path):
            msg = ("роль не найдена в каталоге кита: %s (проверьте _index.md); "
                   "для технического смоука — --allow-unknown-role" % role)
            _append_gate_log(log_path, msg)
            sys.stderr.write(msg + "\n")
            journal_gate_refuse(a.id, prompt_file, None, role, log_path, 12,
                                session=getattr(a, "session", None))
            return 12
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
            orchlib.FRONT_REQUIRED_EXIT, session=getattr(a, "session", None))
        return orchlib.FRONT_REQUIRED_EXIT
    # API-ключ ДО bump_front_runs: нет ключа → отказ без расхода бюджета
    key = resolve_api_key(a)
    if not key:
        msg = api_key_missing_msg()
        _append_gate_log(log_path, "API_KEY_REQUIRED")
        sys.stderr.write(msg + "\n")
        journal_gate_refuse(a.id, prompt_file, front, role, log_path, 2,
                            no_front_reason=no_front_reason,
                            session=getattr(a, "session", None))
        return 2
    gate_rc = apply_launch_gates(prompt, prompt_file, front, log_path)
    if gate_rc is not None:
        journal_gate_refuse(a.id, prompt_file, front, role, log_path, gate_rc,
                            no_front_reason=no_front_reason,
                            session=getattr(a, "session", None),
                            writable=bool(getattr(a, "writable", False)))
        return gate_rc

    writable = bool(getattr(a, "writable", False))
    # Dual-writer ДО journal_start (только если --writable)
    if writable and front:
        dual_rc, dual_msg = rex().check_dual_writer_guard(
            front, a.id, state, session=getattr(a, "session", None),
            readonly=False)
        if dual_rc is not None:
            _append_gate_log(log_path, dual_msg)
            sys.stderr.write(dual_msg + "\n")
            journal_chip("multi_write_front", front=front, run_id=a.id)
            journal_gate_refuse(a.id, prompt_file, front, role, log_path,
                                dual_rc, no_front_reason=no_front_reason,
                                session=getattr(a, "session", None),
                                writable=writable)
            return dual_rc

    # летописец start (parent из OUR env; cloud-агент env не наследует)
    # ORCH_FRONT только в journal/meta — child env НЕ обещать
    journal_start(a.id, prompt_file, front, role, engine="cloud",
                  no_front_reason=no_front_reason,
                  session=getattr(a, "session", None),
                  writable=writable)

    # TOCTOU после journal_start до HTTP create — младший само-отказ
    if writable and front:
        dual_rc2, dual_msg2 = rex().check_dual_writer_guard(
            front, a.id, state, session=getattr(a, "session", None),
            readonly=False, toctou=True)
        if dual_rc2 is not None:
            _append_gate_log(log_path, dual_msg2)
            sys.stderr.write(dual_msg2 + "\n")
            rex().write_tombstone(
                state, a.id, prev_pid=None,
                session=getattr(a, "session", None),
                reason="dual_writer_toctou")
            journal_chip("multi_write_front", front=front, run_id=a.id)
            verdict, gates = orchlib.journal_log_meta(log_path)
            end = {
                "ts": time.time(), "kind": "end", "id": a.id,
                "exit": dual_rc2, "verdict": verdict, "gates": gates,
            }
            if getattr(a, "session", None):
                end["session"] = a.session
            orchlib.journal_append(end)
            orchlib.release_auto_prosecutor_lock_if_any(a.id)
            return dual_rc2

    # F-RULES R2/R4: tried-before + прецеденты в КОПИЮ промта, до HTTP-двигателя
    komu = orchlib.role_to_komu(role)
    prec_lines = []
    if komu:
        try:
            extra = {"kogda": "launch", "role": role, "caller": "run-cloud"}
            tried = orchlib.format_tried_before_lines(
                komu, role=role, caller="run-cloud", extra=extra,
                task_hint=(prompt or "")[:160])
            prec = orchlib.format_precedent_lines(
                komu, prompt_text=prompt, extra=extra)
            prec_lines = (tried + prec)[:3]
        except Exception as e:
            sys.stderr.write("precedent inject failed: %s\n" % e)
            prec_lines = []
    run_prompt = prompt
    # A2: --front (cloud без --readonly; аналитик тоже видит границы)
    if front:
        run_prompt = inject_a2_ownership(run_prompt, front, state_dir=state)
    if prec_lines:
        run_prompt = run_prompt.rstrip() + "\n\n" + "\n".join(prec_lines) + "\n"
    run_prompt_file = os.path.join(state, "prompt-%s.run.md" % a.id)
    try:
        with open(run_prompt_file, "w", encoding="utf-8") as f:
            f.write(run_prompt)
    except Exception as e:
        sys.stderr.write("prompt.run write failed: %s\n" % e)

    extra = {}
    if a.body_file:
        with open(a.body_file, "r", encoding="utf-8") as f:
            extra = json.load(f)

    http_timeout = a.http_timeout
    lf = logf(state, a)
    log_write(lf, "=== %s run\n" % utc_stamp())
    body = dict(extra)
    body["prompt"] = prompt_body(run_prompt)
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
                check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
                                       None, set())
                journal_end(a.id, log_path, 1, session=getattr(a, "session", None),
                            front=front)
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
        check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
                               None, set())
        journal_end(a.id, log_path, 1, session=getattr(a, "session", None),
                    front=front)
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
            # journal_end только после терминала / таймаута wait (rc из wait_and_report)
            journal_end(a.id, log_path, rc, session=getattr(a, "session", None),
                        front=front)
            return rc
        sys.stderr.write("--wait: id не найдены в ответе, поллинг пропущен (см. лог)\n")
        check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
                               None, set())
        journal_end(a.id, log_path, 1, session=getattr(a, "session", None),
                    front=front)
        return 1
    # без --wait: create/follow-up успешен, агент ещё бежит — journal_end не пишем
    check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
                           None, set())
    return 0


def run_status_terminal(status_obj):
    """True если GET run вернул терминальный status."""
    if not isinstance(status_obj, dict):
        return False
    st = status_obj.get("status")
    return isinstance(st, str) and st in TERMINAL_RUN_STATUSES


def wait_and_report(agent, run, key, state, a):
    """Dual-timer --wait (B4): stall по прикладным событиям / max_wall fuse.

    stall сброс: (а) SSE assistant|tool_call|status|result; (б) fallback при
    недоступности SSE — дельты run.updatedAt или artifacts fingerprint.
    НЕ сбрасывают: строки «poll: …», SSE heartbeat/keepalive.
    429: backoff, stall-часы на паузе стоят; суммарно ограничены max_wall.
    """
    params = read_params_json(state)
    stall_s, max_wall_s = resolve_timers(
        params,
        stall_after=getattr(a, "stall_after", None),
        timeout=getattr(a, "timeout", None),
        max_wall=getattr(a, "max_wall", None))
    t0 = time.time()
    wall_deadline = t0 + float(max_wall_s)
    last_progress_at = t0
    status = {}
    lf = logf(state, a)
    http_timeout = getattr(a, "http_timeout", 300.0)
    poll_s = a.poll or 15
    log_path = cloud_log_path(state, a.id, getattr(a, "session", None))
    noted = set()
    write_result_json(state, a, agent, run, status_obj={"status": "POLLING"})

    sse_resp = open_sse_stream(agent, run, key, http_timeout)
    sse_ok = sse_resp is not None
    sse_buf = {"buf": "", "alive": True}
    if not sse_ok:
        log_write(lf, "sse unavailable: fallback updatedAt/artifacts\n")

    last_updated = None
    last_fp = None
    backoff = 1.0

    while True:
        now = time.time()
        if now >= wall_deadline:
            log_write(lf, "WALL: max-wall fuse (deadline reached)\n")
            log_write(lf, "final: %s\n" % json.dumps(status, ensure_ascii=False,
                                                     indent=2))
            write_result_json(state, a, agent, run,
                              status_obj=status if isinstance(status, dict) else None)
            lf.close()
            check_compass_overflow(log_path, None, noted, allow_log_write=True)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 125
        if stall_s and (now - last_progress_at) >= float(stall_s):
            log_write(lf, "STALL: no applied progress for %ss\n" % stall_s)
            cancel_run(agent, run, key, http_timeout, lf)
            log_write(lf, "final: %s\n" % json.dumps(status, ensure_ascii=False,
                                                     indent=2))
            write_result_json(state, a, agent, run,
                              status_obj=status if isinstance(status, dict) else None)
            lf.close()
            check_compass_overflow(log_path, None, noted, allow_log_write=True)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 124

        # SSE primary: drain прикладные события
        if sse_ok:
            if not sse_buf.get("alive", True):
                sse_ok = False
                log_write(lf, "sse ended: fallback updatedAt/artifacts\n")
            elif drain_sse_progress(sse_resp, sse_buf):
                last_progress_at = time.time()

        status = call("GET", "/v1/agents/%s/runs/%s" % (agent, run), key,
                      timeout=http_timeout)
        # 429: не терминал и не прогресс; stall-часы на паузе стоят
        if isinstance(status, dict) and status.get("_http_error") == 429:
            ra = status.get("retry_after")
            sleep_s = float(ra) if ra is not None else backoff
            log_write(lf, "429 backoff: %ss\n" % sleep_s)
            last_progress_at = _wait_backoff_sleep(
                sleep_s, wall_deadline, last_progress_at)
            if ra is None:
                backoff = min(backoff * 2.0, 60.0)
            continue
        # poll-строка — НЕ сигнал жизни (не сбрасывает stall)
        log_write(lf, "poll: %s\n" % json.dumps(status, ensure_ascii=False))
        write_result_json(state, a, agent, run, status_obj=status
                          if isinstance(status, dict) else None)
        check_compass_overflow(log_path, None, noted, allow_log_write=False)
        if report_http_error(status):
            lf.close()
            check_compass_overflow(log_path, None, noted, allow_log_write=True)
            print(json.dumps(status, ensure_ascii=False, indent=2))
            return 1
        if isinstance(status, dict) and "_network_error" in status:
            # сеть: не прогресс; обычный poll-sleep ниже
            pass
        elif not sse_ok and isinstance(status, dict):
            # fallback: дельта updatedAt
            upd = status.get("updatedAt")
            if isinstance(upd, str) and upd:
                if last_updated is not None and upd != last_updated:
                    last_progress_at = time.time()
                last_updated = upd
            # fallback: artifacts fingerprint
            arts = call("GET", "/v1/agents/%s/artifacts" % agent, key,
                        timeout=http_timeout)
            if isinstance(arts, dict) and arts.get("_http_error") == 429:
                ra = arts.get("retry_after")
                sleep_s = float(ra) if ra is not None else backoff
                log_write(lf, "429 backoff (artifacts): %ss\n" % sleep_s)
                last_progress_at = _wait_backoff_sleep(
                    sleep_s, wall_deadline, last_progress_at)
                if ra is None:
                    backoff = min(backoff * 2.0, 60.0)
                continue
            if not (isinstance(arts, dict)
                    and ("_http_error" in arts or "_network_error" in arts)):
                fp = artifacts_fingerprint(arts)
                if last_fp is not None and fp != last_fp:
                    last_progress_at = time.time()
                last_fp = fp

        if run_status_terminal(status):
            break
        # обычный poll-sleep инкрементирует stall (тишина агента)
        remain = wall_deadline - time.time()
        if remain <= 0:
            continue
        time.sleep(min(float(poll_s), remain))

    log_write(lf, "final: %s\n" % json.dumps(status, ensure_ascii=False, indent=2))
    write_result_json(state, a, agent, run,
                      status_obj=status if isinstance(status, dict) else None)
    lf.close()
    check_compass_overflow(log_path, None, noted, allow_log_write=True)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    if run_status_terminal(status):
        st = status.get("status") if isinstance(status, dict) else None
        if st == "FINISHED":
            return 0
        return 1
    return 124


def cmd_status(a):
    state = orchlib.find_state_dir()
    resolved = resolve_status_ids(state, a)
    if resolved is None:
        return 2
    a.agent_id, a.run_id = resolved
    key = api_key(a)
    if a.wait:
        return wait_and_report(a.agent_id, a.run_id, key, state, a)
    out = call("GET", "/v1/agents/%s/runs/%s" % (a.agent_id, a.run_id), key,
               timeout=a.http_timeout)
    write_result_json(state, a, a.agent_id, a.run_id,
                      status_obj=out if isinstance(out, dict) else None)
    if report_http_error(out):
        print(json.dumps(out, ensure_ascii=False, indent=2))
        check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
                               None, set())
        return 1
    print(json.dumps(out, ensure_ascii=False, indent=2))
    with logf(state, a) as lf:
        log_write(lf, "status %s/%s: %s\n" % (a.agent_id, a.run_id,
                                              json.dumps(out, ensure_ascii=False)))
    # пост-проверка после получения статуса без --wait
    check_compass_overflow(cloud_log_path(state, a.id, getattr(a, "session", None)),
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
    # Ранний отказ и для list/status/artifacts (иначе обходят load_params).
    try:
        orchlib.load_params()
    except ValueError as e:
        sys.stderr.write("%s\n" % e)
        return 10
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
