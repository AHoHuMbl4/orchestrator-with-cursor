#!/usr/bin/env bash
# Локальная установка оркестрации для трёх движков: Claude Code, Codex, Kimi.
# Без GitHub: всё живёт в одной рабочей папке + пара файлов в ~/.codex и
# ~/.kimi-code (там, где движки ищут свои конфиги).
#
# Запуск из папке, где вы работаете:
#   bash /путь/к/orchestration-kit/install-local.sh
# Повторный запуск безопасен (идемпотентен, чужие настройки не трогает).
set -euo pipefail

KIT="$(cd "$(dirname "$0")" && pwd)"      # абсолютный путь к orchestration-kit
TARGET="$(pwd)"                           # рабочая папка
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

echo "== 0/6 проверка целостности kit =="
(cd "$KIT" && sha256sum -c SHA256SUMS --quiet) || { echo "ОШИБКА: суммы не сошлись"; exit 1; }
echo "ok (TARGET=$TARGET)"

echo "== 1/6 Claude Code (папка) =="
mkdir -p "$TARGET/.claude/skills" "$TARGET/.claude/commands"
rm -rf "$TARGET/.claude/skills/orchestration"
cp -r "$KIT/skills/orchestration" "$TARGET/.claude/skills/orchestration"
cp "$KIT/commands/claude-orch-menu.md" "$TARGET/.claude/commands/orch-menu.md"
cat > /tmp/orch-claude-snippet.json <<EOF
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py session-start --engine claude", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py prompt-submit --engine claude", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py post-tool --engine claude", "timeout": 10}]}
    ]
  }
}
EOF
$PY - <<'PYEOF'
import json, os
snip = json.load(open("/tmp/orch-claude-snippet.json"))
path = os.path.join(os.environ.get("ORCH_TARGET", os.getcwd()), ".claude", "settings.json")
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
print("  .claude/settings.json: хуки сверки (SessionStart/UserPromptSubmit/PostToolUse)")
PYEOF

echo "== 2/6 Codex (папка) =="
mkdir -p "$TARGET/.codex" "$TARGET/.agents/skills"
rm -rf "$TARGET/.agents/skills/orchestration"
cp -r "$KIT/skills/orchestration" "$TARGET/.agents/skills/orchestration"
cat > "$TARGET/.codex/hooks.json" <<EOF
{
  "description": "orchestration-kit: сверка курса",
  "hooks": {
    "SessionStart": [
      {"matcher": "startup|resume|clear|compact",
       "hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py session-start --engine codex", "timeout": 10}]}
    ],
    "UserPromptSubmit": [
      {"hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py prompt-submit --engine codex", "timeout": 10}]}
    ],
    "PostToolUse": [
      {"hooks": [{"type": "command", "command": "$PY $KIT/bin/reground.py post-tool --engine codex", "timeout": 10}]}
    ]
  }
}
EOF
echo "  .codex/hooks.json + .agents/skills/orchestration"

echo "== 3/6 Kimi (пользовательские конфиги) =="
KIMI_DIR="${KIMI_HOME:-$HOME/.kimi-code}"
mkdir -p "$KIMI_DIR/skills" "$HOME/.agents/skills"
rm -rf "$KIMI_DIR/skills/orchestration" "$HOME/.agents/skills/orchestration"
cp -r "$KIT/skills/orchestration" "$KIMI_DIR/skills/orchestration"
cp -r "$KIT/skills/orchestration" "$HOME/.agents/skills/orchestration"
KIMI_CFG="$KIMI_DIR/config.toml"
touch "$KIMI_CFG"
if ! grep -q "orchestration-kit hooks" "$KIMI_CFG"; then
  cp "$KIMI_CFG" "$KIMI_CFG.bak-orch"
  cat >> "$KIMI_CFG" <<EOF

# >>> orchestration-kit hooks >>>
[[hooks]]
  event = "UserPromptSubmit"
  command = "$PY $KIT/bin/reground.py prompt-submit --engine kimi"
  timeout = 10

[[hooks]]
  event = "SessionHeartbeat"
  command = "$PY $KIT/bin/reground.py heartbeat --engine kimi"
  timeout = 10
# <<< orchestration-kit hooks <<<
EOF
  echo "  ~/.kimi-code/config.toml: блок хуков добавлен (бэкап: $KIMI_CFG.bak-orch)"
else
  echo "  ~/.kimi-code/config.toml: блок хуков уже есть — не трогаю"
fi
echo "  скилл: ~/.kimi-code/skills/orchestration + ~/.agents/skills/orchestration"

echo "== 4/6 параметры, права и панель =="
chmod +x "$KIT"/bin/*.py 2>/dev/null || true
mkdir -p "$TARGET/.orchestration"
[ -f "$TARGET/.orchestration/params.json" ] || cp "$KIT/params.json" "$TARGET/.orchestration/params.json"
[ -f "$TARGET/.orchestration/compass.md" ] || cp "$KIT/compass.md" "$TARGET/.orchestration/compass.md"
# нормализация старых дефолтов (every_min 7 -> 10), явные значения владельца не трогаем
export ORCH_KIT="$KIT" ORCH_TARGET="$TARGET"
$PY - <<'PYEOF' 2>/dev/null || true
import sys, os
sys.path.insert(0, os.path.join(os.environ["ORCH_KIT"], "bin"))
import orchlib
p = orchlib.load_params()
if p.get("reground", {}).get("every_min") == 7:
    p["reground"]["every_min"] = 10
orchlib.save_params(p)
PYEOF
cat > "$TARGET/panel.sh" <<EOF
#!/usr/bin/env bash
# Настройки оркестрации (панель). Запуск из этой папки: ./panel.sh
exec $PY -u "$KIT/panel/server.py" "\$@"
EOF
chmod +x "$TARGET/panel.sh"
for line in ".orchestration/counters/" ".orchestration/*.log" ".orchestration/*.pid" ".orchestration/prompt-*.run.md" ".orchestration/discovered.json" ".orchestration/cursor.key" "__pycache__/"; do
  grep -qxF "$line" "$TARGET/.gitignore" 2>/dev/null || echo "$line" >> "$TARGET/.gitignore"
done

echo "== 5/6 снимок моделей =="
$PY "$KIT/bin/discover.py" >/dev/null 2>&1 && echo "  discover: ok" || echo "  discover: предупреждение (см. .orchestration/discovered.json)"

echo "== 6/6 самопроверка =="
$PY "$KIT/bin/menu.py" --show | head -1
echo '{"session_id":"install-check"}' | $PY "$KIT/bin/reground.py" post-tool --engine claude
echo "  reground молчит (порог не достигнут) — так и должно быть"

cat <<EOF

УСТАНОВЛЕНО в $TARGET
  Настройки:   ./panel.sh  →  http://127.0.0.1:8765 (или соседний порт)
               (та же панель правит .orchestration/params.json; хук донесёт
               изменения агенту при следующем сообщении)
  Claude Code: запускайте claude в этой папке — скилл + хуки + /orch-menu.
  Codex:       запускайте codex в этой папке; ПЕРВЫЙ РАЗ: /hooks -> доверить
               хуки orchestration (Codex требует явного trust).
  Kimi:        /reload в живой сессии или рестарт kimi (конфиг читается при старте).
Дальше: просто пишите задачу — скилл orchestration подхватится сам, исполнение
пойдёт через субагентов-исполнителей. Меню: «меню» / /orch-menu / панель.
EOF
