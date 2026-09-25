#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Markdown-брифинг для входа в сессию оркестрации.

  session-entry.py [--session <sid>] [--out <файл>]

Собирает kit_version, PROJECT.md, handoff, fronts, compass и шаблон
стартового промпта. Вывод — stdout или --out. python3.6+, только stdlib.
"""
from __future__ import print_function

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import orchlib  # noqa: E402

def _project_root():
    """Корень проекта = каталог выше state (.orchestration или ORCHESTRATION_DIR)."""
    return os.path.dirname(orchlib.find_state_dir())


def _read_head(path, n_lines=20):
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = []
            for i, line in enumerate(f):
                if i >= n_lines:
                    break
                lines.append(line.rstrip("\n"))
            return "\n".join(lines)
    except Exception:
        return None


def _read_trunc(path, max_chars):
    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except Exception:
        return None
    if len(text) > max_chars:
        text = text[:max_chars] + "\n… (обрезано)"
    return text


def _parse_project_goal(path):
    """Первая непустая строка после заголовка Цель (# Цель / ## Цель / Цель:)."""
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except Exception:
        return None
    found = False
    for line in lines:
        s = line.strip()
        if not found:
            if (s.startswith("# Цель") or s.startswith("## Цель")
                    or s.startswith("Цель:")):
                found = True
                # «Цель: текст» на той же строке
                if s.startswith("Цель:") and s[5:].strip():
                    return s[5:].strip()
            continue
        if s:
            return s
    return None


def _project_section():
    """Секция «Проект» — первая в брифинге: имя, цель, фронты, mtime, чужой state."""
    root = _project_root()
    name = os.path.basename(root) or root
    project_md = os.path.join(root, "PROJECT.md")
    goal = "⚠️ не определена"
    if os.path.exists(project_md):
        parsed = _parse_project_goal(project_md)
        if parsed:
            goal = parsed

    n_fronts = 0
    graph_mtime = "—"
    try:
        data = orchlib.load_fronts()
        fronts = data.get("fronts") or []
        if isinstance(fronts, list):
            n_fronts = len(fronts)
        fp = os.path.join(orchlib.find_state_dir(), "fronts.json")
        if os.path.exists(fp):
            try:
                mtime = os.path.getmtime(fp)
                graph_mtime = time.strftime(
                    "%Y-%m-%d %H:%M", time.localtime(mtime))
            except Exception:
                graph_mtime = "—"
    except Exception:
        pass

    lines = [
        "## Проект",
        "",
        "Проект: %s" % name,
        "Цель: %s" % goal,
        "Фронтов: %d" % n_fronts,
        "Последнее изменение графа: %s" % graph_mtime,
    ]
    foreign = orchlib.state_is_foreign()
    if foreign:
        lines.append(
            "State: %s (⚠️ найден ВЫШЕ текущей папки — если это не твой "
            "проект, открой отдельную папку со своим .orchestration)"
            % foreign
        )
    lines.append("")
    return "\n".join(lines)


def _project_md_section():
    path = os.path.join(_project_root(), "PROJECT.md")
    head = _read_head(path, 20)
    if head is None:
        return (
            "## PROJECT.md\n\n"
            "нет — создать при старте иерархии\n"
        )
    return "## PROJECT.md (первые ~20 строк)\n\n%s\n" % head


def _handoff_section():
    path = os.path.join(orchlib.find_state_dir(), "handoff.md")
    text = _read_trunc(path, 2000)
    if text is None:
        return "## handoff.md\n\nнет — создай по шаблону\n"
    return "## handoff.md\n\n%s\n" % text


def _fronts_section():
    data = orchlib.load_fronts()
    lines = ["## fronts.json", "", "цель: %s" % (data.get("goal") or "(пусто)"), ""]
    fronts = data.get("fronts") or []
    if not fronts:
        lines.append("(фронтов нет)")
    else:
        for fr in fronts:
            if not isinstance(fr, dict):
                continue
            fid = fr.get("id", "?")
            status = fr.get("status", "?")
            deps = fr.get("deps") if isinstance(fr.get("deps"), list) else []
            dep_s = ", ".join(str(d) for d in deps) if deps else "—"
            lines.append("- id: %s | status: %s | deps: %s" % (fid, status, dep_s))
    lines.append("")
    return "\n".join(lines)


def _compass_section(session_id):
    st = orchlib.find_state_dir()
    if session_id:
        path = os.path.join(st, "sessions", orchlib.safe_name(session_id), "compass.md")
    else:
        path = os.path.join(st, "compass.md")
    text = _read_trunc(path, 300)
    if text is None:
        return "## компас\n\n(файл не найден: %s)\n" % path
    return "## компас (%s)\n\n%s\n" % (path, text)


def _prompt_template_section():
    return (
        "## Шаблон стартового промпта новой сессии\n\n"
        "1. Прочитай PROJECT.md в корне проекта (создай при старте иерархии, если нет).\n"
        "2. Прочитай handoff.md в state — целиком; нет файла — создай по шаблону.\n"
        "3. Прочитай fronts.json: цель и статусы/deps фронтов.\n"
        "4. Прочитай компас сессии (или общий compass.md) — курс, не журнал.\n"
        "5. Доктрина — SKILL и MAP с диска кита (.claude/skills/orchestration/), не из памяти.\n"
        "6. Историю старого чата не восстанавливать: опирайся только на файлы на диске.\n"
    )


def build_briefing(session_id=None):
    parts = [
        "# Session entry briefing\n",
        _project_section(),
        "## kit_version\n\n%s\n" % orchlib.kit_version(),
        _project_md_section(),
        _handoff_section(),
        _fronts_section(),
        _compass_section(session_id),
        _prompt_template_section(),
    ]
    return "\n".join(parts)


def main():
    orchlib.utf8_stdio()
    ap = argparse.ArgumentParser(
        description="Markdown-брифинг входа в сессию оркестрации")
    ap.add_argument("--session", default=None, help="id сессии (compass сессии)")
    ap.add_argument("--out", default=None, help="записать markdown в файл вместо stdout")
    args = ap.parse_args()

    text = build_briefing(args.session)
    if args.out:
        d = os.path.dirname(os.path.abspath(args.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
            if not text.endswith("\n"):
                f.write("\n")
    else:
        sys.stdout.write(text)
        if not text.endswith("\n"):
            sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
