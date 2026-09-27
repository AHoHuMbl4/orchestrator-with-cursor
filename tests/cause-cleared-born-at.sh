#!/usr/bin/env bash
# Пробы гейта CAUSE-CLEARED + иммутабельность born_at (C3-LINT).
# Запуск из корня репо: bash tests/cause-cleared-born-at.sh
# Только python3/git/bash; без новых deps. Стиль: tests/fresh-install-e2e.sh.
set -euo pipefail

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ -d /var/tmp ] && [ ! -e /var/tmp/.orchestration ]; then
  TMP="$(mktemp -d -p /var/tmp)"
else
  TMP="$(mktemp -d)"
fi
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT

KIT="$TMP/kit"
STATE="$TMP/state"
mkdir -p "$STATE/sessions/sess-probe/runs"
git clone --quiet "file://${REPO}" "$KIT"
git -C "$KIT" config user.email "probe@local"
git -C "$KIT" config user.name "probe"

# Подтянуть незакоммиченные правки волны (если тест гоняют до commit).
if ! grep -q 'ORCH_LINT_BASELINE_SHA' "$KIT/bin/orch-lint.py" 2>/dev/null; then
  cp -f "$REPO/bin/orchlib.py" "$KIT/bin/orchlib.py"
  cp -f "$REPO/bin/orch-lint.py" "$KIT/bin/orch-lint.py"
  git -C "$KIT" add bin/orchlib.py bin/orch-lint.py
  git -C "$KIT" commit -qm "wip: sync working tree for probes"
fi

BASELINE="$(python3 -c "import sys; sys.path.insert(0,'$KIT/bin'); import orchlib; print(orchlib.ORCH_LINT_BASELINE_SHA)")"
# Если baseline ещё не предок (свежий clone на том же SHA) — ок.
# Коммиты пробы идут поверх HEAD clone (= baseline до/после wip).
export ORCHESTRATION_DIR="$STATE"

run_lint() {
  (cd "$KIT" && python3 bin/orch-lint.py --kit-dir "$KIT") || return $?
  return 0
}

# --- (а) ослабление без CAUSE-CLEARED → FAIL ---
python3 - <<PY
from pathlib import Path
p = Path("$KIT/bin/orchlib.py")
t = p.read_text(encoding="utf-8")
old, new = "RULES_ACTIVE_LIMIT = 25", "RULES_ACTIVE_LIMIT = 20"
assert old in t, "anchor RULES_ACTIVE_LIMIT = 25 missing"
p.write_text(t.replace(old, new, 1), encoding="utf-8")
PY
git -C "$KIT" add bin/orchlib.py
git -C "$KIT" commit -qm "probe(a): weaken RULES_ACTIVE_LIMIT without clearance"
set +e
OUT_A="$(run_lint 2>&1)"
EC_A=$?
set -e
echo "$OUT_A" | grep -q 'cause-cleared:' || fail "(a) missing cause-cleared in output: $OUT_A"
[ "$EC_A" -ne 0 ] || fail "(a) expected exit!=0, got 0"
pass "(a) weaken without CAUSE-CLEARED → exit $EC_A + cause-cleared"

# --- (б) ослабление + CAUSE-CLEARED:<run-id> + артефакт → гейт молчит ---
git -C "$KIT" reset --hard HEAD~1 >/dev/null
python3 - <<PY
from pathlib import Path
p = Path("$KIT/bin/orchlib.py")
t = p.read_text(encoding="utf-8")
p.write_text(t.replace("RULES_ACTIVE_LIMIT = 25", "RULES_ACTIVE_LIMIT = 20", 1), encoding="utf-8")
PY
RUN_ID="probe-cause-b"
ART_DIR="$STATE/sessions/sess-probe/runs/$RUN_ID"
mkdir -p "$ART_DIR"
printf 'CAUSE-CLEARED: RULES_ACTIVE_LIMIT 25→20 justified; measure ok\n' > "$ART_DIR/artifact.md"
git -C "$KIT" add bin/orchlib.py
git -C "$KIT" commit -qm "probe(b): weaken with CAUSE-CLEARED:${RUN_ID}"
set +e
OUT_B="$(run_lint 2>&1)"
EC_B=$?
set -e
echo "$OUT_B" | grep -q 'cause-cleared:' && fail "(b) still flagged cause-cleared: $OUT_B"
pass "(b) weaken + CAUSE-CLEARED:${RUN_ID} → no cause-cleared (lint exit $EC_B)"

# --- (д) рост LIMIT без маркера → НЕ флагается ---
git -C "$KIT" reset --hard HEAD~1 >/dev/null
python3 - <<PY
from pathlib import Path
p = Path("$KIT/bin/orchlib.py")
t = p.read_text(encoding="utf-8")
p.write_text(t.replace("RULES_ACTIVE_LIMIT = 25", "RULES_ACTIVE_LIMIT = 30", 1), encoding="utf-8")
PY
git -C "$KIT" add bin/orchlib.py
git -C "$KIT" commit -qm "probe(d): strengthen RULES_ACTIVE_LIMIT no marker"
set +e
OUT_D="$(run_lint 2>&1)"
EC_D=$?
set -e
echo "$OUT_D" | grep -q 'cause-cleared:' && fail "(d) false-positive cause-cleared: $OUT_D"
pass "(d) limit growth without marker → no cause-cleared (lint exit $EC_D)"

# --- (в) born_at restamp value→value → FAIL ---
git -C "$KIT" reset --hard HEAD~1 >/dev/null
CARD_ID="$(python3 - <<PY
import json
path = "$KIT/rules/manifest.json"
with open(path, "r", encoding="utf-8") as f:
    data = json.load(f)
c = data["cards"][0]
c["born_at"] = float(c["born_at"]) + 1.0
with open(path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
    f.write("\n")
print(c["id"])
PY
)"
git -C "$KIT" add rules/manifest.json
git -C "$KIT" commit -qm "probe(v): restamp born_at"
set +e
OUT_V="$(run_lint 2>&1)"
EC_V=$?
set -e
echo "$OUT_V" | grep -q 'restamped' || fail "(v) missing restamped: $OUT_V"
[ "$EC_V" -ne 0 ] || fail "(v) expected exit!=0"
pass "(v) born_at restamp → exit $EC_V + restamped ($CARD_ID)"

# --- resurrect: born_at/created неизменены, migrated_at обновлён ---
git -C "$KIT" reset --hard HEAD~1 >/dev/null
python3 - <<PY
import os, sys, time, shutil
sys.path.insert(0, os.path.join("$KIT", "bin"))
import orchlib
kit = "$KIT"
m = orchlib.load_manifest(kit)
card = None
for c in m.get("cards") or []:
    if c.get("archived"):
        card = c
        break
if card is None:
    card = (m.get("cards") or [None])[0]
    assert card, "no cards"
    src = orchlib._rules_card_file_path(card, kit, archived=False)
    dst = orchlib._rules_card_file_path(card, kit, archived=True)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.isfile(src):
        shutil.move(src, dst)
    card["archived"] = True
    orchlib.save_manifest(m, kit)

cid = card["id"]
born_before = card.get("born_at")
created_before = card.get("created")
time.sleep(0.05)
assert orchlib.resurrect(cid, kit_dir=kit)
m2 = orchlib.load_manifest(kit)
c2 = orchlib._rules_card_by_id(m2, cid)
assert c2.get("born_at") == born_before, (c2.get("born_at"), born_before)
assert c2.get("created") == created_before, (c2.get("created"), created_before)
assert c2.get("migrated_at") is not None
print("resurrect_ok cid=%s born_at=%s migrated_at=%s" % (
    cid, c2.get("born_at"), c2.get("migrated_at")))
PY
pass "resurrect: born_at/created immutable, migrated_at updated"

# --- migrate_rules_born_at пишет migrated_at ---
python3 - <<PY
import os, sys
sys.path.insert(0, os.path.join("$KIT", "bin"))
import orchlib
manifest = {"cards": [{
    "id": "tmp-migrate-probe",
    "type": "DON'T",
    "кому": "commander",
    "когда": "acceptance",
    "категория": "приёмка",
    "run_ref": "probe",
    "hit": 0,
    "created": 1000.0,
}], "aliases": {}}
n = orchlib.migrate_rules_born_at(kit_dir="$KIT", manifest=manifest)
assert n == 1, n
c = manifest["cards"][0]
assert c.get("born_at") is not None
assert c.get("migrated_at") == c.get("born_at")
print("migrate_ok born_at=%s migrated_at=%s" % (c["born_at"], c["migrated_at"]))
PY
pass "migrate_rules_born_at sets migrated_at=ts"

echo "ALL PROBES PASS (baseline=$BASELINE)"
