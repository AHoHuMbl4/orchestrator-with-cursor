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


def watch(pid, log_path, pid_path, timeout_s):
    """Detach-watcher: ждёт смерти pid, выводит итог в лог (EXIT= по result-событию)."""
    deadline = time.time() + timeout_s + 300
    while time.time() < deadline:
        time.sleep(2)
        if not pid_alive(int(pid)):
            break
    code = "UNKNOWN"
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            tail = f.read()[-4000:]
        if '"subtype":"success"' in tail.split('"type":"result"')[-1]:
            code = "0"
        elif '"type":"result"' in tail:
            code = "1"
    except Exception:
        pass
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write("\nEXIT=%s\n" % code)
        os.unlink(pid_path)
    except Exception:
        pass


def pid_alive(pid):
    try:
        if os.name == "nt":
            out = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                                 stdout=subprocess.PIPE,
                                 universal_newlines=True).stdout
            return str(pid) in out
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--__watch":
        watch(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
        return 0
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--id", required=True, help="метка прогона (файлы логов/промтов)")
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

    prompt_file = a.prompt_file or os.path.join(state, "prompt-%s.md" % a.id)
    if not os.path.exists(prompt_file):
        sys.stderr.write("промт-файл не найден: %s\n" % prompt_file)
        return 2
    with open(prompt_file, "r", encoding="utf-8") as f:
        prompt = f.read()
    if not prompt.strip():
        sys.stderr.write("промт-файл пуст: %s\n" % prompt_file)
        return 2

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
    cmd = [exe, "-p", prompt, "--force", "--model", a.model,
           "--output-format", "stream-json"] + list(a.extra)

    log_path = os.path.join(state, "cursor-run-%s.log" % a.id)
    log_fh = open(log_path, "w", encoding="utf-8")
    kwargs = {}
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(cmd, stdout=log_fh, stderr=subprocess.STDOUT, **kwargs)

    pid_path = os.path.join(state, "cursor-run-%s.pid" % a.id)
    with open(pid_path, "w", encoding="utf-8") as f:
        f.write(str(proc.pid))

    if a.detach:
        # отпочковать watcher: допишет EXIT= по завершении и уберёт pid-файл
        watch_cmd = [sys.executable, os.path.abspath(__file__), "--__watch",
                     str(proc.pid), log_path, pid_path, str(timeout_s)]
        wflags = {}
        if os.name != "nt":
            wflags["start_new_session"] = True
        else:
            wflags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x00000008  # DETACHED_PROCESS
        subprocess.Popen(watch_cmd, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, **wflags)
        sys.stdout.write("started pid=%s log=%s\n" % (proc.pid, log_path))
        return 0

    deadline = time.time() + timeout_s
    while proc.poll() is None and time.time() < deadline:
        time.sleep(1)
    if proc.poll() is None:
        kill_tree(proc)
        code = 124  # завис/перерос — по доктрине это ошибка декомпозиции
    else:
        code = proc.returncode

    log_fh.close()
    with open(log_path, "a", encoding="utf-8") as f:
        f.write("\nEXIT=%d\n" % code)
    try:
        os.unlink(pid_path)
    except OSError:
        pass
    sys.stdout.write("EXIT=%d log=%s\n" % (code, log_path))
    return code


if __name__ == "__main__":
    sys.exit(main())
