#!/usr/bin/env bash
# Детерминированная установка orchestration-kit в репозиторий.
# Контент едет файлами (unzip/cp), НЕ через пересказ моделью.
#
# Режим «kit в репо» (рядом со скриптом есть skills/):
#   bash orchestration-kit/install.sh
#   bash orchestration-kit/install.sh --commit
#
# Режим «из каталога скилла» (скрипт в <скилл>/scripts/, CWD = корень репо):
#   bash <скилл>/scripts/install.sh
#   bash <скилл>/scripts/install.sh --commit
set -euo pipefail

KIT=orchestration-kit
SCRIPT_DIR="$(dirname "$0")"

PY=python3
command -v python3 >/dev/null 2>&1 || PY=python

verify_checksums() {
  local payload_dir="$1"
  echo "== 1/6 проверка контрольных сумм =="
  (cd "$payload_dir" && sha256sum -c SHA256SUMS --quiet) || {
    echo "ОШИБКА: суммы не сошлись — архив повреждён"
    exit 1
  }
  echo "ok: все файлы байт-в-байт"
}

merge_hooks() {
  echo "== 3/6 хуки (.claude/settings.json) =="
  sed -e 's|__PYTHON__|python3|g' -e "s|<KIT>|$KIT|g" \
      "$KIT/hooks/claude-settings.snippet.json" > /tmp/orch-snippet.json
  $PY - <<'PYEOF'
import json, os
snip = json.load(open("/tmp/orch-snippet.json"))
snip.pop("_readme", None)
path = ".claude/settings.json"
cur = {}
if os.path.exists(path):
    try:
        cur = json.load(open(path))
    except Exception:
        cur = {}
hooks = cur.setdefault("hooks", {})
for event, entries in snip.get("hooks", {}).items():
    merged = hooks.get(event, [])
    have = {json.dumps(e, sort_keys=True) for e in merged}
    for e in entries:
        if json.dumps(e, sort_keys=True) not in have:
            merged.append(e)
    hooks[event] = merged
json.dump(cur, open(path, "w"), indent=2, ensure_ascii=False)
open(path, "a").write("\n")
print("settings.json: хуки на месте" + (" (вмержено в существующий)" if cur else ""))
PYEOF
}

install_menu_and_gitignore() {
  echo "== 4/6 команда меню и gitignore =="
  mkdir -p .claude/commands
  cp "$KIT/commands/claude-orch-menu.md" .claude/commands/orch-menu.md
  while IFS= read -r line; do
    grep -qxF "$line" .gitignore 2>/dev/null || echo "$line" >> .gitignore
  done <<'EOF'
.orchestration/counters/
.orchestration/*.log
.orchestration/*.pid
.orchestration/prompt-*.run.md
.orchestration/discovered.json
.orchestration/cursor.key
__pycache__/
EOF
}

set_perms_and_discover() {
  echo "== 5/6 права и снимок моделей =="
  chmod +x "$KIT"/bin/*.py 2>/dev/null || true
  $PY "$KIT/bin/discover.py" >/dev/null 2>&1 && echo "discover: ok (volatile, не коммитится)" || echo "discover: предупредждение (см. .orchestration/discovered.json)"
}

self_check() {
  echo "== 6/6 самопроверка =="
  $PY "$KIT/bin/menu.py" --show | head -1
  echo '{"session_id":"install-check"}' | $PY "$KIT/bin/reground.py" post-tool --engine claude
  echo "ok: reground молчит (порог не достигнут) — так и должно быть"
}

print_done() {
  echo
  echo "УСТАНОВЛЕНО. Дальше: сессии сказать «настрой оркестрацию» (скилл orch-setup),"
  echo "затем просто писать задачи. Меню: /orch-menu."
}

# ---------------------------------------------------------------------------
# Режим: рядом со скриптом есть skills/ → kit в репо; иначе → skill-режим
# ---------------------------------------------------------------------------
if [ -d "$SCRIPT_DIR/skills" ]; then
  # Старый режим: install.sh лежит в orchestration-kit/, корень репо = родитель
  cd "$SCRIPT_DIR/.."

  verify_checksums "$KIT"

  echo "== 2/6 скиллы =="
  mkdir -p .claude/skills .agents/skills
  for s in cursor-orchestration orch-menu orch-setup; do
    rm -rf ".claude/skills/$s" ".agents/skills/$s"
    cp -r "$KIT/skills/$s" ".claude/skills/$s"
    cp -r "$KIT/skills/$s" ".agents/skills/$s"   # копия надёжнее симлинка
  done

  merge_hooks
  install_menu_and_gitignore
  set_perms_and_discover
  self_check

  if [ "${1:-}" = "--commit" ]; then
    git add .claude/skills/cursor-orchestration .claude/skills/orch-menu .claude/skills/orch-setup \
            .agents/skills/cursor-orchestration .agents/skills/orch-menu .agents/skills/orch-setup \
            .claude/commands/orch-menu.md .claude/settings.json .gitignore \
            orchestration-kit
    git commit -m "orchestration: kit installed (skills, hooks, menu command)"
    git show --stat HEAD | head -15
  fi

  print_done
else
  # Skill-режим: скрипт в <скилл>/scripts/, CWD уже корень целевого репо (не cd)
  PAYLOAD="$SCRIPT_DIR"
  SKILL_SRC="$(dirname "$SCRIPT_DIR")"

  verify_checksums "$PAYLOAD"

  echo "== 2/6 kit payload и скилл =="
  mkdir -p "$KIT"
  for item in bin hooks panel commands; do
    rm -rf "$KIT/$item"
    cp -r "$PAYLOAD/$item" "$KIT/$item"
  done
  for item in params.json compass.md install-notes.md; do
    cp "$PAYLOAD/$item" "$KIT/$item"
  done

  mkdir -p .claude/skills .agents/skills
  rm -rf .claude/skills/orchestration .agents/skills/orchestration
  cp -r "$SKILL_SRC" .claude/skills/orchestration
  cp -r "$SKILL_SRC" .agents/skills/orchestration

  merge_hooks
  install_menu_and_gitignore
  set_perms_and_discover
  self_check

  if [ "${1:-}" = "--commit" ]; then
    git add .claude/skills/orchestration .agents/skills/orchestration \
            .claude/commands/orch-menu.md .claude/settings.json .gitignore \
            orchestration-kit
    git commit -m "orchestration: kit installed (skills, hooks, menu command)"
    git show --stat HEAD | head -15
  fi

  print_done
fi
