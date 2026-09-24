# Карта системы оркестрации (ориентир ~60 с)

Пути кита — от корня `orchestration-kit/`.
State — `.orchestration/` в проекте.
Резолв state: `$ORCHESTRATION_DIR` или первый `.orchestration` вверх от cwd.
Не ищи инструменты по ФС вслепую — сначала эта карта, потом конкретный файл.

## A. Файлы кита (что чем запускать)

- Маршрут: роль из `code/` → код → `run-exec.py`; иначе не-код → `run-cloud.py` (явный executor в params перекрывает)
- `bin/run-exec.py` — локальный CLI для кода: cursor-agent, промт из файла, лог/EXIT/retry (прямая ФС проекта); при overflow compass пишет маркер `COMPASS_OVERFLOW` в лог прогона
- `bin/run-cloud.py` — Cursor Cloud для не-кода: create→poll→artifacts (`run` / `status` / `artifacts` / `list`); при overflow compass пишет маркер `COMPASS_OVERFLOW` в лог прогона
- `bin/run-cloud.py` — оба порядка флагов: `--id`/`--api-key` до и после субкоманды
- `bin/run-cloud.py` — ключ: `--api-key` > `CURSOR_API_KEY` > `<state>/cursor.key`; лог `<state>/cloud-<id>.log`
- `bin/run-cloud.py` — `list`: активные агенты; `<state>/cloud-<id>.result.json` (машиночитаемый итог: agent/run/status/result); `<state>/agent-<id>.json` (id для follow-up без ре-парсинга лога)
- `bin/write-compass.py` — воронка проверенной записи compass (`--path` + `--text-file`/`--stdin`; exit: 0 записано; 1 ошибка чтения/записи; 2 превышение — файл не пишется, pending-флаг; 3 не compass-путь)
- `bin/menu.py` — меню params; задача → сессионный compass (`--task` + `--session <sid>`)
- `bin/verdict.py` — JSON-статус прогона из лога: `python3 bin/verdict.py <лог>`
- `bin/discover.py` — снимок моделей/ключа → `.orchestration/discovered.json`
- `bin/reground.py` — хук-движок: `session-start` / `prompt-submit` / `post-tool` / `heartbeat`
- `bin/orchlib.py` — общая библиотека (find_state_dir, params, сессии, load_fronts / front_compass_path); не лаунчер
- `panel/server.py` — HTTP-панель (порт база 8765, при занятости +1…); API `/api/fronts`; сторож-тред опрашивает compass каждые `compass.guard_poll_s` сек (флаг/подсветка)
- `panel/index.html` — UI: секция «Фронты» (волны, статусы, JSON-редактор)
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
- лимиты compass: сессионный ≤ **8500** символов; `fronts/**` (фронт / мини-compass полковника) ≤ **4000**
- `sessions/<sid>/pending_compass_guard.json` — флаг гарда compass сессии (доставка — ближайший UserPromptSubmit)
- `<state>/pending_compass_guard.json` — общий флаг гарда compass (доставка — ближайший UserPromptSubmit)
- `sessions/<sid>/runs/<id>/prompt.md` — промт прогона
- `sessions/<sid>/runs/<id>/run.log` — лог прогона (локальный/обёртка)
- `sessions/<sid>/runs/<id>/artifact.md` — артефакт приёмки
- `sessions/<sid>/prosecutor/` — закрытый канал прокурора волны (читает только командующий)
- `<state>/cloud-<id>.log` — лог `run-cloud.py` (create/status/artifacts)
- `<state>/cloud-<id>.result.json` — машиночитаемый итог run-cloud (agent/run/status/result)
- `<state>/agent-<id>.json` — id агента для follow-up без ре-парсинга лога
- `cursor.key` — API-ключ Cursor (gitignore; панель сохраняет сюда)
- `counters/` — счётчики хуков (nudge / heartbeat / prompt-submit, …)
- `discovered.json` — снимок `bin/discover.py`
- `<state>/fronts.json` — граф фронтов больших проектов (цель, фронты с ролями/deps/статусами; топосорт-волны; циклы отвергаются; `orchlib.load_fronts`)
- статусы фронта: `planned` / `running` / `blocked` / `done` / `failed`
- `<state>/fronts/<id>/compass.md` — compass каждого фронта (`orchlib.front_compass_path`)
- `<state>/fronts/<id>/colonels/<cid>/compass.md` — мини-compass полковника (лимит как у фронтового)
- Правило: запускай всё из одной папки проекта (или задай `ORCHESTRATION_DIR`)

## C. Скилл и роли (как выбирать)

- `skills/orchestration/SKILL.md` — регламент: исполнители, контроль, компас, вердикт
- `references/planning.md` — план, волны, DAG, preflight-бюджет
- `references/engines.md` — исполнители/ключи/движки (local-cursor / cloud-cursor, Claude, Codex, Kimi)
- `references/traps.md` — ловушки (читать перед пачкой)
- `references/MAP.md` — эта карта (инструменты и state)
- `references/roles/_index.md` — КАТАЛОГ РОЛЕЙ; выбор ТОЛЬКО через него
- Каскад `_index.md`: домен → поддомен → роль (колонка «Когда»); не `ls` дерева
- `references/roles/_template.md` — каркас: `{{ЗАДАЧА}}` / `{{КРИТЕРИЙ}}` / `{{ГРАНИЦЫ}}` + процесс/анти-паттерны
- `references/roles/<домен>/<роль>.md` — шаблон промта роли (путь берётся из `_index.md`)
- `references/roles/meta/` — meta-роли иерархии; выбор через `_index.md`
- `references/roles/meta/front-general.md` — генерал фронта: умная модель, узкий фронт, критики плана, выжимка вверх
- `references/roles/meta/front-colonel.md` — полковник подзадачи: cursor, свита, мини-compass, выжимка генералу
- `references/roles/meta/raw-brief-synthesizer.md` — читатель сырья волны → выжимка ≤15 строк командиру
- `references/roles/meta/front-observer.md` — наблюдатель: 4 прицела, власть ноль
- `references/roles/meta/front-prosecutor.md` — прокурор: внутриволновой надзор, власть ноль
- `references/roles/code/coder.md` — базовый исполнитель кода
- `references/roles/code/code-reviewer.md` — критик кода
- `references/roles/research/fact-checker.md` — критик-скептик
- `references/roles/research/synthesizer.md` — синтез (reconciliation критиков)
- `references/roles/research/report-synthesizer.md` — финальная сшивка отчёта / читатель
- `references/roles/code/simplicity-warden.md` — ворота простоты
- `references/roles/meta/web-scout.md` — разведчик: облачный поиск для советника

## D. Быстрые ответы

| Вопрос | Ответ |
|---|---|
| Где взять инструмент? | секция A |
| Куда пишется задача? | `sessions/<sid>/compass.md` (`menu.py --session`) |
| Куда упал результат? | `sessions/<sid>/runs/<id>/` + лог (`run.log` / `cloud-<id>.log`) |
| Панель? | `./panel.sh` → `panel/server.py` на `127.0.0.1:8765+` |
| Большой проект с нуля? | доктрина иерархии (`SKILL.md`) + `<state>/fronts.json` |
| генералу нужны варианты? | meta/opportunity-advisor (local) + meta/web-scout.md (cloud) |
| Compass фронта? | `<state>/fronts/<id>/compass.md` (≤ 4000 символов) |
| Мини-compass полковника? | `<state>/fronts/<id>/colonels/<cid>/compass.md` (лимит как у фронтового) |
| Правки кода: гит до/после волны? | чекпоинт git до волны, ревизия после — `code/git-warden` |
| Доки протухли/обновить после волны? | `code/docs-keeper` |
| PROJECT.md? | главный документ, ≤1 стр, корень проекта; разделы Цель/Архитектура (до 5 строк)/Карта/Ключевые решения (решение+дата, 5–7)/Ссылки; перезаписью (не дописыванием); создаёт командующий при старте иерархии (новый и существующий при первом появлении нормы); ведёт `docs-keeper`: значимая волна → сверка разделов, ≠ → PROBLEMS |
| дифф переусложнён? | `code/simplicity-warden` |
| Что-то не найти? | НЕ искать по ФС вслепую: сначала эта карта, потом файл из неё |
