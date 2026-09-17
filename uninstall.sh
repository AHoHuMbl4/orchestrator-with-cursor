#!/usr/bin/env bash
# Удаление оркестрации с машины. Обратен install-local.sh.
#
#   bash orchestration-kit/uninstall.sh          # удалить из текущей папки
#   bash orchestration-kit/uninstall.sh --global  # удалить с уровня пользователя
#   bash orchestration-kit/uninstall.sh --all     # и папку .orchestration со всем содержимым
#
# Скрипт идемпотентен: повторный запуск безопасен. Ничего чужого не трогает
# (только файлы, созданные install-local.sh). Перед удалением конфигов делает бэкапы.
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"
TARGET="$(pwd)"
GLOBAL=0; ALL=0
for arg in "$@"; do
  case "$arg" in
    --global) GLOBAL=1 ;;
    --all) ALL=1 ;;
  esac
done

# Пути, которые создавал установщик
if [ "$GLOBAL" = "1" ]; then
  CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
  CODEX_HOOKS="$HOME/.codex/hooks.json"
else
  CLAUDE_DIR="$TARGET/.claude"
  CODEX_HOOKS="$TARGET/.codex/hooks.json"
fi
KIMI_DIR="${KIMI_CODE_HOME:-${KIMI_HOME:-$HOME/.kimi-code}}"

echo "== Удаление оркестрации =="
[ "$GLOBAL" = "1" ] && echo "  режим: --global (пользовательский уровень)" || echo "  режим: локальный (папка $TARGET)"
[ "$ALL" = "1" ] && echo "  --all: будет удалён и .orchestration"

echo ""
echo "== 1/5 Claude Code =="
# Скилл (включая фабричные роли)
if [ -d "$CLAUDE_DIR/skills/orchestration" ]; then
  rm -rf "$CLAUDE_DIR/skills/orchestration"
  echo "  удалён скилл: $CLAUDE_DIR/skills/orchestration/"
else
  echo "  скилл не найден (уже удалён?)"
fi
# Команда /orch-menu
if [ -f "$CLAUDE_DIR/commands/orch-menu.md" ]; then
  rm -f "$CLAUDE_DIR/commands/orch-menu.md"
  echo "  удалена команда: orch-menu.md"
fi
# Хуки из settings.json (аккуратно: мержим только наши)
if [ -f "$CLAUDE_DIR/settings.json" ] && command -v python3 >/dev/null 2>&1; then
  python3 - <<'PYEOF' || true
import json, os, sys
path = os.environ.get("ORCH_CLAUDE_SETTINGS", "")
if not path:
    sys.exit(0)
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
hooks = d.get("hooks", {})
changed = False
for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
    if event in hooks:
        filtered = [e for e in hooks[event]
                    if "reground.py" not in json.dumps(e)]
        if len(filtered) < len(hooks[event]):
            hooks[event] = filtered
            changed = True
        if not filtered:
            del hooks[event]
            changed = True
if changed:
    json.dump(d, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print("  хуки orchestration удалены из settings.json")
else:
    print("  хуки orchestration не найдены в settings.json")
PYEOF
  ORCH_CLAUDE_SETTINGS="$CLAUDE_DIR/settings.json" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
path = os.environ["ORCH_CLAUDE_SETTINGS"]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
hooks = d.get("hooks", {})
changed = False
for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
    if event in hooks:
        filtered = [e for e in hooks[event]
                    if "reground.py" not in json.dumps(e)]
        if len(filtered) < len(hooks[event]):
            hooks[event] = filtered
            changed = True
        if not filtered:
            del hooks[event]
            changed = True
if changed:
    json.dump(d, open(path, "w"), indent=2, ensure_ascii=False)
    open(path, "a").write("\n")
    print("  хуки удалены")
else:
    print("  хуки не найдены")
PYEOF
fi

echo ""
echo "== 2/5 Codex =="
if [ -f "$CODEX_HOOKS" ]; then
  # Если hooks.json содержит ТОЛЬКО наши хуки — удалить файл целиком
  # Иначе — удалить только наши события
  python3 - <<PYEOF 2>/dev/null || true
import json, os
path = "$CODEX_HOOKS"
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)
hooks = d.get("hooks", {})
ours = all("reground.py" in json.dumps(v) for v in hooks.values()) if hooks else False
if ours and len(hooks) <= 3:
    os.unlink(path)
    print("  удалён файл: $CODEX_HOOKS (содержал только наши хуки)")
else:
    changed = False
    for event in list(hooks.keys()):
        filtered = [e for e in hooks[event] if "reground.py" not in json.dumps(e)]
        if len(filtered) < len(hooks[event]):
            hooks[event] = filtered
            changed = True
        if not filtered:
            del hooks[event]
    if changed:
        d["hooks"] = hooks
        json.dump(d, open(path, "w"), indent=2, ensure_ascii=False)
        print("  наши хуки удалены (чужие сохранены)")
    else:
        print("  наши хуки не найдены")
PYEOF
else
  echo "  hooks.json не найден"
fi
# Codex prompt
if [ -f "$HOME/.codex/prompts/orch-menu.md" ]; then
  rm -f "$HOME/.codex/prompts/orch-menu.md"
  echo "  удалён prompt: ~/.codex/prompts/orch-menu.md"
fi

echo ""
echo "== 3/5 Kimi =="
if [ -d "$KIMI_DIR/skills/orchestration" ]; then
  rm -rf "$KIMI_DIR/skills/orchestration"
  echo "  удалён скилл: $KIMI_DIR/skills/orchestration/"
fi
# Блок хуков из config.toml
KIMI_CFG="$KIMI_DIR/config.toml"
if [ -f "$KIMI_CFG" ] && grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
  cp "$KIMI_CFG" "$KIMI_CFG.bak-uninstall"
  sed -i '/# >>> orchestration-kit hooks >>>/,/# <<< orchestration-kit hooks <<</d' "$KIMI_CFG"
  # Убрать пустые строки в конце, если образовались
  sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$KIMI_CFG" 2>/dev/null || true
  echo "  блок хуков удалён из config.toml (бэкап: .bak-uninstall)"
fi

echo ""
echo "== 4/5 Папки .agents/skills =="
for dir in "$TARGET/.agents/skills/orchestration" "$HOME/.agents/skills/orchestration"; do
  if [ -d "$dir" ]; then
    rm -rf "$dir"
    echo "  удалён: $dir"
  fi
done

echo ""
echo "== 5/5 Рабочие файлы =="
if [ "$ALL" = "1" ]; then
  if [ -d "$TARGET/.orchestration" ]; then
    rm -rf "$TARGET/.orchestration"
    echo "  удалён: $TARGET/.orchestration/ (compass, params, сессии, логи)"
  fi
  if [ -f "$TARGET/panel.sh" ]; then
    rm -f "$TARGET/panel.sh" "$TARGET/panel.log"
    echo "  удалён: panel.sh"
  fi
  if [ -f "$TARGET/.gitignore" ]; then
    # Убрать наши строки из .gitignore
    sed -i '/\.orchestration\//d; /__pycache__\//d' "$TARGET/.gitignore" 2>/dev/null || true
    echo "  строки .orchestration убраны из .gitignore"
  fi
else
  echo "  .orchestration/ сохранён (используйте --all для полного удаления)"
fi

echo ""
echo "== Готово =="
echo "  Оркестрация удалена. Для повторной установки: bash $KIT/install-local.sh"
