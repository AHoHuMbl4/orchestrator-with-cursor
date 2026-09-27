# PROJECT.md — orchestrator-with-cursor

## Цель
Кит оркестрации агентов (Claude Code / Codex / Kimi + Cursor local|cloud): задача → свежий исполнитель → критики → приёмка функцией на полигоне (не наличием артефакта); курс и правила доставляет код, не промт-дисциплина.

## Архитектура
- Dual-path: роль `code/` → `bin/run-exec.py` (local cursor-agent, ФС); иначе → `bin/run-cloud.py` (Cloud); иерархия front → general → colonel → executor.
- State `.orchestration/` (params, sessions/compass, journal, fronts, counters); хуки `bin/reground.py` вклеивают params/compass/гарды/нуджи детекторов на событиях движка.
- F-RULES (гибрид Б): `rules/{cards,manifest,archive}` + orchlib R1–R5 (match/inject/retro/Jev-лестница; hit SoT — `counters/rules-hits.json`); health-чипы rules_*.
- F-ACCEPT v1 («Приёмка = функция»): зелёный ⇔ приёмщик прогнал пробу на полигоне + валидная квитанция §3; чипы `probes_missing`/`chip_silenced` + нудж reground; Jev `probe-sufficiency` только advisory.
- Jev-точки: `rules-apply` (Choice), `tried-before` (Noul), `probe-sufficiency` (Score advisory); F-C2 — серия commander `--no-front` → блок reground.

## Карта
- `bin/orchlib.py` — state/fronts/journal + rules API R1–R5 + `commander_no_front_series` (F-C2) + `probes_missing`/`chip_silenced` (F-ACCEPT)
- `bin/reground.py` — хуки Kimi; step-inject R4; `retro --run-id` (R3); блок no-front; нудж `probes_missing`
- `bin/run-exec.py`, `bin/run-cloud.py` — обёртки; tried-before + прецеденты ≤3 строк в `prompt.run`
- `bin/jev-advise.py` + `routing/jev-table.json` — Э2: `rules-apply`, `tried-before`, `probe-sufficiency` (advisory Score)
- `install-local.sh` — главный установщик; audit `install-audit.log` (home-canonical); hint /tmp-клона в stdout (тест/гард)
- `rules/` — DON'T/DO/CASE (в т.ч. категория `приёмка`), `manifest.json`, `archive/` (потолок 25; категория ≤20)
- `skills/orchestration/` — доктрина; «Ретро-шаг волны»; канон «Приёмка = функция» в planning (+ врезка SKILL)
- `panel/` — UI/health-чипы (`rules_*`, `probes_missing`, `chip_silenced`, …)

## Ключевые решения
- 2026-09-27: F-RULES гибрид Б — код сужает по кому×когда×категория; Jev базу не видит, только шорт-лист.
- 2026-09-27 R1–R5: store/API/seed/чипы + inject/precedents + ретро + Jev-лестница/tried-before + hit SoT counters (`1c11af1`…`e9aac51`).
- 2026-09-27 F-C2: серия ≥3 unmasked commander `--no-front` при живой иерархии → блок reground; фикс=фронт (`cdc53da`).
- 2026-09-27 F-ACCEPT v1: канон «Приёмка = функция» — гейт `probes_missing`/`chip_silenced`, квитанция `probe-receipt.md` §3, Jev `probe-sufficiency` advisory, карточки `приёмка/*` (`b522c78`).
- Знания в шаг — только кодом, 1–3 карточки ≤2 строки/часть; ретро/чип без карточки — контракт Э1. Пуш с волн F-RULES/F-DEBTS/F-ACCEPT запрещён приказом фронта.

## Ссылки
- `README.md` / `README.ru.md` — установка и обзор
- `install-notes.md` — установка/перенос; audit и контур тестов на /tmp-клоне (DB6)
- `skills/orchestration/SKILL.md`, `skills/orchestration/references/MAP.md`, `skills/orchestration/references/planning.md` — регламент; ретро-шаг; § «Приёмка = функция» (канон v1)
- `rules/manifest.json` — индекс карточек; hit — state `counters/rules-hits.json`; state: `fronts/F-ACCEPT/order.md` — контракт фронта (вне репо)
- Коммиты: F-ACCEPT `b522c78`; R2–R5 `edde13d`…`e9aac51`; F-C2 `cdc53da`
