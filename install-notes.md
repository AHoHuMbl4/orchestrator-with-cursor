# orchestration-kit — установка и перенос

## Локальный режим (главный): одна папка, Claude Code + Codex + Kimi, без GitHub

1. Распакуйте `orchestration-kit/` куда удобно (он самодостаточен).
2. В рабочей папке (где будете запускать claude/codex/kimi) выполните:
   `bash /путь/к/orchestration-kit/install-local.sh`
   Скрипт идемпотентен, чужие настройки не трогает (kimi-конфиг — бэкап
   `.bak-orch`, блок хуков добавляется один раз).
3. Итог: в папке — `.claude/` (скилл+хуки+команда `/orch-menu`), `.codex/hooks.json`,
   `.agents/skills/`, `.orchestration/` (params/compass); в домашней —
   `~/.kimi-code/` (скилл + хуки) и `~/.agents/skills/`.
4. Первый запуск: Codex — `/hooks` → доверить хуки (обязательный trust);
   Kimi — `/reload` или рестарт; Claude — работает сразу.
5. Проверка: спросите агента «какие скиллы доступны?» — должен быть
   orchestration; «меню» — покажет параметры пачки.

Состав kit: skills/orchestration (доктрина), bin/ (reground — сверка курса,
menu — детерминированное меню, run-exec — локальный cursor-agent, run-cloud —
облако Cursor, discover — снимок моделей), hooks/ (сниппеты), panel/ (опция),
install.sh (режим «kit внутри репо»), install-local.sh (локальный режим).


Портативный набор: параметры пачки (params.json + compass), динамическое
подтягивание моделей, периодическая сверка курса (re-ground хуки), меню в чате,
HTML-панель, локальный и облачный запуск исполнителей. Всё — python3.6+ stdlib,
кроссплатформенно (Linux/macOS/Windows), без зависимостей и без git для слоя
параметров.

## Состав

```
params.json            шаблон параметров (сеется в .orchestration/params.json)
compass.md             шаблон задачи
bin/orchlib.py         общая библиотека (state-каталог, params, валидация)
bin/discover.py        снимок моделей/efforts -> .orchestration/discovered.json
bin/reground.py        движок сверки курса (session-start / post-tool / heartbeat)
bin/run-exec.py        локальный запуск cursor-agent (доктрина §2, кроссплатформенно)
bin/run-cloud.py       облачный запуск: Cursor Cloud Agents API (нулевая установка)
hooks/*.snippet.*      сниппеты хуков для Claude Code / Codex / Kimi
panel/server.py        HTTP-панель параметр-редактор + статус прогонов
panel/index.html       интерфейс панели
skills/cursor-orchestration/  скилл-регламент (SKILL.md + references)
```

Runtime (создаётся сам): `.orchestration/` — params.json, compass.md,
discovered.json, counters/, prompt-*.md, cursor-run-*.log, cloud-*.log,
pid-файлы. Добавить в `.gitignore`. Сменить расположение: env `ORCHESTRATION_DIR`.

## Быстрый старт (любая ОС)

```bash
python3 orchestration-kit/bin/discover.py                # модели/efforts движков
python3 orchestration-kit/panel/server.py                # панель (порт из params)
python3 orchestration-kit/bin/run-exec.py --id T1        # запуск исполнителя
python3 orchestration-kit/bin/menu.py --set review.reviewers_per_diff=5   # меню-скрипт
```

На Windows: `python` или `py -3` вместо `python3`.

## Команда меню `/orch-menu`

- Claude Code: `commands/claude-orch-menu.md` → `.claude/commands/orch-menu.md`.
- Codex CLI: `commands/codex-orch-menu.md` → `~/.codex/prompts/orch-menu.md`.
- Kimi Code: ничего не нужно — скилл `skills/orch-menu/` сам регистрируется
  слэш-командой `/orch-menu`; при занятом агенте команда встаёт в очередь,
  Ctrl-S — вклинить немедленно.
Применение — всегда через `bin/menu.py` (валидация, rc=2 на мусор), плюс хук
`UserPromptSubmit` вклеивает изменённые параметры при следующем сообщении.

## Бутстрап веб-режима без компа

`python3 orchestration-kit/bin/make-bootstrap.py bootstrap-web.md` — собирает
весь kit в один промт (23 файла, ~100КБ) для ПЕРВОЙ облачной сессии
claude.ai/code: она создаёт файлы, хуки, команды, коммитит и показывает
приёмку (git show --stat, discover, ls .claude/skills). Дальше — работа с
телефона: `/orch-menu` в чате, исполнители через run-cloud.py (ключ
CURSOR_API_KEY в настройках окружения claude.ai + allowlist api.cursor.com).

## Установка скилла

- Claude Code: `.claude/skills/cursor-orchestration/` (проект) или
  `%USERPROFILE%\.claude\skills\` (пользователь).
- Codex: `.agents/skills/` (репо; сканирует от CWD вверх) или
  `%USERPROFILE%\.agents\skills\`. НЕ `.codex/skills/`.
- Kimi Code: `~/.kimi-code/skills/` или `~/.agents/skills/`.
- Cursor: `.cursor/skills/`, `~/.cursor/skills/`, `.agents/skills/`.
- Абзац-указатель в `AGENTS.md` — правка владельцем.

## Установка хуков re-ground (сниппеты в hooks/)

1. Замени `__PYTHON__` → `python3` (Linux/macOS) или `python` / полный путь к
   `python.exe` (Windows), `<KIT>` → абсолютный путь к orchestration-kit.
2. Claude Code: содержимое `claude-settings.snippet.json` (без ключа `_readme`)
   → `.claude/settings.json` проекта. Windows-надёжный вариант — exec-форма:
   `"command": "C:\\...\\python.exe", "args": ["<KIT>\\bin\\reground.py", ...]`.
3. Codex: `codex-hooks.snippet.json` — схему сверить с
   developers.openai.com/codex/hooks (машина сборки была без codex).
4. Kimi: блоки из `kimi-config.snippet.toml` → `~/.kimi-code/config.toml`,
   рестарт процесса kimi web (env читается при старте сервера — ловушка №10).
5. Проверка standalone — см. `skills/.../references/reground.md`.

## Облачные исполнители (нулевая установка)

`bin/run-cloud.py` — Cursor Cloud Agents API (`api.cursor.com`, public beta):
ключ из Cursor Dashboard → API Keys (`--api-key` или env `CURSOR_API_KEY`),
paid-план, биллинг по токенам. `run` создаёт агента с промтом из файла
(`--wait` — поллить до done), `status`/`artifacts` — опрос. Схема beta: первый
живой прогон калибрует парсинг id (ответ логируется в cloud-<id>.log целиком).
Альтернативы: Claude Managed Agents API (см. references/cloud.md), Codex cloud —
только веб-UI/CLI.

## Панель

`python3 panel/server.py [--host H] [--port P]` — по умолчанию 127.0.0.1:8765
(порт конфигурируется в params.json; в песочке сборки 8765 был занят — там
панель на 8766). Только редактор и наблюдатель: не запускает задачи, не решает.
Наружу не выставлять; доступ извне — ssh-туннель/tailscale.

## Проверено при сборке (16.09, песочница /srv/data/cursor/1/ai-setup)

- discover.py: cursor 223 модели (после фикса дефиса в regex), claude алиасы +
  effort-уровни из --help, codex absent-заглушка;
- reground.py: standalone-триггеры (тихо/тихо/nudge) для post-tool и heartbeat,
  session-start вклейка;
- хук Claude Code live: SessionStart hook_response с вклейкой параметров пачки
  (панель → params.json → хук — цепочка доказана); полный in-run прогон не
  выполним в песочнице (нет авторизации claude API) — проверить на боевой машине;
- панель: GET/POST params, валидация (кривой parallel_per_task отбивается),
  compass, status/logs с tail;
- run-exec.py: микро-прогон exit 0, отчёт-файл, строка самопроверки курса в
  промте (исполнитель её применял);
- e2e: 2 параллельных research-прогона + меню (ответы владельца записаны в
  params через панель);
- research-основа: .claude/state/research-R1..R7.md (все URL выборочно проверены).
