# Специфика движков

## Claude Code

- Субагенты: инструмент Agent (в ранних версиях Task); headless-запуск оркестратора с субагентами —
  `claude --agents '{"имя":{"description":...,"prompt":...,"tools":[...],"model":"..."}}'`
  (или файлы `.claude/agents/*.md`).
- Вызов скилла: `/orchestration`.
- Headless `claude -p`: молчаливые отказы прав не меняют exit code — ловушка №9
  в `traps.md`.
- Модели/effort: алиасы default/best/sonnet/opus/haiku/fable;
  `--effort low|medium|high|xhigh|max`; бюджет прогона `--max-budget-usd`.

## Codex CLI

- Субагенты включены по умолчанию (built-in `default`/`worker`/`explorer`);
  кастомные — TOML в `~/.codex/agents/` или `.codex/agents/` (name,
  description, developer_instructions, model, model_reasoning_effort,
  sandbox_mode). Рычаг параллельности (аналог parallel_per_task) —
  `max_concurrent_threads_per_session` в `[agents]` (`~/.codex/config.toml`),
  там же дешёвая модель обёртки: `default_subagent_model`.
- Headless: `codex exec --sandbox workspace-write -a never`.
- Модели динамически: `codex debug models`. Вызов скилла: `$orchestration`.

## Kimi Code

- Субагенты: одиночные — инструмент Agent; залпы параллельных — AgentSwarm.
  Вызов скилла: `/skill:orchestration`; команды при занятом агенте встают в
  очередь, Ctrl-S — вклинить немедленно.
- Хуки: UserPromptSubmit может дописывать текст в контекст; SessionHeartbeat
  — observation-only (таймер решает «пора сверкиться», доставляет следующий
  UserPromptSubmit); PostToolUse — только наблюдение.

## Внешние исполнители (режимы: auto | cursor-cloud | subagents)

Единственный курсор-путь — **удалённый** Cursor Cloud API. Локального CLI
нет: ни PATH-поиска, ни лаунчера бинарника.

- **cursor-cloud** (отдельная квота Cursor): Cloud Agents API
  (`https://api.cursor.com`), ключ в **cursor.com/dashboard → API Keys**,
  файл `.orchestration/cursor.key` или env `CURSOR_API_KEY`. Чистый REST
  (curl/python-stdlib); нужны платный план, ключ и привязка GitHub.
  Поток: create → polling → artifacts. Создание `POST /v1/agents`,
  follow-up `POST /v1/agents/{id}/runs`, статус/результат `GET
  /v1/agents/{id}/runs/{runId}`. Результат — текст + git-ветка/PR.
  Тело create/follow-up: `"prompt": {"text": "..."}` (объект, не строка) —
  см. https://cursor.com/docs/cloud-agent/api/endpoints. Клиент кита —
  **`run-cloud.py` (единственный лаунчер Cursor)**: общие флаги
  `--id`/`--api-key` допустимы до и после субкоманды
  (`run-cloud.py --id w1 run --prompt-file P.md` и
  `run-cloud.py run --id w1 --prompt-file P.md`). Промт — только из файла
  (`--prompt-file`); приёмка по артефактам/`run-cloud.py status|artifacts`
  и логу, не по «словам» исполнителя.   Детект исчерпания квоты (для
  `on_cursor_fail`): `429` + «Rate limit…» — временный лимит, подождать;
  «Usage limit exceeded»/spend limit — квота исчерпана, действовать по
  правилу скилла. `resource_exhausted` на create («Rate limit exceeded for
  creating cloud agent environments») — burst-лимит попыток: пауза ≥15–30 мин,
  одиночный повтор, при повторе — экспоненциальный рост паузы (ловушка №17
  в `traps.md`); 500 internal на create может маскировать тот же лимит.
  Ключ: `--api-key` > env `CURSOR_API_KEY` > `<state>/cursor.key`; сохранение
  через панель (абсолютный путь к ключу панель показывает после сохранения).
  Весь прогон — в
  `.orchestration/sessions/<sid>/runs/<id>/` (prompt.md, run.log, артефакты).
- **auto** (дефолт): есть ключ → cursor-cloud; нет ключа → СТОП и вопрос
  владельцу (никакого молчаливого fallback). Субагенты движка — только после
  явного «да» или `execution.executor = subagents`.
- **subagents** — субагенты движка оркестратора (расход его квоты); см. разделы
  Claude/Codex/Kimi выше.
- **Claude Managed Agents API** — облачные сессии без CLI; детали — по доке
  Anthropic на момент запуска.

## Модели

Имена моделей и уровни effort не хардкодить: снимать с движков на месте
(`claude --help`, `codex debug models`) — списки меняются. Для cursor-cloud
модель задаёт облако/API; локальных `--list-models` у Cursor в продукте нет.
