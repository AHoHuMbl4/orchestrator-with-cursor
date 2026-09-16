#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Снимок доступных моделей/efforts оркестраторов -> .orchestration/discovered.json.

Модели НЕ хардкодятся: снимаются с установленных движков в момент запуска.
В params.json хранятся роли (default/cheap/best); конкретные имена выбираются
из этого снимка (меню/панель показывают только актуальное).

  cursor:  cursor-agent --list-models   (исполнителю всегда auto — здесь справка)
  claude:  claude --version/--help      (list-команды нет: алиасы + effort из help)
  codex:   codex debug models --bundled (если установлен)

Кроссплатформенно, python3.6+, stdlib. Выход 0 всегда, кроме фатальных ошибок.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

CLAUDE_ALIASES = ["default", "best", "sonnet", "opus", "haiku", "fable"]
CLAUDE_EFFORTS_FALLBACK = ["low", "medium", "high", "xhigh", "max", "ultracode"]
CODEX_EFFORTS_FALLBACK = ["minimal", "low", "medium", "high", "xhigh"]


def run(cmd, timeout=30):
    try:
        out = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             timeout=timeout, universal_newlines=True)
        return out.returncode, (out.stdout or "") + "\n" + (out.stderr or "")
    except Exception as e:
        return 127, str(e)


def probe_cursor():
    exe = shutil.which("cursor-agent") or shutil.which("cursor-agent.exe")
    if not exe:
        return {"status": "absent"}
    code, out = run([exe, "--list-models"], timeout=60)
    models = []
    for line in out.splitlines():
        m = re.match(r"^\s*([A-Za-z0-9._\-\[\]=,]+)\s+-\s+", line)
        if m:
            models.append(m.group(1))
    return {"status": "ok" if models else "no-models", "models": models,
            "note": "исполнителю всегда auto; список — справка/для ролей оркестратора"}


def probe_claude():
    exe = shutil.which("claude") or shutil.which("claude.exe")
    if not exe:
        return {"status": "absent"}
    _, ver = run([exe, "--version"])
    _, help_out = run([exe, "--help"], timeout=30)
    models = list(CLAUDE_ALIASES)
    eff = []
    m = re.search(r"--effort[^\n]*?(low[^.\n]*)", help_out)
    if m:
        eff = [e.strip() for e in re.split(r"[,/]", m.group(1)) if e.strip()]
    if not eff:
        eff = CLAUDE_EFFORTS_FALLBACK
    return {"status": "ok", "version": ver.strip()[:80], "models": models,
            "efforts": eff}


def probe_codex():
    exe = shutil.which("codex") or shutil.which("codex.exe")
    if not exe:
        return {"status": "absent", "note": "поставь codex — модели подтянутся сами"}
    code, out = run([exe, "debug", "models", "--bundled"], timeout=60)
    models = []
    if code == 0:
        try:
            data = json.loads(out)
            cand = data if isinstance(data, list) else data.get("models", data)
            if isinstance(cand, list):
                for item in cand:
                    if isinstance(item, str):
                        models.append(item)
                    elif isinstance(item, dict):
                        models.append(str(item.get("id") or item.get("slug")
                                          or item.get("name") or ""))
            models = [m for m in models if m]
        except ValueError:
            for line in out.splitlines():
                mm = re.match(r"^\s*([a-z0-9][a-z0-9.\-]*[a-z0-9])\s*$", line)
                if mm:
                    models.append(mm.group(1))
    return {"status": "ok" if models else "unknown",
            "models": models, "efforts": CODEX_EFFORTS_FALLBACK}


def main():
    snap = {
        "checked_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "cursor": probe_cursor(),
        "claude": probe_claude(),
        "codex": probe_codex(),
    }
    out = orchlib.state_path("discovered.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    print(json.dumps(snap, ensure_ascii=False, indent=2))
    print("\n-> %s" % out)


if __name__ == "__main__":
    main()
