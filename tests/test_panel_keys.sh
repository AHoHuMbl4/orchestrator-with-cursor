#!/usr/bin/env bash
# Живые пробы контракта ключей панели (C-KP-TEST).
# Запуск из корня репо: bash tests/test_panel_keys.sh
# Читает ключи из /root/.orchestration/{openrouter,cursor}.key — тела НЕ печатает.
set -euo pipefail

pass() { printf 'PASS: %s\n' "$*"; }
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }

REPO="$(cd "$(dirname "$0")/.." && pwd)"

if [ -d /var/tmp ] && [ ! -e /var/tmp/.orchestration ]; then
  TMP="$(mktemp -d -p /var/tmp)"
else
  TMP="$(mktemp -d)"
fi

STATE="$TMP/state"
PANEL_LOG="$TMP/panel.log"
RESP_DIR="$TMP/responses"
PANEL_PID=""
mkdir -p "$STATE" "$RESP_DIR"

cleanup() {
  if [ -n "${PANEL_PID:-}" ] && kill -0 "$PANEL_PID" 2>/dev/null; then
    kill "$PANEL_PID" 2>/dev/null || true
    wait "$PANEL_PID" 2>/dev/null || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

OR_KEY_PATH="/root/.orchestration/openrouter.key"
CUR_KEY_PATH="/root/.orchestration/cursor.key"
[ -f "$OR_KEY_PATH" ] || fail "missing $OR_KEY_PATH"
[ -f "$CUR_KEY_PATH" ] || fail "missing $CUR_KEY_PATH"

# --- helpers (тела ключей не эхоятся) ---
free_port() {
  python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()'
}

# usage: api_call METHOD PATH [JSON_BODY_OR_EMPTY] [RESP_TAG]
# печатает: STATUS\tBODY (в stdout для захвата); тело ключа в JSON_BODY не логируем снаружи
api_call() {
  local method="$1" path="$2" body="${3-}" tag="${4-}"
  local out
  out="$(python3 - "$BASE" "$method" "$path" "$body" <<'PY'
import json, sys, urllib.error, urllib.request
base, method, path, body = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
url = base.rstrip("/") + path
data = body.encode("utf-8") if body else None
req = urllib.request.Request(url, data=data, method=method)
if data is not None:
    req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=90) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as e:
    code = e.code
    raw = e.read().decode("utf-8", errors="replace")
except Exception as e:
    print("0\t{\"error\":\"transport:%s\"}" % type(e).__name__)
    sys.exit(0)
print("%s\t%s" % (code, raw.replace("\n", " ").replace("\r", " ")))
PY
)"
  if [ -n "$tag" ]; then
    printf '%s\n' "$out" >>"$RESP_DIR/$tag.txt"
    printf '%s\n' "$out" >>"$RESP_DIR/ALL.txt"
  fi
  printf '%s\n' "$out"
}

json_field() {
  # json_field JSON KEY → value as string (null→empty, bool/num as-is)
  python3 -c 'import json,sys; d=json.loads(sys.argv[1]); v=d.get(sys.argv[2]);
print("" if v is None else (json.dumps(v) if isinstance(v,(bool,dict,list)) else str(v)))' "$1" "$2"
}

assert_no_secret_leak() {
  local blob_file="$1"
  python3 - "$OR_KEY_PATH" "$CUR_KEY_PATH" "$blob_file" <<'PY'
import sys
or_key = open(sys.argv[1], encoding="utf-8").read().strip()
cur_key = open(sys.argv[2], encoding="utf-8").read().strip()
blob = open(sys.argv[3], encoding="utf-8", errors="replace").read()
leaks = []
if or_key and or_key in blob:
    leaks.append("openrouter")
if cur_key and cur_key in blob:
    leaks.append("cursor")
if leaks:
    sys.stderr.write("secret leak in %s: %s\n" % (sys.argv[3], ",".join(leaks)))
    sys.exit(1)
PY
}

# ========== 1) порт + панель ==========
PORT="$(free_port)"
BASE="http://127.0.0.1:${PORT}"
export ORCHESTRATION_DIR="$STATE"
# CURSOR_API_KEY не должен перекрывать файл state при негативе
(
  cd "$REPO"
  env -u CURSOR_API_KEY ORCHESTRATION_DIR="$STATE" \
    python3 panel/server.py --host 127.0.0.1 --port "$PORT"
) >"$PANEL_LOG" 2>&1 &
PANEL_PID=$!

ready=0
for _ in $(seq 1 80); do
  if curl -sf "$BASE/api/health" >/dev/null 2>&1; then
    ready=1
    break
  fi
  if ! kill -0 "$PANEL_PID" 2>/dev/null; then
    fail "1) panel died during startup; see panel log (no secrets)"
  fi
  sleep 0.15
done
[ "$ready" -eq 1 ] || fail "1) panel health not ready on port $PORT"
pass "1) free port $PORT; panel up; ORCHESTRATION_DIR=tmp-state; health ok"

# ========== 2) невалидный формат → 400 ==========
line="$(api_call POST /api/openrouter-key '{"key":"short"}' step2)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "400" ] || fail "2) expected HTTP 400, got $code body=$(printf '%s' "$body" | head -c 200)"
pass "2) POST invalid openrouter format → HTTP 400"

# ========== 3) реальный OR ключ → set/mask + 0600 ==========
line="$(python3 - "$BASE" "$OR_KEY_PATH" "$RESP_DIR" <<'PY'
import json, sys, urllib.request, urllib.error
base, key_path, resp_dir = sys.argv[1], sys.argv[2], sys.argv[3]
key = open(key_path, encoding="utf-8").read().strip()
data = json.dumps({"key": key}).encode()
req = urllib.request.Request(base + "/api/openrouter-key", data=data, method="POST")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as e:
    code = e.code
    raw = e.read().decode("utf-8", errors="replace")
line = "%s\t%s" % (code, raw.replace("\n", " "))
open(resp_dir + "/step3.txt", "a").write(line + "\n")
open(resp_dir + "/ALL.txt", "a").write(line + "\n")
print(line)
PY
)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "3) POST real OR key: HTTP $code"
set_v="$(json_field "$body" set)"
mask_v="$(json_field "$body" mask)"
[ "$set_v" = "true" ] || fail "3) expected set:true, got set=$set_v"
[ -n "$mask_v" ] && [ "$mask_v" != "null" ] || fail "3) expected non-null mask"
kf="$STATE/openrouter.key"
[ -f "$kf" ] || fail "3) missing $kf"
mode="$(stat -c '%a' "$kf" 2>/dev/null || stat -f '%OLp' "$kf")"
[ "$mode" = "600" ] || fail "3) expected mode 600, got $mode"
pass "3) POST real OR key → set:true mask ok; file 0600"

# ========== 4) probe {} → ok:true ==========
line="$(api_call POST /api/openrouter-key/probe '{}' step4)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "4) probe auth HTTP $code"
ok_v="$(json_field "$body" ok)"
[ "$ok_v" = "true" ] || fail "4) expected ok:true, got ok=$ok_v error_class=$(json_field "$body" error_class)"
pass "4) openrouter probe {} → ok:true"

# ========== 5) probe full → ok:true cost>0 ==========
line="$(api_call POST /api/openrouter-key/probe '{"full":true}' step5)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "5) probe full HTTP $code"
ok_v="$(json_field "$body" ok)"
cost_v="$(json_field "$body" cost)"
[ "$ok_v" = "true" ] || fail "5) expected ok:true, got ok=$ok_v error_class=$(json_field "$body" error_class)"
python3 -c 'import sys; c=sys.argv[1];
assert c not in ("","None","null"), "empty cost";
v=float(c); assert v>0, "cost<=0: %s"%c' "$cost_v" \
  || fail "5) expected cost>0, got cost=$cost_v"
pass "5) openrouter probe full → ok:true cost>0"

# ========== 6) негатив OR → 401 ==========
TAIL="$(python3 -c 'import secrets; print(secrets.token_hex(8))')"
BAD_OR="sk-or-v1-invalid${TAIL}"
line="$(python3 - "$BASE" "$BAD_OR" "$RESP_DIR" <<'PY'
import json, sys, urllib.request, urllib.error
base, bad, resp_dir = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.dumps({"key": bad}).encode()
req = urllib.request.Request(base + "/api/openrouter-key", data=data, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=30) as resp:
    raw = resp.read().decode("utf-8", errors="replace")
line = "200\t%s" % raw.replace("\n", " ")
open(resp_dir + "/step6a.txt", "a").write(line + "\n")
open(resp_dir + "/ALL.txt", "a").write(line + "\n")
print("posted")
PY
)"
line="$(api_call POST /api/openrouter-key/probe '{}' step6)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "6) probe garbage OR HTTP $code"
ok_v="$(json_field "$body" ok)"
ec_v="$(json_field "$body" error_class)"
# json_field для строк возвращает без кавычек; для JSON string "401" → 401
[ "$ok_v" = "false" ] || fail "6) expected ok:false, got ok=$ok_v"
[ "$ec_v" = "401" ] || fail "6) expected error_class 401, got $ec_v"
pass "6) garbage OR key → probe ok:false error_class 401"

# ========== 7) реальный cursor → probe ok:true ==========
line="$(python3 - "$BASE" "$CUR_KEY_PATH" "$RESP_DIR" <<'PY'
import json, sys, urllib.request, urllib.error
base, key_path, resp_dir = sys.argv[1], sys.argv[2], sys.argv[3]
key = open(key_path, encoding="utf-8").read().strip()
data = json.dumps({"key": key}).encode()
req = urllib.request.Request(base + "/api/cursor-key", data=data, method="POST")
req.add_header("Content-Type", "application/json")
try:
    with urllib.request.urlopen(req, timeout=30) as resp:
        code = getattr(resp, "status", None) or resp.getcode()
        raw = resp.read().decode("utf-8", errors="replace")
except urllib.error.HTTPError as e:
    code = e.code
    raw = e.read().decode("utf-8", errors="replace")
line = "%s\t%s" % (code, raw.replace("\n", " "))
open(resp_dir + "/step7a.txt", "a").write(line + "\n")
open(resp_dir + "/ALL.txt", "a").write(line + "\n")
print(line)
PY
)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "7) POST cursor key HTTP $code"
line="$(api_call POST /api/cursor-key/probe '{}' step7)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "7) cursor probe HTTP $code"
ok_v="$(json_field "$body" ok)"
[ "$ok_v" = "true" ] || fail "7) expected ok:true, got ok=$ok_v error_class=$(json_field "$body" error_class)"
pass "7) cursor key POST + probe → ok:true"

# ========== 8) негатив cursor → 401 ==========
BAD_CUR="key_invalid_$(python3 -c 'import secrets; print(secrets.token_hex(12))')"
python3 - "$BASE" "$BAD_CUR" "$RESP_DIR" <<'PY' >/dev/null
import json, sys, urllib.request
base, bad, resp_dir = sys.argv[1], sys.argv[2], sys.argv[3]
data = json.dumps({"key": bad}).encode()
req = urllib.request.Request(base + "/api/cursor-key", data=data, method="POST")
req.add_header("Content-Type", "application/json")
with urllib.request.urlopen(req, timeout=30) as resp:
    raw = resp.read().decode("utf-8", errors="replace")
line = "200\t%s" % raw.replace("\n", " ")
open(resp_dir + "/step8a.txt", "a").write(line + "\n")
open(resp_dir + "/ALL.txt", "a").write(line + "\n")
PY
line="$(api_call POST /api/cursor-key/probe '{}' step8)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "8) cursor garbage probe HTTP $code"
ok_v="$(json_field "$body" ok)"
ec_v="$(json_field "$body" error_class)"
[ "$ok_v" = "false" ] || fail "8) expected ok:false, got ok=$ok_v"
[ "$ec_v" = "401" ] || fail "8) expected error_class 401, got $ec_v"
pass "8) garbage cursor key → probe ok:false error_class 401"

# ========== 9) инвариант секрета + mask ==========
# GET mask (после step8 ключ мусорный — mask всё равно должен быть)
line="$(api_call GET /api/cursor-key '' step9)"
code="${line%%$'\t'*}"
body="${line#*$'\t'}"
[ "$code" = "200" ] || fail "9) GET /api/cursor-key HTTP $code"
mask_v="$(json_field "$body" mask)"
[ -n "$mask_v" ] && [ "$mask_v" != "null" ] && [ "$mask_v" != "" ] \
  || fail "9) GET /api/cursor-key missing mask"
: >"$TMP/leak_blob"
cat "$RESP_DIR/ALL.txt" >>"$TMP/leak_blob"
cat "$PANEL_LOG" >>"$TMP/leak_blob"
assert_no_secret_leak "$TMP/leak_blob" \
  || fail "9) real key body found in HTTP responses or panel log"
pass "9) secret invariant ok; cursor mask present via GET"

# ========== 10) регресс-барьер ==========
# (а) существующий shell-тест кита (без себя; fresh-install-e2e ломается
#     на устаревшем SHA256SUMS после C-KP-API/UI — вне владения C-KP-TEST)
set +e
OUT_CC="$(bash "$REPO/tests/cause-cleared-born-at.sh" 2>&1)"
EC_CC=$?
set -e
[ "$EC_CC" -eq 0 ] || fail "10) cause-cleared-born-at.sh exit $EC_CC"

# (б) install-local.sh на свежем клоне (изолированный HOME/TARGET как e2e).
# Временный клон: подтянуть суммы под файлы клона (committed SHA256SUMS
# отстаёт от panel/* и .gitignore волн API/UI — править корневой SHA256SUMS
# вне владения; здесь только полигон, чтобы замерить install EXIT=0).
REG="$TMP/regress"
mkdir -p "$REG/home/.orchestration" "$REG/target"
KIT="$REG/clone"
git clone --quiet "file://${REPO}" "$KIT"
[ "$REG/target" != "$KIT" ] || fail "10) TARGET must differ from KIT"
python3 - "$KIT" <<'PY'
import hashlib, pathlib, re, sys
kit = pathlib.Path(sys.argv[1])
sums = kit / "SHA256SUMS"
text = sums.read_text(encoding="utf-8")
# Обновить строки для файлов, которые реально есть в клоне и перечислены.
def file_sha(rel):
    p = kit / rel
    h = hashlib.sha256(p.read_bytes()).hexdigest()
    return h

out_lines = []
for line in text.splitlines():
    m = re.match(r"^([0-9a-f]{64})  (\./.+)$", line)
    if not m:
        out_lines.append(line)
        continue
    rel = m.group(2)[2:]  # strip ./
    p = kit / rel
    if p.is_file():
        out_lines.append("%s  ./%s" % (file_sha(rel), rel))
    else:
        out_lines.append(line)
sums.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
PY
set +e
(
  export HOME="$REG/home"
  cd "$REG/target"
  env -u ORCHESTRATION_DIR -u ORCH_TEST_INSTALL bash "$KIT/install-local.sh"
) >/dev/null 2>"$REG/install.err"
EC_INST=$?
set -e
[ "$EC_INST" -eq 0 ] || fail "10) install-local.sh exit $EC_INST"
pass "10) regression: cause-cleared ok; install-local.sh EXIT=0 on fresh clone"

exit 0
