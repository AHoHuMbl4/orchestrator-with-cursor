#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""commit-wave: штатный коммит волны фронта (pathspec + mkdir-lock).

Stdlib only — zero-dep (M2 / F-MULTIWRITE волна 1).
"""
from __future__ import print_function

import argparse
import os
import subprocess
import sys
import time

# owns.py рядом в bin/
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import owns  # noqa: E402

LOCK_NAME = "orchestration-commit-wave.lock"
LOCK_TIMEOUT = 30


def _git(repo, args, env=None, check=False):
    r = subprocess.run(
        ["git"] + list(args),
        cwd=repo,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if check and r.returncode != 0:
        raise RuntimeError(
            "git %s failed: %s%s"
            % (" ".join(args), r.stdout or "", r.stderr or "")
        )
    return r


def _git_env(fid, state_dir):
    env = os.environ.copy()
    env["ORCH_FRONT"] = fid
    if state_dir:
        env["ORCHESTRATION_DIR"] = state_dir
    return env


def _lock_path(repo):
    return os.path.join(repo, ".git", LOCK_NAME)


def _read_owner(lock_dir):
    owner = os.path.join(lock_dir, "owner")
    try:
        with open(owner, "r", encoding="utf-8") as f:
            line = f.read().strip()
        parts = line.split()
        pid = int(parts[0])
        ts = float(parts[1]) if len(parts) > 1 else 0.0
        return pid, ts
    except (OSError, ValueError, IndexError):
        return None, None


def _pid_alive(pid):
    """True=живой; False=мёртв(ESRCH). EPERM → живой чужой."""
    if pid is None:
        return True  # неизвестный holder — не reclaim по времени
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:  # ESRCH
        return False
    except PermissionError:  # EPERM — живой чужой
        return True
    except OSError as e:
        # py2-ish / errno
        if getattr(e, "errno", None) == 3:  # ESRCH
            return False
        return True


def acquire_lock(repo, timeout=LOCK_TIMEOUT):
    """mkdir-lock; reclaim мёртвого через rename; timeout → lock busy."""
    lock = _lock_path(repo)
    git_dir = os.path.join(repo, ".git")
    if not os.path.isdir(git_dir):
        raise RuntimeError("нет .git в %s" % repo)
    deadline = time.time() + timeout
    while True:
        try:
            os.mkdir(lock)
            with open(os.path.join(lock, "owner"), "w", encoding="utf-8") as f:
                f.write("%d %s\n" % (os.getpid(), time.time()))
            return lock
        except FileExistsError:
            pass
        except OSError as e:
            if getattr(e, "errno", None) != 17:  # EEXIST
                raise
        # try reclaim dead
        pid, _ts = _read_owner(lock)
        if pid is not None and not _pid_alive(pid):
            reclaim = lock + ".reclaim-%d" % int(time.time() * 1000)
            try:
                os.rename(lock, reclaim)
                # best-effort cleanup reclaim dir
                try:
                    for name in os.listdir(reclaim):
                        try:
                            os.remove(os.path.join(reclaim, name))
                        except OSError:
                            pass
                    os.rmdir(reclaim)
                except OSError:
                    pass
                continue  # retry mkdir
            except OSError:
                pass  # проигравший waiter
        if time.time() >= deadline:
            raise RuntimeError("lock busy")
        time.sleep(0.05)


def release_lock(lock):
    if not lock:
        return
    try:
        owner = os.path.join(lock, "owner")
        if os.path.isfile(owner):
            os.remove(owner)
        os.rmdir(lock)
    except OSError:
        pass


def _parse_porcelain(stdout):
    """Полный XY-парсинг; путь = NEW-колонка; U → conflict path."""
    entries = []
    for raw in (stdout or "").splitlines():
        if not raw or len(raw) < 3:
            continue
        xy = raw[:2]
        rest = raw[3:] if raw[2:3] == " " else raw[2:].lstrip()
        # rename/copy: "old -> new"
        path = rest
        if " -> " in rest and (xy[0] in "RC" or xy[1] in "RC"):
            path = rest.split(" -> ", 1)[1]
        conflict = "U" in xy
        entries.append((xy, path, conflict))
    return entries


def _snapshot_index(repo, paths, env):
    """{path: ls-files -s line or None}."""
    snap = {}
    for p in paths:
        r = _git(repo, ["ls-files", "-s", "--", p], env=env)
        line = (r.stdout or "").strip()
        snap[p] = line if line else None
    return snap


def _restore_index(repo, snap, paths, env):
    """Вернуть index к снимку только для paths (foreign не трогать)."""
    # снять текущие L из индекса, затем восстановить snap
    for p in paths:
        _git(repo, ["rm", "--cached", "-f", "--", p], env=env)
    # восстановить через update-index --index-info
    lines = []
    for p in paths:
        info = snap.get(p)
        if info:
            lines.append(info)
    if lines:
        proc = subprocess.run(
            ["git", "update-index", "--index-info"],
            cwd=repo,
            env=env,
            input="\n".join(lines) + "\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
        if proc.returncode != 0:
            print(
                "предупреждение: не удалось полностью восстановить index: %s"
                % (proc.stderr or proc.stdout),
                file=sys.stderr,
            )


def commit_wave(fid, message, repo, state_dir):
    owns_map = owns.load_owns(state_dir)
    globs = owns_map.get(fid)
    if globs is None:
        # неизвестный фронт — нет владений
        globs = []
    if not globs:
        print("owns пуст", file=sys.stderr)

    env = _git_env(fid, state_dir)
    lock = None
    try:
        lock = acquire_lock(repo)
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 1

    try:
        # unexpected staged вне owns → отказ без мутации
        r = _git(repo, ["diff", "--cached", "--name-only"], env=env)
        staged = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        foreign = [p for p in staged if not match_owned(p, globs)]
        if foreign:
            print(
                "отказ: unexpected staged вне owns %s: %s"
                % (fid, ", ".join(foreign)),
                file=sys.stderr,
            )
            return 1

        st = _git(repo, ["status", "--porcelain", "-u"], env=env)
        entries = _parse_porcelain(st.stdout)
        for xy, path, conflict in entries:
            if conflict:
                print("сначала разреши конфликт %s" % path, file=sys.stderr)
                return 1

        L = []
        seen = set()
        for xy, path, _c in entries:
            if match_owned(path, globs) and path not in seen:
                seen.add(path)
                L.append(path)

        if not L:
            print("нет изменений во владениях %s" % fid, file=sys.stderr)
            return 1

        snap = _snapshot_index(repo, L, env)
        add = _git(repo, ["add", "--"] + L, env=env)
        if add.returncode != 0:
            print(
                "git add failed: %s%s" % (add.stdout or "", add.stderr or ""),
                file=sys.stderr,
            )
            return 1

        commit = _git(repo, ["commit", "-m", message, "--"] + L, env=env)
        if commit.returncode != 0:
            print(
                "предупреждение: commit failed, восстанавливаю index для L",
                file=sys.stderr,
            )
            print(commit.stderr or commit.stdout or "", file=sys.stderr)
            _restore_index(repo, snap, L, env)
            return commit.returncode or 1

        if commit.stdout:
            print(commit.stdout.rstrip())
        return 0
    finally:
        release_lock(lock)


def match_owned(path, globs):
    if not globs:
        return False
    return owns.match_path(path, globs)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="commit-wave.py")
    ap.add_argument("--front", required=True)
    ap.add_argument("--message", required=True)
    ap.add_argument("--cwd", default=None, help="repo path")
    ap.add_argument("--state", default=None)
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.cwd or os.getcwd())
    sd, err = owns.resolve_state_dir(args.state)
    if err:
        print(err, file=sys.stderr)
        return 1

    return commit_wave(args.front, args.message, repo, sd)


if __name__ == "__main__":
    sys.exit(main())
