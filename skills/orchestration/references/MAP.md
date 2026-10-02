# Карта системы оркестрации (ориентир ~60 с)

Пути кита — от корня `orchestration-kit/`.
State — `.orchestration/` в проекте.
Резолв state: `$ORCHESTRATION_DIR` или первый `.orchestration` вверх от cwd.
Не ищи инструменты по ФС вслепую — сначала эта карта, потом конкретный файл.

## A. Файлы кита (что чем запускать)

- Маршрут: роль из `code/` → код → `run-exec.py`; иначе не-код → `run-cloud.py` (явный executor в params перекрывает)
- `bin/run-exec.py` — локальный CLI для кода: cursor-agent, промт из файла, лог/EXIT/retry (прямая ФС проекта); обязателен `--front <fid>` или `--no-front "<причина>"` (hierarchy≠off); exit 8 = `FRONT_REQUIRED`; exit 9 = `FRONT_LOCK_BUSY` (см. секцию C); dual-timer: `--stall-after` (stall по росту run.log) / `--max-wall` (fuse); `--timeout` — алиас stall («stall/no-output, не wall-clock»); EXIT 124 = STALL (retry), EXIT 125 = WALL (не retry); при overflow compass пишет маркер `COMPASS_OVERFLOW` в лог прогона; env `ORCH_RUN_ID` — id текущего прогона, наследуется parent→child в local-обёртке (летописец parent; parent≠свой id); env `ORCH_FRONT` — в дочерние при `--front`; A2: вклейка «## Владение (A2)» в промт; kill-протокол: `--kill` ТОЧНЫМ run-id (не подстрокой), tombstone отменяет авторетрай; дубль `--id` при живом → exit 11; второй пишущий ран фронта → exit 13 + чип `multi_write_front`; `--no-verify` в логе коммита → чип `commit_no_verify`
- `bin/run-cloud.py` — Cursor Cloud для не-кода: create→poll→artifacts (`run` / `status` / `artifacts` / `list`); те же `--front`/`--no-front`; exit 8 = `FRONT_REQUIRED`; exit 9 = `FRONT_LOCK_BUSY` (см. секцию C); `--http-timeout` на HTTP-вызовы; `--wait` dual-timer: `--stall-after` / `--max-wall` (EXIT 124/125 как у run-exec); `--timeout` — алиас stall; при overflow compass пишет маркер `COMPASS_OVERFLOW` в лог прогона; cloud-ран пишущий ТОЛЬКО с `--writable` (иначе аналитик); kill-протокол / dual-writer / A2 — как у run-exec
- `bin/owns.py` — owns-map: поле `owns` в `fronts.json` = канон владений; гейт активации (`--check-activation`): пересечение owns с active-фронтом = отказ; пустые owns = предупреждение
- `bin/commit-wave.py` — commit_wave: lock → unexpected staged вне owns → `git add -- <owned>` → `git commit` pathspec → unlock; сырой `git commit -a` / `git add -A` агентам запрещён; сумм `SHA256SUMS` не трогает
- Таймеры (три класса имён): `http_timeout` — один HTTP-запрос (jev/panel/`--http-timeout`); `stall_after` — нет роста сигнала прогресса (лог/события) → вердикт зависания; `max_wall` — fuse wall-clock, отдельный ярлык. params: `execution.stall_s` (optional) и `execution.max_wall_s` (дефолт 86400); compat: `timeout_s` читается как stall. Канон индустрии no-output: CircleCI `no_output_timeout` / Travis `log-timeout` / Jenkins `activity:true` — НЕ GitHub Actions (там wall)
- Пример: `run-exec.py --id T1 --front KIT --role code/coder.md --prompt-file P.md`
- Пример вне фронта: `run-exec.py --id smoke --no-front "smoke" --prompt-file P.md`
- `bin/run-cloud.py` — оба порядка флагов: `--id`/`--api-key` до и после субкоманды
- `bin/run-cloud.py` — ключ: `--api-key` > `CURSOR_API_KEY` > `<state>/cursor.key`; лог `<state>/cloud-<id>.log`
- `bin/run-cloud.py` — `list`: активные агенты; `<state>/cloud-<id>.result.json` (машиночитаемый итог: agent/run/status/result); `<state>/agent-<id>.json` (id для follow-up без ре-парсинга лога)
- `panel/server.py` — `/api/health`: красные чипы из `orchlib.health_red_chips` (в т.ч. runs_no_front, orders_without_basis, fronts_no_prosecutor, waves_no_critic, code_waves_no_gitwarden, budget_warn, advisors_without_scouts, commander_no_children, wave_no_docs, rules_*, lint_failures, probes_missing, chip_silenced, general_resume_chain; WARN: general_resume_chain_warn); кэш по mtime journal+fronts + HEAD хэш кита; F-ACCEPT: probes_missing/chip_silenced не фильтровать
- Семантика чипов (`orchlib.health_red_chips`, mask 3b73688): `advisors_without_scouts` — покрытие советника: ветка `parent==advisor.id` (без изменений) ИЛИ cloud-scout с пустым parent в окне советника при `front=None` (`--no-front`) либо том же front; scout с другим непустым front — не покрытие. `general_resume_chain` — wire: агент с >1 distinct фронтом генерала; в красный чип только активные цепочки: (а) ≥1 фронт цепочки в fronts.json со status ∉ {done,cancelled,rejected}, ИЛИ (б) последний промт генерала моложе `GENERAL_RESUME_LIVE_WINDOW_S`=1800с; закрытые фронты + неживое окно → история, чип не краснеет
- `orders_suspect` — детектор лживых «без советников»; снятие = маркеры ушли ИЛИ advisor-записи по фронту в журнале ИЛИ пометка «допущение проверено замером:» в order.md
- `handoff_oversize` — handoff >2000 симв при активной иерархии
- `project_md_missing` — нет PROJECT.md при старте иерархии
- `mustmap_stale` — доктрина новее audit/mustmap/mustmap.json
- `bin/write-compass.py` — воронка проверенной записи compass (`--path` + `--text-file`/`--stdin`; exit: 0 записано; 1 ошибка чтения/записи; 2 превышение — файл не пишется, pending-флаг; 3 не compass-путь)
- `bin/menu.py` — меню params; задача → сессионный compass (`--task` + `--session <sid>`)
- `bin/verdict.py` — JSON-статус прогона из лога: `python3 bin/verdict.py <лог>`
- `bin/discover.py` — снимок моделей/ключа → `.orchestration/discovered.json`
- `bin/reground.py` — хук-движок Kimi: UserPromptSubmit, SessionHeartbeat, PreToolUse, PostToolUse, SubagentStart/Stop (+ session-start)
- События хуков Kimi: **UserPromptSubmit** (вклейка params/compass/гарда; блок при overflow; нуджи детекторов: приказ без обоснования / волна без прокурора / probes_missing); **SessionHeartbeat** (сверка; overflow → pending-флаг, observation-only); **PreToolUse** (мгновенный deny Write/Edit в compass — только через write-compass.py; + гейт order.md (без строки „подход:“/„без советников“ — deny)); **PostToolUse** (нуджи каждые N + гард при записи в compass); **SubagentStart/Stop** → видимость генералов-субагентов движка в `journal.jsonl` (не только обёртки run-exec/run-cloud)
- `bin/orchlib.py` — общая библиотека (find_state_dir, params, сессии, load_fronts / front_compass_path); детекторы `orders_without_basis` + `waves_without_prosecutor` + `probes_missing` / `chip_silenced` (F-ACCEPT канон v1; снятие probes_missing только валидной квитанцией §3); не лаунчер
- `routing/jev-table.json` — Jev-точки: `rules-apply` (Choice), `tried-before` (Noul), `probe-sufficiency` (Score, только advisory — не разрешает/не запрещает закрытие волны)
- `panel/server.py` — HTTP-панель (порт база 8765, при занятости +1…); API `/api/fronts`; ключи: `/api/openrouter-key` и `/api/cursor-key` (GET/POST/DELETE; в ответе только `mask` вида `…abcd`, не тело); живые пробы `POST /api/openrouter-key/probe` (`{"full":true}` — полная) и `POST /api/cursor-key/probe`; `error_class` OpenRouter: `401`/`402` (+`403`/`429`); Cursor: `403`/`429` (+`401`); сторож-тред опрашивает compass каждые `compass.guard_poll_s` сек (флаг/подсветка)
- `panel/index.html` — UI: секция «Ключи» (маска + Проверить) и «Фронты» (волны, статусы, JSON-редактор); контракт: `tests/test_panel_keys.sh`
- `./panel.sh` (корень проекта после install) — запуск панели → `http://127.0.0.1:8765+`
- `install-local.sh` / `install-local.ps1` — установка скилла, хуков, `/orch-menu`, panel; по умолчанию `TARGET=$HOME` (установка из папки клона из коробки); тест-режим только при явном `ORCH_TEST_INSTALL=1` (конфиги движков не трогает)
- `uninstall.sh` / `uninstall.ps1` — снятие установки
- `SHA256SUMS` — целостность дистрибутива кита; пересбор — ТОЛЬКО командующий; commit_wave сумм не делает
- `commands/claude-orch-menu.md` — источник `/orch-menu` для Claude (install → `orch-menu.md`)
- `commands/codex-orch-menu.md` — источник меню для Codex (install → `~/.codex/prompts/orch-menu.md`)
- `hooks/` — сниппеты хуков движков (подключает install-local)
- `skills/orchestration/SKILL.md` — регламент оркестратора
- `compass.md` (корень кита) — исходник общего шаблона compass

## B. State-каталог `.orchestration` (что где лежит)

- **Изоляция state:** 1 проект = 1 папка верхнего уровня со своим `.orchestration`; работа внутри чужого дерева подхватывает чужой state (фронты/лимиты/журнал); session-entry показывает принадлежность и чужой state; хук предупреждает (нет проекта / state родителя)
- `params.json` — параметры пачки (`execution.*`, `review.*`, `reground.every_min`, …)
- `compass.md` — ОБЩИЙ шаблон; правится только в панели «Расширенные»
- `sessions/<sid>/compass.md` — личная копия сессии; авто-сеется; пишет `menu.py --session`
- лимиты compass: сессионный ≤ **8500** символов; `fronts/**` (фронт / мини-compass полковника) ≤ **4000**
- `sessions/<sid>/pending_compass_guard.json` — флаг гарда compass сессии (доставка — ближайший UserPromptSubmit)
- `<state>/pending_compass_guard.json` — общий флаг гарда compass (доставка — ближайший UserPromptSubmit)
- `sessions/<sid>/runs/<id>/prompt.md` — промт прогона
- `sessions/<sid>/runs/<id>/run.log` — лог прогона (локальный/обёртка)
- `sessions/<sid>/runs/<id>/artifact.md` — артефакт волны (наличие файла ≠ зелёная приёмка)
- `sessions/<sid>/runs/<id>/probe-receipt.md` — квитанция приёмки §3 (tool-only: `probe-receipt.py` / `run-exec --probe`; generator+cmd_sha256); снимает чип `probes_missing`
- `sessions/<sid>/prosecutor/` — закрытый канал прокурора волны (читает только командующий)
- `<state>/cloud-<id>.log` — лог `run-cloud.py` (create/status/artifacts)
- `<state>/cloud-<id>.result.json` — машиночитаемый итог run-cloud (agent/run/status/result)
- `<state>/agent-<id>.json` — id агента для follow-up без ре-парсинга лога
- `<state>/journal.jsonl` — летописец вызовов (start/end от `run-exec` / `run-cloud`; плюс SubagentStart/Stop — генералы-субагенты движка, не только обёртки)
- `cursor.key` — API-ключ Cursor (gitignore; панель `/api/cursor-key`; в UI/API только маска)
- `openrouter.key` — API-ключ OpenRouter (gitignore; панель `/api/openrouter-key`; в UI/API только маска)
- `counters/` — счётчики хуков (nudge / heartbeat / prompt-submit, …)
- `discovered.json` — снимок `bin/discover.py`
- `<state>/fronts.json` — граф фронтов больших проектов (цель, фронты с ролями/deps/статусами/`owns`; топосорт-волны; циклы отвергаются; `orchlib.load_fronts`); owns = канон владений; гейт активации — пересечение с active = отказ, пустые owns = предупреждение
- статусы фронта: `proposed` / `active` / `stalled` / `cancelled` / `rejected` / `done` (legacy-алиасы при чтении: planned→proposed, running→active, blocked→stalled, failed→rejected)
- `<state>/fronts/<id>/order.md` — приказ командующего фронту (цель/границы/критерий/CANON); read-only для генерала
- `<state>/fronts/<id>/compass.md` — курс фронта только (состояние/TODO/следующий шаг + ссылка на order.md; без текста приказа) (`orchlib.front_compass_path`)
- `<state>/fronts/<id>/colonels/<cid>/order.md` — приказ генерала полковнику; read-only для полковника
- `<state>/fronts/<id>/colonels/<cid>/compass.md` — мини-курс полковника только (ссылка на order.md; без текста приказа; лимит как у фронтового)
- Правило: запускай всё из одной папки проекта (или задай `ORCHESTRATION_DIR`)

## C. Параллельность и изоляция state

- `journal.jsonl` — все записи только через `orchlib.journal_append` (`flock` `LOCK_EX`, flush+fsync ДО снятия замка); start/end несут `session`
- sid-изоляция: артефакты строго `sessions/<safe_name(sid)>/runs/<id>/`; `safe_name` — инъективное percent-кодирование (`%`→`%%`, небезопасные → `%XX`): `session_<uuid>` не меняется; `a/b`→`a%2Fb` ≠ `a_b`
- общие `fronts.json`/`params.json` — RMW только под каталог-замком (3-way merge); первичный seed params — single-winner (`O_EXCL` `seed.lock`)
- front-runs: `counters/front-runs-<fid>.json` инкремент под mkdir-замком; stale-steal по возрасту с heartbeat/reclaim-gate (один владелец); `FRONT_LOCK_BUSY` → обёртка exit 9, retry — обязанность вызывающего (`RETRYABLE={4,124}`; EXIT 125 WALL — не retryable)
- ключи `cursor.key`/`openrouter.key`: прод-писатель `panel/server.py` не атомарен → ESCALATED (решение командующего отдельно); норма «целое-или-старое» НЕ гарантирована — partial-read возможен (`tests/test_isolation.py::test_b_escalated_key_partial_read`)
- точка истины: `tests/test_isolation.py` (29 рисков аудита; RED закрыты; ESCALATED×2 known)

## D. Скилл и роли (как выбирать)

- `skills/orchestration/SKILL.md` — регламент: исполнители, контроль, компас, вердикт
- `references/planning.md` — план, волны, DAG, preflight-бюджет; § «Приёмка = функция» (канон v1: проба/квитанция/`probes_missing`/`chip_silenced`; Jev `probe-sufficiency` advisory)
- `references/FLOW.md` — канонический граф потоков: кто кого запускает / данные / гейты / жизненный цикл
- `references/engines.md` — исполнители/ключи/движки (local-cursor / cursor-cloud, Claude, Codex, Kimi)
- `references/traps.md` — ловушки (читать перед пачкой)
- `references/MAP.md` — эта карта (инструменты и state)
- `references/roles/_index.md` — КАТАЛОГ РОЛЕЙ; выбор ТОЛЬКО через него
- Каскад `_index.md`: домен → поддомен → роль (колонка «Когда»); не `ls` дерева
- `references/roles/_template.md` — каркас: `{{ЗАДАЧА}}` / `{{КРИТЕРИЙ}}` / `{{ГРАНИЦЫ}}` + процесс/анти-паттерны
- `references/roles/<домен>/<роль>.md` — шаблон промта роли (путь берётся из `_index.md`)
- `references/roles/meta/` — meta-роли иерархии; выбор через `_index.md`
- Легенда моделей: **умный движок** — командующий, генералы фронтов; **cursor local** — наблюдатель/прокурор (запуск командующим через `run-exec`), полковники (код), свита, исполнители кода; **cursor cloud** — не-код + web-scout; ребро: командующий → наблюдатель/прокурор (`run-exec`)
- `references/roles/meta/front-general.md` — генерал фронта: умная модель, узкий фронт, критики плана, выжимка вверх
- `references/roles/meta/front-colonel.md` — полковник подзадачи: cursor, свита, мини-compass, выжимка генералу
- `references/roles/meta/raw-brief-synthesizer.md` — читатель сырья волны → выжимка ≤15 строк командиру
- `references/roles/meta/front-observer.md` — наблюдатель: cursor local (`run-exec`, запуск командующим на КТ); 4 прицела, власть ноль
- `references/roles/meta/front-prosecutor.md` — прокурор: cursor local (`run-exec`, запуск командующим на волну); внутриволновой надзор, власть ноль; закрытый канал; by-design (не ПРОБЛЕМА): end без `front` (фронт на start; `case-end-records-front-on-start`); старт после `max_rounds=3` при задокументированной эскалации (`case-e2e-audit-retro-2`)
- `references/roles/meta/invocation-inspector.md` — инспектор вызовов: аудит дерева по journal.jsonl
- `references/roles/code/coder.md` — базовый исполнитель кода
- `references/roles/code/code-reviewer.md` — критик кода
- `references/roles/research/fact-checker.md` — критик-скептик
- `references/roles/research/synthesizer.md` — синтез (reconciliation критиков)
- `references/roles/research/report-synthesizer.md` — финальная сшивка отчёта / читатель
- `references/roles/code/simplicity-warden.md` — ворота простоты
- `references/roles/meta/web-scout.md` — разведчик: облачный поиск для советника

## E. Быстрые ответы

| Вопрос | Ответ |
|---|---|
| Где взять инструмент? | секция A |
| Параллельность / изоляция state? | секция C |
| Куда пишется задача? | `sessions/<sid>/compass.md` (`menu.py --session`) |
| Куда упал результат? | `sessions/<sid>/runs/<id>/` + лог (`run.log` / `cloud-<id>.log`); приёмка — ещё `probe-receipt.md` (§3) |
| Приёмка волны / написать квитанцию? | **одна команда:** дефолт `run-exec --probe 'CMD' --oracle N`; ручные/отложенные — `bin/probe-receipt.py` (тот же writer); канон §3 + `probes_missing`; Jev `probe-sufficiency` только advisory |
| Панель? | `./panel.sh` → `panel/server.py` на `127.0.0.1:8765+`; ключи `/api/openrouter-key` + `/api/cursor-key` (+ `/probe`); маска `…abcd`; тест `tests/test_panel_keys.sh` |
| Большой проект с нуля? | доктрина иерархии (`SKILL.md`) + `<state>/fronts.json` |
| командующему/генералу/полковнику нужны варианты? | meta/opportunity-advisor (local) + meta/web-scout.md (cloud) |
| Приказ фронту? | `<state>/fronts/<id>/order.md` (read-only для генерала) |
| Compass фронта? | `<state>/fronts/<id>/compass.md` (≤ 4000; только курс + ссылка на order.md) |
| Приказ полковнику? | `<state>/fronts/<id>/colonels/<cid>/order.md` (read-only для полковника) |
| Мини-compass полковника? | `<state>/fronts/<id>/colonels/<cid>/compass.md` (только курс + ссылка на order.md; лимит как у фронтового) |
| Правки кода: гит до/после волны? | чекпоинт git до волны, ревизия после — `code/git-warden` |
| Доки протухли/обновить после волны? | `code/docs-keeper` |
| PROJECT.md? | главный документ, ≤1 стр, корень проекта; разделы Цель/Архитектура (до 5 строк)/Карта/Ключевые решения (решение+дата, 5–7)/Ссылки; перезаписью (не дописыванием); создаёт командующий при старте иерархии (новый и существующий при первом появлении нормы); ведёт `docs-keeper`: значимая волна → сверка разделов, ≠ → PROBLEMS |
| дифф переусложнён? | `code/simplicity-warden` |
| Что-то не найти? | НЕ искать по ФС вслепую: сначала эта карта, потом файл из неё |
