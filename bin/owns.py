#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Owns-map: чтение fronts.json, identity коммиттера, check-staged / check-activation.

Stdlib only — zero-dep (M2 / F-MULTIWRITE волна 1).
"""
from __future__ import print_function

import argparse
import fnmatch
import json
import os
import subprocess
import sys


def find_state_dir(state_override=None):
    """`--state` → $ORCHESTRATION_DIR → walk-up `.orchestration` от cwd.

    Fail-closed: вызывающий проверяет существование и наличие fronts.json.
    """
    if state_override:
        return os.path.abspath(state_override)
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
    return None


def resolve_state_dir(state_override=None):
    """Резолв state; нет каталога или нет fronts.json → (None, error_msg)."""
    sd = find_state_dir(state_override)
    if not sd or not os.path.isdir(sd):
        return None, "state не найден"
    fronts = os.path.join(sd, "fronts.json")
    if not os.path.isfile(fronts):
        return None, "state не найден"
    return sd, None


def _load_fronts_raw(state_dir):
    path = os.path.join(state_dir, "fronts.json")
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    fronts = data.get("fronts") if isinstance(data, dict) else data
    if not isinstance(fronts, list):
        return []
    return fronts


def load_owns(state_dir):
    """{fid: [globs]} из fronts.json (пустой owns → [])."""
    out = {}
    for fr in _load_fronts_raw(state_dir):
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not fid:
            continue
        owns = fr.get("owns")
        if owns is None:
            out[fid] = []
        elif isinstance(owns, list):
            out[fid] = [str(g) for g in owns]
        else:
            out[fid] = []
    return out


def load_active_fronts(state_dir):
    """Список (fid, owns_list, status) для всех фронтов."""
    rows = []
    for fr in _load_fronts_raw(state_dir):
        if not isinstance(fr, dict):
            continue
        fid = fr.get("id")
        if not fid:
            continue
        owns = fr.get("owns")
        if owns is None:
            globs = []
        elif isinstance(owns, list):
            globs = [str(g) for g in owns]
        else:
            globs = []
        rows.append((fid, globs, fr.get("status") or ""))
    return rows


def _posix(path):
    return path.replace("\\", "/").lstrip("./")


def glob_supported(glob):
    """True если glob в поддерживаемых формах; иначе False."""
    g = _posix(glob)
    if not g:
        return False
    if "?" in g or "[" in g or "]" in g:
        return False
    if g.endswith("/**"):
        prefix = g[:-3]
        if "**" in prefix:
            return False
        if "?" in prefix or "[" in prefix:
            return False
        # * в сегментах prefix допустим
        return True
    if "**" in g:
        return False  # ** в середине / не хвостовой
    # сегменты: литералы или * внутри одного сегмента
    for seg in g.split("/"):
        if "**" in seg:
            return False
    return True


def match_path(path, globs):
    """Путь матчит хотя бы один glob (posix; dir/** = префикс-каталог)."""
    p = _posix(path)
    if not p:
        return False
    p_parts = p.split("/")
    for g in globs or []:
        g = _posix(g)
        if not g:
            continue
        if g.endswith("/**"):
            prefix = g[:-3]
            if not prefix:
                return True
            # a/** матчит a/b, a/b/c — НЕ сам a
            pref_parts = prefix.split("/")
            if len(p_parts) <= len(pref_parts):
                continue
            ok = True
            for i, seg in enumerate(pref_parts):
                if not fnmatch.fnmatchcase(p_parts[i], seg):
                    ok = False
                    break
            if ok:
                return True
            continue
        g_parts = g.split("/")
        if len(g_parts) != len(p_parts):
            continue
        ok = True
        for gp, pp in zip(g_parts, p_parts):
            if not fnmatch.fnmatchcase(pp, gp):
                ok = False
                break
        if ok:
            return True
    return False


def _materialize_glob(glob):
    """Конкретный путь-свидетель из glob (для /** — глубже префикса)."""
    g = _posix(glob)
    if g.endswith("/**"):
        prefix = g[:-3]
        if not prefix:
            return "x"
        parts = []
        for seg in prefix.split("/"):
            if "*" in seg:
                parts.append(seg.replace("*", "x") or "x")
            else:
                parts.append(seg)
        parts.append("x")
        return "/".join(parts)
    parts = []
    for seg in g.split("/"):
        if "*" in seg:
            parts.append(seg.replace("*", "x") or "x")
        else:
            parts.append(seg)
    return "/".join(parts)


def _seg_compatible(a, b):
    """Общий литерал, матчащий оба сегмента, или None."""
    if a == b:
        if "*" in a:
            return a.replace("*", "x") or "x"
        return a
    if "*" in a and "*" not in b:
        if fnmatch.fnmatchcase(b, a):
            return b
        return None
    if "*" in b and "*" not in a:
        if fnmatch.fnmatchcase(a, b):
            return a
        return None
    if "*" in a and "*" in b:
        # общий пример
        return "x"
    return None


def witness(g1, g2):
    """Свидетель-путь пересечения двух globs или None.

    Оракулы: a/**∩a/b/**=да; идентичные=да; a/*∩b/*=нет; a/**∩a=нет.
    """
    g1 = _posix(g1)
    g2 = _posix(g2)
    if g1 == g2:
        return _materialize_glob(g1)

    # префикс/** ∩ любой матч другого (симметрично), затем verify match_path
    if g1.endswith("/**") or g2.endswith("/**"):
        # пробуем материализовать оба и проверить взаимный матч
        candidates = []
        if g2.endswith("/**") or not g1.endswith("/**"):
            candidates.append(_materialize_glob(g2))
        if g1.endswith("/**") or not g2.endswith("/**"):
            candidates.append(_materialize_glob(g1))
        # также: если один /**, путь из другого должен матчить оба
        for cand in candidates:
            if match_path(cand, [g1]) and match_path(cand, [g2]):
                return cand
        # доп: углубить путь из более короткого
        for base_g in (g1, g2):
            base = _materialize_glob(base_g)
            for extra in ("x", "x/y"):
                cand = base + "/" + extra if not base_g.endswith("/**") else base
                if match_path(cand, [g1]) and match_path(cand, [g2]):
                    return cand
        return None

    # посегментно; короче без /** → нет
    p1 = g1.split("/")
    p2 = g2.split("/")
    if len(p1) != len(p2):
        return None
    out = []
    for a, b in zip(p1, p2):
        lit = _seg_compatible(a, b)
        if lit is None:
            return None
        out.append(lit)
    return "/".join(out)


def resolve_front(env, journal_path):
    """Identity: ORCH_FRONT → ORCH_RUN_ID→journal назад → None (человек).

    Возврат: (front_id|None, error|None).
    error «нерезолвимый front» если ORCH_RUN_ID есть, но front не найден.
    """
    env = env or {}
    front = (env.get("ORCH_FRONT") or "").strip()
    if front:
        return front, None
    run_id = (env.get("ORCH_RUN_ID") or "").strip()
    if not run_id:
        return None, None  # человек
    if not journal_path or not os.path.isfile(journal_path):
        return None, "нерезолвимый front"
    # сканировать НАЗАД до первой записи с этим id и непустым front
    try:
        with open(journal_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return None, "нерезолвимый front"
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue  # битые — пропуск
        if not isinstance(rec, dict):
            continue
        rid = rec.get("id") or rec.get("run_id") or rec.get("ORCH_RUN_ID")
        if rid != run_id:
            continue
        fr = rec.get("front")
        if fr is not None and str(fr).strip():
            return str(fr).strip(), None
        # end без front — продолжаем искать раньше
    return None, "нерезолвимый front"


def owner_of_path(path, owns_map):
    """Первый fid, чьи owns матчат path, или None."""
    for fid, globs in owns_map.items():
        if globs and match_path(path, globs):
            return fid
    return None


def check_staged(state_dir, env=None, repo_cwd=None):
    """Проверка staged vs owns. Возврат exit_code, сообщения stderr-строки."""
    env = env if env is not None else os.environ
    owns_map = load_owns(state_dir)
    journal = os.path.join(state_dir, "journal.jsonl")
    fid, err = resolve_front(env, journal)
    if err:
        return 1, [err]
    if fid is None:
        return 0, []  # человек
    globs = owns_map.get(fid)
    if globs is None:
        # фронт неизвестен — treat as empty owns (warn+pass) or fail?
        # identity resolved but no entry: owns пуст
        return 0, ["owns пуст"]
    if not globs:
        return 0, ["owns пуст"]

    cwd = repo_cwd or os.getcwd()
    try:
        out = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as e:
        return 1, ["git diff failed: %s" % e]
    staged = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
    msgs = []
    bad = False
    for p in staged:
        if not match_path(p, globs):
            bad = True
            owner = owner_of_path(p, owns_map)
            if owner:
                msgs.append("%s владелец=%s чужое для %s" % (p, owner, fid))
            else:
                msgs.append("%s чужое для %s" % (p, fid))
    if bad:
        return 1, msgs
    return 0, []


def check_activation(state_dir, fid):
    """C1: пересечение owns(fid) с active (exclude-self)."""
    rows = load_active_fronts(state_dir)
    by_id = {r[0]: r for r in rows}
    if fid not in by_id:
        return 1, ["неизвестный фронт"]
    _fid, my_owns, _st = by_id[fid]
    if not my_owns:
        return 0, ["owns пуст"]

    for g in my_owns:
        if not glob_supported(g):
            return 1, ["неподдерживаемый glob: %s" % g]

    for other_id, other_owns, status in rows:
        if other_id == fid:
            continue
        if status != "active":
            continue
        if not other_owns:
            continue
        for g in other_owns:
            if not glob_supported(g):
                return 1, ["неподдерживаемый glob: %s" % g]
        for g1 in my_owns:
            for g2 in other_owns:
                w = witness(g1, g2)
                if w:
                    return 1, [
                        "пересечение %s (%s ∩ %s)" % (w, fid, other_id)
                    ]
    return 0, []


def main(argv=None):
    ap = argparse.ArgumentParser(prog="owns.py")
    ap.add_argument("--state", default=None, help="override state dir")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check-staged", action="store_true")
    g.add_argument("--check-activation", metavar="FID")
    args = ap.parse_args(argv)

    if args.check_staged:
        # identity-first: человек — exit 0 без резолва state
        front = (os.environ.get("ORCH_FRONT") or "").strip()
        run_id = (os.environ.get("ORCH_RUN_ID") or "").strip()
        if not front and not run_id:
            return 0
        sd, err = resolve_state_dir(args.state)
        if err:
            print(
                "агентский коммит: state не найден — задайте "
                "ORCHESTRATION_DIR или .orchestration в дереве",
                file=sys.stderr,
            )
            return 1
        code, msgs = check_staged(sd, os.environ, os.getcwd())
        for m in msgs:
            print(m, file=sys.stderr)
        return code

    sd, err = resolve_state_dir(args.state)
    if err:
        print(err, file=sys.stderr)
        return 1

    code, msgs = check_activation(sd, args.check_activation)
    for m in msgs:
        print(m, file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
