#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск исполнителя cursor-agent (доктрина §2) — кроссплатформенно, без shell.

Промт — ТОЛЬКО из файла (ловушка №1: текст промта не должен попадать в командную
строку; передаётся в stdin cursor-agent через subprocess.PIPE, без argv-текста,
без $(cat ...) и без кавычек). Модель исполнителя всегда auto.

К cursor-агенту в конец промта доклеивается строка самопроверки курса
(у cursor нет хуков — периодический re-ground идёт на уровне промта).

  python3 run-exec.py --id T1 [--prompt-file ...] [--stall-after 600]
                      [--timeout 600] [--max-wall 86400] [--detach]
                      [--yield-after 480] [--no-reground-line] [--model auto]
                      [--readonly] [доп. флаги cursor-agent]

  --stall-after N  — канон: stall по стагнации run.log (mtime+size), не wall-clock.
  --timeout N      — алиас той же stall-семантики (stall/no-output, не wall-clock).
  --max-wall N     — fuse: абсолютный потолок wall-clock от t0 первого старта агента.

  --readonly (барьер A): добавляет `--mode plan` в вызов cursor-agent
  (только local); journal start получает "readonly": true. Назначение —
  аналитические прогоны без артефактов на диск (чтение + выжимка в ответ:
  аудит/ревью). НЕ для командирных ролей с артефактами (советник/инспектор/
  наблюдатель/генерал) — те запускаются обычно, без --readonly.

Лог: <state>/cursor-run-<id>.log, в конце строка EXIT=<код>.
При --detach: pid-файл <state>/cursor-run-<id>.pid, процесс не ждём.
Foreground: после --yield-after сек (дефолт 480; 0 = выкл) — авто-уступка
в фон через тот же watcher, exit 0.

Коды EXIT:
  0       — result success
  1       — result-событие с неуспехом (агент отчитался о фейле)
  3       — умер без result, но в логе есть финальный текст ассистента
  4       — умер без result и без текста отчёта (работа потеряна)
  5       — секрет в промте (SECRETS_IN_PROMPT); процесс не стартовал
  6       — фронт закрыт (cancelled/rejected); FRONT_CLOSED
  7       — жёсткий бюджет фронта (hard>0 и used>hard); BUDGET_HARD
  8       — нет --front/--no-front при hierarchy≠off; FRONT_REQUIRED
  9       — замок front-runs занят; FRONT_LOCK_BUSY
  10      — params.json битый
  11      — id занят живым прогоном (pid); нужен другой id или --force
  12      — роль не найдена в каталоге кита; --allow-unknown-role для смоука
  13      — FRONT_DUAL_WRITER: фронт уже имеет другой живой пишущий ран
  124     — STALL: нет прогресса run.log (mtime+size) дольше stall_s
  125     — WALL: сработал fuse --max-wall (не retryable)
  UNKNOWN — не удалось определить (нет/нечитаемый лог)

Kill-протокол:
  --kill <id> [--session SID]  — TOMBSTONE + journal kind=killed + kill дерева
  --unkill <id> [--session SID] — удалить TOMBSTONE
  TOMBSTONE блокирует watcher/apply_retries при EXIT∈{4,124}; свежий CLI-старт
  того же id при мёртвом pid разрешён. GC: sweep TOMBSTONE старше 24ч при старте.

Бюджет фронта (--front): warn = churn-датчик (FRONT_BUDGET_WARN + pending,
запуск продолжается); hard=0 по умолчанию выключен. Качество > токены.
При hierarchy≠off обязателен --front <id> или --no-front "<причина>".

Автоперезапуск (execution.retry_on_fail, по умолчанию 1):
  только при EXIT=4 и EXIT=124 (STALL); не при 1, 3 и 125 (WALL).
  Перед рестартом в лог: RETRY=<n>/<max> (prev EXIT=<code>).
  Итоговый EXIT — от последней попытки. В --detach рестарт делает watcher
  (cursor-agent — его потомок, код возврата через wait).
  При retry max_wall — ОСТАТОК от t0 первого старта (абсолютный wall_deadline).
"""
import argparse
import glob
import json
import os
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402
import owns  # noqa: E402 — A2-манифест; owns НЕ импортирует orchlib
import verdict as verdict_mod  # noqa: E402

REGROUND_LINE = (
    "\n\n---\nСамопроверка курса: в начале каждого подшага перечитай файл задания "
    "{path} и сверяй следующий шаг с целью и критерием приёмки из него. "
    "Если заметил дрейф от цели — вернись на шаг назад и отметь это в ответе.\n"
)

RETRYABLE = frozenset(("4", "124"))  # 125 WALL — не retryable
TOMBSTONE_TTL_S = 24 * 3600
CLOUD_WRITER_TTL_S = 6 * 3600
FRONT_DUAL_WRITER_EXIT = 13

# Builtin-фолбэки (A3). Переходный эффективный stall = execution.timeout_s
# из params (compat), если нет stall_s; builtin STALL — только без обоих ключей.
BUILTIN_STALL_S = 600
BUILTIN_MAX_WALL_S = 86400


# --- MW2-B: pid/starttime, TOMBSTONE, dual-writer, A2, no-verify -------------

def proc_starttime(pid):
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


def write_pid_file(pid_path, pid):
    """pid\\n[starttime\\n] — starttime опционален при отсутствии /proc."""
    lines = [str(int(pid))]
    st = proc_starttime(pid)
    if st is not None:
        lines.append(str(st))
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def read_pid_file(pid_path):
    """Совместимость: первая строка = pid (int); иначе None."""
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            first = f.readline().strip()
            return int(first)
    except Exception:
        return None


def read_pid_file_full(pid_path):
    """(pid, starttime_or_None) из pid-файла."""
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f.read().splitlines() if ln.strip()]
        if not lines:
            return None, None
        pid = int(lines[0])
        st = lines[1] if len(lines) > 1 else None
        return pid, st
    except Exception:
        return None, None


def pid_alive(pid):
    """ProcessLookupError=мёртв; PermissionError=ЖИВ; прочее OSError по errno."""
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 stdout=subprocess.PIPE,
                                 encoding="utf-8", errors="replace").stdout
            pid_s = str(pid)
            for line in out.splitlines():
                if not line.strip():
                    continue
                if pid_s in line.split():
                    return True
            return False
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError as e:
        errno = getattr(e, "errno", None)
        if errno == 3:  # ESRCH
            return False
        if errno == 1:  # EPERM
            return True
        return False
    except Exception:
        return False


def run_pid_is_alive(pid_path):
    """Живость = pid жив И (если есть) starttime совпадает; нет /proc → pid-only."""
    pid, recorded = read_pid_file_full(pid_path)
    if pid is None:
        return False
    if not pid_alive(pid):
        return False
    if recorded is None:
        return True
    current = proc_starttime(pid)
    if current is None:
        return True  # нет /proc — деградация до pid-only
    return str(current) == str(recorded)


def resolve_tombstone_path(state, run_id, session=None):
    """Per-id TOMBSTONE через resolve_run_paths sibling (НЕ общий state/TOMBSTONE)."""
    if session:
        run_dir = os.path.join(orchlib.session_dir(session), "runs", run_id)
        return os.path.join(run_dir, "TOMBSTONE")
    return os.path.join(state, "cursor-run-%s.TOMBSTONE" % run_id)


def tombstone_exists(state, run_id, session=None):
    return os.path.isfile(resolve_tombstone_path(state, run_id, session))


def write_tombstone(state, run_id, prev_pid=None, session=None, reason="manual"):
    path = resolve_tombstone_path(state, run_id, session)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "ts": time.time(),
        "prev_pid": prev_pid,
        "reason": reason,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
        f.write("\n")
    return path


def remove_tombstone(state, run_id, session=None):
    path = resolve_tombstone_path(state, run_id, session)
    try:
        os.unlink(path)
        return True
    except OSError:
        return False


def sweep_tombstones(state, now=None):
    """Удалить TOMBSTONE старше 24ч (session runs/*/TOMBSTONE + cursor-run-*.TOMBSTONE)."""
    now = time.time() if now is None else float(now)
    removed = 0
    candidates = []
    for path in glob.glob(os.path.join(state, "cursor-run-*.TOMBSTONE")):
        candidates.append(path)
    sessions = os.path.join(state, "sessions")
    if os.path.isdir(sessions):
        for path in glob.glob(os.path.join(sessions, "*", "runs", "*", "TOMBSTONE")):
            candidates.append(path)
    for path in candidates:
        try:
            ts = None
            with open(path, "r", encoding="utf-8") as f:
                obj = json.load(f)
            if isinstance(obj, dict):
                ts = float(obj.get("ts") or 0)
            if ts and (now - ts) > TOMBSTONE_TTL_S:
                os.unlink(path)
                removed += 1
        except Exception:
            try:
                age = now - os.path.getmtime(path)
                if age > TOMBSTONE_TTL_S:
                    os.unlink(path)
                    removed += 1
            except Exception:
                pass
    return removed


def journal_chip(name, front=None, run_id=None, extra=None):
    """journal kind=chip (читает health_red_chips / MW2-A)."""
    entry = {"ts": time.time(), "kind": "chip", "name": name}
    if front is not None:
        entry["front"] = front
    if run_id is not None:
        entry["id"] = run_id
    if extra:
        entry.update(extra)
    orchlib.journal_append(entry)


def journal_killed(run_id, prev_pid=None, session=None, front=None):
    entry = {
        "ts": time.time(),
        "kind": "killed",
        "id": run_id,
        "prev_pid": prev_pid,
        "reason": "manual",
    }
    if session is not None:
        entry["session"] = session
    if front is not None:
        entry["front"] = front
    orchlib.journal_append(entry)


def inject_a2_ownership(prompt, front_id, state_dir=None):
    """После шапки «роль:» вклеить секцию A2; owns пуст/нет front → без изменений."""
    if not front_id:
        return prompt
    try:
        sd = state_dir or orchlib.find_state_dir()
        globs = owns.load_owns(sd).get(front_id) or []
    except Exception:
        return prompt
    if not globs:
        return prompt
    section = (
        "## Владение (A2): front %s; owns: %s; forbids: всё вне owns "
        "(чужие active-владения не трогать)\n"
        % (front_id, globs)
    )
    lines = prompt.splitlines(keepends=True)
    if not lines:
        return section + (prompt or "")
    insert_at = 0
    for i, line in enumerate(lines[:5]):
        low = line.lstrip().lower()
        if low.startswith("роль:") or low.startswith("role:"):
            insert_at = i + 1
            break
    block = section if section.endswith("\n") else section + "\n"
    if insert_at < len(lines) and lines[insert_at].strip():
        block = block + "\n" if not block.endswith("\n\n") else block
    lines.insert(insert_at, "\n" + block if insert_at > 0 else block)
    return "".join(lines)


def detect_commit_no_verify(log_path):
    """True если в логе --no-verify/--no-verify= рядом с git commit."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception:
        return False
    if "--no-verify" not in text and "--no-verify=" not in text:
        return False
    # окно: одна строка / соседние токены с git commit
    for line in text.splitlines():
        low = line.lower()
        if "git" in low and "commit" in low and "--no-verify" in low:
            return True
        if "--no-verify" in low and ("commit" in low or "git commit" in text):
            # соседние строки: если в ±3 строках есть git commit
            pass
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "--no-verify" not in line and "--no-verify=" not in line:
            continue
        window = "\n".join(lines[max(0, i - 3):i + 4]).lower()
        if "git" in window and "commit" in window:
            return True
    return False


def _journal_open_writers(front_id, entries=None):
    """Пишущие start без end для фронта → dict id → start_entry."""
    if entries is None:
        entries = orchlib.journal_read(limit=8000)
    ended = set()
    starts = {}  # id → last start entry for this front (writing)
    for e in entries:
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        rid = e.get("id")
        if kind == "end" and rid:
            ended.add(rid)
            starts.pop(rid, None)
            continue
        if kind != "start" or not rid:
            continue
        if e.get("front") != front_id:
            continue
        engine = e.get("engine") or "local"
        if engine == "cloud":
            if not e.get("writable"):
                continue
        else:
            if e.get("readonly") is True:
                continue
            if orchlib.role_is_oversight(e.get("role")):
                continue
        if rid in ended:
            # later start after end — reopen
            ended.discard(rid)
        starts[rid] = e
    # filter ended again (chronology already handled if we process in order)
    return {rid: st for rid, st in starts.items() if rid not in ended}


def _writer_still_alive(run_id, start_entry, state, session_hint=None):
    """Живость пишущего: local=pid+starttime (in-flight start без pid — жив ≤60с);
    cloud=нет end + TTL 6ч."""
    engine = (start_entry or {}).get("engine") or "local"
    if engine == "cloud":
        ts = float((start_entry or {}).get("ts") or 0)
        if ts and (time.time() - ts) > CLOUD_WRITER_TTL_S:
            return False
        return True
    session = session_hint or (start_entry or {}).get("session")
    _log, pid_path = resolve_run_paths(state, run_id, session)
    if not os.path.isfile(pid_path):
        if session:
            _log2, pid_path2 = resolve_run_paths(state, run_id, None)
            if os.path.isfile(pid_path2):
                pid_path = pid_path2
            else:
                ts = float((start_entry or {}).get("ts") or 0)
                return bool(ts and (time.time() - ts) < 60.0)
        else:
            ts = float((start_entry or {}).get("ts") or 0)
            return bool(ts and (time.time() - ts) < 60.0)
    return run_pid_is_alive(pid_path)


def find_live_dual_writer(front_id, self_id, state, session=None):
    """Живой пишущий ран ДРУГОГО id того же фронта, иначе None.

    Parent-полковник (ORCH_RUN_ID / start.parent) не блокирует child;
    sibling → считается dual-writer.
    """
    if not front_id:
        return None
    writers = _journal_open_writers(front_id)
    skip = set()
    parent_env = orchlib.resolve_journal_parent(self_id)
    if parent_env:
        skip.add(parent_env)
    self_st = writers.get(self_id) or {}
    parent_j = self_st.get("parent")
    if parent_j:
        skip.add(parent_j)
    for rid, st in writers.items():
        if rid == self_id:
            continue
        if rid in skip:
            continue
        if _writer_still_alive(rid, st, state, session_hint=st.get("session")):
            return rid
    return None


def check_dual_writer_guard(front_id, self_id, state, session=None,
                            readonly=False, toctou=False, role=None):
    """До journal_start: другой пишущий → (13, other_id); тот же id жив → не сюда.

    toctou=True (после journal_start): отказывать self только если
    other.ts <= self.ts (младший/равный само-отказ; старший идёт дальше).
    readonly=True или role_is_oversight(role) — не берём write-lock
    (надзор без --mode plan).
    """
    if not front_id or readonly or orchlib.role_is_oversight(role):
        return None, None
    other = find_live_dual_writer(front_id, self_id, state, session=session)
    if other is None:
        return None, None
    if toctou:
        writers = _journal_open_writers(front_id)
        self_ts = float((writers.get(self_id) or {}).get("ts") or 0)
        other_ts = float((writers.get(other) or {}).get("ts") or 0)
        if not (other_ts <= self_ts):
            return None, None
    msg = "фронт %s уже имеет пишущий ран %s" % (front_id, other)
    return FRONT_DUAL_WRITER_EXIT, msg


def cmd_kill(state, run_id, session=None):
    """--kill <id>: TOMBSTONE + journal killed + kill дерева. Нет pid → tombstone+warn.

    Живость = pid∧starttime (run_pid_is_alive); mismatch/reuse → без kill_pid.
    """
    log_path, pid_path = resolve_run_paths(state, run_id, session)
    pid = read_pid_file(pid_path) if os.path.isfile(pid_path) else None
    alive = bool(os.path.isfile(pid_path) and run_pid_is_alive(pid_path))
    write_tombstone(state, run_id, prev_pid=pid, session=session, reason="manual")
    journal_killed(run_id, prev_pid=pid, session=session)
    if not alive:
        sys.stderr.write(
            "warn: --kill %s: живой pid не найден; TOMBSTONE записан "
            "(будущий retry отменён)\n" % run_id)
        return 0
    kill_pid(pid)
    # дождаться смерти
    for _ in range(20):
        time.sleep(0.1)
        if not pid_alive(pid):
            break
    else:
        kill_pid(pid)
    sys.stdout.write("killed id=%s pid=%s tombstone=ok\n" % (run_id, pid))
    return 0


def cmd_unkill(state, run_id, session=None):
    """--unkill <id>: удалить TOMBSTONE."""
    if remove_tombstone(state, run_id, session):
        sys.stdout.write("unkill: TOMBSTONE удалён id=%s\n" % run_id)
        return 0
    sys.stderr.write("unkill: TOMBSTONE не найден id=%s\n" % run_id)
    return 0


def log_progress(log_path):
    """(st_mtime, st_size) run.log; при отсутствии — (0.0, 0)."""
    try:
        st = os.stat(log_path)
        return float(st.st_mtime), int(st.st_size)
    except Exception:
        return 0.0, 0


def resolve_timers(params, stall_after=None, timeout=None, max_wall=None):
    """Приоритеты A3: stall и max_wall. Читает keys локально из params dict.

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


def apply_secret_gate(prompt, prompt_file, log_path):
    """Секрет-сканер до exe/bump. → (5, lines) | (None, [])."""
    hit = orchlib.scan_secrets(prompt)
    if not hit:
        return None, []
    line = "SECRETS_IN_PROMPT=%s, %s" % (hit, prompt_file)
    append_log(log_path, line)
    sys.stderr.write(
        "секрет в промте: вынеси в .orchestration/cursor.key / ENV; "
        "промт без секрета\n")
    return 5, [line]


def apply_front_gates(front_id, log_path):
    """Статус/бюджет после exe: closed → 6; hard → 7; lock busy → 9; успех → FRONT_RUNS.

    Бюджет: used>hard (hard>0) → BUDGET_HARD/exit 7; used>=warn →
    FRONT_BUDGET_WARN + pending_budget_warn, запуск продолжается.
    bump_front_runs RuntimeError(front-runs lock busy) → FRONT_LOCK_BUSY/exit 9.

    Возвращает (exit_code|None, log_lines). Отказы и FRONT_RUNS — сразу через
    append_log; log_lines дублируются после open(log_path,'w') перед Popen.
    """
    lines = []
    if not front_id:
        return None, lines
    front_status = getattr(orchlib, "front_status", None)
    if callable(front_status):
        status = front_status(front_id)
        if status in ("cancelled", "rejected"):
            lines.append("FRONT_CLOSED=%s" % front_id)
            for line in lines:
                append_log(log_path, line)
            return 6, lines
    bump = getattr(orchlib, "bump_front_runs", None)
    if callable(bump):
        try:
            used, warn, hard = bump(front_id)
        except RuntimeError as exc:
            msg = str(exc)
            if "front-runs lock busy" in msg:
                bline = "FRONT_LOCK_BUSY=%s" % front_id
                lines.append(bline)
                append_log(log_path, bline)
                return 9, lines
            if "front-runs closed" in msg:
                lines.append("FRONT_CLOSED=%s" % front_id)
                for line in lines:
                    append_log(log_path, line)
                return 6, lines
            raise
        line = "FRONT_RUNS=%s %s warn=%s hard=%s" % (front_id, used, warn, hard)
        lines.append(line)
        append_log(log_path, line)
        if hard > 0 and used > hard:
            hline = "BUDGET_HARD=%s %s/%s" % (front_id, used, hard)
            lines.append(hline)
            append_log(log_path, hline)
            return 7, lines
        if used >= warn:
            wline = "FRONT_BUDGET_WARN=%s %s/%s" % (front_id, used, warn)
            lines.append(wline)
            append_log(log_path, wline)
            emit = getattr(orchlib, "emit_pending_budget_warn", None)
            if callable(emit):
                emit(front_id, used, warn)
    return None, lines


def find_cursor_agent():
    for name in ("cursor-agent", "cursor-agent.exe", "cursor-agent.cmd"):
        path = shutil.which(name)
        if path:
            return path
    return None


def kill_tree(proc):
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def kill_pid(pid):
    """Убить внешний pid (не наш Popen) — detach-агент в своей сессии."""
    try:
        if os.name == "nt":
            subprocess.call(["taskkill", "/F", "/T", "/PID", str(pid)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(os.getpgid(int(pid)), signal.SIGKILL)
            except Exception:
                os.kill(int(pid), signal.SIGKILL)
    except Exception:
        pass


def has_assistant_text(tail):
    """Эвристика: stream-json событие assistant с text в content[]."""
    return '"type":"assistant"' in tail and '"type":"text"' in tail


def classify_log(log_path):
    """Классификация по хвосту лога: 0/1/3/4/UNKNOWN."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, 2)
            size = f.tell()
            # до 512KiB — длинная финальная result-строка; assistant — по последним 8k
            f.seek(max(0, size - 512 * 1024))
            data = f.read().decode("utf-8", "replace")
    except Exception:
        return "UNKNOWN"
    if not data.strip():
        return "4"
    if '"type":"result"' in data:
        after = data.split('"type":"result"')[-1]
        # subtype сразу после type в той же строке/событии
        head = after[:800]
        if '"subtype":"success"' in head:
            return "0"
        return "1"
    tail = data[-8000:]
    if has_assistant_text(tail):
        return "3"
    return "4"


def classify_after_wait(log_path, returncode):
    """После wait(): всегда по логу (rc==0 не маскирует отсутствие result)."""
    # returncode в сигнатуре для совместимости вызовов; EXIT=0 ≠ готовность
    return classify_log(log_path)


def append_log(log_path, line):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(line if line.endswith("\n") else line + "\n")
    except Exception:
        pass


def _log_trailing_incomplete(log_path):
    """Хвост файла после последнего \\n, либо None если файл пуст/кончается на \\n."""
    try:
        with open(log_path, "rb") as f:
            data = f.read()
        if not data:
            return None
        if data.endswith(b"\n"):
            return None
        # текст после последнего \\n
        idx = data.rfind(b"\n")
        chunk = data if idx < 0 else data[idx + 1:]
        return chunk.decode("utf-8", "replace")
    except Exception:
        return None


def check_compass_overflow(log_path, session, noted, allow_log_write=True):
    """Скан compass: COMPASS_OVERFLOW= + pending-флаг. Ошибки глотаем.

    noted — set (path, size) уже отмеченных в этом прогоне; мутируется.
    allow_log_write=False: скан overflows + emit_pending; в лог не пишем;
      noted не трогаем (живой агент пишет в тот же файл).
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
            marker = "COMPASS_OVERFLOW=%s:%s/%s" % (
                o["path"], o["size"], o["limit"])
            key = (o.get("path"), o.get("size"))
            if key in noted:
                continue
            if marker in present:
                noted.add(key)
                continue
            noted.add(key)
            present.add(marker)
            # обрывок == полный маркер → только \\n; иначе при грязном хвосте
            # сначала \\n, затем полная строка-маркер
            try:
                with open(log_path, "a", encoding="utf-8") as f:
                    if trailing is not None and trailing.strip() == marker:
                        f.write("\n")
                        trailing = None
                    else:
                        if trailing is not None:
                            f.write("\n")
                            trailing = None
                        f.write(marker if marker.endswith("\n") else marker + "\n")
            except Exception:
                pass
            wrote = True
        if wrote:
            orchlib.emit_pending_compass_guard(session, overflows)
    except Exception:
        pass


def build_agent_cmd(exe, model, extra, readonly=False):
    """Собрать argv cursor-agent. readonly → `--mode plan` (барьер A).

    Флаг -p/--print остаётся; текст промта в argv НЕ кладётся — только stdin.
    readonly — только аналитика (чтение+выжимка в ответ), не командные роли.
    """
    cmd = [exe, "-p", "--force", "--model", model,
           "--output-format", "stream-json"]
    if readonly:
        cmd.extend(["--mode", "plan"])
    return cmd + list(extra or [])


def feed_prompt_stdin(proc, prompt):
    """Записать текст промта в stdin дочернего cursor-agent и закрыть PIPE."""
    data = (prompt or "").encode("utf-8")
    try:
        proc.stdin.write(data)
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass


def is_readonly_env():
    """Readonly-флаг прогона (наследуется watcher/retry через ORCH_READONLY)."""
    return os.environ.get("ORCH_READONLY") == "1"


def wait_child(proc, log_fh, log_path, stall_s, max_wall_s, yield_after=0,
               wall_deadline=None, last_mtime=None, last_size=None):
    """Ждём дочерний proc; dual-timer: stall(log mtime+size) / max_wall fuse.

    stall_s — тишина run.log (сброс ростом st_mtime или st_size).
    max_wall_s — fuse от t0; wall_deadline — абсолют (retry/yield передают остаток).
    Стартовый якорь stall = st_mtime лога (НЕ now()), если last_* не переданы.
    yield_after — только UX-уступка («YIELDED»), таймеры не сбрасывает.
    Возвращает код ('124' STALL / '125' WALL / classify / 'YIELDED').
    """
    t0 = time.time()
    if wall_deadline is None:
        wall_deadline = t0 + float(max_wall_s)
    if last_mtime is None or last_size is None:
        lm, ls = log_progress(log_path)
        last_mtime = lm if last_mtime is None else last_mtime
        last_size = ls if last_size is None else last_size
    # якорь тишины = mtime последней записи (0 → t0, лог ещё не создан)
    last_progress_at = float(last_mtime) if last_mtime else t0
    started = t0  # только для yield_after
    while proc.poll() is None:
        now = time.time()
        if yield_after and (now - started) >= yield_after:
            try:
                log_fh.close()
            except Exception:
                pass
            return "YIELDED"
        if now >= wall_deadline:
            kill_tree(proc)
            try:
                log_fh.close()
            except Exception:
                pass
            append_log(log_path,
                       "WALL: max-wall fuse (deadline reached)")
            return "125"
        mtime, size = log_progress(log_path)
        if mtime > last_mtime or size > last_size:
            last_mtime, last_size = mtime, size
            last_progress_at = float(mtime) if mtime else now
        elif stall_s and (now - last_progress_at) >= float(stall_s):
            kill_tree(proc)
            try:
                log_fh.close()
            except Exception:
                pass
            append_log(log_path,
                       "STALL: no run.log progress (mtime+size) for %ss"
                       % stall_s)
            return "124"
        time.sleep(1)
    rc = proc.returncode
    try:
        log_fh.close()
    except Exception:
        pass
    return classify_after_wait(log_path, rc)


def agent_popen_kwargs(run_id=None, front=None):
    """detach_popen_kwargs + env ORCH_RUN_ID / ORCH_FRONT для дочернего cursor-agent."""
    kwargs = detach_popen_kwargs()
    env = dict(os.environ)
    rid = run_id or env.get("ORCH_RUN_ID")
    if rid:
        env["ORCH_RUN_ID"] = str(rid)
    if front:
        env["ORCH_FRONT"] = str(front)
    else:
        env.pop("ORCH_FRONT", None)
    kwargs["env"] = env
    return kwargs


def journal_start(run_id, prompt_file, front, role, engine="local",
                  readonly=False, no_front_reason=None, auto=False,
                  session=None):
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
    if session is not None:
        entry["session"] = session
    if no_front_reason:
        entry["no_front_reason"] = no_front_reason
    if readonly:
        entry["readonly"] = True
    if auto or os.environ.get("ORCH_RUN_AUTO") == "1":
        entry["auto"] = True
    orchlib.journal_append(entry)


def journal_end(run_id, log_path, exit_code, session=None, front=None,
                readonly=False, no_verify_hit=None):
    """Запись kind=end в journal (ошибки глотает orchlib)."""
    if not run_id:
        return
    verdict, gates = orchlib.journal_log_meta(log_path)
    # --no-verify-детектор (end-гейт): chip + пометка; exit не менять
    hit = no_verify_hit
    if hit is None and (not readonly) and front:
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
    # S2: автопрокурор на волну (после end).
    orchlib.maybe_auto_prosecutor_after_end(run_id)


def journal_gate_refuse(run_id, prompt_file, front, role, log_path, exit_code,
                        engine="local", readonly=False, no_front_reason=None,
                        session=None):
    """start+end при отказе гейта (exit 5/6/7/8/9) — без дыры в journal."""
    journal_start(run_id, prompt_file, front, role, engine=engine,
                  readonly=readonly, no_front_reason=no_front_reason,
                  session=session)
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
    # finally-семантика: снять lockdir на любом завершении (в т.ч. gate-refuse
    # автопрокурора при BUDGET_HARD и т.п.); спавн — только journal_end.
    orchlib.release_auto_prosecutor_lock_if_any(run_id)


def start_watcher(pid, log_path, pid_path, stall_s, wall_deadline,
                  last_mtime, last_size, run_prompt_file, model,
                  session=None, state=None, run_id=None, front=None,
                  readonly=False):
    """Запуск detached --__watch (общий для --detach и авто-уступки).

    Минимальная схема argv dual-timer (A3):
      --__watch <pid> <log_path> <pid_path> <stall_s> <wall_deadline>
                <last_mtime> <last_size> [run_prompt_file [model [session
                [state [run_id [front [readonly]]]]]]]
    wall_deadline — абсолютный unix time (t0+max_wall; yield/retry = тот же
    абсолют, НЕ полный запас заново). last_mtime/last_size — стартовый якорь
    stall (= st_mtime/st_size лога, НЕ now()).
    """
    watch_cmd = [sys.executable, os.path.abspath(__file__), "--__watch",
                 str(pid), log_path, pid_path, str(int(stall_s)),
                 str(wall_deadline), str(last_mtime), str(int(last_size)),
                 run_prompt_file or "", model, session or "",
                 state or "", run_id or "", front or "",
                 "1" if readonly else "0"]
    # наследует ORCH_RUN_ID из os.environ (выставлен родителем до вызова)
    wkwargs = detach_popen_kwargs()
    wenv = dict(os.environ)
    if front:
        wenv["ORCH_FRONT"] = str(front)
    wkwargs["env"] = wenv
    subprocess.Popen(watch_cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **wkwargs)


def run_retry_child(run_prompt_file, log_path, pid_path, stall_s,
                    wall_deadline, model, extra, front=None):
    """Рестарт cursor-agent как потомок; max_wall = остаток (wall_deadline)."""
    exe = find_cursor_agent()
    if not exe:
        return "4"
    try:
        with open(run_prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    except Exception:
        return "4"
    log_fh = open(log_path, "a", encoding="utf-8")
    cmd = build_agent_cmd(exe, model, extra, readonly=is_readonly_env())
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log_fh,
                            stderr=subprocess.STDOUT,
                            **agent_popen_kwargs(front=front))
    feed_prompt_stdin(proc, prompt)
    try:
        write_pid_file(pid_path, proc.pid)
    except Exception:
        pass
    # остаток max_wall через абсолютный wall_deadline; stall якорь = текущий лог
    remaining = max(0.0, float(wall_deadline) - time.time())
    lm, ls = log_progress(log_path)
    return wait_child(proc, log_fh, log_path, stall_s, remaining,
                      wall_deadline=wall_deadline,
                      last_mtime=lm, last_size=ls)


def apply_retries(code, log_path, pid_path, stall_s, wall_deadline,
                  run_prompt_file, retry_on_fail, model, extra,
                  state=None, run_id=None, session=None, front=None):
    """Автоперезапуск при 4/124; TOMBSTONE → не респаун; 125 WALL не в RETRYABLE."""
    retries = 0
    max_r = int(retry_on_fail)
    while str(code) in RETRYABLE and retries < max_r:
        if state and run_id and tombstone_exists(state, run_id, session):
            append_log(log_path, "TOMBSTONE: retry отменён (kill вручную)")
            break
        retries += 1
        append_log(log_path, "RETRY=%d/%d (prev EXIT=%s)" % (retries, max_r, code))
        code = run_retry_child(run_prompt_file, log_path, pid_path,
                               stall_s, wall_deadline, model, extra,
                               front=front)
    return code


def watch(pid, log_path, pid_path, stall_s, wall_deadline, last_mtime,
          last_size, run_prompt_file=None, model="auto", session=None,
          state=None, run_id=None, front=None, readonly=False):
    """Detach-watcher: dual-timer stall(mtime+size) / max_wall; retry 4/124."""
    last_mtime = float(last_mtime)
    last_size = int(last_size)
    # стартовый якорь = переданный st_mtime лога (НЕ now())
    last_progress_at = last_mtime if last_mtime else time.time()
    kill_code = None  # "124" | "125"
    noted = set()
    while True:
        time.sleep(2)
        check_compass_overflow(log_path, session, noted, allow_log_write=False)
        now = time.time()
        alive = pid_alive(int(pid))
        if not alive:
            break
        if now >= float(wall_deadline):
            kill_pid(pid)
            kill_code = "125"
            append_log(log_path, "WALL: max-wall fuse (deadline reached)")
            for _ in range(15):
                time.sleep(0.4)
                if not pid_alive(int(pid)):
                    break
            break
        mtime, size = log_progress(log_path)
        if mtime > last_mtime or size > last_size:
            last_mtime, last_size = mtime, size
            last_progress_at = float(mtime) if mtime else now
        elif stall_s and (now - last_progress_at) >= float(stall_s):
            kill_pid(pid)
            kill_code = "124"
            append_log(log_path,
                       "STALL: no run.log progress (mtime+size) for %ss"
                       % stall_s)
            for _ in range(15):
                time.sleep(0.4)
                if not pid_alive(int(pid)):
                    break
            break

    # финальная проверка — агент уже не пишет; дописываем маркеры в лог
    check_compass_overflow(log_path, session, noted, allow_log_write=True)

    if kill_code is not None:
        code = kill_code
    else:
        code = classify_log(log_path)

    try:
        params = orchlib.load_params()
    except ValueError as e:
        sys.stderr.write("%s\n" % e)
        sys.exit(10)
    retry_on_fail = int(params.get("execution", {}).get("retry_on_fail", 1))
    st = state or orchlib.find_state_dir()
    rid = run_id or os.environ.get("ORCH_RUN_ID")
    if run_prompt_file:
        code = apply_retries(code, log_path, pid_path, stall_s, wall_deadline,
                             run_prompt_file, retry_on_fail, model, [],
                             state=st, run_id=rid, session=session,
                             front=front)

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\nEXIT=%s\n" % code)
        os.unlink(pid_path)
    except Exception:
        pass
    journal_end(rid, log_path, code, session=session, front=front,
                readonly=readonly)


def detach_popen_kwargs():
    """Флаги отсоединения от консоли: start_new_session (POSIX) / DETACHED (Windows)."""
    if os.name != "nt":
        return {"start_new_session": True}
    # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS — не умирать при закрытии консоли
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008}


def resolve_run_paths(state, run_id, session=None):
    """Пути лога и pid: state/cursor-run-<id>.* или sessions/<sid>/runs/<id>/."""
    if session:
        run_dir = os.path.join(orchlib.session_dir(session), "runs", run_id)
        return (os.path.join(run_dir, "run.log"),
                os.path.join(run_dir, "run.pid"))
    return (os.path.join(state, "cursor-run-%s.log" % run_id),
            os.path.join(state, "cursor-run-%s.pid" % run_id))


def check_duplicate_id_guard(state, run_id, session=None, force=False):
    """Отказ exit 11, если pid-файл есть и процесс жив (pid+starttime). --force обходит."""
    if force:
        return None
    _log_path, pid_path = resolve_run_paths(state, run_id, session)
    if not os.path.isfile(pid_path):
        return None
    if not run_pid_is_alive(pid_path):
        return None
    pid = read_pid_file(pid_path)
    sys.stderr.write(
        "id %s занят живым прогоном (pid %s); используйте другой id или --force\n"
        % (run_id, pid))
    return 11


def last_marker_line(log_path):
    """Последняя строка EXIT=... или RETRY=... из лога (если есть)."""
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return None
    marker = None
    for line in lines:
        s = line.strip()
        if s.startswith("EXIT=") or s.startswith("RETRY="):
            marker = s
    return marker


def collect_run_info(run_id, log_path, pid_path):
    """Сводка по прогону; отсутствующий лог → None (caller печатает «не найден»)."""
    if not log_path or not os.path.isfile(log_path):
        return None
    try:
        age_s = int(max(0, time.time() - os.path.getmtime(log_path)))
    except Exception:
        age_s = None
    pid = read_pid_file(pid_path) if pid_path else None
    alive = bool(pid is not None and (
        run_pid_is_alive(pid_path) if pid_path and os.path.isfile(pid_path)
        else pid_alive(pid)))
    analyzed = verdict_mod.analyze(log_path)
    # exit из EXIT=; если маркера ещё нет — классификация по хвосту (classify_log)
    exit_code = analyzed.get("exit") or "UNKNOWN"
    if exit_code == "UNKNOWN" and not alive:
        exit_code = classify_log(log_path)
    # report_present: analyze уже использует эвристику assistant/text;
    # при сомнении — has_assistant_text на хвосте
    report_present = bool(analyzed.get("report_present"))
    if not report_present:
        try:
            with open(log_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 8000))
                tail = f.read().decode("utf-8", "replace")
            report_present = has_assistant_text(tail)
        except Exception:
            pass
    return {
        "id": run_id,
        "log": log_path,
        "pid": pid,
        "pid_alive": alive,
        "exit": exit_code,
        "retries": int(analyzed.get("retries") or 0),
        "report_present": report_present,
        "verdict": analyzed.get("verdict") or "NONE",
        "age_s": age_s,
        "marker": last_marker_line(log_path),
    }


def cmd_status(state, run_id, session=None):
    """--status: компактный отчёт + JSON; всегда exit 0."""
    log_path, pid_path = resolve_run_paths(state, run_id, session)
    info = collect_run_info(run_id, log_path, pid_path)
    if info is None:
        sys.stdout.write("не найден\n")
        payload = {
            "id": run_id,
            "log": None,
            "pid_alive": False,
            "exit": None,
            "retries": 0,
            "report_present": False,
            "verdict": None,
            "age_s": None,
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
        return 0
    pid_s = "нет" if info["pid"] is None else (
        "%s (%s)" % (info["pid"], "жив" if info["pid_alive"] else "нет"))
    marker = info["marker"] or "(нет EXIT/RETRY)"
    sys.stdout.write("лог: %s\n" % info["log"])
    sys.stdout.write("pid: %s\n" % pid_s)
    sys.stdout.write("маркер: %s\n" % marker)
    sys.stdout.write("возраст: %ss\n" % info["age_s"])
    sys.stdout.write("отчёт: %s  вердикт: %s  exit: %s  retries: %s\n" % (
        "да" if info["report_present"] else "нет",
        info["verdict"], info["exit"], info["retries"]))
    payload = {
        "id": info["id"],
        "log": info["log"],
        "pid_alive": info["pid_alive"],
        "exit": info["exit"],
        "retries": info["retries"],
        "report_present": info["report_present"],
        "verdict": info["verdict"],
        "age_s": info["age_s"],
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False,
                                separators=(",", ":")) + "\n")
    return 0


def iter_state_logs(state, session=None):
    """Список (id, log_path, pid_path, mtime) — state-уровень + runs при --session."""
    entries = []
    for log_path in glob.glob(os.path.join(state, "cursor-run-*.log")):
        base = os.path.basename(log_path)
        # cursor-run-<id>.log
        run_id = base[len("cursor-run-"):-len(".log")]
        if not run_id:
            continue
        pid_path = os.path.join(state, "cursor-run-%s.pid" % run_id)
        try:
            mtime = os.path.getmtime(log_path)
        except Exception:
            continue
        entries.append((run_id, log_path, pid_path, mtime))
    if session:
        runs_root = os.path.join(orchlib.session_dir(session), "runs")
        for log_path in glob.glob(os.path.join(runs_root, "*", "run.log")):
            run_id = os.path.basename(os.path.dirname(log_path))
            pid_path = os.path.join(os.path.dirname(log_path), "run.pid")
            try:
                mtime = os.path.getmtime(log_path)
            except Exception:
                continue
            entries.append((run_id, log_path, pid_path, mtime))
    entries.sort(key=lambda e: e[3], reverse=True)
    return entries


def cmd_list(state, session=None):
    """--list: таблица + JSON на каждый прогон; свежие сверху; exit 0."""
    entries = iter_state_logs(state, session)
    if not entries:
        sys.stdout.write("не найден\n")
        return 0
    sys.stdout.write("id | exit | retries | age_s | verdict\n")
    for run_id, log_path, pid_path, _mtime in entries:
        info = collect_run_info(run_id, log_path, pid_path)
        if info is None:
            continue
        sys.stdout.write("%s | %s | %s | %s | %s\n" % (
            info["id"], info["exit"], info["retries"],
            info["age_s"], info["verdict"]))
        payload = {
            "id": info["id"],
            "log": info["log"],
            "pid_alive": info["pid_alive"],
            "exit": info["exit"],
            "retries": info["retries"],
            "report_present": info["report_present"],
            "verdict": info["verdict"],
            "age_s": info["age_s"],
        }
        sys.stdout.write(json.dumps(payload, ensure_ascii=False,
                                    separators=(",", ":")) + "\n")
    return 0


def _write_failed_oracle_audit(path, probe, cmd, exit_code, critic_id, artifact):
    """Аудит-квитанция при oracle_match=false.

    write_probe_receipt волны A откатывает запись: parse требует oracle_match
    true (снятие probes_missing). Автопуть --probe обязан оставить след
    провала оракула в том же каноне полей + generator (не второй writer).
    """
    import hashlib
    cmd_s = "" if cmd is None else str(cmd)
    ts = time.time()
    generator = "orch-probe-receipt/%s" % orchlib.kit_version()
    cmd_sha = hashlib.sha256(cmd_s.encode("utf-8")).hexdigest()
    block = (
        "probe: %s\n"
        "cmd: %s\n"
        "exit: %s\n"
        "oracle_match: false\n"
        "ts: %s\n"
        "critic_id: %s\n"
        "artifact: %s\n"
        "generator: %s\n"
        "cmd_sha256: %s\n"
    ) % (probe, cmd_s, int(exit_code), ts, critic_id, artifact,
         generator, cmd_sha)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(block)
    return path


def cmd_probe(a, state):
    """--probe CMD [--oracle N]: исполнить CMD, квитанция §3, без cursor-agent.

    Журнал — существующий start/end; новых полей нет.
    Dual-writer/agent-guards не применяются: проба не запускает пишущий агент.
    """
    run_id = a.id
    session = a.session
    cmd = a.probe
    oracle = int(a.oracle)
    # роль кода — чтобы start того же id не выбивал волну из скопа probes_missing
    role = orchlib.normalize_journal_role(a.role) if a.role else "code/coder.md"

    if session:
        run_dir = os.path.join(orchlib.session_dir(session), "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        log_path = os.path.join(run_dir, "run.log")
        receipt_path = os.path.join(run_dir, "probe-receipt.md")
    else:
        os.makedirs(state, exist_ok=True)
        run_dir = os.path.join(state, "runs", run_id)
        os.makedirs(run_dir, exist_ok=True)
        log_path = os.path.join(state, "cursor-run-%s.log" % run_id)
        receipt_path = os.path.join(run_dir, "probe-receipt.md")

    front_out, no_front_reason, front_refuse = orchlib.resolve_front_launch(
        a.front, a.no_front)
    if front_refuse is not None:
        append_log(log_path, front_refuse)
        sys.stderr.write(front_refuse + "\n")
        journal_gate_refuse(
            run_id, None, None, role, log_path,
            orchlib.FRONT_REQUIRED_EXIT, session=session)
        return orchlib.FRONT_REQUIRED_EXIT
    a.front = front_out

    # гейты фронта — apply_front_gates; dual-writer — нет (probe не агент-писатель)
    gate_rc, _ = apply_front_gates(a.front, log_path)
    if gate_rc is not None:
        journal_gate_refuse(
            run_id, None, a.front, role, log_path, gate_rc,
            no_front_reason=no_front_reason, session=session)
        return gate_rc

    # parent ORCH_RUN_ID ещё не перезаписан — journal_start зафиксирует parent
    journal_start(run_id, None, a.front, role, engine="local",
                  no_front_reason=no_front_reason, session=session)
    os.environ["ORCH_RUN_ID"] = run_id
    if a.front:
        os.environ["ORCH_FRONT"] = a.front
    else:
        os.environ.pop("ORCH_FRONT", None)

    env = dict(os.environ)
    env["ORCH_RUN_ID"] = str(run_id)
    if a.front:
        env["ORCH_FRONT"] = str(a.front)
    else:
        env.pop("ORCH_FRONT", None)

    try:
        with open(log_path, "w", encoding="utf-8") as log_fh:
            log_fh.write("PROBE_CMD=%s\n" % cmd)
    except Exception:
        pass

    try:
        proc = subprocess.run(
            cmd, shell=True, cwd=os.getcwd(), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        exit_code = int(proc.returncode if proc.returncode is not None else 1)
        out = proc.stdout or b""
        if out:
            try:
                text = out.decode("utf-8", "replace")
            except Exception:
                text = ""
            if text:
                append_log(log_path, text.rstrip("\n"))
    except Exception as e:
        append_log(log_path, "PROBE_EXEC_ERROR=%s" % e)
        exit_code = 1

    oracle_match = (exit_code == oracle)
    ok, info = orchlib.write_probe_receipt(
        probe=run_id,
        cmd=cmd,
        exit_code=exit_code,
        oracle_match=oracle_match,
        critic_id=run_id,
        artifact=run_id,
        run_id=run_id,
        path=receipt_path,
        state=state,
    )
    if (not ok) and (not oracle_match) and str(info).startswith(
            "validate_failed:"):
        # writer A откатывает failed-oracle (и exit_ne_oracle от §1 артефакта);
        # аудит-след обязателен для --probe при провале оракула
        info = _write_failed_oracle_audit(
            receipt_path, probe=run_id, cmd=cmd, exit_code=exit_code,
            critic_id=run_id, artifact=run_id)
        ok = True

    if oracle_match:
        wrapper_exit = 0
    else:
        wrapper_exit = exit_code if exit_code != 0 else 1

    append_log(log_path, "EXIT=%s" % wrapper_exit)
    journal_end(run_id, log_path, wrapper_exit, session=session, front=a.front)

    if not ok:
        sys.stderr.write("probe receipt failed: %s\n" % info)
        sys.stdout.write("fail %s\n" % info)
        return 1

    status = "ok" if oracle_match else "fail"
    sys.stdout.write("%s %s\n" % (status, info))
    return wrapper_exit


def main():
    orchlib.utf8_stdio()
    if len(sys.argv) > 1 and sys.argv[1] == "--__watch":
        # Dual-timer argv (см. start_watcher):
        # --__watch pid log pid_path stall_s wall_deadline last_mtime last_size
        #           [run_prompt [model [session [state [run_id [front [ro]]]]]]]
        argv = sys.argv
        run_prompt = argv[9] if len(argv) > 9 else None
        if run_prompt == "":
            run_prompt = None
        model = argv[10] if len(argv) > 10 else "auto"
        session = argv[11] if len(argv) > 11 and argv[11] else None
        wstate = argv[12] if len(argv) > 12 and argv[12] else None
        run_id = argv[13] if len(argv) > 13 and argv[13] else None
        front = argv[14] if len(argv) > 14 and argv[14] else None
        readonly = (len(argv) > 15 and argv[15] in ("1", "true", "True"))
        watch(argv[2], argv[3], argv[4],
              int(argv[5]), float(argv[6]), float(argv[7]), int(float(argv[8])),
              run_prompt, model, session,
              state=wstate, run_id=run_id, front=front, readonly=readonly)
        return 0

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", default=None, help="метка прогона (файлы логов/промтов)")
    ap.add_argument("--front", default=None,
                    help="id фронта: статус cancelled/rejected; бюджет warn/hard "
                         "(warn=датчик, hard=стоп при hard>0)")
    ap.add_argument("--no-front", default=None, metavar="REASON",
                    help="запуск вне фронта с причиной (journal no_front_reason); "
                         "при hierarchy=off можно без --front/--no-front")
    ap.add_argument("--session", default=None,
                    help="id сессии: все файлы прогона лягут в .orchestration/sessions/<id>/runs/<run>/")
    ap.add_argument("--prompt-file", default=None,
                    help="по умолчанию <state>/prompt-<id>.md")
    ap.add_argument("--stall-after", type=int, default=None,
                    help="сек без прогресса run.log (mtime+size); канон stall")
    ap.add_argument("--timeout", type=int, default=None,
                    help="алиас --stall-after: stall/no-output, не wall-clock")
    ap.add_argument("--max-wall", type=int, default=None,
                    help="fuse: абсолютный потолок wall-clock от старта агента, сек")
    ap.add_argument("--detach", action="store_true", help="фон: pid-файл, не ждать")
    ap.add_argument("--yield-after", type=int, default=480,
                    help="сек foreground-ожидания до авто-уступки в фон "
                         "(дефолт 480; 0 = ждать до конца/таймаута); "
                         "UX-уступка, stall/max_wall не сбрасывает")
    ap.add_argument("--status", action="store_true",
                    help="статус прогона --id (лог/pid/exit/вердикт); exit 0")
    ap.add_argument("--list", action="store_true",
                    help="список прогонов state (и runs при --session); exit 0")
    ap.add_argument("--kill", default=None, metavar="ID",
                    help="точный id: TOMBSTONE + journal killed + kill дерева")
    ap.add_argument("--unkill", default=None, metavar="ID",
                    help="удалить TOMBSTONE для id (явное разрешение retry)")
    ap.add_argument("--no-reground-line", action="store_true",
                    help="не доклеивать строку самопроверки в промт")
    ap.add_argument("--role", default=None,
                    help="роль (приоритет над шапкой); иначе «роль: path.md» в первых 3 строках промта")
    ap.add_argument("--model", default="auto", help="всегда auto (доктрина)")
    ap.add_argument("--readonly", action="store_true",
                    help="барьер A: --mode plan; journal readonly=true; "
                         "только аналитика (чтение+выжимка), не командные роли")
    ap.add_argument("--force", action="store_true",
                    help="обойти гард дубль-id (exit 11); гейты 5–9 не затрагивает")
    ap.add_argument("--allow-unknown-role", action="store_true",
                    help="обойти валидацию роли по каталогу кита (exit 12); "
                         "для технического смоука")
    ap.add_argument("--probe", default=None, metavar="CMD",
                    help="исполнить CMD (shell), записать квитанцию §3; "
                         "без --prompt-file — прогон-проба без cursor-agent")
    ap.add_argument("--oracle", type=int, default=0,
                    help="ожидаемый exit CMD (default 0); "
                         "mismatch → ненулевой exit обёртки")
    ap.add_argument("extra", nargs="*", help="доп. флаги cursor-agent наперед")
    a = ap.parse_args()

    state = orchlib.find_state_dir()
    # Ранний отказ и для лёгких --list/--status (иначе обходят load_params).
    try:
        params = orchlib.load_params()
    except ValueError as e:
        sys.stderr.write("%s\n" % e)
        return 10
    if a.list:
        return cmd_list(state, a.session)
    if a.status:
        if not a.id:
            sys.stderr.write("--status требует --id\n")
            return 2
        return cmd_status(state, a.id, a.session)
    if a.kill:
        return cmd_kill(state, a.kill, a.session)
    if a.unkill:
        return cmd_unkill(state, a.unkill, a.session)
    if not a.id:
        ap.error("--id обязателен (кроме --list/--kill/--unkill/--status)")

    # TTL/GC tombstones при старте любого прогона
    sweep_tombstones(state)

    # Гард дубль-id: после --status/--list, до создания процессов.
    dup_rc = check_duplicate_id_guard(state, a.id, a.session, force=a.force)
    if dup_rc is not None:
        return dup_rc

    os.makedirs(state, exist_ok=True)

    # --probe без --prompt-file → прогон-проба без cursor-agent
    if a.probe is not None and a.prompt_file is None:
        return cmd_probe(a, state)

    run_dir = os.path.join(state, "prompt-%s" % a.id)  # совместимость без --session
    if a.session:
        run_dir = os.path.join(orchlib.session_dir(a.session), "runs", a.id)
        os.makedirs(run_dir, exist_ok=True)
        if not a.prompt_file:
            a.prompt_file = os.path.join(run_dir, "prompt.md")

    prompt_file = a.prompt_file or os.path.join(state, "prompt-%s.md" % a.id)
    if not os.path.exists(prompt_file):
        sys.stderr.write("промт-файл не найден: %s\n" % prompt_file)
        return 2
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not prompt.strip():
        sys.stderr.write("промт-файл пуст: %s\n" % prompt_file)
        return 2

    if a.session:
        run_prompt_file = os.path.join(run_dir, "prompt.run.md")
        log_path = os.path.join(run_dir, "run.log")
        pid_path = os.path.join(run_dir, "run.pid")
    else:
        run_prompt_file = os.path.join(state, "prompt-%s.run.md" % a.id)
        log_path = os.path.join(state, "cursor-run-%s.log" % a.id)
        pid_path = os.path.join(state, "cursor-run-%s.pid" % a.id)

    # Порядок: (а) роль по каталогу → 12; (б) FRONT_REQUIRED → 8; (в) секрет → 5;
    # (г) exe → 3; (д) front/bump → 6/7/FRONT_RUNS; (е) dual-writer → 13.
    # Гейт-отказы: journal start+end.
    role = orchlib.resolve_run_role(a.role, prompt)
    readonly = bool(a.readonly)
    if role is not None and not a.allow_unknown_role:
        role_path = os.path.join(
            orchlib.KIT_DIR, "skills", "orchestration", "references", "roles",
            role)
        if not os.path.isfile(role_path):
            msg = ("роль не найдена в каталоге кита: %s (проверьте _index.md); "
                   "для технического смоука — --allow-unknown-role" % role)
            append_log(log_path, msg)
            sys.stderr.write(msg + "\n")
            journal_gate_refuse(
                a.id, prompt_file, None, role, log_path, 12, readonly=readonly,
                session=a.session)
            return 12
    front_out, no_front_reason, front_refuse = orchlib.resolve_front_launch(
        a.front, a.no_front)
    if front_refuse is not None:
        append_log(log_path, front_refuse)
        sys.stderr.write(front_refuse + "\n")
        journal_gate_refuse(
            a.id, prompt_file, None, role, log_path,
            orchlib.FRONT_REQUIRED_EXIT, readonly=readonly, session=a.session)
        return orchlib.FRONT_REQUIRED_EXIT
    # Надзор: --no-front не exemption; нужен --front (FRONT_REQUIRED не ослабляем).
    if orchlib.role_is_oversight(role) and not front_out:
        front_refuse = (
            "FRONT_REQUIRED: укажите --front <id> "
            "(надзор не освобождается через --no-front)")
        append_log(log_path, front_refuse)
        sys.stderr.write(front_refuse + "\n")
        journal_gate_refuse(
            a.id, prompt_file, None, role, log_path,
            orchlib.FRONT_REQUIRED_EXIT, readonly=readonly, session=a.session)
        return orchlib.FRONT_REQUIRED_EXIT
    a.front = front_out

    sec_rc, _sec_lines = apply_secret_gate(prompt, prompt_file, log_path)
    if sec_rc is not None:
        journal_gate_refuse(a.id, prompt_file, a.front, role, log_path, sec_rc,
                            readonly=readonly, no_front_reason=no_front_reason,
                            session=a.session)
        return sec_rc

    exe = find_cursor_agent()
    if not exe:
        sys.stderr.write("cursor-agent не найден в PATH\n")
        return 3

    gate_rc, gate_lines = apply_front_gates(a.front, log_path)
    if gate_rc is not None:
        journal_gate_refuse(a.id, prompt_file, a.front, role, log_path, gate_rc,
                            readonly=readonly, no_front_reason=no_front_reason,
                            session=a.session)
        return gate_rc

    # Dual-writer ДО journal_start (отказ — journal_gate_refuse-пара)
    dual_rc, dual_msg = check_dual_writer_guard(
        a.front, a.id, state, session=a.session, readonly=readonly,
        role=role)
    if dual_rc is not None:
        append_log(log_path, dual_msg)
        sys.stderr.write(dual_msg + "\n")
        journal_chip("multi_write_front", front=a.front, run_id=a.id,
                     extra={"other_id": dual_msg.rsplit(" ", 1)[-1]})
        journal_gate_refuse(a.id, prompt_file, a.front, role, log_path, dual_rc,
                            readonly=readonly, no_front_reason=no_front_reason,
                            session=a.session)
        return dual_rc

    # летописец: parent до перезаписи ORCH_RUN_ID; start до Popen
    prompt_abs = os.path.abspath(prompt_file)
    if readonly:
        os.environ["ORCH_READONLY"] = "1"
    elif "ORCH_READONLY" in os.environ:
        del os.environ["ORCH_READONLY"]
    journal_start(a.id, prompt_file, a.front, role, engine="local",
                  readonly=readonly, no_front_reason=no_front_reason,
                  session=a.session)
    os.environ["ORCH_RUN_ID"] = a.id
    if a.front:
        os.environ["ORCH_FRONT"] = a.front
    else:
        os.environ.pop("ORCH_FRONT", None)

    # TOCTOU: сразу после journal_start до Popen — младший само-отказ
    dual_rc2, dual_msg2 = check_dual_writer_guard(
        a.front, a.id, state, session=a.session, readonly=readonly,
        toctou=True, role=role)
    if dual_rc2 is not None:
        append_log(log_path, dual_msg2)
        sys.stderr.write(dual_msg2 + "\n")
        write_tombstone(state, a.id, prev_pid=None, session=a.session,
                        reason="dual_writer_toctou")
        journal_chip("multi_write_front", front=a.front, run_id=a.id)
        # end-запись (start уже есть) — без Popen / без SIGKILL чужого
        verdict, gates = orchlib.journal_log_meta(log_path)
        orchlib.journal_append({
            "ts": time.time(), "kind": "end", "id": a.id,
            "exit": dual_rc2, "verdict": verdict, "gates": gates,
            **({"session": a.session} if a.session else {}),
        })
        orchlib.release_auto_prosecutor_lock_if_any(a.id)
        return dual_rc2

    if not a.no_reground_line:
        prompt += REGROUND_LINE.format(path=prompt_abs)
    # A2-манифест: НЕ-readonly + --front
    if (not readonly) and a.front:
        prompt = inject_a2_ownership(prompt, a.front, state_dir=state)
    # F-RULES R2/R4: tried-before + прецеденты в КОПИЮ промта, до двигателя
    komu = orchlib.role_to_komu(role)
    prec_lines = []
    if komu:
        try:
            extra = {"kogda": "launch", "role": role, "caller": "run-exec"}
            tried = orchlib.format_tried_before_lines(
                komu, role=role, caller="run-exec", extra=extra,
                task_hint=(prompt or "")[:160])
            prec = orchlib.format_precedent_lines(
                komu, prompt_text=prompt, extra=extra)
            # суммарно ≤3 строк (tried-before сначала)
            prec_lines = (tried + prec)[:3]
        except Exception as e:
            sys.stderr.write("precedent inject failed: %s\n" % e)
            prec_lines = []
    if prec_lines:
        prompt = prompt.rstrip() + "\n\n" + "\n".join(prec_lines) + "\n"
    with open(run_prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    stall_s, max_wall_s = resolve_timers(
        params, stall_after=a.stall_after, timeout=a.timeout,
        max_wall=a.max_wall)
    retry_on_fail = int(params.get("execution", {}).get("retry_on_fail", 1))
    cmd = build_agent_cmd(exe, a.model, a.extra, readonly=readonly)

    # Truncate: переносим gate success markers в начало лога (FRONT_RUNS уже
    # был append'нут в apply_front_gates — без rewrite open('w') стёр бы его).
    log_fh = open(log_path, "w", encoding="utf-8")
    for line in gate_lines:
        log_fh.write(line if line.endswith("\n") else line + "\n")
    log_fh.flush()
    kwargs = agent_popen_kwargs(a.id, front=a.front)
    # t0 = первый старт агента; wall_deadline абсолютен на весь прогон+retry
    t0 = time.time()
    wall_deadline = t0 + float(max_wall_s)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=log_fh,
                            stderr=subprocess.STDOUT, **kwargs)
    feed_prompt_stdin(proc, prompt)

    try:
        write_pid_file(pid_path, proc.pid)
    except Exception:
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))

    lm, ls = log_progress(log_path)
    if a.detach:
        # watcher допишет EXIT= и при 4/124 рестартнет агента как своего потомка
        start_watcher(proc.pid, log_path, pid_path, stall_s, wall_deadline,
                      lm, ls, run_prompt_file, a.model, a.session,
                      state=state, run_id=a.id, front=a.front,
                      readonly=readonly)
        sys.stdout.write("started pid=%s log=%s\n" % (proc.pid, log_path))
        # fd родителя: потомок держит свой dup; без close — утечка в detach
        log_fh.close()
        return 0

    # foreground: pid-файл уже записан; при уступке watcher его удалит
    code = wait_child(proc, log_fh, log_path, stall_s, max_wall_s,
                      yield_after=a.yield_after, wall_deadline=wall_deadline,
                      last_mtime=lm, last_size=ls)
    if code == "YIELDED":
        # абсолютный wall_deadline + stall_s + текущий last_progress (не полный
        # запас max_wall заново); --yield-after таймеры не сбрасывает
        lm2, ls2 = log_progress(log_path)
        start_watcher(proc.pid, log_path, pid_path, stall_s, wall_deadline,
                      lm2, ls2, run_prompt_file, a.model, a.session,
                      state=state, run_id=a.id, front=a.front,
                      readonly=readonly)
        sys.stdout.write(
            "yield: ожидание >%ss — прогон продолжается в фоне "
            "(переживает смерть этой сессии). pid=%s лог=%s. "
            "Статус: run-exec.py --id %s --status\n"
            % (a.yield_after, proc.pid, log_path, a.id))
        return 0

    code = apply_retries(code, log_path, pid_path, stall_s, wall_deadline,
                         run_prompt_file, retry_on_fail, a.model, a.extra,
                         state=state, run_id=a.id, session=a.session,
                         front=a.front)

    # foreground: маркер до EXIT= (агент уже завершён)
    check_compass_overflow(log_path, a.session, set(), allow_log_write=True)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\nEXIT=%s\n" % code)
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    journal_end(a.id, log_path, code, session=a.session, front=a.front,
                readonly=readonly)
    try:
        out_code = int(code)
    except ValueError:
        out_code = 1
    sys.stdout.write("EXIT=%s log=%s\n" % (code, log_path))
    return out_code


if __name__ == "__main__":
    sys.exit(main())
