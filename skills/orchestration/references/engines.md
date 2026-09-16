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

## Внешние исполнители (приоритет режима auto: дёшевы, отдельная квота Cursor)

- **Локальный cursor-agent** (дёшево, на машине оркестратора):
  `cursor-agent -p "$(cat промт-файл)" --force --model auto --output-format
  stream-json` под системным `timeout` = `timeout_min` из params. Промт —
  только из файла; модель всегда auto; приёмка по логу/файлу, не по exit code.
  Запуск идёт через Bash-инструмент сессии: применяются её разрешения,
  sandbox и таймауты инструмента — укладывайся в них (или проси повышения).
- **Облако Cursor** (без установки, отдельная квота): Cloud Agents API
  (`https://api.cursor.com`), ключ в **cursor.com/dashboard → API Keys**.
  **Локальный бинарник НЕ нужен** — чистый REST (curl/python-stdlib);
  нужны платный план, ключ и привязка GitHub. Создание `POST /v1/agents`,
  follow-up `POST /v1/agents/{id}/runs`, статус/результат `GET
  /v1/agents/{id}/runs/{runId}`. Результат — текст + git-ветка/PR.
  Детект исчерпания квоты (для `on_cursor_fail`): `429` + «Rate limit…» —
  временный лимит, подождать; «Usage limit exceeded»/spend limit — квота
  исчерпана, действовать по правилу скилла.
- **Claude Managed Agents API** — облачные сессии без CLI; детали — по доке
  Anthropic на момент запуска.

## Модели

Имена моделей и уровни effort не хардкодить: снимать с движков на месте
(`claude --help`, `codex debug models`, `cursor-agent --list-models`) —
списки меняются.
