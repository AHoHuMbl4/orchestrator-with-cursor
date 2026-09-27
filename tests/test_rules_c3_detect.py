#!/usr/bin/env python3
# Живые пробы C3-DETECT (работы 1–5): /tmp-синтетика, без новых deps.
# Запуск из корня репо: python3 tests/test_rules_c3_detect.py
"""Пробы: undelivered/dead/opportunity/LRU/age-hours/fresh-тишина."""
from __future__ import print_function

import json
import os
import shutil
import sys
import tempfile
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO)

from bin import orchlib  # noqa: E402


def _pass(msg):
    print("PASS: %s" % msg)


def _fail(msg):
    print("FAIL: %s" % msg, file=sys.stderr)
    raise AssertionError(msg)


def _write_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _append_journal(state, entries):
    path = os.path.join(state, "journal.jsonl")
    with open(path, "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _mk_poly():
    """Изолированный kit+state в /tmp (или /var/tmp)."""
    base = "/var/tmp" if os.path.isdir("/var/tmp") else None
    root = tempfile.mkdtemp(prefix="c3-detect-", dir=base)
    kit = os.path.join(root, "kit")
    state = os.path.join(root, "state")
    os.makedirs(os.path.join(state, "counters"), exist_ok=True)
    os.makedirs(os.path.join(kit, "rules", "cards"), exist_ok=True)
    os.makedirs(os.path.join(kit, "rules", "archive"), exist_ok=True)
    _write_json(os.path.join(kit, "rules", "manifest.json"),
                {"cards": [], "aliases": {}})
    _write_json(os.path.join(state, "counters", "rules-hits.json"), {})
    open(os.path.join(state, "journal.jsonl"), "w", encoding="utf-8").close()
    # минимальный kit stub для lint-путей (не обязателен для детекторов)
    return root, kit, state


def _add(kit, cid, komu, kogda, born_at, category="промты", typ="DON'T"):
    parts = [("ловушка", "синтетика %s" % cid)]
    # add_rule_card ставит born_at=now — правим манифест после
    got = orchlib.add_rule_card(
        typ, category, komu, kogda, parts, run_ref="syn-%s" % cid,
        kit_dir=kit, card_id=cid)
    assert got == cid
    manifest = orchlib.load_manifest(kit, allow_migrate=False)
    for c in manifest.get("cards") or []:
        if c.get("id") == cid:
            c["born_at"] = born_at
            c["created"] = born_at
    orchlib.save_manifest(manifest, kit)
    return cid


def test_address_to_nowhere():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        born = now - 25 * 3600
        _add(kit, "cand-undeliv", "commander", "decomposition", born)
        _add(kit, "sib-ok", "commander", "decomposition", born - 100)
        # sibling delivered with matching journal.kogda × кому (i-join)
        _append_journal(state, [
            {"kind": "card_injected", "card": "sib-ok", "ts": born + 10,
             "kogda": "decomposition", "role": "commander", "hit": 1},
        ])
        _write_json(os.path.join(state, "counters", "rules-hits.json"),
                    {"sib-ok": 1})
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered") or []
        dead = chips.get("rules_dead") or []
        if "cand-undeliv" not in und:
            _fail("address-to-nowhere: expected undelivered, got %r" % und)
        if "cand-undeliv" in dead:
            _fail("address-to-nowhere: must not be dead")
        # после «правки адреса» и доставки — чип гаснет
        orchlib.record_hit(
            "cand-undeliv", kit_dir=kit, state=state,
            extra={"kogda": "decomposition", "role": "commander"})
        chips2 = orchlib.health_red_chips(state=state, kit_dir=kit)
        und2 = chips2.get("rules_undelivered") or []
        if "cand-undeliv" in und2:
            _fail("address-to-nowhere: chip should clear after delivery")
        _pass("address-to-nowhere → undelivered then clear")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_side_channel_not_opportunity():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        born = now - 25 * 3600
        # оба decomposition×general — адреса совпадают, но inject kogda=launch
        _add(kit, "dont-side-cand", "general", "decomposition", born)
        _add(kit, "dont-side-sib", "general", "decomposition", born - 50)
        _append_journal(state, [
            {"kind": "card_injected", "card": "dont-side-sib", "ts": born + 10,
             "kogda": "launch", "role": "meta/front-general.md", "hit": 1},
        ])
        _write_json(os.path.join(state, "counters", "rules-hits.json"),
                    {"dont-side-sib": 1})
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered") or []
        dead = chips.get("rules_dead") or []
        if "dont-side-cand" in und or "dont-side-cand" in dead:
            _fail("side-channel: expected silence, und=%r dead=%r" % (und, dead))
        _pass("side-channel not opportunity → silence")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_trigger_never_came():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        born = now - 25 * 3600
        # редкое когда=retro, нет событий/доставок/красных чипов
        _add(kit, "rare-retro", "executor", "retro", born)
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered") or []
        dead = chips.get("rules_dead") or []
        if "rare-retro" in und or "rare-retro" in dead:
            _fail("trigger-never: expected silence, und=%r dead=%r" % (und, dead))
        _pass("trigger never came → silence")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_delivered_hit_zero():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        born = now - 25 * 3600
        _add(kit, "dead-path", "commander", "acceptance", born)
        _append_journal(state, [
            {"kind": "card_injected", "card": "dead-path", "ts": born + 100,
             "kogda": "acceptance", "role": "commander", "hit": 1},
        ])
        # counters hit остаётся 0 (патология)
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        dead = chips.get("rules_dead") or []
        und = chips.get("rules_undelivered") or []
        if "dead-path" not in dead:
            _fail("delivered-hit0: expected rules_dead, got dead=%r" % dead)
        if "dead-path" in und:
            _fail("delivered-hit0: must not be undelivered")
        _pass("delivered hit=0 → rules_dead")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_lru_protects():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        # (1) undelivered age>grace — СТАРЕЙШИЙ hit=0: без protect уйдёт в moved первым.
        born_old = now - 25 * 3600
        _add(kit, "lru-undeliv", "commander", "decomposition", born_old,
             category="запуск")
        _add(kit, "lru-sib", "commander", "decomposition", born_old - 10,
             category="запуск")
        # (2) age=1ч, deliveries=0, opportunity (sibling inject) — тоже защищён;
        # старше filler → без protect во второй слот moved.
        born_young = now - 1 * 3600
        _add(kit, "lru-young", "commander", "decomposition", born_young,
             category="запуск")
        # sibling inject ПОСЛЕ born_young (и born_old): opportunity для обеих проб
        _append_journal(state, [
            {"kind": "card_injected", "card": "lru-sib", "ts": born_young + 5,
             "kogda": "decomposition", "role": "commander", "hit": 1},
        ])
        _write_json(os.path.join(state, "counters", "rules-hits.json"),
                    {"lru-sib": 1})
        # наполняем >25 активных hit=0 (кроме sibling с hit=1);
        # filler НОВЕЕ защищаемых (now−30мин − i·мин) — без protect ушли бы они
        cats = ("процессы", "маршрутизация", "промты")
        for i in range(26):
            _add(kit, "filler-%02d" % i, "executor", "retro",
                 now - 30 * 60 - i * 60, category=cats[i % len(cats)])
        # негативный контроль: среди hit=0 старейшие — защищаемые → protect обязателен
        manifest_pre = orchlib.load_manifest(kit, allow_migrate=False)
        active_pre = [c for c in (manifest_pre.get("cards") or [])
                      if not c.get("archived")]
        if len(active_pre) <= 25:
            _fail("setup: need active>25, got %d" % len(active_pre))
        hits_pre = {}
        hp = os.path.join(state, "counters", "rules-hits.json")
        if os.path.isfile(hp):
            with open(hp, encoding="utf-8") as f:
                hits_pre = json.load(f) or {}
        zeros_pre = [c for c in active_pre
                     if int(hits_pre.get(c.get("id")) or 0) == 0]
        zeros_pre.sort(key=lambda c: (float(c.get("created") or 0),
                                      str(c.get("id") or "")))
        need = len(active_pre) - 25
        protect_ids = {"lru-undeliv", "lru-young"}
        first_need = {c.get("id") for c in zeros_pre[:need]}
        if not protect_ids.issubset(first_need):
            _fail("setup: protected must be in oldest hit0 need-window, got %r"
                  % [c.get("id") for c in zeros_pre[:need + 2]])
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered") or []
        if "lru-undeliv" not in und:
            _fail("lru: setup undelivered missing: %r" % und)
        moved = orchlib.archive_lru(kit_dir=kit, state=state)
        manifest = orchlib.load_manifest(kit, allow_migrate=False)
        by_id = {c["id"]: c for c in manifest.get("cards") or []}
        if by_id.get("lru-undeliv", {}).get("archived"):
            _fail("lru: undelivered archived; moved=%r" % moved)
        if by_id.get("lru-young", {}).get("archived"):
            _fail("lru: young-opportunity archived; moved=%r" % moved)
        if "lru-undeliv" in moved or "lru-young" in moved:
            _fail("lru: protected ids in moved=%r" % moved)
        # хоть кто-то из filler должен уйти
        if not moved:
            _fail("lru: expected some fillers archived")
        _pass("LRU protects undelivered + young-opportunity")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_lru_protects_young_pathological_dead():
    """hit=0 ∧ deliv≥1 ∧ age<grace — LRU не архивирует (protect без age)."""
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        # Цель — СТАРЕЙШАЯ среди hit=0 при age<grace: без protect уйдёт в moved первой.
        born_young = now - 1 * 3600
        _add(kit, "lru-young-dead", "commander", "acceptance", born_young,
             category="запуск")
        _append_journal(state, [
            {"kind": "card_injected", "card": "lru-young-dead",
             "ts": born_young + 100, "kogda": "acceptance",
             "role": "commander", "hit": 1},
        ])
        # counters hit остаётся 0 (патология); age=1ч < RULES_DEAD_AGE_HOURS
        # filler НОВЕЕ цели (now−30мин − i·мин) — без protect архивировалась бы цель
        cats = ("процессы", "маршрутизация", "промты")
        for i in range(26):
            _add(kit, "fill-ypd-%02d" % i, "executor", "retro",
                 now - 30 * 60 - i * 60, category=cats[i % len(cats)])
        # негативный контроль порядка created: цель старейшая → protect обязателен
        manifest_pre = orchlib.load_manifest(kit, allow_migrate=False)
        active_pre = [c for c in (manifest_pre.get("cards") or [])
                      if not c.get("archived")]
        if len(active_pre) <= 25:
            _fail("setup: need active>25, got %d" % len(active_pre))
        by_created = sorted(
            active_pre,
            key=lambda c: (float(c.get("created") or 0), str(c.get("id") or "")))
        if by_created[0].get("id") != "lru-young-dead":
            _fail("setup: lru-young-dead must be oldest hit0, got %r"
                  % [c.get("id") for c in by_created[:3]])
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        dead = chips.get("rules_dead") or []
        if "lru-young-dead" in dead:
            _fail("young-path-dead: must NOT be in rules_dead (age<grace)")
        moved = orchlib.archive_lru(kit_dir=kit, state=state)
        manifest = orchlib.load_manifest(kit, allow_migrate=False)
        by_id = {c["id"]: c for c in manifest.get("cards") or []}
        if by_id.get("lru-young-dead", {}).get("archived"):
            _fail("lru: young pathological dead archived; moved=%r" % moved)
        if "lru-young-dead" in moved:
            _fail("lru: young pathological dead in moved=%r" % moved)
        if not moved:
            _fail("lru: expected some fillers archived")
        _pass("LRU protects young pathological dead (deliv≥1, age<grace)")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_age_by_hours():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        now = time.time()
        _add(kit, "age-25h", "commander", "decomposition", now - 25 * 3600)
        _add(kit, "age-23h", "commander", "decomposition", now - 23 * 3600)
        _add(kit, "age-sib", "commander", "decomposition", now - 30 * 3600)
        # возможность (i) для обоих кандидатов + 20+ end-прогонов (волны не старят)
        entries = [
            {"kind": "card_injected", "card": "age-sib", "ts": now - 20 * 3600,
             "kogda": "decomposition", "role": "commander", "hit": 1},
        ]
        for i in range(22):
            rid = "wave-end-%02d" % i
            entries.append({"kind": "start", "id": rid, "ts": now - 1000 + i,
                            "role": "code/coder.md", "front": "F-T"})
            entries.append({"kind": "end", "id": rid, "ts": now - 999 + i,
                            "exit": 0})
        _append_journal(state, entries)
        _write_json(os.path.join(state, "counters", "rules-hits.json"),
                    {"age-sib": 1})
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered") or []
        if "age-25h" not in und:
            _fail("age-hours: 25h should be candidate, und=%r" % und)
        if "age-23h" in und:
            _fail("age-hours: 23h must NOT be candidate despite 20+ ends")
        _pass("age by hours (25h yes / 23h no; waves ignored)")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_fresh_silence():
    root, kit, state = _mk_poly()
    os.environ["ORCHESTRATION_DIR"] = state
    os.environ["ORCH_RULES_NO_MIGRATE"] = "1"
    try:
        # пустой журнал, старая hit=0 — нет возможности → тишина обоих чипов
        now = time.time()
        _add(kit, "fresh-old", "panel", "post-tool", now - 48 * 3600)
        chips = orchlib.health_red_chips(state=state, kit_dir=kit)
        und = chips.get("rules_undelivered")
        dead = chips.get("rules_dead")
        if und is None or dead is None:
            _fail("fresh: keys missing und=%r dead=%r" % (und, dead))
        if und or dead:
            _fail("fresh silence: expected [],[] got und=%r dead=%r" % (und, dead))
        _pass("fresh silence → both chips empty")
    finally:
        os.environ.pop("ORCHESTRATION_DIR", None)
        os.environ.pop("ORCH_RULES_NO_MIGRATE", None)
        shutil.rmtree(root, ignore_errors=True)


def test_chip_red_in_kogda():
    if "chip-red" not in orchlib.RULES_KOGDA:
        _fail("chip-red missing from RULES_KOGDA")
    if not hasattr(orchlib, "RULES_DEAD_AGE_HOURS"):
        _fail("RULES_DEAD_AGE_HOURS missing")
    if float(orchlib.RULES_DEAD_AGE_HOURS) != 24.0:
        _fail("RULES_DEAD_AGE_HOURS != 24.0")
    _pass("chip-red + RULES_DEAD_AGE_HOURS=24.0")


def main():
    tests = [
        test_chip_red_in_kogda,
        test_address_to_nowhere,
        test_side_channel_not_opportunity,
        test_trigger_never_came,
        test_delivered_hit_zero,
        test_lru_protects,
        test_lru_protects_young_pathological_dead,
        test_age_by_hours,
        test_fresh_silence,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print("ERROR in %s: %s" % (fn.__name__, e), file=sys.stderr)
    if failed:
        print("RESULT: %d FAILED" % failed)
        return 1
    print("RESULT: ALL PASS (%d)" % len(tests))
    return 0


if __name__ == "__main__":
    sys.exit(main())
