#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Запуск исполнителя cursor-agent (доктрина §2) — кроссплатформенно, без shell.

Промт — ТОЛЬКО из файла (ловушка №1: текст промта не должен попадать в командную
строку shell оркестратора; здесь он передаётся в argv через subprocess, без
$(cat ...) и без кавычек). Модель исполнителя всегда auto.

К cursor-агенту в конец промта доклеивается строка самопроверки курса
(у cursor нет хуков — периодический re-ground идёт на уровне промта).

  python3 run-exec.py --id T1 [--prompt-file ...] [--timeout 1800] [--detach]
                      [--no-reground-line] [--model auto] [доп. флаги cursor-agent]

Лог: <state>/cursor-run-<id>.log, в конце строка EXIT=<код>.
При --detach: pid-файл <state>/cursor-run-<id>.pid, процесс не ждём.

Коды EXIT:
  0       — result success
  1       — result-событие с неуспехом (агент отчитался о фейле)
  3       — умер без result, но в логе есть финальный текст ассистента
  4       — умер без result и без текста отчёта (работа потеряна)
  124     — таймаут
  UNKNOWN — не удалось определить (нет/нечитаемый лог)

Автоперезапуск (execution.retry_on_fail, по умолчанию 1):
  только при EXIT=4 и EXIT=124; не при 1 и 3.
  Перед рестартом в лог: RETRY=<n>/<max> (prev EXIT=<code>).
  Итоговый EXIT — от последней попытки. В --detach рестарт делает watcher
  (cursor-agent — его потомок, код возврата через wait).
"""
import argparse
import os
import shutil
import signal
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

REGROUND_LINE = (
    "\n\n---\nСамопроверка курса: в начале каждого подшага перечитай файл задания "
    "{path} и сверяй следующий шаг с целью и критерием приёмки из него. "
    "Если заметил дрейф от цели — вернись на шаг назад и отметь это в ответе.\n"
)

RETRYABLE = frozenset(("4", "124"))


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


def build_agent_cmd(exe, prompt, model, extra):
    return [exe, "-p", prompt, "--force", "--model", model,
            "--output-format", "stream-json"] + list(extra or [])


def wait_child(proc, log_fh, log_path, timeout_s):
    """Ждём дочерний proc; таймаут → kill → 124. Возвращает строковый код."""
    deadline = time.time() + timeout_s
    while proc.poll() is None and time.time() < deadline:
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
    cmd = build_agent_cmd(exe, prompt, model, extra)
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT,
                            **detach_popen_kwargs())
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


def watch(pid, log_path, pid_path, timeout_s, run_prompt_file=None, model="auto"):
    """Detach-watcher: ждёт pid / таймаут, EXIT= по контракту, рестарт при 4/124."""
    deadline = time.time() + timeout_s
    timed_out = False
    while time.time() < deadline:
        time.sleep(2)
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


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--__watch":
        # --__watch pid log_path pid_path timeout_s [run_prompt_file [model]]
        argv = sys.argv
        run_prompt = argv[6] if len(argv) > 6 else None
        model = argv[7] if len(argv) > 7 else "auto"
        watch(argv[2], argv[3], argv[4], int(argv[5]), run_prompt, model)
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True, help="метка прогона (файлы логов/промтов)")
    ap.add_argument("--session", default=None,
                    help="id сессии: все файлы прогона лягут в .orchestration/sessions/<id>/runs/<run>/")
    ap.add_argument("--prompt-file", default=None,
                    help="по умолчанию <state>/prompt-<id>.md")
    ap.add_argument("--timeout", type=int, default=None, help="сек; по умолчанию из params")
    ap.add_argument("--detach", action="store_true", help="фон: pid-файл, не ждать")
    ap.add_argument("--no-reground-line", action="store_true",
                    help="не доклеивать строку самопроверки в промт")
    ap.add_argument("--model", default="auto", help="всегда auto (доктрина)")
    ap.add_argument("extra", nargs="*", help="доп. флаги cursor-agent наперед")
    a = ap.parse_args()

    params = orchlib.load_params()
    state = orchlib.find_state_dir()
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
    if not a.no_reground_line:
        prompt += REGROUND_LINE.format(path=os.path.abspath(prompt_file))
    with open(run_prompt_file, "w", encoding="utf-8") as f:
        f.write(prompt)

    exe = find_cursor_agent()
    if not exe:
        sys.stderr.write("cursor-agent не найден в PATH\n")
        return 3

    timeout_s = a.timeout or int(params.get("execution", {}).get("timeout_s", 1800))
    retry_on_fail = int(params.get("execution", {}).get("retry_on_fail", 1))
    cmd = build_agent_cmd(exe, prompt, a.model, a.extra)

    if not a.session:
        log_path = os.path.join(state, "cursor-run-%s.log" % a.id)
    log_fh = open(log_path, "w", encoding="utf-8")
    kwargs = detach_popen_kwargs()
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, **kwargs)

    if not a.session:
        pid_path = os.path.join(state, "cursor-run-%s.pid" % a.id)
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    if a.detach:
        # watcher допишет EXIT= и при 4/124 рестартнет агента как своего потомка
        watch_cmd = [sys.executable, os.path.abspath(__file__), "--__watch",
                     str(proc.pid), log_path, pid_path, str(timeout_s),
                     run_prompt_file, a.model]
        subprocess.Popen(watch_cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **detach_popen_kwargs())
        sys.stdout.write("started pid=%s log=%s\n" % (proc.pid, log_path))
        return 0

    code = wait_child(proc, log_fh, log_path, timeout_s)
    code = apply_retries(code, log_path, pid_path, timeout_s,
                         run_prompt_file, retry_on_fail, a.model, a.extra)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\nEXIT=%s\n" % code)
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    try:
        out_code = int(code)
    except ValueError:
        out_code = 1
    sys.stdout.write("EXIT=%s log=%s\n" % (code, log_path))
    return out_code


if __name__ == "__main__":
    sys.exit(main())
