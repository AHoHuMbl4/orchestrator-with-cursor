# PROJECT.md — orchestrator-with-cursor

## Цель
Кит оркестрации агентов (Claude Code / Codex / Kimi + Cursor local|cloud): задача → свежий исполнитель → критики → приёмка замером; курс и правила доставляет код, не промт-дисциплина.

## Архитектура
- Dual-path: роль `code/` → `bin/run-exec.py` (local cursor-agent, ФС); иначе → `bin/run-cloud.py` (Cloud); иерархия front → general → colonel → executor.
- State `.orchestration/` (params, sessions/compass, journal, fronts); хуки `bin/reground.py` вклеивают params/compass/гарды на событиях движка.
- База правил F-RULES (гибрид Б): `rules/{cards,manifest,archive}` + orchlib API R1 (`match_cards` / `record_hit` / archive_lru) и health-чипы.
- Доставка R2: шаг — `format_step_inject_lines` (reground); запуск — `format_precedent_lines` в копию промта обёрток; адрес `role_to_komu`; hit → journal `card_injected`.
- Ретро R3: `reground retro --run-id` → `format_retro_lines`; рождение карточек — `add_rule_card` (run_ref гасит `rules_no_retro`); ошибка → DON'T, образцовая приёмка → подсказка DO/CASE.

## Карта
- `bin/orchlib.py` — state/fronts/journal + rules API (R1), delivery (R2), `add_rule_card` / `format_retro_lines` (R3)
- `bin/reground.py` — хуки Kimi; step-inject R2; подкоманда `retro --run-id` (R3)
- `bin/run-exec.py`, `bin/run-cloud.py` — обёртки; ≤3 строк «прецедент:» / «осторожно:» в `prompt.run`
- `rules/` — DON'T/DO/CASE, `manifest.json`, `archive/` (потолок 25 активных)
- `skills/orchestration/` — доктрина; врезка «Ретро-шаг волны» в SKILL + planning (R3); MAP sync после R1 откатили (`693bf24`)
- `panel/` — UI/health-чипы (`rules_no_retro` / `rules_dead` / `manifest_category_oversize`); `routing/jev-table.json` — Jev Э2

## Ключевые решения
- 2026-09-27: F-RULES гибрид Б — код сужает по кому×когда×категория; Jev Choice только Э2 из шорт-листа 2–10 (≤3 → без Jev).
- 2026-09-27 R1: store+API+seed+чипы (`1c11af1`, `4bd76cf`, `cde3c33`); docs MAP откатили (`693bf24`) — не возвращать MAP без явного scope.
- 2026-09-27 R2: helpers поверх match/record_hit; reground step-inject; precedents в обёртках (`edde13d`, `662cb7f`, `d256771`).
- 2026-09-27 R3: ретро-детектор + `add_rule_card`; доктрина «Ретро-шаг волны» (`dc82236`, `5327d5a`, `9e5a785`).
- Знания в шаг — только кодом, 1–3 карточки ≤2 строки/часть; ретро/чип без карточки — контракт Э1.
- Пуш с волн F-RULES запрещён приказом фронта.

## Ссылки
- `README.md` / `README.ru.md` — установка и обзор
- `skills/orchestration/SKILL.md`, `references/MAP.md`, `references/planning.md` — регламент; ретро-шаг в SKILL+planning
- `rules/manifest.json` — индекс карточек; `.orchestration/fronts/F-RULES/order.md` — контракт фронта
- Коммиты R2: `edde13d`, `662cb7f`, `d256771`; R3: `dc82236`, `5327d5a`, `9e5a785`
