# PROJECT.md — orchestrator-with-cursor

## Цель
Кит оркестрации агентов (Claude Code / Codex / Kimi + Cursor local|cloud): задача → свежий исполнитель → критики → приёмка замером; курс и правила доставляет код, не промт-дисциплина.

## Архитектура
- Dual-path: роль `code/` → `bin/run-exec.py` (local cursor-agent, ФС); иначе → `bin/run-cloud.py` (Cloud); иерархия front → general → colonel → executor.
- State `.orchestration/` (params, sessions/compass, journal, fronts); хуки `bin/reground.py` вклеивают params/compass/гарды на событиях движка.
- База правил F-RULES (гибрид Б, Э1): `rules/{cards,manifest,archive}` + API в `orchlib` (`match_cards` / `record_hit` / archive_lru / health-чипы).
- Доставка R2: шаг — `format_step_inject_lines` через reground (prompt-submit / post-tool); запуск — `format_precedent_lines` в копию промта обёрток; адрес `role_to_komu(role)`; безадресный → 0 строк; hit → journal `card_injected`.
- Панель `/api/health`: дисциплинарные чипы + `rules_no_retro` / `rules_dead` / `manifest_category_oversize`.

## Карта
- `bin/orchlib.py` — ядро state/fronts/journal + rules API (R1) и delivery helpers (R2)
- `bin/reground.py` — хуки Kimi; вклейка 1–3 строк `[rules/<id>] …` на адресованных шагах
- `bin/run-exec.py`, `bin/run-cloud.py` — обёртки; ≤3 строк «прецедент:» / «осторожно:» в `prompt.run` до двигателя
- `rules/` — DON'T/DO/CASE (≥15 seed), `manifest.json`, `archive/` (потолок 25 активных)
- `skills/orchestration/` — доктрина (SKILL, MAP, roles); MAP sync rules-API после R1 откатили (`693bf24`)
- `panel/` — UI и подписи health-чипов; `routing/jev-table.json` — Jev Э2 (ещё не этап доставки)

## Ключевые решения
- 2026-09-27: F-RULES гибрид Б — код сужает по кому×когда×категория; Jev Choice только Э2 из шорт-листа 2–10 (≤3 → без Jev).
- 2026-09-27 R1: store+API+seed+чипы (`1c11af1`, `4bd76cf`, `cde3c33`); docs MAP откатили (`693bf24`) — не возвращать MAP без явного scope.
- 2026-09-27 R2: helpers поверх match/record_hit; reground step-inject; precedents в обёртках (`edde13d`, `662cb7f`, `d256771`).
- Знания в шаг — только кодом, 1–3 карточки, карточка ≤2 строки на часть; ретро/чип без карточки — контракт Э1.
- Пуш с волн F-RULES запрещён приказом фронта.

## Ссылки
- `README.md` / `README.ru.md` — установка и обзор
- `skills/orchestration/SKILL.md`, `references/MAP.md` — регламент и карта инструментов
- `rules/manifest.json` — индекс карточек; `.orchestration/fronts/F-RULES/order.md` — контракт фронта
- Коммиты волны R2: `edde13d`, `662cb7f`, `d256771`
