# Карта системы оркестрации (ориентир ~60 с)

Пути кита — от корня `orchestration-kit/`.
State — `.orchestration/` в проекте.
Резолв state: `$ORCHESTRATION_DIR` или первый `.orchestration` вверх от cwd.
Не ищи инструменты по ФС вслепую — сначала эта карта, потом конкретный файл.

## A. Файлы кита (что чем запускать)

- Маршрут: роль из `code/` → код → `run-exec.py`; иначе не-код → `run-cloud.py` (явный executor в params перекрывает)
- `bin/run-exec.py` — локальный CLI для кода: cursor-agent, промт из файла, лог/EXIT/retry (прямая ФС проекта)
- `bin/run-cloud.py` — Cursor Cloud для не-кода: create→poll→artifacts (`run` / `status` / `artifacts` / `list`)
- `bin/run-cloud.py` — оба порядка флагов: `--id`/`--api-key` до и после субкоманды
- `bin/run-cloud.py` — ключ: `--api-key` > `CURSOR_API_KEY` > `<state>/cursor.key`; лог `<state>/cloud-<id>.log`
- `bin/run-cloud.py` — `list`: активные агенты; `<state>/cloud-<id>.result.json` (машиночитаемый итог: agent/run/status/result); `<state>/agent-<id>.json` (id для follow-up без ре-парсинга лога)
- `bin/menu.py` — меню params; задача → сессионный compass (`--task` + `--session <sid>`)
- `bin/verdict.py` — JSON-статус прогона из лога: `python3 bin/verdict.py <лог>`
- `bin/discover.py` — снимок моделей/ключа → `.orchestration/discovered.json`
- `bin/reground.py` — хук-движок: `session-start` / `prompt-submit` / `post-tool` / `heartbeat`
- `bin/orchlib.py` — общая библиотека (find_state_dir, params, сессии); не лаунчер
- `panel/server.py` — HTTP-панель (порт база 8765, при занятости +1…)
- `./panel.sh` (корень проекта после install) — запуск панели → `http://127.0.0.1:8765+`
- `install-local.sh` / `install-local.ps1` — установка скилла, хуков, `/orch-menu`, panel
- `uninstall.sh` / `uninstall.ps1` — снятие установки
- `SHA256SUMS` — целостность дистрибутива кита
- `commands/claude-orch-menu.md` — источник `/orch-menu` для Claude (install → `orch-menu.md`)
- `commands/codex-orch-menu.md` — источник меню для Codex (install → `~/.codex/prompts/orch-menu.md`)
- `hooks/` — сниппеты хуков движков (подключает install-local)
- `skills/orchestration/SKILL.md` — регламент оркестратора
- `compass.md` (корень кита) — исходник общего шаблона compass

## B. State-каталог `.orchestration` (что где лежит)

- `params.json` — параметры пачки (`execution.*`, `review.*`, `reground.every_min`, …)
- `compass.md` — ОБЩИЙ шаблон; правится только в панели «Расширенные»
- `sessions/<sid>/compass.md` — личная копия сессии; авто-сеется; пишет `menu.py --session`
- `sessions/<sid>/runs/<id>/prompt.md` — промт прогона
- `sessions/<sid>/runs/<id>/run.log` — лог прогона (локальный/обёртка)
- `sessions/<sid>/runs/<id>/artifact.md` — артефакт приёмки
- `<state>/cloud-<id>.log` — лог `run-cloud.py` (create/status/artifacts)
- `<state>/cloud-<id>.result.json` — машиночитаемый итог run-cloud (agent/run/status/result)
- `<state>/agent-<id>.json` — id агента для follow-up без ре-парсинга лога
- `cursor.key` — API-ключ Cursor (gitignore; панель сохраняет сюда)
- `counters/` — счётчики хуков (nudge / heartbeat / prompt-submit, …)
- `discovered.json` — снимок `bin/discover.py`
- Правило: запускай всё из одной папки проекта (или задай `ORCHESTRATION_DIR`)

## C. Скилл и роли (как выбирать)

- `skills/orchestration/SKILL.md` — регламент: исполнители, контроль, компас, вердикт
- `references/planning.md` — план, волны, DAG, preflight-бюджет
- `references/engines.md` — исполнители/ключи/движки (local-cursor / cursor-cloud, Claude, Codex, Kimi)
- `references/traps.md` — ловушки (читать перед пачкой)
- `references/MAP.md` — эта карта (инструменты и state)
- `references/roles/_index.md` — КАТАЛОГ РОЛЕЙ; выбор ТОЛЬКО через него
- Каскад `_index.md`: домен → поддомен → роль (колонка «Когда»); не `ls` дерева
- `references/roles/_template.md` — каркас: `{{ЗАДАЧА}}` / `{{КРИТЕРИЙ}}` / `{{ГРАНИЦЫ}}` + процесс/анти-паттерны
- `references/roles/<домен>/<роль>.md` — шаблон промта роли (путь берётся из `_index.md`)

## D. Быстрые ответы

| Вопрос | Ответ |
|---|---|
| Где взять инструмент? | секция A |
| Куда пишется задача? | `sessions/<sid>/compass.md` (`menu.py --session`) |
| Куда упал результат? | `sessions/<sid>/runs/<id>/` + лог (`run.log` / `cloud-<id>.log`) |
| Панель? | `./panel.sh` → `panel/server.py` на `127.0.0.1:8765+` |
| Что-то не найти? | НЕ искать по ФС вслепую: сначала эта карта, потом файл из неё |
