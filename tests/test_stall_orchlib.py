#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""STALL-C4-W2 + STALL-FIX1: orchlib stall params + parse_probe_receipt ts.

Запуск: cd /root/orchestrator-with-cursor && python3 tests/test_stall_orchlib.py
State: только ORCHESTRATION_DIR=/tmp/stall-orchlib-…; /root/.orchestration не трогаем.
Порт 8765 не слушаем (только JSON-поля params / parse_probe_receipt).

Покрытие (params):
  (а) load_params без stall_s/max_wall_s в файле → validate OK;
      после merge DEFAULTS: max_wall_s есть, stall_s нет, timeout_s есть
  (б) execution.stall_s=650 → validate OK при любом timeout_s
  (в) execution.stall_s=10 (ниже RANGES) → ошибка валидации
  (г) execution.max_wall_s=86400 проходит RANGES / validate
  (д) seed/save_params на дефолтном конфиге — без падений (tmp state)

Покрытие (квитанция §3 / ts):
  (а) ts epoch-числом → валидна
  (б) ts ISO Z → валидна
  (в) ts ISO +03:00 → валидна и epoch == (б)
  (г) ts «мусор» → (False, ts_not_number)
  (д) ts ISO раньше mtime артефакта → ts_stale
"""
from __future__ import print_function

import copy
import json
import os
import shutil
import sys
import tempfile
import uuid

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BIN = os.path.join(REPO, "bin")
LIVE_STATE = "/root/.orchestration"

if BIN not in sys.path:
    sys.path.insert(0, BIN)

import orchlib  # noqa: E402

# Канонический UTC-момент для ISO Z / +03:00 (эквивалентны по epoch)
_ISO_Z = "2026-09-28T09:15:54Z"
_ISO_OFFSET = "2026-09-28T12:15:54+03:00"


def _pass(msg):
    print("PASS: %s" % msg)


def _fail(msg):
    print("FAIL: %s" % msg, file=sys.stderr)
    raise AssertionError(msg)


def _assert_not_live(path):
    live = os.path.realpath(LIVE_STATE)
    cur = os.path.realpath(path)
    if cur == live or cur.startswith(live + os.sep):
        _fail("poly touches live state: %s" % path)


def _mk_poly(label="orch"):
    """Изолированный state в /tmp; session_UUID с дефисами (где уместно)."""
    root = tempfile.mkdtemp(prefix="stall-orchlib-", dir="/tmp")
    state = os.path.join(root, "state")
    os.makedirs(state, exist_ok=True)
    sid = "session_%s" % uuid.uuid4()
    os.makedirs(os.path.join(state, "sessions", sid), exist_ok=True)
    os.environ["ORCHESTRATION_DIR"] = state
    orchlib._params_base = None
    _assert_not_live(state)
    return root, state, sid


def _write_params(state, data):
    pf = os.path.join(state, "params.json")
    with open(pf, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return pf


def _minimal_params_no_stall_wall():
    """params без stall_s/max_wall_s (compat: только timeout_s)."""
    return {
        "orchestration": {"enabled": True, "hierarchy": "auto"},
        "task": {"description_file": ".orchestration/compass.md"},
        "execution": {
            "executor": "auto",
            "on_cursor_fail": "ask",
            "parallel_per_task": 3,
            "timeout_s": 1800,
            "retry_on_fail": 1,
            "ask_before_runs": 20,
        },
        "review": {"reviewers_per_diff": 3, "max_rounds": 3},
        "panel": {"host": "127.0.0.1", "port": 18765},
    }


def _mk_artifact(root, content="probe artifact\n"):
    """Артефакт в /tmp-полигоне; возвращает (path, mtime)."""
    path = os.path.join(root, "artifact.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    mtime = float(os.path.getmtime(path))
    _assert_not_live(path)
    return path, mtime


def _receipt_text(ts, artifact_name="artifact.md"):
    """Минимальная валидная inline-квитанция §3 с заданным ts."""
    return (
        "probe: stall-fix1; cmd: true; exit: 0; oracle_match: true; "
        "ts: %s; critic_id: FIX1-CRIT; artifact: %s\n" % (ts, artifact_name)
    )


def _parse_ts_only(raw):
    """Нормализация ts через приватный хелпер (для сверки epoch (б)/(в))."""
    return orchlib._parse_receipt_ts(raw)


# ---------------------------------------------------------------------------
# (а) load_params без stall_s/max_wall_s
# ---------------------------------------------------------------------------

def test_a_load_params_compat_timeout_only():
    root, state, sid = _mk_poly("a")
    try:
        raw = _minimal_params_no_stall_wall()
        if "stall_s" in raw["execution"] or "max_wall_s" in raw["execution"]:
            _fail("(а) fixture must not contain stall_s/max_wall_s")
        _write_params(state, raw)

        p = orchlib.load_params()
        errs = orchlib.validate_params(p)
        if errs:
            _fail("(а) validate_params errors: %s" % errs)

        ex = p.get("execution") or {}
        if "max_wall_s" not in ex:
            _fail("(а) after merge DEFAULTS: max_wall_s missing")
        if ex.get("max_wall_s") != orchlib.DEFAULTS["execution"]["max_wall_s"]:
            _fail("(а) max_wall_s=%r want DEFAULTS %r"
                  % (ex.get("max_wall_s"),
                     orchlib.DEFAULTS["execution"]["max_wall_s"]))
        if "stall_s" in ex:
            _fail("(а) stall_s must be absent after merge (got %r)" % ex.get("stall_s"))
        if "timeout_s" not in ex:
            _fail("(а) timeout_s must be present (compat stall source)")
        if ex.get("timeout_s") != 1800:
            _fail("(а) timeout_s=%r want 1800" % ex.get("timeout_s"))

        # файл на диске по-прежнему без ключей (load не дописывает их обратно)
        with open(os.path.join(state, "params.json"), "r", encoding="utf-8") as f:
            on_disk = json.load(f)
        disk_ex = on_disk.get("execution") or {}
        if "stall_s" in disk_ex or "max_wall_s" in disk_ex:
            _fail("(а) on-disk file gained stall/max_wall unexpectedly")

        _pass("(а) load_params compat: max_wall_s from DEFAULTS, no stall_s, "
              "timeout_s present (sid=%s)" % sid)
    finally:
        orchlib._params_base = None
        os.environ.pop("ORCHESTRATION_DIR", None)
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# (б) stall_s=650 валиден при любом timeout_s
# ---------------------------------------------------------------------------

def test_b_stall_s_650_valid_any_timeout():
    timeouts = (60, 600, 1800, 7200, 21600)
    for tmo in timeouts:
        p = copy.deepcopy(orchlib.DEFAULTS)
        p["execution"]["stall_s"] = 650
        p["execution"]["timeout_s"] = tmo
        errs = orchlib.validate_params(p)
        if errs:
            _fail("(б) stall_s=650 timeout_s=%s → errs %s" % (tmo, errs))
    # приоритет наличия ключа: stall_s в RANGES независимо от timeout_s
    lo, hi = orchlib.RANGES["execution.stall_s"]
    if not (lo <= 650 <= hi):
        _fail("(б) 650 not in RANGES %s..%s" % (lo, hi))
    _pass("(б) stall_s=650 validate OK for timeout_s in %s" % (timeouts,))


# ---------------------------------------------------------------------------
# (в) stall_s=10 ниже RANGES
# ---------------------------------------------------------------------------

def test_c_stall_s_below_range():
    p = copy.deepcopy(orchlib.DEFAULTS)
    p["execution"]["stall_s"] = 10
    errs = orchlib.validate_params(p)
    if not errs:
        _fail("(в) expected validation error for stall_s=10")
    joined = "; ".join(errs)
    if "execution.stall_s" not in joined:
        _fail("(в) error must mention execution.stall_s, got %r" % joined)
    if "10" not in joined:
        _fail("(в) error must mention value 10, got %r" % joined)
    _pass("(в) stall_s=10 → validation error: %s" % joined)


# ---------------------------------------------------------------------------
# (г) max_wall_s=86400 в RANGES
# ---------------------------------------------------------------------------

def test_d_max_wall_s_86400():
    lo, hi = orchlib.RANGES["execution.max_wall_s"]
    if not (lo <= 86400 <= hi):
        _fail("(г) 86400 not in RANGES %s..%s" % (lo, hi))
    p = copy.deepcopy(orchlib.DEFAULTS)
    p["execution"]["max_wall_s"] = 86400
    errs = orchlib.validate_params(p)
    if errs:
        _fail("(г) max_wall_s=86400 validate errs: %s" % errs)
    _pass("(г) max_wall_s=86400 in RANGES %s..%s, validate OK" % (lo, hi))


# ---------------------------------------------------------------------------
# (д) seed + save_params на дефолтном конфиге (tmp state)
# ---------------------------------------------------------------------------

def test_e_seed_save_params_default():
    root, state, sid = _mk_poly("e")
    try:
        pf = os.path.join(state, "params.json")
        if os.path.exists(pf):
            _fail("(д) params.json must not exist before seed")

        # first-boot seed via load_params
        p = orchlib.load_params()
        if not os.path.exists(pf):
            _fail("(д) seed did not create params.json")
        errs = orchlib.validate_params(p)
        if errs:
            _fail("(д) seeded params invalid: %s" % errs)
        ex = p.get("execution") or {}
        if "timeout_s" not in ex:
            _fail("(д) seeded: timeout_s missing")
        if "max_wall_s" not in ex:
            _fail("(д) seeded merge: max_wall_s missing from DEFAULTS")
        if "stall_s" in ex:
            _fail("(д) seeded: stall_s must stay absent (optional)")

        # round-trip save_params without crash
        orchlib.save_params(p)
        p2 = orchlib.load_params()
        errs2 = orchlib.validate_params(p2)
        if errs2:
            _fail("(д) after save_params invalid: %s" % errs2)
        if p2["execution"].get("timeout_s") != p["execution"].get("timeout_s"):
            _fail("(д) timeout_s changed after save")
        if p2["execution"].get("max_wall_s") != p["execution"].get("max_wall_s"):
            _fail("(д) max_wall_s changed after save")

        _assert_not_live(state)
        _pass("(д) seed+save_params default OK (sid=%s, state=%s)" % (sid, state))
    finally:
        orchlib._params_base = None
        os.environ.pop("ORCHESTRATION_DIR", None)
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# квитанция §3: ts epoch / ISO / мусор / stale
# ---------------------------------------------------------------------------

def test_receipt_a_ts_epoch_valid():
    root = tempfile.mkdtemp(prefix="stall-receipt-", dir="/tmp")
    try:
        art, mtime = _mk_artifact(root)
        ts = mtime + 10.0
        text = _receipt_text("%.3f" % ts)
        ok, reason = orchlib.parse_probe_receipt(text, artifact_path=art)
        if not ok:
            _fail("receipt (а) epoch ts expected ok, got (%s, %s)" % (ok, reason))
        if reason != "ok":
            _fail("receipt (а) reason=%r want ok" % reason)
        _pass("receipt (а) ts epoch-числом валидна (ts=%.3f ≥ mtime=%.3f)"
              % (ts, mtime))
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_receipt_b_ts_iso_z_valid():
    root = tempfile.mkdtemp(prefix="stall-receipt-", dir="/tmp")
    try:
        art, _mtime = _mk_artifact(root)
        # артефакт «в прошлом» относительно канонического ISO
        past = orchlib._parse_receipt_ts(_ISO_Z) - 3600.0
        os.utime(art, (past, past))
        text = _receipt_text(_ISO_Z)
        ok, reason = orchlib.parse_probe_receipt(text, artifact_path=art)
        if not ok:
            _fail("receipt (б) ISO Z expected ok, got (%s, %s)" % (ok, reason))
        _pass("receipt (б) ts ISO Z валидна (%s)" % _ISO_Z)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_receipt_c_ts_iso_offset_eq_z():
    root = tempfile.mkdtemp(prefix="stall-receipt-", dir="/tmp")
    try:
        art, _mtime = _mk_artifact(root)
        past = orchlib._parse_receipt_ts(_ISO_Z) - 3600.0
        os.utime(art, (past, past))
        text = _receipt_text(_ISO_OFFSET)
        ok, reason = orchlib.parse_probe_receipt(text, artifact_path=art)
        if not ok:
            _fail("receipt (в) ISO +03:00 expected ok, got (%s, %s)"
                  % (ok, reason))
        ep_z = _parse_ts_only(_ISO_Z)
        ep_off = _parse_ts_only(_ISO_OFFSET)
        if ep_z is None or ep_off is None:
            _fail("receipt (в) normalize failed Z=%r off=%r" % (ep_z, ep_off))
        if ep_z != ep_off:
            _fail("receipt (в) epoch Z=%r != +03:00=%r" % (ep_z, ep_off))
        _pass("receipt (в) ts ISO +03:00 валидна и epoch==ISO Z (%.0f)"
              % ep_z)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_receipt_d_ts_garbage_not_number():
    text = _receipt_text("not-a-timestamp")
    ok, reason = orchlib.parse_probe_receipt(text)
    if ok:
        _fail("receipt (г) garbage ts must fail, got ok")
    if reason != "ts_not_number":
        _fail("receipt (г) reason=%r want ts_not_number" % reason)
    _pass("receipt (г) ts «мусор» → (False, ts_not_number)")


def test_receipt_e_ts_iso_stale():
    root = tempfile.mkdtemp(prefix="stall-receipt-", dir="/tmp")
    try:
        art, _mtime = _mk_artifact(root)
        # mtime артефакта ПОЗЖЕ ISO ts → ts_stale
        future = orchlib._parse_receipt_ts(_ISO_Z) + 3600.0
        os.utime(art, (future, future))
        text = _receipt_text(_ISO_Z)
        ok, reason = orchlib.parse_probe_receipt(text, artifact_path=art)
        if ok:
            _fail("receipt (д) stale ISO must fail, got ok")
        if reason != "ts_stale":
            _fail("receipt (д) reason=%r want ts_stale" % reason)
        _pass("receipt (д) ts ISO раньше mtime → ts_stale")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def main():
    tests = [
        test_a_load_params_compat_timeout_only,
        test_b_stall_s_650_valid_any_timeout,
        test_c_stall_s_below_range,
        test_d_max_wall_s_86400,
        test_e_seed_save_params_default,
        test_receipt_a_ts_epoch_valid,
        test_receipt_b_ts_iso_z_valid,
        test_receipt_c_ts_iso_offset_eq_z,
        test_receipt_d_ts_garbage_not_number,
        test_receipt_e_ts_iso_stale,
    ]
    failed = 0
    for fn in tests:
        try:
            fn()
        except Exception as e:
            failed += 1
            print("FAIL: %s: %s" % (fn.__name__, e), file=sys.stderr)
    if failed:
        print("RESULT: %d/%d FAILED" % (failed, len(tests)))
        return 1
    print("RESULT: %d/%d PASSED" % (len(tests), len(tests)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
