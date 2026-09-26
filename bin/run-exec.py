#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск исполнителя cursor-agent (доктрина §2) — кроссплатформенно, без shell.

Промт — ТОЛЬКО из файла (ловушка №1: текст промта не должен попадать в командную
строку shell оркестратора; здесь он передаётся в argv через subprocess, без
$(cat ...) и без кавычек). Модель исполнителя всегда auto.

К cursor-агенту в конец промта доклеивается строка самопроверки курса
(у cursor нет хуков — периодический re-ground идёт на уровне промта).

  python3 run-exec.py --id T1 [--prompt-file ...] [--timeout 1800] [--detach]
                      [--yield-after 480] [--no-reground-line] [--model auto]
                      [--readonly] [доп. флаги cursor-agent]

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
  124     — таймаут
  UNKNOWN — не удалось определить (нет/нечитаемый лог)

Бюджет фронта (--front): warn = churn-датчик (FRONT_BUDGET_WARN + pending,
запуск продолжается); hard=0 по умолчанию выключен. Качество > токены.
При hierarchy≠off обязателен --front <id> или --no-front "<причина>".

Автоперезапуск (execution.retry_on_fail, по умолчанию 1):
  только при EXIT=4 и EXIT=124; не при 1 и 3.
  Перед рестартом в лог: RETRY=<n>/<max> (prev EXIT=<code>).
  Итоговый EXIT — от последней попытки. В --detach рестарт делает watcher
  (cursor-agent — его потомок, код возврата через wait).
"""
import argparse
import glob
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402
import verdict as verdict_mod  # noqa: E402

REGROUND_LINE = (
    "\n\n---\nСамопроверка курса: в начале каждого подшага перечитай файл задания "
    "{path} и сверяй следующий шаг с целью и критерием приёмки из него. "
    "Если заметил дрейф от цели — вернись на шаг назад и отметь это в ответе.\n"
)

RETRYABLE = frozenset(("4", "124"))

# Паттерны секретов в промте — до старта cursor-agent.
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


def apply_secret_gate(prompt, prompt_file, log_path):
    """Секрет-сканер до exe/bump. → (5, lines) | (None, [])."""
    hit = scan_secrets(prompt)
    if not hit:
        return None, []
    line = "SECRETS_IN_PROMPT=%s, %s" % (hit, prompt_file)
    append_log(log_path, line)
    sys.stderr.write(
        "секрет в промте: вынеси в .orchestration/cursor.key / ENV; "
        "промт без секрета\n")
    return 5, [line]


def apply_front_gates(front_id, log_path):
    """Статус/бюджет после exe: closed → 6; hard-бюджет → 7; успех → FRONT_RUNS.

    Бюджет: used>hard (hard>0) → BUDGET_HARD/exit 7; used>=warn →
    FRONT_BUDGET_WARN + pending_budget_warn, запуск продолжается.

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
        used, warn, hard = bump(front_id)
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


def apply_launch_gates(prompt, prompt_file, front_id, log_path):
    """Гейты: секреты → 5; затем фронт closed → 6; hard-бюджет → 7.

    Сохранена для совместимости; main вызывает секрет и фронт по отдельности,
    чтобы вставить find_cursor_agent между ними.
    """
    sec_rc, sec_lines = apply_secret_gate(prompt, prompt_file, log_path)
    if sec_rc is not None:
        return sec_rc, sec_lines
    return apply_front_gates(front_id, log_path)


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
    """После wait(): 0 при rc==0 без разбора лога; иначе — по логу."""
    if returncode == 0:
        return "0"
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


def build_agent_cmd(exe, prompt, model, extra, readonly=False):
    """Собрать argv cursor-agent. readonly → `--mode plan` (барьер A).

    readonly — только аналитика (чтение+выжимка в ответ), не командные роли.
    """
    cmd = [exe, "-p", prompt, "--force", "--model", model,
           "--output-format", "stream-json"]
    if readonly:
        cmd.extend(["--mode", "plan"])
    return cmd + list(extra or [])


def is_readonly_env():
    """Readonly-флаг прогона (наследуется watcher/retry через ORCH_READONLY)."""
    return os.environ.get("ORCH_READONLY") == "1"


def wait_child(proc, log_fh, log_path, timeout_s, yield_after=0):
    """Ждём дочерний proc; таймаут → kill → 124; yield_after → 'YIELDED'.

    yield_after: сек ожидания до авто-уступки в фон (0 = отключить).
    Возвращает строковый код или 'YIELDED'.
    """
    deadline = time.time() + timeout_s
    started = time.time()
    while proc.poll() is None and time.time() < deadline:
        if yield_after and (time.time() - started) >= yield_after:
            try:
                log_fh.close()
            except Exception:
                pass
            return "YIELDED"
        time.sleep(1)
    if proc.poll() is None:
        kill_tree(proc)
        try:
            log_fh.close()
        except Exception:
            pass
        return "124"
    rc = proc.returncode
    try:
        log_fh.close()
    except Exception:
        pass
    return classify_after_wait(log_path, rc)


def agent_popen_kwargs(run_id=None):
    """detach_popen_kwargs + env с ORCH_RUN_ID для дочернего cursor-agent."""
    kwargs = detach_popen_kwargs()
    env = dict(os.environ)
    rid = run_id or env.get("ORCH_RUN_ID")
    if rid:
        env["ORCH_RUN_ID"] = str(rid)
    kwargs["env"] = env
    return kwargs


def journal_start(run_id, prompt_file, front, role, engine="local",
                  readonly=False, no_front_reason=None, auto=False):
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
    if readonly:
        entry["readonly"] = True
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
    # Снять lock автопрокурора, если это был он.
    _release_auto_prosecutor_lock_if_any(run_id)
    # S2: автопрокурор на волну (после end).
    maybe_auto_prosecutor_after_end(run_id)


def journal_gate_refuse(run_id, prompt_file, front, role, log_path, exit_code,
                        engine="local", readonly=False, no_front_reason=None):
    """start+end при отказе гейта (exit 5/6/7/8) — без дыры в journal."""
    journal_start(run_id, prompt_file, front, role, engine=engine,
                  readonly=readonly, no_front_reason=no_front_reason)
    # gate-refuse: не триггерим автопрокурора — пишем end напрямую
    verdict, gates = orchlib.journal_log_meta(log_path)
    orchlib.journal_append({
        "ts": time.time(),
        "kind": "end",
        "id": run_id,
        "exit": exit_code,
        "verdict": verdict,
        "gates": gates,
    })


def _release_auto_prosecutor_lock_if_any(run_id):
    """Снять counters/prosecutor-pending-<F> при end автопрокурора."""
    try:
        state = orchlib.find_state_dir()
        entries = orchlib._journal_entries_at(state)
        start = None
        for e in entries:
            if e.get("kind") == "start" and e.get("id") == run_id:
                start = e
        if not start:
            return
        if not (start.get("auto") or orchlib._role_is_prosecutor(start.get("role"))):
            return
        # Снимаем lock только для авто-прокурора (auto=true) или id-префикса.
        rid = run_id or ""
        if not (start.get("auto") or rid.startswith("prosecutor-auto-")):
            return
        fid = start.get("front")
        if not fid:
            return
        lock_dir = orchlib.prosecutor_pending_lock_dir(fid, state)
        orchlib._dir_lock_release(lock_dir)
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


def maybe_auto_prosecutor_after_end(run_id, spawn=True):
    """S2: после end роли волны — detached автопрокурор (один на волну, lockdir).

    spawn=False — только решение+lock+промт (для замеров гонки без агента).
    Returns pros_id или None.
    """
    try:
        if not run_id:
            return None
        state = orchlib.find_state_dir()
        entries = orchlib._journal_entries_at(state)
        start = None
        for e in entries:
            if e.get("kind") == "start" and e.get("id") == run_id:
                start = e
        if not start:
            return None
        fid = start.get("front")
        role = orchlib.normalize_journal_role(start.get("role"))
        if not fid or not orchlib._role_is_wave_work(role):
            return None
        if not orchlib.auto_prosecutor_should_launch(fid, state):
            return None
        lock_dir = orchlib.prosecutor_pending_lock_dir(fid, state)
        # TTL 30 мин по mtime (как в спеке S2); timeout=0 — без ожидания (гонка).
        if not orchlib._dir_lock_acquire(lock_dir, timeout_s=0.0, stale_s=1800.0):
            return None
        # Повторная проверка под lock (гонка двух концов).
        if not orchlib.auto_prosecutor_should_launch(fid, state):
            orchlib._dir_lock_release(lock_dir)
            return None
        n = orchlib.next_auto_prosecutor_n(fid, state)
        pros_id = "prosecutor-auto-%s-%d" % (fid, n)
        # Промт: .orchestration/prompt-prosecutor-auto-<F>-<n>.md
        prompt_path = os.path.join(
            state, "prompt-prosecutor-auto-%s-%d.md" % (fid, n))
        _write_auto_prosecutor_prompt(prompt_path, fid)
        if not spawn:
            return pros_id
        env = dict(os.environ)
        env["ORCH_RUN_AUTO"] = "1"
        # Не наследовать parent=текущий run как себя через ORCH_RUN_ID.
        env.pop("ORCH_RUN_ID", None)
        cmd = [
            sys.executable, os.path.abspath(__file__),
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
    except Exception as exc:
        try:
            sys.stderr.write("auto-prosecutor: %s\n" % exc)
        except Exception:
            pass
        return None


def start_watcher(pid, log_path, pid_path, timeout_s, run_prompt_file, model,
                  session=None):
    """Запуск detached --__watch (общий для --detach и авто-уступки)."""
    watch_cmd = [sys.executable, os.path.abspath(__file__), "--__watch",
                 str(pid), log_path, pid_path, str(timeout_s),
                 run_prompt_file or "", model, session or ""]
    # наследует ORCH_RUN_ID из os.environ (выставлен родителем до вызова)
    wkwargs = detach_popen_kwargs()
    wkwargs["env"] = dict(os.environ)
    subprocess.Popen(watch_cmd, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, **wkwargs)


def run_retry_child(run_prompt_file, log_path, pid_path, timeout_s, model, extra):
    """Рестарт cursor-agent как потомок текущего процесса (watcher/foreground)."""
    exe = find_cursor_agent()
    if not exe:
        return "4"
    try:
        with open(run_prompt_file, "r", encoding="utf-8") as f:
            prompt = f.read()
    except Exception:
        return "4"
    log_fh = open(log_path, "a", encoding="utf-8")
    cmd = build_agent_cmd(exe, prompt, model, extra,
                          readonly=is_readonly_env())
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                            **agent_popen_kwargs())
    try:
        with open(pid_path, "w", encoding="utf-8") as f:
            f.write(str(proc.pid))
    except Exception:
        pass
    return wait_child(proc, log_fh, log_path, timeout_s)


def apply_retries(code, log_path, pid_path, timeout_s, run_prompt_file,
                  retry_on_fail, model, extra):
    """Автоперезапуск при 4/124, не более retry_on_fail раз."""
    retries = 0
    max_r = int(retry_on_fail)
    while str(code) in RETRYABLE and retries < max_r:
        retries += 1
        append_log(log_path, "RETRY=%d/%d (prev EXIT=%s)" % (retries, max_r, code))
        code = run_retry_child(run_prompt_file, log_path, pid_path,
                               timeout_s, model, extra)
    return code


def watch(pid, log_path, pid_path, timeout_s, run_prompt_file=None, model="auto",
          session=None):
    """Detach-watcher: ждёт pid / таймаут, EXIT= по контракту, рестарт при 4/124."""
    deadline = time.time() + timeout_s
    timed_out = False
    noted = set()
    while time.time() < deadline:
        time.sleep(2)
        check_compass_overflow(log_path, session, noted, allow_log_write=False)
        if not pid_alive(int(pid)):
            break
    else:
        if pid_alive(int(pid)):
            kill_pid(pid)
            timed_out = True
            # дождаться исчезновения
            for _ in range(15):
                time.sleep(0.4)
                if not pid_alive(int(pid)):
                    break

    # финальная проверка — агент уже не пишет; дописываем маркеры в лог
    check_compass_overflow(log_path, session, noted, allow_log_write=True)

    if timed_out:
        code = "124"
    else:
        code = classify_log(log_path)

    params = orchlib.load_params()
    retry_on_fail = int(params.get("execution", {}).get("retry_on_fail", 1))
    if run_prompt_file:
        code = apply_retries(code, log_path, pid_path, timeout_s,
                             run_prompt_file, retry_on_fail, model, [])

    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\nEXIT=%s\n" % code)
        os.unlink(pid_path)
    except Exception:
        pass
    journal_end(os.environ.get("ORCH_RUN_ID"), log_path, code)


def detach_popen_kwargs():
    """Флаги отсоединения от консоли: start_new_session (POSIX) / DETACHED (Windows)."""
    if os.name != "nt":
        return {"start_new_session": True}
    # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS — не умирать при закрытии консоли
    return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008}


def pid_alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 stdout=subprocess.PIPE,
                                 encoding="utf-8", errors="replace").stdout
            pid_s = str(pid)
            for line in out.splitlines():
                if not line.strip():
                    continue
                if pid_s in line.split():  # точное совпадение токена, не подстрока
                    return True
            return False
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def resolve_run_paths(state, run_id, session=None):
    """Пути лога и pid: state/cursor-run-<id>.* или sessions/<sid>/runs/<id>/."""
    if session:
        run_dir = os.path.join(state, "sessions", session, "runs", run_id)
        return (os.path.join(run_dir, "run.log"),
                os.path.join(run_dir, "run.pid"))
    return (os.path.join(state, "cursor-run-%s.log" % run_id),
            os.path.join(state, "cursor-run-%s.pid" % run_id))


def read_pid_file(pid_path):
    try:
        with open(pid_path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except Exception:
        return None


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
    alive = bool(pid is not None and pid_alive(pid))
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
        runs_root = os.path.join(state, "sessions", session, "runs")
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


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--__watch":
        # --__watch pid log_path pid_path timeout_s [run_prompt_file [model [session]]]
        argv = sys.argv
        run_prompt = argv[6] if len(argv) > 6 else None
        if run_prompt == "":
            run_prompt = None
        model = argv[7] if len(argv) > 7 else "auto"
        session = argv[8] if len(argv) > 8 and argv[8] else None
        watch(argv[2], argv[3], argv[4], int(argv[5]), run_prompt, model,
              session)
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
    ap.add_argument("--timeout", type=int, default=None, help="сек; по умолчанию из params")
    ap.add_argument("--detach", action="store_true", help="фон: pid-файл, не ждать")
    ap.add_argument("--yield-after", type=int, default=480,
                    help="сек foreground-ожидания до авто-уступки в фон "
                         "(дефолт 480; 0 = ждать до конца/таймаута)")
    ap.add_argument("--status", action="store_true",
                    help="статус прогона --id (лог/pid/exit/вердикт); exit 0")
    ap.add_argument("--list", action="store_true",
                    help="список прогонов state (и runs при --session); exit 0")
    ap.add_argument("--no-reground-line", action="store_true",
                    help="не доклеивать строку самопроверки в промт")
    ap.add_argument("--role", default=None,
                    help="роль (приоритет над шапкой); иначе «роль: path.md» в первых 3 строках промта")
    ap.add_argument("--model", default="auto", help="всегда auto (доктрина)")
    ap.add_argument("--readonly", action="store_true",
                    help="барьер A: --mode plan; journal readonly=true; "
                         "только аналитика (чтение+выжимка), не командные роли")
    ap.add_argument("extra", nargs="*", help="доп. флаги cursor-agent наперед")
    a = ap.parse_args()

    state = orchlib.find_state_dir()
    if a.list:
        return cmd_list(state, a.session)
    if a.status:
        if not a.id:
            sys.stderr.write("--status требует --id\n")
            return 2
        return cmd_status(state, a.id, a.session)
    if not a.id:
        ap.error("--id обязателен (кроме --list)")

    params = orchlib.load_params()
    os.makedirs(state, exist_ok=True)

    run_dir = os.path.join(state, "prompt-%s" % a.id)  # совместимость без --session
    if a.session:
        run_dir = os.path.join(state, "sessions", a.session, "runs", a.id)
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

    # Порядок: (а) FRONT_REQUIRED → 8; (б) секрет → 5; (в) exe → 3;
    # (г) front/bump → 6/7/FRONT_RUNS. Гейт-отказы: journal start+end до return.
    role = orchlib.resolve_run_role(a.role, prompt)
    readonly = bool(a.readonly)
    front_out, no_front_reason, front_refuse = orchlib.resolve_front_launch(
        a.front, a.no_front)
    if front_refuse is not None:
        append_log(log_path, front_refuse)
        sys.stderr.write(front_refuse + "\n")
        journal_gate_refuse(
            a.id, prompt_file, None, role, log_path,
            orchlib.FRONT_REQUIRED_EXIT, readonly=readonly)
        return orchlib.FRONT_REQUIRED_EXIT
    a.front = front_out

    sec_rc, _sec_lines = apply_secret_gate(prompt, prompt_file, log_path)
    if sec_rc is not None:
        journal_gate_refuse(a.id, prompt_file, a.front, role, log_path, sec_rc,
                            readonly=readonly, no_front_reason=no_front_reason)
        return sec_rc

    exe = find_cursor_agent()
    if not exe:
        sys.stderr.write("cursor-agent не найден в PATH\n")
        return 3

    gate_rc, gate_lines = apply_front_gates(a.front, log_path)
    if gate_rc is not None:
        journal_gate_refuse(a.id, prompt_file, a.front, role, log_path, gate_rc,
                            readonly=readonly, no_front_reason=no_front_reason)
        return gate_rc

    # летописец: parent до перезаписи ORCH_RUN_ID; start до Popen
    prompt_abs = os.path.abspath(prompt_file)
    if readonly:
        os.environ["ORCH_READONLY"] = "1"
    elif "ORCH_READONLY" in os.environ:
        del os.environ["ORCH_READONLY"]
    journal_start(a.id, prompt_file, a.front, role, engine="local",
                  readonly=readonly, no_front_reason=no_front_reason)
    os.environ["ORCH_RUN_ID"] = a.id

    if not a.no_reground_line:
        prompt += REGROUND_LINE.format(path=prompt_abs)
    with open(run_prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    timeout_s = a.timeout or int(params.get("execution", {}).get("timeout_s", 1800))
    retry_on_fail = int(params.get("execution", {}).get("retry_on_fail", 1))
    cmd = build_agent_cmd(exe, prompt, a.model, a.extra, readonly=readonly)

    # Truncate: переносим gate success markers в начало лога (FRONT_RUNS уже
    # был append'нут в apply_launch_gates — без rewrite open('w') стёр бы его).
    log_fh = open(log_path, "w", encoding="utf-8")
    for line in gate_lines:
        log_fh.write(line if line.endswith("\n") else line + "\n")
    log_fh.flush()
    kwargs = agent_popen_kwargs(a.id)
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, **kwargs)

    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    if a.detach:
        # watcher допишет EXIT= и при 4/124 рестартнет агента как своего потомка
        start_watcher(proc.pid, log_path, pid_path, timeout_s,
                      run_prompt_file, a.model, a.session)
        sys.stdout.write("started pid=%s log=%s\n" % (proc.pid, log_path))
        return 0

    # foreground: pid-файл уже записан; при уступке watcher его удалит
    code = wait_child(proc, log_fh, log_path, timeout_s,
                      yield_after=a.yield_after)
    if code == "YIELDED":
        start_watcher(proc.pid, log_path, pid_path, timeout_s,
                      run_prompt_file, a.model, a.session)
        sys.stdout.write(
            "yield: ожидание >%ss — прогон продолжается в фоне "
            "(переживает смерть этой сессии). pid=%s лог=%s. "
            "Статус: run-exec.py --id %s --status\n"
            % (a.yield_after, proc.pid, log_path, a.id))
        return 0

    code = apply_retries(code, log_path, pid_path, timeout_s,
                         run_prompt_file, retry_on_fail, a.model, a.extra)

    # foreground: маркер до EXIT= (агент уже завершён)
    check_compass_overflow(log_path, a.session, set(), allow_log_write=True)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\nEXIT=%s\n" % code)
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    journal_end(a.id, log_path, code)
    try:
        out_code = int(code)
    except ValueError:
        out_code = 1
    sys.stdout.write("EXIT=%s log=%s\n" % (code, log_path))
    return out_code


if __name__ == "__main__":
    sys.exit(main())
