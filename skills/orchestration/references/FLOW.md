# FLOW — канонический граф потоков оркестрации

Счёт: **§1:** 19 узлов / 22 ребра · **§2:** 20 узлов / 12 рёбер · **гейты §3:** 15.

## 1. Кто кого запускает

```mermaid
graph TD
  Owner[Владелец] -->|цель / HITL / меню| Cmd[Командующий_движок]
  Cmd -->|иерархия: 1 фронт = 1 генерал| Gen[Генерал_движок]
  Cmd -->|окно зависло/снято; фронт→stalled| GenNew[НовыйГенерал_движок]
  Cmd -->|КТ между волнами графа| Obs[Наблюдатель_движок]
  Cmd -->|старт волны фронта| Pros[Прокурор]
  Cmd -->|развилка проекта/графа| AdvCmd[Советник_local]
  Cmd -->|КТ: сам| Insp[Инспектор]
  Obs -->|КТ: шаг 0b| Insp
  Gen -->|развилка «как делать»| Adv[Советник_local]
  Gen -->|план фронта → без OK не старт| PlanC[КритикиПлана]
  Gen -->|подзадача: order полковнику| Col[Полковник_cursor]
  Gen -->|сырьё волн → выжимка| Raw[Читатели_raw-brief]
  AdvCmd -->|2–3 слепых scout| Scout[Разведчики_cloud]
  Adv -->|2–3 слепых scout| Scout
  Col -->|план работ → без OK не старт| PlanC2[КритикиПлана]
  Col -->|работы каталога| Exec[Исполнители_cursor]
  Col -->|приёмка замером| AccC[КритикиПриёмки]
  Col -->|сырьё → выжимка ≤15| Raw
  Col -->|код-волна до/после| Git[git-warden]
  Col -->|значимая волна| Docs[docs-keeper]
  Col -->|значимая код-волна| Simp[simplicity-warden]
  Col -->|перед выдачей работы с выбором| Adv
```

Легенда моделей: **умный движок** — командующий (оркестратор сессии), генерал, наблюдатель (`SKILL.md:323–349`; `traps.md:№25`). **умная модель** (роль, не traps№25) — прокурор (`front-prosecutor.md:3`). **инспектор** — runtime в роли не задан (`invocation-inspector.md`). **cursor local** — советник; код-исполнители (`opportunity-advisor.md:1–6`; `SKILL.md:65–71`). **cursor cloud** — не-код + web-scout (`SKILL.md:80–86`; `web-scout.md:5–6`). **полковник dual** — код → local-cursor, прочее → cloud (`SKILL.md:385–386`; `front-colonel.md:3`).

Источники рёбер: владелец→командующий (`SKILL.md:16–27`); командующий→генерал/наблюдатель/прокурор/инспектор (`SKILL.md:323–344`; `planning.md:135–169`); командующий→новый генерал при зависании окна (`SKILL.md:434–441`; `planning.md:265–272`; `traps.md:№51`); командующий→советник (`SKILL.md:377–378`; `planning.md:176–179`); наблюдатель→инспектор (`front-observer.md:14`); генерал→советник/критики/полковники/читатели (`front-general.md:18–26`; `planning.md:186–197`); советник→разведчики (`opportunity-advisor.md:13`); полковник→свита (`front-colonel.md:14–17`; `planning.md:186–197`).

## 2. Куда идут данные

```mermaid
graph LR
  Order[order.md_приказ] -->|вниз read-only| GenCol[генерал/полковник]
  Levels[агент_уровня] -->|вызов write-compass.py| Funnel[воронка]
  Funnel -->|пишет файл| CompassFile[compass.md]
  Levels -->|прямой Write/Edit| PreTU[PreToolUse_deny]
  PreTU -->|хуки: мгновенный блок; иначе пост-фактум-гард| CompassFile
  Brief[выжимка_≤15] -->|вверх| Up[командир выше]
  RawArt[артефакты/логи] -->|только читателям| RawR[raw-brief-synthesizer]
  Journal[journal.jsonl] -->|аудит| InspPros[инспектор/прокурор/панель]
  PendCG[pending_compass_guard] -->|reground → UserPromptSubmit| Cmd2[командующий]
  PendBW[pending_budget_warn] -->|reground → UserPromptSubmit| Cmd3[командующий]
  Proj[PROJECT.md] -->|старт/сводка| Owner2[владелец/docs-keeper]
  Hand[handoff.md] -->|новая сессия| Entry[session-entry]
```

Лимиты курса: сессия ≤8500; фронт/полковник ≤4000 (`orchlib.py:54–57`; `MAP.md:39`). Приказы отдельно от курса (`traps.md:№47`; `front-general.md:20`). Журнал пишут `run-exec`/`run-cloud` (`run-exec.py:358–387`; `run-cloud.py:202–231`; `MAP.md:49`). Компас: агент вызывает воронку `write-compass.py`, воронка пишет файл; прямой Write/Edit в компас: блок PreToolUse (сессии с хуками) + пост-фактум-гард для остальных (`SKILL.md:523–527`; `traps.md:№48`).

## 3. Таблица гейтов

| шаг | гейт | что проверяет | механизм | исход | ист. |
|---|---|---|---|---|---|
| запуск run | секрет-сканер | паттерны ключей/токенов в промте | `run-exec`/`run-cloud` `scan_secrets` | `SECRETS_IN_PROMPT`, exit 5 | `run-exec.py:75–93`; `traps.md:№43` |
| запись курса | лимит compass (воронка) | size ≤ max_session/front | `write-compass.py` | exit 2, файл не пишется, pending | `write-compass.py:59–70` |
| прямой Write/Edit в compass | PreToolUse-deny компаса | путь compass + инструмент Write/Edit | PreToolUse (хуки Kimi): мгновенный deny; иначе пост-фактум-гард | deny / громкий блок / флаг | `reground.py` PreToolUse; `SKILL.md:541–542`; `traps.md:№48` |
| обход воронки | лимит compass (пост-фактум) | живое превышение / pending | `reground` prompt-submit; panel guard | громкий блок / флаг | `reground.py:335–366`; `SKILL.md:486–495` |
| запуск `--front` | закрытый фронт | status ∈ cancelled\|rejected | `apply_front_gates` / `apply_launch_gates` | `FRONT_CLOSED`, exit 6 | `run-exec.py:111–115`; `run-cloud.py:120–122` |
| bump прогонов | бюджет warn | used ≥ warn (деф. 60 в params) | `bump_front_runs` + `emit_pending_budget_warn` | `FRONT_BUDGET_WARN`, продолжается | порог: `run-exec.py:127`; `run-cloud.py:132`; деф.60: `orchlib.py:60` |
| bump прогонов | бюджет hard | hard>0 и used>hard | то же | `BUDGET_HARD`, exit 7 | `run-exec.py:122–126` |
| план любого уровня | критики плана | план+цель; без OK не старт | fact-checker / code-reviewer | PROBLEMS/BLOCKED → ремонт плана | `SKILL.md:406–411`; `front-colonel.md:16` |
| после работы | критики приёмки + замер | дифф/артефакт + критерий | красная волна критиков + замер оркестратора | без OK/замера не принято | `planning.md:192`; `SKILL.md:236–238` |
| развилка / приёмка фронта | advisor-обоснование | advisor в journal или «без советников» | роль + чек-лист командующего | иначе PROBLEMS | `front-general.md:27`; `traps.md:№49` |
| граф / деструктив / 2 круга | HITL | Approve/Revise/Reject | доктрина (вопрос владельцу) | TTL → лестница авто | `SKILL.md:417–421`; `planning.md:274–278` |
| КТ между волнами | SLA наблюдателя | heartbeat ≤30 мин + OK | `observer-heartbeat.txt` | гейт не зелёный | `front-observer.md:13`; `SKILL.md:328–335` |
| save/волны графа | порядок фронтов | deps, циклы, топосорт | `validate_fronts` / `front_waves` | ошибка «цикл в deps» | `orchlib.py:980–981,1017–1053` |
| значимая код-волна | простота | YAGNI/KISS на диффе | `simplicity-warden` | PROBLEMS → ремонт | `simplicity-warden.md:1–16`; `front-colonel.md:17` |
| закрытие фронта | приёмка (журнал+order) | compass≤4000; order; advisor; швы | генерал + командующий; швы — наблюдатель | PROBLEMS / stalled | `front-general.md:27`; `front-observer.md:18` |

## 4. Таблица состояний/файлов

| файл | пишет | читает | когда | ист. |
|---|---|---|---|---|
| `fronts.json` | командующий; panel `/api/fronts` | командующий, session-entry, обёртки (status) | граф; статусы proposed/active/stalled/cancelled/rejected/done | `planning.md:249–259`; `orchlib.py:826–884`; `panel/server.py:133` |
| `fronts/<id>/order.md` | командующий | генерал (RO); наблюдатель; прокурор; инспектор | приказ → курс / сверка | ген: `MAP.md:55`; `front-general.md:20`; набл: `front-observer.md:15`; прок: `front-prosecutor.md:9,17`; инсп: `invocation-inspector.md:9,12` |
| `fronts/<id>/compass.md` | генерал через `write-compass.py` | генерал, наблюдатель, командующий | курс фронта ≤4000 | `MAP.md:56`; `front-general.md:20` |
| `fronts/<id>/colonels/<cid>/order.md` | генерал | полковник (RO) | выдача подзадачи | `MAP.md:57`; `front-colonel.md:13` |
| `fronts/<id>/colonels/<cid>/compass.md` | полковник через воронку | полковник, генерал (замер) | мини-курс ≤4000 | `MAP.md:58`; `front-colonel.md:13` |
| `fronts/<fid>/observer-heartbeat.txt` | наблюдатель на КТ | приёмка командующего; панель | свежесть ≤30 мин — гейт волны | `front-observer.md:13`; `SKILL.md:328–335`; `panel/server.py:116–123` |
| `sessions/<sid>/compass.md` | командующий через воронку; владелец — menu/панель | командующий; reground (вклейки) | сеанс командующего; сверка курса | `SKILL.md:460–469`; `MAP.md:38`; `menu.py` |
| `sessions/<sid>/prosecutor/` | прокурор; инспектор (доклад в канал) | только командующий | закрытый канал волны | `SKILL.md:336–340`; `front-prosecutor.md:9`; `invocation-inspector.md:9,37` |
| `journal.jsonl` | `run-exec`/`run-cloud` (`journal_append`) | инспектор, прокурор, панель | start/end каждого прогона | `orchlib.py:134–148`; `MAP.md:49` |
| `pending_compass_guard.json` | обёртки/`write-compass`/reground/panel | командующий (доставка reground → UserPromptSubmit) | overflow compass | `MAP.md:40–41`; `orchlib.py:681–701`; `reground.py:335–366` |
| `pending_budget_warn.json` | обёртки `emit_pending_budget_warn` | командующий (доставка reground → UserPromptSubmit) | warn-бюджет фронта | `orchlib.py:710–733`; `run-exec.py:127–133`; `SKILL.md:425–427` |
| `.orchestration/handoff.md` | командующий | session-entry / новая сессия | границы волн / пауза ≤2000 | `SKILL.md:24–27`; `session-entry.py:60–65` |
| `PROJECT.md` | командующий (старт); docs-keeper | все уровни / владельцу | иерархия; после значимых волн | `SKILL.md:215–223`; `docs-keeper.md:12` |
| `.orchestration/cursor.key` | владелец / panel | `run-cloud` / discover | ключ cloud | `MAP.md:50`; `panel/server.py:476–481` |
| `params.json` | menu/panel/владелец | все обёртки, reground | дефолты пачки | `orchlib.py:40–62,333–366`; `MAP.md:36` |

## 5. Жизненный цикл задачи

1. **Цель владельца** → командующий: `session-entry` (PROJECT/handoff/fronts/compass) (`session-entry.py:7–8,99–108`).
2. **Критерий + compass сессии**; gate неоднозначности / Assumptions (`planning.md:8–30`).
3. **Иерархия?** auto/on → граф `fronts.json` + критики плана графа + **HITL** утверждения (`SKILL.md:305–316`; `planning.md:217–220,274–278`).
4. **Развилка проекта** → советник (local) → web-scout (cloud); решение командующего (`planning.md:176–179`).
5. Командующий пишет `fronts/<id>/order.md` (обязательна строка `подход: … (по N вариантам советника)` ИЛИ `без советников: выбора нет`), стартует **генерала** (движок) + **прокурора** на волну (`SKILL.md:336–340,357–359`).
6. Генерал: compass через воронку → декомпозиция → **критики плана** → OK (`front-general.md:20–24`).
7. Генерал пишет `fronts/<id>/colonels/<cid>/order.md` (обязательна строка `подход: … (по N вариантам советника)` ИЛИ `без советников: выбора нет, механическая`), запускает **полковника** (cursor) (`front-general.md:21–22`).
8. Полковник: перед выдачей работы с выбором → советник→scout; в задании обязательна строка `подход: … (по N вариантам советника)` ИЛИ `без советников: выбора нет`; план → критики OK (`front-colonel.md:15–16`).
9. **Развилка исполнения:** роль `code/*` → **local-cursor** (`run-exec`); иначе → **cloud** (`run-cloud`) (`SKILL.md:65–86`; `MAP.md:10`).
10. Код-волна: **git-warden** checkpoint → исполнители → критики приёмки+замер → git revise; значимая → **docs-keeper**; значимая код → **simplicity-warden** (git/docs: `front-colonel.md:17`; `planning.md:295–298`; simplicity: `planning.md:197`; `front-colonel.md:17`).
11. Сырьё → **raw-brief-synthesizer** (≤15 строк) вверх; генерал/полковник сырьё не читают (`raw-brief-synthesizer.md:5–7`).
12. **КТ:** наблюдатель (4 прицела + heartbeat≤30м) → инспектор по `journal.jsonl` → прокурору/владельцу; без OK наблюдателя следующая волна графа не стартует (`front-observer.md:13–14`; `SKILL.md:328–335`).
13. **Сбой окна генерала** → снять окно; фронт → `stalled` (не `cancelled`); новый генерал входит через `order.md` + компас (курс) + журнал фронта; бегущие волны НЕ перезапускать — окно приёмки (`start` без `end` = бежит); новые волны — только для незакрытых работ (`SKILL.md:434–441`; `planning.md:265–272`; `front-general.md:33–34`; `traps.md:№51`).
14. Приёмка фронта: compass≤4000, order, advisor/journal, швы; статус `done` / иначе PROBLEMS/stalled (`front-general.md:27`; `SKILL.md:430–432`).
15. Итоговая сшивка `report-synthesizer` + критики → доклад владельцу; handoff на паузе (`planning.md:300–302,315–316`; `SKILL.md:24–27`).

## 6. Источники

- `skills/orchestration/SKILL.md`
- `skills/orchestration/references/MAP.md`
- `skills/orchestration/references/planning.md`
- `skills/orchestration/references/traps.md`
- `skills/orchestration/references/roles/meta/front-general.md`
- `skills/orchestration/references/roles/meta/front-colonel.md`
- `skills/orchestration/references/roles/meta/front-observer.md`
- `skills/orchestration/references/roles/meta/front-prosecutor.md`
- `skills/orchestration/references/roles/meta/invocation-inspector.md`
- `skills/orchestration/references/roles/meta/opportunity-advisor.md`
- `skills/orchestration/references/roles/meta/web-scout.md`
- `skills/orchestration/references/roles/meta/raw-brief-synthesizer.md`
- `skills/orchestration/references/roles/code/git-warden.md`
- `skills/orchestration/references/roles/code/docs-keeper.md`
- `skills/orchestration/references/roles/code/simplicity-warden.md`
- `skills/orchestration/references/roles/code/coder.md`
- `skills/orchestration/references/roles/code/code-reviewer.md`
- `skills/orchestration/references/roles/research/fact-checker.md`
- `bin/run-exec.py`, `bin/run-cloud.py`, `bin/orchlib.py`, `bin/write-compass.py`, `bin/reground.py`, `bin/session-entry.py`, `bin/verdict.py`
- `panel/server.py`
