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
# Наши = reground.py ИЛИ orchestration-kit (в т.ч. без маркеров)
if [ -f "$CLAUDE_DIR/settings.json" ] && command -v python3 >/dev/null 2>&1; then
  ORCH_CLAUDE_SETTINGS="$CLAUDE_DIR/settings.json" python3 - <<'PYEOF' 2>/dev/null || true
import json, os, sys
path = os.environ["ORCH_CLAUDE_SETTINGS"]
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)

def is_ours(entry):
    s = json.dumps(entry)
    return ("reground.py" in s) or ("orchestration-kit" in s)

hooks = d.get("hooks", {})
changed = False
for event in ("SessionStart", "UserPromptSubmit", "PostToolUse"):
    if event in hooks:
        filtered = [e for e in hooks[event] if not is_ours(e)]
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
  # Иначе — удалить только наши события (reground.py / orchestration-kit)
  python3 - <<PYEOF 2>/dev/null || true
import json, os, sys
path = "$CODEX_HOOKS"
try:
    d = json.load(open(path))
except Exception:
    sys.exit(0)

def is_ours(entry):
    s = json.dumps(entry)
    return ("reground.py" in s) or ("orchestration-kit" in s)

hooks = d.get("hooks", {})
# unlink только если КАЖДАЯ запись в КАЖДОМ event — наша
ours = (
    all(all(is_ours(e) for e in v) for v in hooks.values())
    if hooks else False
)
if ours and len(hooks) <= 3:
    os.unlink(path)
    print("  удалён файл: $CODEX_HOOKS (содержал только наши хуки)")
else:
    changed = False
    for event in list(hooks.keys()):
        filtered = [e for e in hooks[event] if not is_ours(e)]
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

# Снять маркированный блок + все [[hooks]] с reground.py в command (в т.ч. без маркеров)
clean_kimi_config_toml() {
  local KIMI_CFG="$1"
  [ -f "$KIMI_CFG" ] || return 0
  if ! grep -qE 'orchestration-kit hooks|reground\.py' "$KIMI_CFG" 2>/dev/null; then
    return 0
  fi
  if [ ! -f "$KIMI_CFG.bak-uninstall" ]; then
    cp "$KIMI_CFG" "$KIMI_CFG.bak-uninstall"
  fi
  if grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
    sed -i '/# >>> orchestration-kit hooks >>>/,/# <<< orchestration-kit hooks <<</d' "$KIMI_CFG"
  fi
  local py_ok=1
  if grep -q 'reground\.py' "$KIMI_CFG" 2>/dev/null; then
    if ! command -v python3 >/dev/null 2>&1; then
      echo "  предупреждение: нет python3 — unmarked [[hooks]] с reground.py не зачищены в $KIMI_CFG" >&2
      py_ok=0
    else
      if ! ORCH_KIMI_CFG="$KIMI_CFG" python3 - <<'PYEOF'
import os, re, sys
path = os.environ["ORCH_KIMI_CFG"]
try:
    with open(path) as f:
        lines = f.readlines()
except Exception as exc:
    sys.stderr.write("kimi config read failed: %s\n" % exc)
    sys.exit(1)

def cmd_has_reground(block):
    for ln in block:
        # без якоря EOL: допускаем хвост / # comment после кавычек
        m = re.match(r'\s*command\s*=\s*"([^"]*)"', ln)
        if m is None:
            m = re.match(r"\s*command\s*=\s*'([^']*)'", ln)
        if m is not None and "reground.py" in m.group(1):
            return True
    return False

out = []
i = 0
n = len(lines)
changed = False
while i < n:
    stripped = lines[i].strip()
    if stripped == "[[hooks]]" or stripped.startswith("[[hooks]]"):
        block = [lines[i]]
        i += 1
        while i < n and not lines[i].lstrip().startswith("[["):
            block.append(lines[i])
            i += 1
        if cmd_has_reground(block):
            changed = True
            continue
        out.extend(block)
    else:
        out.append(lines[i])
        i += 1

cleaned = []
for ln in out:
    if ln.strip() in (
        "# >>> orchestration-kit hooks >>>",
        "# <<< orchestration-kit hooks <<<",
    ):
        changed = True
        continue
    cleaned.append(ln)
while cleaned and cleaned[-1].strip() == "":
    cleaned.pop()
    changed = True

if changed:
    try:
        with open(path, "w") as f:
            f.writelines(cleaned)
            if cleaned and not cleaned[-1].endswith("\n"):
                f.write("\n")
    except Exception as exc:
        sys.stderr.write("kimi config write failed: %s\n" % exc)
        sys.exit(1)
PYEOF
      then
        echo "  предупреждение: python3 не смог зачистить unmarked [[hooks]] в $KIMI_CFG" >&2
        py_ok=0
      fi
    fi
  fi
  # Убрать пустые строки в конце, если образовались
  sed -i -e :a -e '/^\n*$/{$d;N;ba' -e '}' "$KIMI_CFG" 2>/dev/null || true
  if grep -q 'reground\.py' "$KIMI_CFG" 2>/dev/null; then
    echo "  предупреждение: в $KIMI_CFG ещё есть reground.py — зачистка неполная" >&2
    return 0
  fi
  if [ "$py_ok" = "1" ]; then
    echo "  хуки reground зачищены в $KIMI_CFG (бэкап: .bak-uninstall)"
  fi
}

clean_kimi_config_toml "$KIMI_DIR/config.toml"
# Доп. путь: CLAUDE_CONFIG_DIR/.kimi-code/config.toml (если задан и отличается)
if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
  _extra_kimi="$CLAUDE_CONFIG_DIR/.kimi-code/config.toml"
  if [ "$_extra_kimi" != "$KIMI_DIR/config.toml" ]; then
    clean_kimi_config_toml "$_extra_kimi"
  fi
fi
unset -f clean_kimi_config_toml 2>/dev/null || true

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
