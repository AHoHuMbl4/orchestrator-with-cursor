#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ADV-TC3: legacy-waiver видимый счётчик (не chip_silenced).

Полигон: только /tmp (tempfile). Живой /root/.orchestration не пишем.
"""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import unittest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
PANEL = os.path.join(REPO, "panel", "index.html")
REG_PRODUCT = os.path.join(REPO, "tests", "adversarial", "legacy-waivers.json")

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402

CANON_TS = 1790993555
CANON_COMMIT = "3598da0a8635954b0f894f5b6639ae8d0951166e"
COMMENT_RULES = (
    "ПРАВИЛА ВНЕСЕНИЯ: только git-правка реестра; "
    "только ts прогона < canon_ts_epoch; запрет новых; "
    "поля run_id,chip,reason=pre-canon-v1,ts_epoch=ts прогона,"
    "added_by=TAIL-CHIPS п.3; несоответствие пары/ts≥canon → игнор; "
    "не chip_silenced"
)


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


def _mk_state(prefix="lw-st-", fid="F-LW"):
    root = tempfile.mkdtemp(prefix=prefix, dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    _write_json(os.path.join(state, "fronts.json"), {
        "goal": "legacy-waiver-test",
        "fronts": [{
            "id": fid,
            "status": "active",
            "title": fid,
        }],
        "notes": "",
    })
    return root, state


def _waiver(run_id, chip, ts_epoch, reason="pre-canon-v1"):
    return {
        "run_id": run_id,
        "chip": chip,
        "reason": reason,
        "ts_epoch": ts_epoch,
        "added_by": "TAIL-CHIPS п.3",
    }


def _mk_kit(waivers, strip_probes_label=False, write_registry=True):
    kit = tempfile.mkdtemp(prefix="lw-kit-", dir="/tmp")
    os.makedirs(os.path.join(kit, "tests", "adversarial"), exist_ok=True)
    os.makedirs(os.path.join(kit, "panel"), exist_ok=True)
    if write_registry:
        _write_json(
            os.path.join(kit, "tests", "adversarial", "legacy-waivers.json"),
            {
                "canon_ts_epoch": CANON_TS,
                "canon_commit": CANON_COMMIT,
                "_comment": COMMENT_RULES,
                "waivers": waivers,
            },
        )
    src = os.path.join(REPO, "panel", "index.html")
    dst = os.path.join(kit, "panel", "index.html")
    with open(src, "r", encoding="utf-8") as f:
        txt = f.read()
    if strip_probes_label:
        txt = txt.replace("probes_missing: \"нет пробы/квитанции\",", "")
        txt = txt.replace('probes_missing: "нет пробы/квитанции",', "")
    _write_text(dst, txt)
    return kit


def _panel_counts(chips):
    return {k: len(v) if isinstance(v, list) else 0 for k, v in chips.items()}


def _chip_cnv_run(state, rid, ts):
    """commit_no_verify красный список содержит run_id (chip без front)."""
    _append_journal(state, [
        {
            "kind": "start", "id": rid, "ts": ts,
            "front": "F-LW", "role": "code/coder.md", "engine": "local",
        },
        {"kind": "end", "id": rid, "ts": ts + 1, "exit": 0, "gates": []},
        {"kind": "chip", "name": "commit_no_verify", "id": rid, "ts": ts + 1},
    ])


def _code_wave_no_receipt(state, rid, ts, fid="F-LW"):
    run_dir = os.path.join(state, "sessions", "s1", "runs", rid)
    os.makedirs(run_dir, exist_ok=True)
    _write_text(os.path.join(run_dir, "artifact.md"), "# a %s\n" % rid)
    _append_journal(state, [
        {
            "kind": "start", "id": rid, "ts": ts,
            "front": fid, "role": "code/coder.md", "engine": "local",
        },
        {"kind": "end", "id": rid, "ts": ts + 1, "exit": 0, "gates": []},
    ])


class TestProductRegistryAndPanel(unittest.TestCase):
    def test_registry_schema_and_panel_label(self):
        _measure("VALIDATE product legacy-waivers.json + panel label")
        with open(REG_PRODUCT, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertEqual(data["canon_ts_epoch"], CANON_TS)
        self.assertEqual(data["canon_commit"], CANON_COMMIT)
        self.assertIn("только git-правка реестра", data["_comment"])
        self.assertIn("не chip_silenced", data["_comment"])
        self.assertIn("reason=pre-canon-v1", data["_comment"])
        self.assertIsInstance(data["waivers"], list)
        for w in data["waivers"]:
            self.assertNotEqual(w.get("run_id"), "")
            self.assertFalse(
                str(w.get("run_id", "")).startswith("cursor-"),
                "живой journal id в kit",
            )
        with open(PANEL, "r", encoding="utf-8") as f:
            panel = f.read()
        self.assertIn("legacy: \"legacy\"", panel)
        self.assertIn('k === "legacy"', panel)
        src = os.path.join(BIN, "orchlib.py")
        with open(src, "r", encoding="utf-8") as f:
            orch = f.read()
        self.assertIn('"legacy": []', orch)


class TestLegacyWaivers(unittest.TestCase):
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

    def test_a_pre_canon_waiver_moves_to_legacy(self):
        _measure("A historical ts<canon + waiver → red empty, legacy=1")
        st_root, state = _mk_state()
        rid = "lw-hist-a"
        ts = CANON_TS - 1000
        _chip_cnv_run(state, rid, ts)
        kit = _mk_kit([_waiver(rid, "commit_no_verify", ts)])
        self._track(st_root, kit)
        os.environ["ORCHESTRATION_DIR"] = state
        orchlib._fronts_base = None
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        self.assertNotIn(rid, chips.get("commit_no_verify") or [])
        self.assertEqual(len(chips.get("legacy") or []), 1)
        self.assertEqual(chips["legacy"], ["%s:commit_no_verify" % rid])
        counts = _panel_counts(chips)
        self.assertEqual(counts.get("legacy"), 1)
        _measure("RESULT A OK")

    def test_b_ts_ge_canon_ignored(self):
        _measure("B ts>=canon + waiver → still red, not in legacy")
        st_root, state = _mk_state()
        rid = "lw-post-b"
        ts = CANON_TS
        _chip_cnv_run(state, rid, ts)
        kit = _mk_kit([_waiver(rid, "commit_no_verify", ts)])
        self._track(st_root, kit)
        os.environ["ORCHESTRATION_DIR"] = state
        orchlib._fronts_base = None
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        self.assertIn(rid, chips.get("commit_no_verify") or [])
        pair = "%s:commit_no_verify" % rid
        self.assertNotIn(pair, chips.get("legacy") or [])
        _measure("RESULT B OK")

    def test_c_wrong_pair_ignored(self):
        _measure("C waiver wrong chip/run_id → not applied")
        st_root, state = _mk_state()
        rid = "lw-pair-c"
        ts = CANON_TS - 50
        _chip_cnv_run(state, rid, ts)
        kit = _mk_kit([
            _waiver(rid, "multi_write_front", ts),
            _waiver("other-run", "commit_no_verify", ts),
        ])
        self._track(st_root, kit)
        os.environ["ORCHESTRATION_DIR"] = state
        orchlib._fronts_base = None
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        self.assertIn(rid, chips.get("commit_no_verify") or [])
        self.assertEqual(chips.get("legacy") or [], [])
        _measure("RESULT C OK")

    def test_d_chip_silenced_unchanged_by_other_waiver(self):
        _measure("D chip_silenced same with/without registry; other waiver no mute")
        st_root, state = _mk_state()
        probe_rid = "lw-probe-d"
        cnv_rid = "lw-cnv-d"
        ts = CANON_TS - 200
        _code_wave_no_receipt(state, probe_rid, ts)
        _chip_cnv_run(state, cnv_rid, ts + 10)
        kit_with = _mk_kit(
            [_waiver(cnv_rid, "commit_no_verify", ts + 10)],
            strip_probes_label=True,
        )
        kit_without = _mk_kit([], strip_probes_label=True, write_registry=False)
        self._track(st_root, kit_with, kit_without)
        os.environ["ORCHESTRATION_DIR"] = state
        orchlib._fronts_base = None

        raw = orchlib.probes_missing(state=state)
        self.assertIn(probe_rid, raw)
        sil_rep = orchlib.chip_silenced_ids(
            state=state, kit_dir=kit_with, reported_probes=[])
        sil_rep2 = orchlib.chip_silenced_ids(
            state=state, kit_dir=kit_without, reported_probes=[])
        self.assertEqual(sil_rep, sil_rep2)
        self.assertTrue(sil_rep)

        chips_with = orchlib.health_red_chips(state=state, kit_dir=kit_with)
        chips_without = orchlib.health_red_chips(
            state=state, kit_dir=kit_without)
        self.assertEqual(
            chips_with.get("chip_silenced"),
            chips_without.get("chip_silenced"),
        )
        self.assertTrue(chips_with.get("chip_silenced"))
        self.assertIn(probe_rid, chips_with["chip_silenced"])
        self.assertNotIn(cnv_rid, chips_with.get("commit_no_verify") or [])
        self.assertIn(cnv_rid, chips_without.get("commit_no_verify") or [])
        _measure("RESULT D OK")


if __name__ == "__main__":
    unittest.main()
