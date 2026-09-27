# PROJECT.md — orchestrator-with-cursor

## Цель
Кит оркестрации агентов (Claude Code / Codex / Kimi + Cursor local|cloud): задача → свежий исполнитель → критики → приёмка замером; курс и правила доставляет код, не промт-дисциплина.

## Архитектура
- Dual-path: роль `code/` → `bin/run-exec.py` (local cursor-agent, ФС); иначе → `bin/run-cloud.py` (Cloud); иерархия front → general → colonel → executor.
- State `.orchestration/` (params, sessions/compass, journal, fronts, counters); хуки `bin/reground.py` вклеивают params/compass/гарды на событиях движка.
- База правил F-RULES (гибрид Б): `rules/{cards,manifest,archive}` + orchlib API R1 (`match_cards` / `record_hit` / archive_lru) и health-чипы; hit SoT — `counters/rules-hits.json` (стейт), не manifest репо.
- Доставка R2: шаг — `format_step_inject_lines` (reground); запуск — `format_precedent_lines` в копию промта обёрток; адрес `role_to_komu`; hit → counters + journal `card_injected` (`record_hit` не грязнит git).
- Ретро R3: `reground retro --run-id` → `format_retro_lines`; рождение карточек — `add_rule_card` (run_ref гасит `rules_no_retro`); ошибка → DON'T, образцовая приёмка → подсказка DO/CASE.
- Jev Э2 / R4: `select_rule_card_ids` — лестница после адреса (n=0→[]; ≤3→все без Jev; 4–10→Choice `rules-apply`; >10→топ-10→Choice); fail-open/defer→без вставки. Launch — Noul `tried-before` (`format_tried_before_lines`). `add_rule_card` гейтит 21-ю карточку в категории; чип `manifest_category_oversize`.

## Карта
- `bin/orchlib.py` — state/fronts/journal + rules API R1–R5 (`select_rule_card_ids`, hit в `counters/rules-hits.json`, oversize-гейт add) + `commander_no_front_series` (F-C2)
- `bin/reground.py` — хуки Kimi; step-inject через лестницу R4; `retro --run-id` (R3); блок серии no-front в prompt-submit head
- `bin/run-exec.py`, `bin/run-cloud.py` — обёртки; tried-before + прецеденты ≤3 строк в `prompt.run`
- `bin/jev-advise.py` + `routing/jev-table.json` — точки Э2 `rules-apply` (Choice) и `tried-before` (Noul)
- `install-local.sh` — главный установщик; audit `install-audit.log` (home-canonical); hint /tmp-клона в stdout (тест/гард)
- `rules/` — DON'T/DO/CASE, `manifest.json` (индекс, без runtime hit), `archive/` (потолок 25 активных; категория ≤20)
- `skills/orchestration/` — доктрина; врезка «Ретро-шаг волны» в SKILL + planning (R3); MAP sync после R1 откатили (`693bf24`)
- `panel/` — UI/health-чипы (`rules_no_retro` / `rules_dead` / `manifest_category_oversize`)

## Ключевые решения
- 2026-09-27: F-RULES гибрид Б — код сужает по кому×когда×категория; Jev базу не видит, только шорт-лист.
- 2026-09-27 R1–R2: store/API/seed/чипы + step-inject/precedents (`1c11af1`…`d256771`); MAP sync после R1 откатили (`693bf24`).
- 2026-09-27 R3: ретро-детектор + `add_rule_card`; доктрина «Ретро-шаг волны» (`dc82236`, `5327d5a`, `9e5a785`).
- 2026-09-27 R4/Э2: Jev-точки + трёхступенчатый выбор + tried-before + fail-open + oversize-гейт add (`d494106`, `5761416`, `d68351b`).
- 2026-09-27 R5: hit SoT = `.orchestration/counters/rules-hits.json`; `record_hit` не пишет manifest/репо (`e9aac51`).
- 2026-09-27 F-C2: серия ≥3 unmasked commander `--no-front` при живой иерархии → блок reground; фикс=фронт (`cdc53da`); карточка `dont-komandirskaya-volna-bez-fronta-seriya-ru`.
- Знания в шаг — только кодом, 1–3 карточки ≤2 строки/часть; ретро/чип без карточки — контракт Э1. Пуш с волн F-RULES/F-DEBTS запрещён приказом фронта.

## Ссылки
- `README.md` / `README.ru.md` — установка и обзор
- `install-notes.md` — установка/перенос; audit и контур тестов на /tmp-клоне (DB6)
- `skills/orchestration/SKILL.md`, `skills/orchestration/references/MAP.md`, `skills/orchestration/references/planning.md` — регламент; ретро-шаг в SKILL+planning
- `rules/manifest.json` — индекс карточек; hit — state `counters/rules-hits.json`; state: `fronts/F-RULES/order.md` — контракт фронта (вне репо)
- Коммиты R2–R5: `edde13d`…`e9aac51`; DB6: `11fe4c4`, `f3d41ca`, `f698083`
