#!/usr/bin/env bash
# E2E: свежая установка / обновление / ORCH_TEST_INSTALL=1 в изолированном HOME.
# Запуск из корня репо: bash tests/fresh-install-e2e.sh
# Только python3/git/bash. P14+: тест-режим только по ORCH_TEST_INSTALL=1;
# P15+: install без флагов пишет в $HOME (TARGET по умолчанию).
set -euo pipefail

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"

# /var/tmp: нет предка .orchestration (в отличие от /tmp и /root на этом полигоне).
if [ -d /var/tmp ] && [ ! -e /var/tmp/.orchestration ]; then
  TMP="$(mktemp -d -p /var/tmp)"
else
  TMP="$(mktemp -d)"
fi

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

# --- §1 ИЗОЛЯЦИЯ ---
export HOME="$TMP/home"
mkdir -p "$HOME/.orchestration"
TARGET="$TMP/target"
mkdir -p "$TARGET"
KIT="$TMP/clone"
git clone --quiet "file://${REPO}" "$KIT"
[ "$TARGET" != "$KIT" ] || fail "TARGET must differ from KIT"

# Эффективный корень установки (P15: TARGET по умолчанию = HOME).
INSTALL_ROOT="$HOME"

count_reground() {
  local n=0 f
  for f in \
    "$INSTALL_ROOT/.claude/settings.json" \
    "$INSTALL_ROOT/.codex/hooks.json" \
    "$HOME/.kimi-code/config.toml"
  do
    if [ -f "$f" ]; then
      n=$((n + $(grep -c reground "$f" || true)))
    fi
  done
  printf '%s' "$n"
}

hash_configs() {
  local f
  for f in \
    "$INSTALL_ROOT/.claude/settings.json" \
    "$INSTALL_ROOT/.codex/hooks.json" \
    "$HOME/.kimi-code/config.toml"
  do
    if [ -f "$f" ]; then
      sha256sum "$f"
    else
      printf 'MISSING %s\n' "$f"
    fi
  done
}

run_install() {
  # §2/§3: без флагов; cwd=TARGET; env очищен (кроме явного тест-режима снаружи).
  (cd "$TARGET" && env -u ORCHESTRATION_DIR -u ORCH_TEST_INSTALL bash "$KIT/install-local.sh") >/dev/null
}

# --- §3 УСТАНОВКА БЕЗ ФЛАГОВ ---
run_install || fail "fresh install-local.sh"
pass "fresh install (cwd=TARGET, dest=HOME/P15)"

# --- §4 АССЕРТЫ ---
# (а) хуки reground в конфигах установки (P15 → INSTALL_ROOT=$HOME)
hook_ok=0
if [ -f "$INSTALL_ROOT/.claude/settings.json" ] && grep -q reground "$INSTALL_ROOT/.claude/settings.json"; then
  hook_ok=1
fi
if [ -f "$INSTALL_ROOT/.codex/hooks.json" ] && grep -q reground "$INSTALL_ROOT/.codex/hooks.json"; then
  hook_ok=1
fi
# контракт также допускает пути под shell TARGET (если когда-либо совпадут с dest)
if [ -f "$TARGET/.claude/settings.json" ] && grep -q reground "$TARGET/.claude/settings.json"; then
  hook_ok=1
fi
if [ -f "$TARGET/.codex/hooks.json" ] && grep -q reground "$TARGET/.codex/hooks.json"; then
  hook_ok=1
fi
[ "$hook_ok" -eq 1 ] || fail "no reground in claude/codex hooks under INSTALL_ROOT/TARGET"
pass "hooks contain reground"

if [ -f "$HOME/.kimi-code/config.toml" ]; then
  grep -q reground "$HOME/.kimi-code/config.toml" || fail "kimi config.toml missing reground"
  pass "kimi config.toml contains reground"
fi

# (б) живой reground
echo '{"session_id":"e2e-test"}' | (cd "$TARGET" && env -u ORCHESTRATION_DIR -u ORCH_TEST_INSTALL \
  python3 "$KIT/bin/reground.py" session-start --engine claude) >/dev/null \
  || fail "reground session-start"
pass "reground session-start exit 0"

echo '{"session_id":"e2e-test"}' | (cd "$TARGET" && env -u ORCHESTRATION_DIR -u ORCH_TEST_INSTALL \
  python3 "$KIT/bin/reground.py" prompt-submit --engine claude) >/dev/null \
  || fail "reground prompt-submit"
pass "reground prompt-submit exit 0"

[ -f "$HOME/.orchestration/params.json" ] || fail "missing HOME/.orchestration/params.json"
[ -e "$HOME/.orchestration/compass.md" ] || fail "missing HOME/.orchestration/compass.md"
pass "HOME/.orchestration params+compass exist"

# (в) audit: install TARGET (P15=$HOME) и HEAD
AUDIT="$HOME/.orchestration/install-audit.log"
[ -f "$AUDIT" ] || fail "missing install-audit.log"
HEAD_AT_INSTALL="$(git -C "$KIT" rev-parse HEAD)"
grep -Fq "$INSTALL_ROOT" "$AUDIT" || fail "audit missing INSTALL_ROOT/TARGET=$INSTALL_ROOT"
grep -Fq "$HEAD_AT_INSTALL" "$AUDIT" || fail "audit missing HEAD=$HEAD_AT_INSTALL"
pass "install-audit.log contains TARGET(HOME) and HEAD"

# --- §5 ОБНОВЛЕНИЕ (идемпотентность) ---
git -C "$KIT" \
  -c user.email=e2e@test -c user.name=e2e \
  commit --allow-empty -m e2e-update >/dev/null
COUNT_BEFORE="$(count_reground)"
run_install || fail "update install-local.sh"
COUNT_AFTER="$(count_reground)"
[ "$COUNT_AFTER" -le "$COUNT_BEFORE" ] || fail "reground count grew: $COUNT_BEFORE -> $COUNT_AFTER"
pass "update install idempotent (reground count $COUNT_BEFORE -> $COUNT_AFTER)"

python3 - <<PY
import json, sys
from pathlib import Path
home = Path(r'''$HOME''')
root = Path(r'''$INSTALL_ROOT''')
for p in (root / ".claude" / "settings.json", root / ".codex" / "hooks.json"):
    if p.is_file():
        json.load(p.open())
kimi = home / ".kimi-code" / "config.toml"
if kimi.is_file():
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    tomllib.loads(kimi.read_text())
params = home / ".orchestration" / "params.json"
json.load(params.open())
print("ok")
PY
pass "json/tomllib valid; params readable"

# --- §6 ТЕСТ-РЕЖИМ ---
HASH_BEFORE="$(hash_configs)"
(cd "$TARGET" && env -u ORCHESTRATION_DIR ORCH_TEST_INSTALL=1 bash "$KIT/install-local.sh") >/dev/null \
  || fail "ORCH_TEST_INSTALL=1 install"
HASH_AFTER="$(hash_configs)"
[ "$HASH_BEFORE" = "$HASH_AFTER" ] || fail "config hashes changed under ORCH_TEST_INSTALL=1"
pass "ORCH_TEST_INSTALL=1 left configs unchanged"

pass "fresh-install-e2e complete"
exit 0
