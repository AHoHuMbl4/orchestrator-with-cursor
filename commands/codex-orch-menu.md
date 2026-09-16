---
description: Меню параметров оркестрации (применяет orchestration-kit/bin/menu.py)
argument-hint: "--set key=value ... | --show | --task текст"
---

Выполни ровно: `python3 orchestration-kit/bin/menu.py $ARGUMENTS`
(без аргументов — сначала `--show`, затем спроси, что поменять).
Вывод скрипта покажи дословно. Не правь params.json/compass.md сам и не
оценивай, нужны ли изменения: слово владельца → скрипт → вывод.
Правила и ключи: skills/orchestration/SKILL.md.
Примечание: этот файл кладётся в ~/.codex/prompts/orch-menu.md (custom prompt),
после чего доступен как /orch-menu в интерактиве Codex.
