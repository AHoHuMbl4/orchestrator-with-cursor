#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADV-TC4: health-чип kit_dirty_outside_wave (tracked porcelain вне волны).

Полигон: только /tmp (tempfile). Живой /root/.orchestration не пишем.
"""
from __future__ import print_function

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
PANEL = os.path.join(REPO, "panel", "index.html")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402


def _measure(line):
    sys.stdout.write("\n%s\n" % line)
    sys.stdout.flush()


def _write_json(path, obj):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _write_text(path, text):
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def _append_journal(state, entries):
    path = os.path.join(state, "journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _git(repo, args, check=True):
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    r = subprocess.run(
        ["git", "-C", repo] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        universal_newlines=True,
        env=env,
    )
    if check and r.returncode != 0:
        raise AssertionError(
            "git %s rc=%s\n%s\n%s"
            % (args, r.returncode, r.stdout, r.stderr)
        )
    return r


def _mk_state(prefix="kdow-st-"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "kit-dirty-test",
        "fronts": [{
            "id": "F-KDOW",
            "status": "active",
            "title": "F-KDOW",
        }],
        "notes": "",
    })
    return root, state


def _mk_kit_repo(prefix="kdow-kit-"):
    kit = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    _git(kit, ["init"])
    _git(kit, ["config", "user.name", "kdow-test"])
    _git(kit, ["config", "user.email", "kdow@test.local"])
    _write_text(os.path.join(kit, "README.md"), "clean\n")
    _write_text(os.path.join(kit, "notes.outside"), "outside\n")
    _git(kit, ["add", "--", "README.md", "notes.outside"])
    _git(kit, ["commit", "-m", "init"])
    return kit


class TestKitDirtyOutsideWave(unittest.TestCase):
    def setUp(self):
        self._prev = os.environ.get("ORCHESTRATION_DIR")
        self.roots = []

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("ORCHESTRATION_DIR", None)
        else:
            os.environ["ORCHESTRATION_DIR"] = self._prev
        orchlib._fronts_base = None
        for r in self.roots:
            shutil.rmtree(r, ignore_errors=True)

    def _track(self, *paths):
        for p in paths:
            self.roots.append(p)

    def _chips(self, state, kit):
        os.environ["ORCHESTRATION_DIR"] = state
        orchlib._fronts_base = None
        return orchlib.health_red_chips(state=state, kit_dir=kit)

    def test_a_dirty_readme_empty_journal_red(self):
        _measure("A dirty README.md + empty journal → chip lists README.md")
        st_root, state = _mk_state()
        kit = _mk_kit_repo()
        self._track(st_root, kit)
        with open(os.path.join(kit, "README.md"), "a", encoding="utf-8") as f:
            f.write("echo-hands\n")
        chips = self._chips(state, kit)
        paths = chips.get("kit_dirty_outside_wave") or []
        self.assertIn("README.md", paths)
        _measure("RESULT A OK")

    def test_b_open_coder_start_suppresses(self):
        _measure("B dirty README + open code/coder.md start → empty")
        st_root, state = _mk_state()
        kit = _mk_kit_repo()
        self._track(st_root, kit)
        with open(os.path.join(kit, "README.md"), "a", encoding="utf-8") as f:
            f.write("echo-hands\n")
        _append_journal(state, [{
            "kind": "start", "id": "wave-coder-open", "ts": 1,
            "role": "code/coder.md", "engine": "local",
        }])
        chips = self._chips(state, kit)
        self.assertEqual(chips.get("kit_dirty_outside_wave") or [], [])
        _measure("RESULT B OK")

    def test_c_clean_tree_empty(self):
        _measure("C clean tree → empty")
        st_root, state = _mk_state()
        kit = _mk_kit_repo()
        self._track(st_root, kit)
        chips = self._chips(state, kit)
        self.assertEqual(chips.get("kit_dirty_outside_wave") or [], [])
        _measure("RESULT C OK")

    def test_d_outside_mask_dirty_empty(self):
        _measure("D dirty notes.outside, README clean → empty")
        st_root, state = _mk_state()
        kit = _mk_kit_repo()
        self._track(st_root, kit)
        with open(os.path.join(kit, "notes.outside"), "a", encoding="utf-8") as f:
            f.write("not-in-mask\n")
        chips = self._chips(state, kit)
        self.assertEqual(chips.get("kit_dirty_outside_wave") or [], [])
        _measure("RESULT D OK")

    def test_e_wiring_mute(self):
        _measure("E wiring mute: empty literal + health return + panel label")
        src_path = os.path.join(BIN, "orchlib.py")
        with open(src_path, "r", encoding="utf-8") as f:
            orch = f.read()
        self.assertIn('"kit_dirty_outside_wave": []', orch)
        with open(PANEL, "r", encoding="utf-8") as f:
            panel = f.read()
        self.assertIn(
            'kit_dirty_outside_wave: "правки кита вне волны"', panel)
        self.assertNotIn(
            'k === "kit_dirty_outside_wave"',
            panel.split("const isWarn")[1].split(";")[0]
            if "const isWarn" in panel else "",
        )
        st_root, state = _mk_state()
        kit = _mk_kit_repo()
        self._track(st_root, kit)
        chips = self._chips(state, kit)
        self.assertIn("kit_dirty_outside_wave", chips)
        self.assertIsInstance(chips["kit_dirty_outside_wave"], list)
        _measure("RESULT E OK")


if __name__ == "__main__":
    unittest.main()
