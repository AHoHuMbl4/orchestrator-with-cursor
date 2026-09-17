#!/usr/bin/env bash
# Локальная установка оркестрации для трёх движков: Claude Code, Codex, Kimi.
# Без GitHub: всё живёт в одной рабочей папке + пара файлов в ~/.codex и
# ~/.kimi-code. Без python ставятся только скиллы (хуки/панель/скрипты пропускаются
# с явным предупреждением).
#
# Запуск из рабочей папки: bash /путь/к/orchestration-kit/install-local.sh
# Повторный запуск безопасен (идемпотентен, чужие настройки не трогает).
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
TARGET="$(pwd)"

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
(cd "$KIT" && sha256sum -c SHA256SUMS --quiet) || {
  echo "ОШИБКА: суммы не сошлись. Если склонировали на Windows — Git конвертирует LF→CRLF; переклонируйте: git clone -c core.autocrlf=false <repo> (или обновите репо: git rm --cached -r . && git reset --hard после добавления .gitattributes)"
  exit 1
}
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
      {"hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py session-start --engine claude", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py prompt-submit --engine claude", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py post-tool --engine claude", "timeout": 10}]}
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
for event, entries in snip["hooks"].items():
    merged = hooks.get(event, [])
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
       "hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py session-start --engine codex", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py prompt-submit --engine codex", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$PY_ABS $KIT/bin/reground.py post-tool --engine codex", "timeout": 10}]}
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
for event, entries in snip["hooks"].items():
    merged = hooks.get(event, [])
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
  if grep -q "orchestration-kit hooks" "$KIMI_CFG" && grep -A20 "orchestration-kit hooks" "$KIMI_CFG" | grep -q 'command = "python3 '; then
    cp "$KIMI_CFG" "$KIMI_CFG.bak-orch"
    sed -i '/# >>> orchestration-kit hooks >>>/,/# <<< orchestration-kit hooks <<</d' "$KIMI_CFG"
    echo "  ~/.kimi-code/config.toml: старый блок хуков заменён (абсолютный python)"
  fi
  if ! grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
    cp "$KIMI_CFG" "$KIMI_CFG.bak-orch"
    cat >> "$KIMI_CFG" <<EOF

# >>> orchestration-kit hooks >>>
[[hooks]]
  event = "UserPromptSubmit"
  command = "$PY_ABS $KIT/bin/reground.py prompt-submit --engine kimi"
  timeout = 10

[[hooks]]
  event = "SessionHeartbeat"
  command = "$PY_ABS $KIT/bin/reground.py heartbeat --engine kimi"
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
for line in ".orchestration/counters/" ".orchestration/*.log" ".orchestration/*.pid" ".orchestration/prompt-*.run.md" ".orchestration/discovered.json" ".orchestration/cursor.key" "__pycache__/"; do
  grep -qxF "$line" "$TARGET/.gitignore" 2>/dev/null || echo "$line" >> "$TARGET/.gitignore"
done
chmod +x "$KIT"/bin/*.py 2>/dev/null || true
if [ "$HAVE_PY" = "1" ]; then
  # нормализация старых дефолтов (every_min 7 -> 10), явные значения владельца не трогаем
  ORCH_KIT="$KIT" "$PY" - <<'PYEOF' 2>/dev/null || true
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

cat <<EOF

УСТАНОВЛЕНО в $TARGET
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
