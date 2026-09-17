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

Состав kit: skills/orchestration (скилл: SKILL.md + references), commands/
(файлы команды /orch-menu для Claude и Codex), bin/ (orchlib — общее ядро,
reground — сверка курса, menu — детерминированное меню, run-exec — локальный
cursor-agent, run-cloud — облако Cursor, discover — снимок моделей),
hooks/ (сниппеты), panel/ (панель настроек), params.json и compass.md
(шаблоны), install.sh (режим «kit внутри репо»), install-local.sh (главный).


Портативный набор: параметры пачки (params.json + compass), динамическое
подтягивание моделей, периодическая сверка курса (re-ground хуки), меню в чате,
HTML-панель, локальный и облачный запуск исполнителей. Всё — python3.6+ stdlib,
кроссплатформенно (Linux/macOS/Windows), без зависимостей и без git для слоя
параметров.

## Состав

```
params.json            шаблон параметров (сеется в .orchestration/params.json)
compass.md             общий стартовый шаблон (сеется в .orchestration/compass.md)
bin/orchlib.py         общая библиотека (state-каталог, params, валидация)
bin/discover.py        снимок моделей/efforts -> .orchestration/discovered.json
bin/reground.py        движок сверки курса (session-start / post-tool / heartbeat)
bin/run-exec.py        локальный запуск cursor-agent (доктрина §2, кроссплатформенно)
bin/run-cloud.py       облачный запуск: Cursor Cloud Agents API (нулевая установка)
bin/verdict.py         JSON-статус прогона из лога (exit/verdict/report_present/retries)
hooks/*.snippet.*      сниппеты хуков для Claude Code / Codex / Kimi
panel/server.py        HTTP-панель параметр-редактор + статус прогонов
panel/index.html       интерфейс панели
skills/orchestration/  скилл-регламент (SKILL.md + references)
```

Runtime (создаётся сам): `.orchestration/` — params.json, compass.md (шаблон),
sessions/<id>/compass.md, discovered.json, counters/, runs/, логи, pid-файлы.
Добавить в `.gitignore`. Сменить расположение: env `ORCHESTRATION_DIR`.

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

- Claude Code: `.claude/skills/orchestration/` (проект) или
  `%USERPROFILE%\.claude\skills\` (пользователь).
- Codex: `.agents/skills/` (репо; сканирует от CWD вверх) или
  `%USERPROFILE%\.agents\skills\`. НЕ `.codex/skills/`.
- Kimi Code: `~/.kimi-code/skills/` или `~/.agents/skills/`.
- Cursor: `.cursor/skills/`, `~/.cursor/skills/`, `.agents/skills/`.
- Абзац-указатель в `AGENTS.md` — правка владельцем.

## Установка хуков re-ground (сниппеты в hooks/)

1. Замени `__PYTHON__` → `python3` (Linux/macOS) или полный путь к `python.exe`
   (Windows), `<KIT>` → абсолютный путь к orchestration-kit. На Windows —
   полные пути к python.exe и kit в двойных кавычках с прямыми слэшами
   (см. ## Windows / `_readme` в сниппете).
2. Claude Code: содержимое `claude-settings.snippet.json` (без ключа `_readme`)
   → `.claude/settings.json` проекта.
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

Основная зона: **селектор сессий** (compass только сессионные —
`sessions/<id>/compass.md`), тумблер, исполнители, критики, таймаут,
`retry_on_fail`, модели, токен Cursor. Раздел **«Расширенные»**: общий стартовый
шаблон `.orchestration/compass.md` (сохранение с подтверждением, кнопка
восстановления стандартного из kit). CLI `menu.py` шаблон не пишет
(`--global-template` → exit 2).

## Итоги прогонов (EXIT / RETRY / verdict.py)

В конце лога локального прогона — строка `EXIT=<код>`:

| EXIT | Смысл |
|---|---|
| 0 | успех |
| 1 | агент отчитался о фейле |
| 3 | умер с отчётом ассистента в логе |
| 4 | умер без отчёта |
| 124 | таймаут |
| UNKNOWN | лог нет/нечитаем |

Автоперезапуск при EXIT=4 / EXIT=124 по `execution.retry_on_fail` (в логе
`RETRY=n/max`). Отчёты исполнителей/критиков кончаются
`Вердикт: OK | PROBLEMS | BLOCKED` + доказательства. Сводка одной командой:

```bash
python3 orchestration-kit/bin/verdict.py .orchestration/sessions/<sid>/runs/<id>/run.log
```

JSON: `exit`, `verdict`, `report_present`, `retries`.

## ENV установщиков

- `CLAUDE_CONFIG_DIR` — каталог конфигурации Claude (замена `~/.claude` при
  `--global` / `-Global`).
- `KIMI_CODE_HOME` / `KIMI_HOME` — домашний каталог Kimi (приоритет:
  `KIMI_CODE_HOME` > `KIMI_HOME` > `~/.kimi-code`).
- `ORCHESTRATION_DIR` — расположение runtime-state (см. выше).
- `CURSOR_API_KEY` — ключ облачных исполнителей (альтернатива панели).

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


## Удаление

```bash
bash orchestration-kit/uninstall.sh          # из текущей папки
bash orchestration-kit/uninstall.sh --global  # пользовательский уровень (--global install)
bash orchestration-kit/uninstall.sh --all     # и .orchestration/ со всем содержимым
```

Безопасно: чужие хуки/настройки сохраняются, бэкапы создаются, идемпотентен.

## GLM Code (не движок, а бэкенд)

GLM Coding Plan — способ запускать Claude Code / Codex на моделях Zhipu:
`ANTHROPIC_BASE_URL=https://api.z.ai/api/anthropic` + `ANTHROPIC_AUTH_TOKEN=<ключ>`
в `~/.claude/settings.json`. Скиллы и хуки работают без изменений.

## Служебные файлы

- `install-check` — временная сессия самопроверки установщика; автоматически удаляется после установки и скрывается из панели
- `.orchestration/compass.md` — шаблон; рабочие compass каждой сессии — в `.orchestration/sessions/<id>/compass.md`

## Известные нюансы

- Codex требует `/hooks` trust после установки — без него хуки молчат (скиллы работают)
- Python 3.6+ обязателен для хуков/панели/скриптов (скилл работает и без него)
- cursor-agent или ключ Cursor — для исполнителей; без них система спрашивает явно
- `--permission-prompts none` требует Claude Code ≥ v2.1.259; fallback: `dontAsk`

## Windows

Платформы: Linux, macOS (bash-инсталлер), Windows 10/11 (PowerShell-инсталлер);
python 3.6+.

Установка (из рабочей папки, куда скопирован/склонирован репозиторий):

```powershell
powershell -ExecutionPolicy Bypass -File orchestration-kit\install-local.ps1
```

Или `pwsh` вместо `powershell`. `-Global` — уровень пользователя. Повторный
запуск безопасен (идемпотентен).

Удаление:

```powershell
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -Global
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -All
```

Python ищется: `python` → `py -3` → `python3`; без python — режим «только
скиллы» с предупреждением (как в bash-версии). Установка:
`winget install Python.Python.3.12` (отметить Add to PATH).

Хуки Claude на Windows — в **exec-форме** (`command` + `args`): python
запускается напрямую, **без Git Bash**. Codex/Kimi: рекомендуются путь к kit
**без пробелов** и лаунчер `py` (`py -3 …`); иначе cmd.exe-спавн может сломаться
на квотированных путях с пробелами.

Codex: в каждый хук `hooks.json` добавляется поле `commandWindows` (camelCase —
формат Codex); первый запуск codex → `/hooks` → доверить (общее правило,
действующее и на Linux).

Kimi на Windows: нативная установка, хуки в `config.toml` (маркерные блоки
`>>>` / `<<<`).

Python-скрипты оркестрации принудительно держат stdio в UTF-8 (защита от
cp1251/cp866 на русской Windows); subprocess-вывод (tasklist, cursor-agent)
декодируется UTF-8. Скрипты `.ps1` сохранены в UTF-8 с BOM.

Панель на Windows: `panel.ps1` (ключ `-Bg` — фон) и `panel.cmd` (для
cmd/проводника).

Windows-инсталлер проверен статическим ревью и паритет-аудитом против
bash-версии; живой прогон на Windows выполните по первой установке и сообщите
результат.
