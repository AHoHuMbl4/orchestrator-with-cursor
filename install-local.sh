#!/usr/bin/env bash
# Локальная установка оркестрации для трёх движков: Claude Code, Codex, Kimi.
# Без GitHub: всё живёт в одной рабочей папке + пара файлов в ~/.codex и
# ~/.kimi-code. Без python ставятся только скиллы (хуки/панель/скрипты пропускаются
# с явным предупреждением).
#
# Запуск: bash /путь/к/orchestration-kit/install-local.sh
#         или: cd клон && bash install-local.sh
# По умолчанию TARGET=$HOME (не cwd и не KIT). Переопределение: TARGET=/path bash …
# Повторный запуск безопасен (идемпотентен, чужие настройки не трогает).
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
# Дефолт — $HOME: «клон → cd клон → bash install-local.sh» ставит хуки/state в HOME,
# не мутируя репо кита. Явный TARGET=… перекрывает.
TARGET="${TARGET:-$HOME}"

# Якорь песочницы / подсказка при отказе гарда TARGET-в-репо.
ORCH_INSTALL_SANDBOX_HINT='не указывайте TARGET внутри репо кита; по умолчанию TARGET=$HOME'

# audit — home-canonical, cwd-независимо
_orch_install_audit_log_path() {
  local audit_dir state py
  if [ -d "${HOME}/.orchestration" ]; then
    audit_dir="${HOME}/.orchestration"
  else
    state=""
    if command -v python3 >/dev/null 2>&1; then py=python3
    elif command -v python >/dev/null 2>&1; then py=python
    else py=""
    fi
    if [ -n "$py" ]; then
      state="$("$py" -c "
import os, sys
sys.path.insert(0, os.path.join(r'''$KIT''', 'bin'))
from orchlib import find_state_dir
print(find_state_dir())
" 2>/dev/null || true)"
    fi
    if [ -n "$state" ]; then
      audit_dir="$state"
    else
      audit_dir="${HOME}/.orchestration"
    fi
  fi
  # НИКОГДА не писать audit внутрь репо кита
  case "$audit_dir" in
    "$KIT"|"$KIT"/*) audit_dir="${HOME}/.orchestration" ;;
  esac
  printf '%s\n' "${audit_dir}/install-audit.log"
}

_orch_write_install_audit() {
  # Первое действие main — до гарда и тест-раннего-выхода (атрибуция и отказа гарда).
  local audit_log audit_dir ts run_id caller mode head
  audit_log="$(_orch_install_audit_log_path)"
  audit_dir="$(dirname "$audit_log")"
  mkdir -p "$audit_dir"
  ts="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
  run_id="${ORCH_RUN_ID:--}"
  if [ -n "${ORCH_CALLER:-}" ]; then
    caller="$ORCH_CALLER"
  else
    caller="$(id -un 2>/dev/null || echo user)@$(hostname 2>/dev/null || echo host)"
  fi
  if [ "${ORCH_TEST_INSTALL:-}" = "1" ]; then
    mode=test
  else
    mode=normal
  fi
  if git -C "$KIT" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    head="$(git -C "$KIT" rev-parse HEAD 2>/dev/null || echo no-git)"
  else
    head=no-git
  fi
  printf '%s | ORCH_RUN_ID=%s | caller=%s | TARGET=%s | режим=%s | HEAD=%s\n' \
    "$ts" "$run_id" "$caller" "$TARGET" "$mode" "$head" >>"$audit_log"
}

main() {
# Тест-режим: ТОЛЬКО явный ORCH_TEST_INSTALL=1.
# Путь KIT (включая /tmp|/var/tmp) сам по себе тест-режим НЕ включает.
# В тест-режиме не пишем конфиги движков и не трогаем TARGET
# (ранний выход до любой мутации, включая проверку SHA256SUMS).
_orch_write_install_audit

TEST_INSTALL=0
TEST_REASON=""
if [ "${ORCH_TEST_INSTALL:-}" = "1" ]; then
  TEST_INSTALL=1
  TEST_REASON="ORCH_TEST_INSTALL=1"
fi
if [ "$TEST_INSTALL" = "1" ]; then
  for f in \
    "$HOME/.claude/settings.json" \
    "$HOME/.codex/hooks.json" \
    "$HOME/.kimi-code/config.toml"
  do
    if [ -e "$f" ]; then
      [ -r "$f" ] || { echo "ОШИБКА: не читается $f" >&2; exit 1; }
    fi
  done
  echo "TEST INSTALL: хуки/конфиги не тронуты (проверка: $TEST_REASON)"
  echo "тест только по явному ORCH_TEST_INSTALL=1; установка из любого каталога (включая /tmp) — обычная"
  echo "$ORCH_INSTALL_SANDBOX_HINT"
  exit 0
fi

# Гард: явный TARGET == KIT или TARGET внутри KIT → отказ (репо кита не цель установки).
# При дефолте TARGET=$HOME гард не срабатывает — установка из клона идёт в HOME.
# Исключение — ORCH_TEST_INSTALL=1 (ранний выход выше). $1 не задаёт TARGET (только --global).
KIT_DIR="$KIT"
case "$TARGET" in
  "$KIT_DIR"|"$KIT_DIR"/*)
    echo "ОШИБКА: нельзя устанавливать оркестрацию в репо кита (TARGET=$TARGET, KIT=$KIT_DIR)" >&2
    echo "Подсказка: TARGET=\$HOME (это значение по умолчанию) или другой каталог вне репо." >&2
    echo "$ORCH_INSTALL_SANDBOX_HINT" >&2
    exit 1
    ;;
esac

# --global: скилл/хуки Claude и Codex на уровень пользователя (работает во всех папках);
# .orchestration (params/compass) всегда остаётся per-folder — у каждой папки свои параметры.
GLOBAL=0
[ "${1:-}" = "--global" ] && GLOBAL=1
CLAUDE_DIR="$TARGET/.claude"
CODEX_HOOKS="$TARGET/.codex/hooks.json"
if [ "$GLOBAL" = "1" ]; then
  CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  CODEX_HOOKS="$HOME/.codex/hooks.json"
  echo "--global: Claude/Codex ставятся на уровень пользователя (все папки)"
fi

# --- python: без него живут только скиллы ---
HAVE_PY=1
PY=python3
if command -v "$PY" >/dev/null 2>&1; then :;
elif command -v python >/dev/null 2>&1; then PY=python;
else HAVE_PY=0; fi
if [ "$HAVE_PY" = "1" ]; then
  PY_ABS="$(command -v "$PY")"   # абсолютный путь: хуки переживают рестарт из другого окружения
else
  PY_ABS=""
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
  echo "!! ПИТОН НЕ НАЙДЕН (ни python3, ни python).                       !!"
  echo "!! Работать БУДЕТ: скилл orchestration (это просто инструкции).  !!"
  echo "!! Работать НЕ будет: хуки сверки, панель, меню-скрипты.         !!"
  echo "!! Установить:  Linux: sudo apt install python3                  !!"
  echo "!!   macOS: brew install python (или python.org)                 !!"
  echo "!!   Windows: winget install Python.Python.3.12 (или python.org, !!"
  echo "!!   при установке отметить Add to PATH). Затем повторить ввод.  !!"
  echo "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
fi

echo "== 1/6 проверка целостности kit =="
# _index.md — живой каталог ролей: локальные строки (фабрика/свои роли) не блокируют.
# Статус успеха зависит от локали: OK (C), ОК, ЦЕЛ (ru_RU coreutils). Иначе — несовпадение.
LIVING_REL="skills/orchestration/references/roles/_index.md"
set +e
check_out="$(cd "$KIT" && sha256sum -c SHA256SUMS 2>/dev/null)"
check_rc=$?
set -e
hard=0
living_bad=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  case "$line" in
    *:*) ;;
    *) hard=1; continue ;;
  esac
  status="${line##*: }"
  name="${line%": $status"}"
  name="${name#./}"
  status="${status%$'\r'}"
  name="${name%$'\r'}"
  case "$status" in
    OK|ОК|ЦЕЛ) ;;
    *)
      if [ "$name" = "$LIVING_REL" ]; then
        living_bad=1
      else
        hard=1
      fi
      ;;
  esac
done <<< "$check_out"
if [ "$hard" = "1" ] || { [ "$check_rc" -ne 0 ] && [ "$living_bad" != "1" ]; }; then
  echo "ОШИБКА: суммы не сошлись. Если склонировали на Windows — Git конвертирует LF→CRLF; переклонируйте: git clone -c core.autocrlf=false <repo> (или обновите репо: git rm --cached -r . && git reset --hard после добавления .gitattributes)"
  exit 1
fi
if [ "$living_bad" = "1" ]; then
  printf '\033[33m%s\033[0m\n' "каталог ролей локально расширен (фабрика/ваши роли) — не блокирует; целостность остальных файлов подтверждена"
fi
echo "ok (TARGET=$TARGET)"

echo "== 2/6 скиллы (все три движка) =="
mkdir -p "$CLAUDE_DIR/skills" "$CLAUDE_DIR/commands" "$TARGET/.agents/skills"
# Перезаписываем базовые файлы скилла, СОХРАНЯЯ роли созданные фабрикой
mkdir -p "$CLAUDE_DIR/skills/orchestration/references/roles" "$TARGET/.agents/skills/orchestration/references/roles"
cp -r "$KIT/skills/orchestration/." "$CLAUDE_DIR/skills/orchestration/"
cp -r "$KIT/skills/orchestration/." "$TARGET/.agents/skills/orchestration/"
# Фабричные роли (созданные после установки) не перезаписываем — они ценнее
if [ -d "$CLAUDE_DIR/skills/orchestration/_factory_roles" ]; then
  cp -rn "$CLAUDE_DIR/skills/orchestration/_factory_roles/." "$CLAUDE_DIR/skills/orchestration/references/roles/" 2>/dev/null || true
fi
cp "$KIT/commands/claude-orch-menu.md" "$CLAUDE_DIR/commands/orch-menu.md"
echo "  скилл+команда: $CLAUDE_DIR (+ .agents/skills в папке)"

echo "== 3/6 хуки Claude (.claude/settings.json) =="
if [ "$HAVE_PY" = "1" ]; then
  cat > /tmp/orch-claude-snippet.json <<EOF
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" session-start --engine claude", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" prompt-submit --engine claude", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" post-tool --engine claude", "timeout": 10}]}
    ]
  }
}
EOF
  ORCH_CLAUDE_DIR="$CLAUDE_DIR" "$PY" - <<'PYEOF'
import json, os
snip = json.load(open("/tmp/orch-claude-snippet.json"))
path = os.path.join(os.environ["ORCH_CLAUDE_DIR"], "settings.json")
cur = {}
if os.path.exists(path):
    try:
        cur = json.load(open(path))
    except Exception:
        cur = {}
hooks = cur.setdefault("hooks", {})

def refs_reground(entry):
    for h in entry.get("hooks", []):
        if "reground.py" in h.get("command", ""):
            return True
    return False

for event, entries in snip["hooks"].items():
    merged = [e for e in hooks.get(event, []) if not refs_reground(e)]
    have = {json.dumps(e, sort_keys=True) for e in merged}
    for e in entries:
        if json.dumps(e, sort_keys=True) not in have:
            merged.append(e)
    hooks[event] = merged
json.dump(cur, open(path, "w"), indent=2, ensure_ascii=False)
open(path, "a").write("\n")
print("  хуки SessionStart/UserPromptSubmit/PostToolUse (абсолютный python)")
PYEOF
else
  echo "  пропущено (нет python)"
fi

echo "== 4/6 Codex + Kimi =="
if [ "$HAVE_PY" = "1" ]; then
  mkdir -p "$(dirname "$CODEX_HOOKS")"
  cat > /tmp/orch-codex-snippet.json <<EOF
{
  "description": "orchestration-kit: сверка курса",
  "hooks": {
    "SessionStart": [
      {"matcher": "startup|resume|clear|compact",
       "hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" session-start --engine codex", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" prompt-submit --engine codex", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "\"$PY_ABS\" \"$KIT/bin/reground.py\" post-tool --engine codex", "timeout": 10}]}
    ]
  }
}
EOF
  ORCH_CODEX_HOOKS="$CODEX_HOOKS" "$PY" - <<'PYEOF'
import json, os
snip = json.load(open("/tmp/orch-codex-snippet.json"))
path = os.environ["ORCH_CODEX_HOOKS"]
cur = {}
if os.path.exists(path):
    try:
        cur = json.load(open(path))
    except Exception:
        cur = {}
hooks = cur.setdefault("hooks", {})

def refs_reground(entry):
    for h in entry.get("hooks", []):
        if "reground.py" in h.get("command", ""):
            return True
    return False

for event, entries in snip["hooks"].items():
    merged = [e for e in hooks.get(event, []) if not refs_reground(e)]
    have = {json.dumps(e, sort_keys=True) for e in merged}
    for e in entries:
        if json.dumps(e, sort_keys=True) not in have:
            merged.append(e)
    hooks[event] = merged
json.dump(cur, open(path, "w"), indent=2, ensure_ascii=False)
open(path, "a").write("\n")
PYEOF
  echo "  $CODEX_HOOKS (после первого запуска codex: /hooks -> доверить)"
  mkdir -p "$HOME/.codex/prompts"
  cp "$KIT/commands/codex-orch-menu.md" "$HOME/.codex/prompts/orch-menu.md"
  echo "  ~/.codex/prompts/orch-menu.md (команда /prompts:orch-menu в Codex)"
else
  echo "  .codex/hooks.json пропущен (нет python)"
fi
KIMI_DIR="${KIMI_CODE_HOME:-${KIMI_HOME:-$HOME/.kimi-code}}"
mkdir -p "$KIMI_DIR/skills" "$HOME/.agents/skills"
mkdir -p "$KIMI_DIR/skills/orchestration/references/roles" "$HOME/.agents/skills/orchestration/references/roles"
cp -r "$KIT/skills/orchestration/." "$KIMI_DIR/skills/orchestration/"
cp -r "$KIT/skills/orchestration/." "$HOME/.agents/skills/orchestration/"
echo "  скилл: ~/.kimi-code/skills + ~/.agents/skills"
if [ "$HAVE_PY" = "1" ]; then
  KIMI_CFG="$KIMI_DIR/config.toml"
  touch "$KIMI_CFG"
  # Идемпотентность: при маркере orchestration-kit hooks ВСЕГДА удаляем
  # старый блок и пишем текущий канон (кавычки вокруг абсолютных путей,
  # полный набор хуков). Условный grep по python3/pre-tool недостаточен —
  # unquoted abs-пути не матчились и оставались «актуальными».
  # ОГРАНИЧЕНИЕ: PreToolUse/PostToolUse гарантированно в сессии с хуками
  # (главная/командующий); для субагентов движка — зависит от стрельбы
  # событий на их вызовах (проверить на живой машине). SubagentStart/Stop —
  # видимость генералов независимо.
  if grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
    cp "$KIMI_CFG" "$KIMI_CFG.bak-orch"
    sed -i '/# >>> orchestration-kit hooks >>>/,/# <<< orchestration-kit hooks <<</d' "$KIMI_CFG"
    echo "  ~/.kimi-code/config.toml: старый блок хуков удалён (канон будет перезаписан)"
  fi
  if ! grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
    cp "$KIMI_CFG" "$KIMI_CFG.bak-orch"
    cat >> "$KIMI_CFG" <<EOF

# >>> orchestration-kit hooks >>>
[[hooks]]
  event = "UserPromptSubmit"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" prompt-submit --engine kimi"
  timeout = 10

[[hooks]]
  event = "SessionHeartbeat"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" heartbeat --engine kimi"
  timeout = 10

[[hooks]]
  event = "PreToolUse"
  matcher = "Write|Edit"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" pre-tool --engine kimi"
  timeout = 10

[[hooks]]
  event = "PostToolUse"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" post-tool --engine kimi"
  timeout = 10

[[hooks]]
  event = "SubagentStart"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" subagent-start --engine kimi"
  timeout = 10

[[hooks]]
  event = "SubagentStop"
  command = "\"$PY_ABS\" \"$KIT/bin/reground.py\" subagent-stop --engine kimi"
  timeout = 10
# <<< orchestration-kit hooks <<<
EOF
    echo "  ~/.kimi-code/config.toml: блок хуков добавлен (бэкап .bak-orch)"
  else
    echo "  ~/.kimi-code/config.toml: блок хуков уже актуален"
  fi
else
  echo "  хуки Kimi пропущены (нет python)"
fi

echo "== 5/6 параметры, панель =="
mkdir -p "$TARGET/.orchestration"
[ -f "$TARGET/.orchestration/params.json" ] || cp "$KIT/params.json" "$TARGET/.orchestration/params.json"
[ -f "$TARGET/.orchestration/compass.md" ] || cp "$KIT/compass.md" "$TARGET/.orchestration/compass.md"
# Идемпотентно: не трогаем .gitignore, если все строки уже на месте
# (иначе мутация tracked-файла ломает sha256sum -c / обновление с GitHub).
GI_LINES=".orchestration/counters/
.orchestration/*.log
.orchestration/*.pid
.orchestration/prompt-*.run.md
.orchestration/discovered.json
.orchestration/cursor.key
__pycache__/"
GI_NEED=0
while IFS= read -r line; do
  [ -z "$line" ] && continue
  if ! grep -qxF "$line" "$TARGET/.gitignore" 2>/dev/null; then
    GI_NEED=1
    break
  fi
done <<EOF
$GI_LINES
EOF
if [ "$GI_NEED" = "1" ]; then
  [ -f "$TARGET/.gitignore" ] || touch "$TARGET/.gitignore"
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    grep -qxF "$line" "$TARGET/.gitignore" 2>/dev/null || echo "$line" >> "$TARGET/.gitignore"
  done <<EOF
$GI_LINES
EOF
fi
chmod +x "$KIT"/bin/*.py 2>/dev/null || true
if [ "$HAVE_PY" = "1" ]; then
  # нормализация старых дефолтов (every_min 7 -> 10), явные значения владельца не трогаем
  if ! ORCH_KIT="$KIT" "$PY" - <<'PYEOF'
import sys, os
sys.path.insert(0, os.path.join(os.environ["ORCH_KIT"], "bin"))
import orchlib
p = orchlib.load_params()
if p.get("reground", {}).get("every_min") == 7:
    p["reground"]["every_min"] = 10
if p.get("execution", {}).get("executor") == "subagents":
    p["execution"]["executor"] = "auto"  # старый дефолт -> курсор-первым
orchlib.save_params(p)
PYEOF
  then
    echo "WARN: params.json битый — нормализация пропущена" >&2
  fi
  cat > "$TARGET/panel.sh" <<EOF
#!/usr/bin/env bash
# Настройки оркестрации (панель). ./panel.sh — на переднем плане; ./panel.sh --bg — в фоне
if [ "\${1:-}" = "--bg" ]; then shift
  nohup "$PY_ABS" -u "$KIT/panel/server.py" "\$@" > panel.log 2>&1 &
  echo "панель в фоне: pid \$! (адрес в panel.log), остановка: kill \$!"
  exit 0
fi
exec "$PY_ABS" -u "$KIT/panel/server.py" "\$@"
EOF
  chmod +x "$TARGET/panel.sh"
  echo "  .orchestration/ посеян, panel.sh готов"
else
  echo "  .orchestration/ посеян; panel.sh пропущен (нет python)"
fi

# --- pre-commit репо проекта (owns-check; cwd установщика) ---
# toplevel под set -euo: голый git вне worktree ВАЛИТ → || true.
echo "== pre-commit owns-check (репо от cwd) =="
toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [ -z "$toplevel" ]; then
  echo "репо не найдено, pre-commit пропущен" >&2
elif [ "$HAVE_PY" != "1" ] || [ -z "$PY_ABS" ]; then
  echo "WARN: pre-commit пропущен (нет python)" >&2
else
  HOOK_DIR="$toplevel/.git/hooks"
  HOOK="$HOOK_DIR/pre-commit"
  MARKER="orchestration-kit owns-check"
  mkdir -p "$HOOK_DIR"
  BLOCK_BEGIN="# >>> ${MARKER} >>>"
  BLOCK_END="# <<< ${MARKER} <<<"
  CANON_BLOCK=$(cat <<EOF
${BLOCK_BEGIN}
# orchestration-kit owns-check: staged vs fronts.json owns
"$PY_ABS" "$KIT/bin/owns.py" --check-staged || exit \$?
${BLOCK_END}
EOF
)
  if [ -f "$HOOK" ]; then
    if grep -q "$MARKER" "$HOOK" 2>/dev/null; then
      # идемпотентность: удалить старый маркер-блок, записать канон
      tmp_hook="$(mktemp)"
      # shellcheck disable=SC2016
      awk -v b="$BLOCK_BEGIN" -v e="$BLOCK_END" '
        $0==b {skip=1; next}
        $0==e {skip=0; next}
        !skip {print}
      ' "$HOOK" >"$tmp_hook"
      printf '%s\n' "$CANON_BLOCK" >>"$tmp_hook"
      if cat "$tmp_hook" >"$HOOK"; then
        rm -f "$tmp_hook"
        chmod +x "$HOOK"
        echo "  pre-commit: блок owns-check обновлён ($HOOK)"
      else
        rm -f "$tmp_hook"
        echo "pre-commit пропущен: чужой хук" >&2
      fi
    else
      # чужой хук: встроить маркер-блок, не затирая
      if printf '\n%s\n' "$CANON_BLOCK" >>"$HOOK" 2>/dev/null; then
        chmod +x "$HOOK"
        echo "  pre-commit: блок owns-check встроен в чужой хук ($HOOK)"
      else
        echo "pre-commit пропущен: чужой хук" >&2
      fi
    fi
  else
    printf '%s\n' "#!/usr/bin/env bash" >"$HOOK"
    printf '%s\n' "$CANON_BLOCK" >>"$HOOK"
    chmod +x "$HOOK"
    echo "  pre-commit: установлен ($HOOK)"
  fi
fi

echo "== 6/6 снимок моделей и самопроверка =="
if [ "$HAVE_PY" = "1" ]; then
  "$PY" "$KIT/bin/discover.py" >/dev/null 2>&1 && echo "  discover: ok" || echo "  discover: предупреждение"
  "$PY" "$KIT/bin/menu.py" --show | head -1
  echo '{"session_id":"install-check"}' | "$PY" "$KIT/bin/reground.py" post-tool --engine claude
  echo "  reground молчит (порог не достигнут) — так и должно быть"
  # Убрать служебную сессию self-check
  rm -rf "$TARGET/.orchestration/sessions/install-check" 2>/dev/null || true
else
  echo "  пропущено (нет python)"
fi

STATE_ABS="$(cd "$TARGET" && pwd)/.orchestration"
cat <<EOF

УСТАНОВЛЕНО в $TARGET
  State/ключ: $STATE_ABS (токен Cursor вставляется в панели)
EOF
if [ "$HAVE_PY" = "1" ]; then
cat <<EOF
  Настройки:   ./panel.sh  →  http://127.0.0.1:8765 (или соседний порт; --bg — в фоне)
               В панели: тумблер вкл/выкл, задача, исполнители/критики/круги,
               модели, токен Cursor (как его взять — подсказка прямо у поля).
  Claude Code: запускайте claude в этой папке — скилл + хуки + /orch-menu.
  Codex:       запускайте codex в этой папке; ПЕРВЫЙ РАЗ: /hooks -> доверить
               хуки orchestration (Codex требует явного trust).
  Kimi:        НОВАЯ сессия (скиллы регистрируются при старте); вызов
               /skill:orchestration; хуки подхватятся сами.
EOF
else
cat <<EOF
  БЕЗ PYTHON: скилл orchestration работает как инструкции (Claude/Codex/Kimi),
  но хуки сверки, панель и меню-скрипты не установлены. Поставьте python и
  повторите установщик — он доведёт остальное.
EOF
fi
}

main "$@"
