#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""F-MULTIWRITE wave 1: owns / commit-wave / pre-commit / C1 (stdlib).

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_multiwrite.py
State: только ORCHESTRATION_DIR=/tmp/mw-… (cleanup); /root/.orchestration не трогаем.
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
OWNS_PY = os.path.join(BIN, "owns.py")
COMMIT_WAVE_PY = os.path.join(BIN, "commit-wave.py")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import owns  # noqa: E402 — только owns (zero-dep)


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _mw_tmpdir():
    return tempfile.mkdtemp(prefix="mw1-", dir="/tmp")


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _git(repo, args, env=None, check=True):
    e = os.environ.copy()
    if env:
        e.update(env)
    r = subprocess.run(
        ["git"] + list(args),
        cwd=repo,
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if check and r.returncode != 0:
        raise AssertionError(
            "git %s rc=%s\n%s\n%s"
            % (args, r.returncode, r.stdout, r.stderr)
        )
    return r


def _init_repo(path):
    os.makedirs(path, exist_ok=True)
    _git(path, ["init"])
    _git(path, ["config", "user.name", "mw-test"])
    _git(path, ["config", "user.email", "mw@test.local"])
    # initial commit so HEAD exists
    with open(os.path.join(path, "README"), "w") as f:
        f.write("init\n")
    _git(path, ["add", "README"])
    _git(path, ["commit", "-m", "init"])


def _make_state(base, fronts):
    state = os.path.join(base, "state")
    os.makedirs(state, exist_ok=True)
    _write_json(os.path.join(state, "fronts.json"), {"fronts": fronts, "goal": "mw"})
    open(os.path.join(state, "journal.jsonl"), "a", encoding="utf-8").close()
    return state


def _run_owns(args, env, cwd):
    e = os.environ.copy()
    # clear identity unless provided
    e.pop("ORCH_FRONT", None)
    e.pop("ORCH_RUN_ID", None)
    e.update(env)
    return subprocess.run(
        [sys.executable, OWNS_PY] + list(args),
        cwd=cwd,
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )


def _run_cw(args, env, cwd):
    e = os.environ.copy()
    e.pop("ORCH_FRONT", None)
    e.pop("ORCH_RUN_ID", None)
    e.update(env)
    return subprocess.run(
        [sys.executable, COMMIT_WAVE_PY] + list(args),
        cwd=cwd,
        env=e,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )


def _install_precommit(repo):
    hook_dir = os.path.join(repo, ".git", "hooks")
    os.makedirs(hook_dir, exist_ok=True)
    hook = os.path.join(hook_dir, "pre-commit")
    with open(hook, "w", encoding="utf-8") as f:
        f.write("#!/usr/bin/env bash\n")
        f.write("# >>> orchestration-kit owns-check >>>\n")
        f.write(
            '"%s" "%s" --check-staged || exit $?\n'
            % (sys.executable, OWNS_PY)
        )
        f.write("# <<< orchestration-kit owns-check <<<\n")
    os.chmod(hook, 0o755)
    return hook


# ---------------------------------------------------------------------------
# (а) pre-commit / check-staged
# ---------------------------------------------------------------------------


class TestPreCommit(unittest.TestCase):
    def setUp(self):
        self.tmp = _mw_tmpdir()
        self.repo = os.path.join(self.tmp, "repo")
        _init_repo(self.repo)
        self.state = _make_state(
            self.tmp,
            [
                {
                    "id": "F-DOCS",
                    "status": "active",
                    "owns": ["docs/**"],
                },
                {
                    "id": "F-RULES",
                    "status": "active",
                    "owns": ["rules/**"],
                },
                {
                    "id": "F-EMPTY",
                    "status": "active",
                    "owns": [],
                },
            ],
        )
        self.env_base = {"ORCHESTRATION_DIR": self.state}

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_a_foreign_staged_reject(self):
        os.makedirs(os.path.join(self.repo, "rules"), exist_ok=True)
        with open(os.path.join(self.repo, "rules", "x.md"), "w") as f:
            f.write("foreign\n")
        _git(self.repo, ["add", "rules/x.md"])
        r = _run_owns(
            ["--check-staged", "--state", self.state],
            {**self.env_base, "ORCH_FRONT": "F-DOCS"},
            self.repo,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("чужое для F-DOCS", r.stderr)
        _measure("MEASURE pre-commit foreign reject exit=%s" % r.returncode)

    def test_a_own_staged_pass(self):
        os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "a.md"), "w") as f:
            f.write("own\n")
        _git(self.repo, ["add", "docs/a.md"])
        r = _run_owns(
            ["--check-staged", "--state", self.state],
            {**self.env_base, "ORCH_FRONT": "F-DOCS"},
            self.repo,
        )
        self.assertEqual(r.returncode, 0)
        _measure("MEASURE pre-commit own pass exit=0")

    def test_a_human_no_orch_pass(self):
        os.makedirs(os.path.join(self.repo, "rules"), exist_ok=True)
        with open(os.path.join(self.repo, "rules", "h.md"), "w") as f:
            f.write("human\n")
        _git(self.repo, ["add", "rules/h.md"])
        r = _run_owns(
            ["--check-staged", "--state", self.state],
            self.env_base,  # no ORCH_*
            self.repo,
        )
        self.assertEqual(r.returncode, 0)
        _measure("MEASURE pre-commit human pass exit=0")

    def test_a_empty_owns_warn_pass(self):
        os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "e.md"), "w") as f:
            f.write("e\n")
        _git(self.repo, ["add", "docs/e.md"])
        r = _run_owns(
            ["--check-staged", "--state", self.state],
            {**self.env_base, "ORCH_FRONT": "F-EMPTY"},
            self.repo,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("owns пуст", r.stderr)
        _measure("MEASURE pre-commit empty owns warn+pass")

    def test_a_orch_run_id_unresolvable(self):
        r = _run_owns(
            ["--check-staged", "--state", self.state],
            {**self.env_base, "ORCH_RUN_ID": "no-such-run"},
            self.repo,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("нерезолвимый front", r.stderr)
        _measure("MEASURE pre-commit unresolvable front reject")


# ---------------------------------------------------------------------------
# (б) commit_wave
# ---------------------------------------------------------------------------


class TestCommitWave(unittest.TestCase):
    def setUp(self):
        self.tmp = _mw_tmpdir()
        self.repo = os.path.join(self.tmp, "repo")
        _init_repo(self.repo)
        self.state = _make_state(
            self.tmp,
            [
                {"id": "F-A", "status": "active", "owns": ["a/**"]},
                {"id": "F-B", "status": "active", "owns": ["b/**"]},
            ],
        )
        self.env = {"ORCHESTRATION_DIR": self.state}
        _install_precommit(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_b_parallel_two_fronts_own_paths_only(self):
        os.makedirs(os.path.join(self.repo, "a"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "b"), exist_ok=True)
        with open(os.path.join(self.repo, "a", "1.txt"), "w") as f:
            f.write("A\n")
        with open(os.path.join(self.repo, "b", "1.txt"), "w") as f:
            f.write("B\n")
        results = {}

        def wave(fid, path_hint):
            r = _run_cw(
                [
                    "--front",
                    fid,
                    "--message",
                    "wave %s" % fid,
                    "--cwd",
                    self.repo,
                    "--state",
                    self.state,
                ],
                self.env,
                self.repo,
            )
            results[fid] = r

        t1 = threading.Thread(target=wave, args=("F-A", "a"))
        t2 = threading.Thread(target=wave, args=("F-B", "b"))
        t1.start()
        t2.start()
        t1.join(timeout=60)
        t2.join(timeout=60)
        self.assertEqual(results["F-A"].returncode, 0, results["F-A"].stderr)
        self.assertEqual(results["F-B"].returncode, 0, results["F-B"].stderr)
        # last two commits — check name-only
        log = _git(self.repo, ["log", "-2", "--name-only", "--pretty=format:===%s"])
        text = log.stdout
        # each commit should only have its path
        show_a = None
        show_b = None
        for i in (1, 2):
            s = _git(self.repo, ["show", "--name-only", "--pretty=format:%s", "HEAD~%d" % (i - 1) if i > 1 else "HEAD"])
            # simpler: iterate commits
        commits = _git(
            self.repo, ["log", "-2", "--pretty=format:%H %s"]
        ).stdout.strip().splitlines()
        for line in commits:
            sha, _, subj = line.partition(" ")
            names = _git(
                self.repo, ["show", "--name-only", "--pretty=format:", sha]
            ).stdout.strip().splitlines()
            names = [n for n in names if n]
            if "F-A" in subj:
                self.assertEqual(names, ["a/1.txt"], names)
                show_a = names
            elif "F-B" in subj:
                self.assertEqual(names, ["b/1.txt"], names)
                show_b = names
        self.assertIsNotNone(show_a)
        self.assertIsNotNone(show_b)
        _measure("MEASURE commit_wave parallel own paths only")

    def test_b_unexpected_staged_no_index_mutation(self):
        os.makedirs(os.path.join(self.repo, "a"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "b"), exist_ok=True)
        with open(os.path.join(self.repo, "a", "ok.txt"), "w") as f:
            f.write("ok\n")
        with open(os.path.join(self.repo, "b", "foreign.txt"), "w") as f:
            f.write("f\n")
        _git(self.repo, ["add", "b/foreign.txt"])
        before = _git(self.repo, ["diff", "--cached", "--name-only"]).stdout
        r = _run_cw(
            [
                "--front",
                "F-A",
                "--message",
                "should fail",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            self.env,
            self.repo,
        )
        self.assertNotEqual(r.returncode, 0)
        after = _git(self.repo, ["diff", "--cached", "--name-only"]).stdout
        self.assertEqual(before, after)
        _measure("MEASURE unexpected staged no mutation")

    def test_b_empty_L_no_changes(self):
        r = _run_cw(
            [
                "--front",
                "F-A",
                "--message",
                "nothing",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            self.env,
            self.repo,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("нет изменений во владениях F-A", r.stderr)
        _measure("MEASURE L empty no changes")

    def test_b_lock_busy(self):
        lock = os.path.join(self.repo, ".git", "orchestration-commit-wave.lock")
        os.mkdir(lock)
        with open(os.path.join(lock, "owner"), "w") as f:
            f.write("%d %s\n" % (os.getpid(), time.time()))
        os.makedirs(os.path.join(self.repo, "a"), exist_ok=True)
        with open(os.path.join(self.repo, "a", "x.txt"), "w") as f:
            f.write("x\n")
        t0 = time.time()
        r = _run_cw(
            [
                "--front",
                "F-A",
                "--message",
                "busy",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            self.env,
            self.repo,
        )
        elapsed = time.time() - t0
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("lock busy", r.stderr)
        self.assertGreaterEqual(elapsed, 25)  # ~30s timeout
        # cleanup lock so tearDown ok
        try:
            os.remove(os.path.join(lock, "owner"))
            os.rmdir(lock)
        except OSError:
            pass
        _measure("MEASURE lock busy reject")

    def test_b_dead_pid_reclaim(self):
        lock = os.path.join(self.repo, ".git", "orchestration-commit-wave.lock")
        os.mkdir(lock)
        with open(os.path.join(lock, "owner"), "w") as f:
            f.write("999999999 %s\n" % time.time())  # dead pid
        os.makedirs(os.path.join(self.repo, "a"), exist_ok=True)
        with open(os.path.join(self.repo, "a", "reclaim.txt"), "w") as f:
            f.write("r\n")
        r = _run_cw(
            [
                "--front",
                "F-A",
                "--message",
                "reclaim ok",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            self.env,
            self.repo,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        names = _git(
            self.repo, ["show", "--name-only", "--pretty=format:", "HEAD"]
        ).stdout.strip().splitlines()
        self.assertIn("a/reclaim.txt", names)
        _measure("MEASURE dead-pid reclaim success")

    def test_b_commit_wave_passes_precommit(self):
        os.makedirs(os.path.join(self.repo, "a"), exist_ok=True)
        with open(os.path.join(self.repo, "a", "hook.txt"), "w") as f:
            f.write("hook\n")
        r = _run_cw(
            [
                "--front",
                "F-A",
                "--message",
                "via precommit",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            self.env,
            self.repo,
        )
        self.assertEqual(r.returncode, 0, r.stderr + r.stdout)
        _measure("MEASURE commit-wave passes pre-commit")


# ---------------------------------------------------------------------------
# (в) C1 activation
# ---------------------------------------------------------------------------


class TestC1Activation(unittest.TestCase):
    def setUp(self):
        self.tmp = _mw_tmpdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _state(self, fronts):
        return _make_state(self.tmp, fronts)

    def test_c_intersect_prefix_reject(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["a/**"]},
                {"id": "F2", "status": "proposed", "owns": ["a/b/**"]},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("пересечение", r.stderr)
        _measure("MEASURE C1 a/**∩a/b/** reject: %s" % r.stderr.strip())

    def test_c_identical_globs_reject(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["docs/**"]},
                {"id": "F2", "status": "proposed", "owns": ["docs/**"]},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("пересечение", r.stderr)
        _measure("MEASURE C1 identical reject")

    def test_c_a_star_b_star_pass(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["a/*"]},
                {"id": "F2", "status": "proposed", "owns": ["b/*"]},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        _measure("MEASURE C1 a/*∩b/* pass")

    def test_c_a_globstar_vs_a_pass(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["a/**"]},
                {"id": "F2", "status": "proposed", "owns": ["a"]},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        # library oracle too
        self.assertIsNone(owns.witness("a/**", "a"))
        _measure("MEASURE C1 a/**∩a pass")

    def test_c_empty_owns_warn_pass(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["a/**"]},
                {"id": "F2", "status": "proposed", "owns": []},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertEqual(r.returncode, 0)
        self.assertIn("owns пуст", r.stderr)
        _measure("MEASURE C1 empty owns warn+pass")

    def test_c_unsupported_glob_middle_starstar(self):
        state = self._state(
            [
                {"id": "F1", "status": "active", "owns": ["x/**"]},
                {"id": "F2", "status": "proposed", "owns": ["a/**/b"]},
            ]
        )
        r = _run_owns(
            ["--check-activation", "F2", "--state", state],
            {"ORCHESTRATION_DIR": state},
            self.tmp,
        )
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("неподдерживаемый glob", r.stderr)
        _measure("MEASURE C1 unsupported glob reject")


# ---------------------------------------------------------------------------
# (г) инцидент: dirty rules not captured by docs wave
# ---------------------------------------------------------------------------


class TestIncident(unittest.TestCase):
    def setUp(self):
        self.tmp = _mw_tmpdir()
        self.repo = os.path.join(self.tmp, "repo")
        _init_repo(self.repo)
        self.state = _make_state(
            self.tmp,
            [
                {"id": "F-DOCS", "status": "active", "owns": ["docs/**"]},
                {"id": "F-RULES", "status": "active", "owns": ["rules/**"]},
            ],
        )
        _install_precommit(self.repo)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_g_dirty_rules_not_in_docs_commit(self):
        os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
        os.makedirs(os.path.join(self.repo, "rules"), exist_ok=True)
        with open(os.path.join(self.repo, "rules", "card.md"), "w") as f:
            f.write("dirty neighbor\n")
        # NOT staged
        with open(os.path.join(self.repo, "docs", "note.md"), "w") as f:
            f.write("docs wave\n")
        r = _run_cw(
            [
                "--front",
                "F-DOCS",
                "--message",
                "docs only",
                "--cwd",
                self.repo,
                "--state",
                self.state,
            ],
            {"ORCHESTRATION_DIR": self.state},
            self.repo,
        )
        self.assertEqual(r.returncode, 0, r.stderr)
        stat = _git(self.repo, ["show", "--stat", "--pretty=format:", "HEAD"])
        self.assertIn("docs/note.md", stat.stdout)
        self.assertNotIn("rules", stat.stdout)
        # rules still dirty unstaged (porcelain may show ?? rules/ or ?? rules/card.md)
        st = _git(self.repo, ["status", "--porcelain", "-u"])
        self.assertTrue(
            any("rules" in ln for ln in st.stdout.splitlines()),
            st.stdout,
        )
        _measure("MEASURE incident dirty rules not in docs commit")


def main():
    # sanity: zero-dep — substring check on sources
    src = open(OWNS_PY, encoding="utf-8").read()
    cw = open(COMMIT_WAVE_PY, encoding="utf-8").read()
    needle = "orch" + "lib"
    assert needle not in src, "owns.py must be zero-dep"
    assert needle not in cw, "commit-wave.py must be zero-dep"
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(TestPreCommit))
    suite.addTests(loader.loadTestsFromTestCase(TestCommitWave))
    suite.addTests(loader.loadTestsFromTestCase(TestC1Activation))
    suite.addTests(loader.loadTestsFromTestCase(TestIncident))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    _measure(
        "MEASURE test_multiwrite ok=%s fail=%s err=%s"
        % (result.wasSuccessful(), len(result.failures), len(result.errors))
    )
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
