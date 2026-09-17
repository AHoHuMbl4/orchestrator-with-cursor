---
description: Меню параметров оркестрации (применяет orchestration-kit/bin/menu.py)
argument-hint: "[--show | --set key=value ... | --task текст --session <id> | --reset-template]"
---

Выполни ровно: `python3 orchestration-kit/bin/menu.py $ARGUMENTS`
(без аргументов — сначала `--show`, затем спроси, что поменять).
Вывод скрипта покажи дословно. Не правь params.json/compass.md сам и не
оценивай, нужны ли изменения: слово владельца → скрипт → вывод.

Примеры (id сессии — из строки «Сессия: <id>» во вклейке хука):
- шаблон общий редактируется только в панели (Расширенные); задача сессии: `--task "текст" --session <id>` (или env ORCH_SESSION_ID)
- показать compass сессии: `--show --session <id>`
- аварийное восстановление шаблона (только если владелец попросил): `--reset-template`

Правила и ключи: skills/orchestration/SKILL.md.
