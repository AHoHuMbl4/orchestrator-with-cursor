# MUSTMAP matrix (MM-C1)

Сгенерировано: `2026-10-04T00:00:34Z`

**Консенсус:** 3/3 = 92; 2/3 = 61; 1/3-проверено = 83; всего = 236

status: mechanism=77; prompt=150; human=9

## commander (106)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-001 | commander | Читай planning.md перед нарезкой нетривиальной и при каждом replan | planning.md before nontrivial split | prompt | — | `skills/orchestration/SKILL.md:11` |
| MM-002 | commander | Новая сессия: session-entry --session; истина на диске, не история чата | session start → брифинг с диска+шаблон | mechanism | session-entry.py | `skills/orchestration/SKILL.md:18` |
| MM-003 | commander | Изоляция state: 1 проект = 1 папка с .orchestration; чужое дерево = подхват чужого state; STATE БЕЗ ПРОЕКТА — остановиться | хук STATE БЕЗ ПРОЕКТА → стоп и развести папки | prompt | — | `skills/orchestration/SKILL.md:24` |
| MM-004 | commander | HANDOFF .orchestration/handoff.md ≤2000 символов; обновляет командующий на границах волн и перед закрытием сессии | границы волн/закрытие → handoff обновлён ≤2000 | prompt | — | `skills/orchestration/SKILL.md:30` |
| MM-005 | commander | При смене версии кита — перечитай SKILL/MAP с диска; противоречия истории чата отменены | смена Kit-версии → перечтение SKILL/MAP с диска | mechanism | reground.py | `skills/orchestration/SKILL.md:37` |
| MM-006 | commander | Иерархия промтов — не импровизируй; каскад домен→поддомен→роль только по _index.md, не листать ~146 файлов | выбор роли → только _index.md каскадом | prompt | — | `skills/orchestration/SKILL.md:43` |
| MM-007 | commander | Не листай ~146 файлов ролей — только каталог _index.md | поиск роли → только _index.md | prompt | — | `skills/orchestration/SKILL.md:51` |
| MM-008 | commander | Роли нет — фабрика по _template.md + строка в _index.md; роль остаётся навсегда | нет роли → фабрика по шаблону и _index.md | prompt | — | `skills/orchestration/SKILL.md:54` |
| MM-009 | commander | В промте роли обязательно заполняй АРТЕФАКТ — приёмка замером по нему | промт роли → поле АРТЕФАКТ заполнено | prompt | — | `skills/orchestration/SKILL.md:57` |
| MM-010 | commander | Критики волны — всегда шаблон критика из библиотеки; критик не задаёт вопросов и не видит лог исполнителя | критик видит лог → нарушение | prompt | — | `skills/orchestration/SKILL.md:59` |
| MM-011 | commander | Расхождение критиков → свежие критики на спорные + synthesizer; после max_rounds без схождения — стоп и доклад владельцу | max_rounds без схождения → доклад с картой | mechanism | review.max_rounds | `skills/orchestration/SKILL.md:62` |
| MM-012 | commander | Режим auto: роль code/ → код-задача; иначе не-код; объяви владельцу одной строкой | старт пачки → объявить режим одной строкой | prompt | — | `skills/orchestration/SKILL.md:70` |
| MM-013 | commander | Код-задача → local-cursor через run-exec; нет бинарника → СТОП/лестница, НЕ cloud авто | код без cursor-agent → стоп/вопрос, не авто-cloud | human | — | `skills/orchestration/SKILL.md:75` |
| MM-014 | commander | Кураторство контекста в промте local-cursor НЕ нужно — агент сам читает/правит ФС | лишнее кураторство CLI → не нужно | prompt | — | `skills/orchestration/SKILL.md:79` |
| MM-015 | commander | Код: нет cursor-agent → СТОП/лестница, НЕ cloud автоматически; cloud только явный fallback | нет CLI → спросить владельца, не auto-cloud | human | — | `skills/orchestration/SKILL.md:83` |
| MM-016 | commander | Cloud-код-протокол — только явный fallback при недоступности CLI, не авто-выбор | код через cloud → только явный fallback | prompt | — | `skills/orchestration/SKILL.md:86` |
| MM-017 | commander | Не-код без CURSOR_API_KEY → СТОП/лестница; ключ → cursor-cloud через run-cloud | нет ключа cloud → стоп/лестница | mechanism | API_KEY_REQUIRED | `skills/orchestration/SKILL.md:88` |
| MM-018 | commander | Не-код: нет ключа → СТОП/лестница; Usage limit = исчерпание; Rate limit = ждать | нет CURSOR_API_KEY → спросить | human | — | `skills/orchestration/SKILL.md:93` |
| MM-019 | commander | Никогда не переходи на субагентов без явного «да» владельца в этой сессии | переход на subagents → только явное да владельца | human | — | `skills/orchestration/SKILL.md:98` |
| MM-020 | commander | Похоже на лимит/квоту — спроси владельца ровно один раз; после ответа НЕ переспрашивай | квота Cursor → один вопрос, без повторов | human | — | `skills/orchestration/SKILL.md:103` |
| MM-021 | commander | Молчаливый переход на субагентов запрещён всегда; вопросы недоступны → пауза≥10м + доклад текстом + разреженные ретраи | молча subagents → запрещено | prompt | — | `skills/orchestration/SKILL.md:113` |
| MM-022 | commander | Молчаливый переход на субагентов запрещён всегда | нет явного да → субагенты не включаются | prompt | — | `skills/orchestration/SKILL.md:116` |
| MM-023 | commander | Авто-режим без вопросов: Assumptions → compass ДО волн → раздел Допущения в финале; on_cursor_fail ask → субагенты НЕ молча | вопросы недоступны → Assumptions+доклад, не silent switch | prompt | — | `skills/orchestration/SKILL.md:122` |
| MM-024 | commander | Интерактив: СНАЧАЛА спроси 1–3 вопроса; Assumptions только если не ответил | владелец на связи → сначала вопросы | human | — | `skills/orchestration/SKILL.md:131` |
| MM-025 | commander | Любая задача через fresh-исполнителя; сам не исполняешь; напрямую — только мета-вопросы о сессии | задача владельца → только через исполнителя | prompt | — | `skills/orchestration/SKILL.md:137` |
| MM-026 | commander | orchestration.enabled=false — работай напрямую без исполнителей | enabled=false → прямой режим | mechanism | params.json | `skills/orchestration/SKILL.md:143` |
| MM-027 | commander | Промт проверяющего содержит ТОЛЬКО артефакт+критерий+адресат; ЗАПРЕЩЕНЫ карты «где проверить» | промт критика → без карт локаций/grep-директив | prompt | — | `skills/orchestration/SKILL.md:155` |
| MM-028 | commander | Механика не опциональна: роль ТОЛЬКО через _index.md; промт ТОЛЬКО из шаблона (исключение — ремонтный follow-up) | пропуск шаблона вне follow-up → ошибка курса | prompt | — | `skills/orchestration/SKILL.md:182` |
| MM-029 | commander | Оптимизировать механику запрещено; шаг невозможен → отступление в compass + доклад; строка Механика: в отчёте волны | нет строки Механика: → критики ловят | prompt | — | `skills/orchestration/SKILL.md:185` |
| MM-030 | commander | Критики исследований проверяют утверждения с URL; слабые — выкинуть или пометить | утверждение без URL → выкинуть/пометить | prompt | — | `skills/orchestration/SKILL.md:197` |
| MM-031 | commander | Пишущая волна кода: git-warden чекпоинт до + ревизия после; правки продукта только коммитами | правка мимо гита → запрещено | mechanism | code_waves_no_gitwarden | `skills/orchestration/SKILL.md:207` |
| MM-032 | commander | Косяк волны: возврат ТОЛЬКО git-механиками (reset/revert), не чинить поверх | чинить поверх чекпоинта → запрещено | prompt | — | `skills/orchestration/SKILL.md:211` |
| MM-033 | commander | Пересбор SHA256SUMS — не зона commit_wave, только командующий | агент пересобрал SHA256SUMS → вне зоны | prompt | — | `skills/orchestration/SKILL.md:219` |
| MM-034 | commander | Волна с ошибкой не принимается без DON'T-карточки (чип rules_no_retro) | error wave without card → rules_no_retro | mechanism | rules_no_retro | `skills/orchestration/SKILL.md:230` |
| MM-035 | commander | При старте иерархии командующий создаёт PROJECT.md; docs-keeper ведёт перезаписью, не дописыванием | нет PROJECT.md при иерархии → создать | prompt | — | `skills/orchestration/SKILL.md:239` |
| MM-036 | commander | Готов = процесс завершился И критерий подтверждён замером; со слов исполнителя — никогда | принятие со слов → запрещено | prompt | — | `skills/orchestration/SKILL.md:262` |
| MM-037 | commander | Фейл — один перезапуск; второй подряд — разбери или доложи; завис повторно — стоп ветки | второй фейл без доклада → нарушение | prompt | — | `skills/orchestration/SKILL.md:264` |
| MM-038 | commander | Завис — стоп, разбор, перезапуск; повторный завис — стоп ветки, доклад | повторный stall → стоп ветки | prompt | — | `skills/orchestration/SKILL.md:266` |
| MM-039 | commander | stall_after EXIT 124 retry; max_wall EXIT 125 без retry; частые таймауты = дробить, не поднимать лимиты | таймауты → replan мельче | prompt | — | `skills/orchestration/SKILL.md:268` |
| MM-040 | commander | Частые таймауты = ошибка декомпозиции: дробить, а не поднимать лимиты | частые timeout → replan мельче, не ↑timeout | prompt | — | `skills/orchestration/SKILL.md:279` |
| MM-041 | commander | Замер-гигиена: SHA в выводе; не глушить вывод проверок; ключ-мис = кривая проба | measurement prints SHA; no >/dev/null on checks | prompt | — | `skills/orchestration/SKILL.md:283` |
| MM-042 | commander | Перед пачкой — фундамент planning.md; неоднозначность → СТОП/вопросы или Assumptions | пачка без фундамента → не стартовать | prompt | — | `skills/orchestration/SKILL.md:287` |
| MM-043 | commander | Preflight: прогноз×критики×круги +15%; >ask_before_runs → СТОП, один вопрос владельцу | запасной > порога → стоп-вопрос | human | — | `skills/orchestration/SKILL.md:290` |
| MM-044 | commander | Авто при превышении порога: Assumptions, не вечный стоп | auto over threshold → Assumptions continue | prompt | — | `skills/orchestration/SKILL.md:295` |
| MM-045 | commander | Параллельный залп — только на независимых задачах; пишущие — по одной на область | пересечение файлов пишущих → сериализация | prompt | — | `skills/orchestration/SKILL.md:298` |
| MM-046 | commander | Синтез N артефактов — отдельная волна, не оркестратор; report-synthesizer; synthesizer только reconciliation | оркестратор сам сшил отчёт → нарушение | prompt | — | `skills/orchestration/SKILL.md:309` |
| MM-047 | commander | Ремонтный follow-up — тому же agent-id; единственный случай без шаблона; >2 ремонтных кругов → стоп и доклад | 3+ repair → стоп/доклад | prompt | — | `skills/orchestration/SKILL.md:333` |
| MM-048 | commander | hierarchy=off — иерархия ЗАПРЕЩЕНА, всегда плоский режим | hierarchy=off → фронты не создавать | prompt | — | `skills/orchestration/SKILL.md:350` |
| MM-049 | commander | Приёмка уровня: командир читает ТОЛЬКО отчёт-выжимку; сырьё и нижний код не читаются | командир уровня → нет чтения сырья | prompt | — | `skills/orchestration/SKILL.md:372` |
| MM-050 | commander | Наблюдатель на КТ через run-exec (не --readonly); heartbeat ≤30 мин; без OK следующая волна не стартует | heartbeat старше 30м → гейт не зелёный | mechanism | observer-heartbeat | `skills/orchestration/SKILL.md:382` |
| MM-051 | commander | Без OK наблюдателя следующая волна не стартует | observer OK gate before next wave | prompt | — | `skills/orchestration/SKILL.md:391` |
| MM-052 | commander | Прокурор на каждую волну фронта через run-exec; канал sessions/<sid>/prosecutor/; генералы не читают | генерал читает канал прокурора → нарушение | mechanism | fronts_no_prosecutor | `skills/orchestration/SKILL.md:393` |
| MM-053 | commander | Инспектор вызовов на КТ: вход journal.jsonl; отчёт прокурору и владельцу | КТ без инспектора при запуске → пробел процесса | prompt | — | `skills/orchestration/SKILL.md:404` |
| MM-054 | commander | НОВЫЙ ФРОНТ = НОВЫЙ ГЕНЕРАЛ; resume только то же имя; чужому фронту окно ЗАПРЕЩЕНО (чип general_resume_chain) | resume чужого фронта → general_resume_chain | mechanism | general_resume_chain | `skills/orchestration/SKILL.md:415` |
| MM-055 | commander | В order.md обязательна строка подход:… ИЛИ без советников: выбора нет; иначе PreToolUse-deny / нудж | order без основы → deny _order_has_basis | mechanism | orders_without_basis | `skills/orchestration/SKILL.md:426` |
| MM-056 | commander | Активный фронт без записей исполнителей → stalled + новый генерал | no executor journal → stalled | mechanism | health_red_chips | `skills/orchestration/SKILL.md:448` |
| MM-057 | commander | Приёмка=функция: зелёный ⇔ приёмщик прогнал пробу; квитанция снимает probes_missing | probe receipt clears probes_missing | mechanism | probes_missing | `skills/orchestration/SKILL.md:450` |
| MM-058 | commander | --readonly ТОЛЬКО для аналитики без артефактов; командирные роли — обычно без --readonly | советник/наблюдатель/генерал → не --readonly | mechanism | run-exec.py | `skills/orchestration/SKILL.md:467` |
| MM-059 | commander | --kill только точным run-id; дубль --id живой → exit 11; dual-writer → exit 13 + multi_write_front | second writer on front → exit 13 | mechanism | multi_write_front | `skills/orchestration/SKILL.md:474` |
| MM-060 | commander | При --front обёртка вклеивает ## Владение (A2); ORCH_FRONT в дочерние | запуск с --front → блок owns/forbids в промте | mechanism | run-exec.py | `skills/orchestration/SKILL.md:478` |
| MM-061 | commander | Развилки командующего → opportunity-advisor+web-scout; решение в order.md строкой подхода | развилка без подхода в order → нудж/чип | mechanism | orders_without_basis | `skills/orchestration/SKILL.md:481` |
| MM-062 | commander | Чек-лист статуса: развилки/руки/компас — директива генералу, не делать самому | командующий чинит compass фронта сам → нарушение | prompt | — | `skills/orchestration/SKILL.md:487` |
| MM-063 | commander | План через критиков на КАЖДОМ уровне; граф/фронт безусловно; задачи — при нетривиальности | план фронта без критиков → не старт | mechanism | waves_no_critic | `skills/orchestration/SKILL.md:517` |
| MM-064 | commander | HITL обязателен: утверждение графа / деструктив / эскалация 2 кругов — Approve/Revise/Reject | граф без HITL → нарушение | human | — | `skills/orchestration/SKILL.md:528` |
| MM-065 | commander | Фикс = фронт при живой иерархии; серия ≥3 no-front — блок реграунда | ≥3 no-front при hierarchy → нудж/блок | mechanism | reground.py | `skills/orchestration/SKILL.md:537` |
| MM-066 | commander | Owns-map: пересечение owns с active = отказ активации | owns overlap → отказ | mechanism | owns.py | `skills/orchestration/SKILL.md:542` |
| MM-067 | commander | warn_runs ≥warn → FRONT_BUDGET_WARN продолжается; hard>0 и used>hard → BUDGET_HARD exit 7 | budget warn vs hard gates | mechanism | FRONT_BUDGET_WARN | `skills/orchestration/SKILL.md:547` |
| MM-068 | commander | Качество > токены: не режь осознанную сложность ради экономии | don't cut quality for token save | prompt | — | `skills/orchestration/SKILL.md:554` |
| MM-069 | commander | Зависшее окно: stalled НЕ cancelled; новый генерал: order+compass+журнал; бегущие не перезапускать | stalled resume = acceptance window | prompt | — | `skills/orchestration/SKILL.md:559` |
| MM-070 | commander | По умолчанию НИЧЕГО не спрашивай у владельца; меню — только если позвал «меню» | лишние вопросы меню → нарушение | prompt | — | `skills/orchestration/SKILL.md:570` |
| MM-071 | commander | Пишущая задача parallel_per_task=1 + критики; spike — отдельное решение не по дефолту | spike по дефолту → запрещено | prompt | — | `skills/orchestration/SKILL.md:577` |
| MM-072 | commander | Компас — обязателен и ТОЛЬКО сессионный; НИКОГДА не пиши в общий .orchestration/compass.md | запись в общий compass.md → запрещена | mechanism | write-compass.py | `skills/orchestration/SKILL.md:594` |
| MM-073 | commander | COMPASS ПРЕВЫШЕН фронта: НЕ ужимать самому; директива генералу через воронку; повтор → stalled | overflow → order general rewrite | prompt | — | `skills/orchestration/SKILL.md:643` |
| MM-074 | commander | Повторное превышение compass — фронт в stalled до исправления генералом | повторный COMPASS_OVERFLOW → stalled | mechanism | reground.py | `skills/orchestration/SKILL.md:648` |
| MM-075 | commander | Перед каждой волной и каждые reground.every_min — перечитай params/compass; дрейф → назад | дрейф без возврата → нарушение | mechanism | reground.py | `skills/orchestration/SKILL.md:658` |
| MM-076 | commander | После end роли волны возможен автопрокурор prosecutor-auto (один на волну) | wave-end idle → auto prosecutor | mechanism | maybe_auto_prosecutor_after_end | `skills/orchestration/references/FLOW.md:70` |
| MM-077 | commander | validate_fronts: циклы deps запрещены | cycle in fronts deps → error | mechanism | validate_fronts | `skills/orchestration/references/FLOW.md:76` |
| MM-078 | commander | order.md пишет командующий; генерал RO; compass фронта пишет генерал через воронку | генерал пишет order фронта → нарушение | prompt | — | `skills/orchestration/references/FLOW.md:85` |
| MM-079 | commander | Нет измеримого критерия — не декомпозируй: сначала добудь критерий | no measurable done → no split | prompt | — | `skills/orchestration/references/planning.md:9` |
| MM-080 | commander | Запиши в compass цель, критерий, границы и TODO-чеклист до нарезки | compass has goal+criterion+TODO before split | prompt | — | `skills/orchestration/references/planning.md:13` |
| MM-081 | commander | Неоднозначность/противоречие — СТОП, 1–3 вопроса владельцу; декомпозиция вслепую запрещена | нет предмета/критерия → стоп+вопросы | human | — | `skills/orchestration/references/planning.md:23` |
| MM-082 | commander | Никогда не задавай вопросов исполнителям: нерешённое — владельцу или Assumptions | вопрос исполнителю → запрещено | prompt | — | `skills/orchestration/references/planning.md:29` |
| MM-083 | commander | Перед разбиением jev need-split/split-quality; низкий quality → пересобрать; иначе без советников: выбора нет | split без jev/пометки → нарушение | prompt | — | `skills/orchestration/references/planning.md:47` |
| MM-084 | commander | Низкий split-quality → пересобрать до критиков плана | low split-quality → re-split | prompt | — | `skills/orchestration/references/planning.md:53` |
| MM-085 | commander | Артефакт всегда обязателен кроме чисто мета-диалога | non-meta tasks need artifact path | prompt | — | `skills/orchestration/references/planning.md:56` |
| MM-086 | commander | Циклы в DAG запрещены; пишущие с пересечением — слить или сериализовать | цикл deps → ошибка | mechanism | validate_fronts | `skills/orchestration/references/planning.md:63` |
| MM-087 | commander | Пишущая задача — всегда ОДИН исполнитель; parallel_per_task только READ-ONLY | writers: parallel_per_task=1 | prompt | — | `skills/orchestration/references/planning.md:69` |
| MM-088 | commander | depends_on в state НЕ заводим — рёбра в TODO compass | no machine depends_on in task state | prompt | — | `skills/orchestration/references/planning.md:89` |
| MM-089 | commander | Валидация декомпозиции: ведёт к критерию / нет дыр / нет дублей / самодостаточна; иначе переделай | хоть один «нет» → переделка до запуска | prompt | — | `skills/orchestration/references/planning.md:95` |
| MM-090 | commander | Фундамент до волн обязателен: gate→декомпозиция→роли→критерии критиков→прогноз | пачка без §3.6 → не стартовать | prompt | — | `skills/orchestration/references/planning.md:107` |
| MM-091 | commander | PROJECT.md обновляется перезаписью, не дописыванием; расхождение = PROBLEMS | docs-keeper сверка → расхождение PROBLEMS | prompt | — | `skills/orchestration/references/planning.md:142` |
| MM-092 | commander | Приказ без строки обоснования: PreToolUse или чип orders_without_basis; молчание детекторов = инцидент №57 | order basis missing → chip/deny | mechanism | orders_without_basis | `skills/orchestration/references/planning.md:186` |
| MM-093 | commander | Приёмка=функция: текст/артефакт/чип-в-норме ≠ доказательство | green iff probe oracle on polygon | mechanism | probes_missing | `skills/orchestration/references/planning.md:246` |
| MM-094 | commander | Блок пробы обязателен в карточке волны кода/фикса; нет → нудж + probes_missing | нет проба:/оракул: → probes_missing | mechanism | probes_missing | `skills/orchestration/references/planning.md:250` |
| MM-095 | commander | narrative не самообъявляемый; артефакт-оракул narrative быть не может | narrative class restricted | prompt | — | `skills/orchestration/references/planning.md:255` |
| MM-096 | commander | Ремонт плана = новая волна критиков плана (не ремонтный follow-up артефакта) | follow-up на план → запрещено | prompt | — | `skills/orchestration/references/planning.md:354` |
| MM-097 | commander | Фронт стартует когда все deps done; cancelled/rejected — запуски закрыты | FRONT_CLOSED on cancelled/rejected | mechanism | FRONT_CLOSED | `skills/orchestration/references/planning.md:363` |
| MM-098 | commander | Между волнами gate: критерии зелёные замером + сверка с compass | волна без замера/сверки → не закрывать | prompt | — | `skills/orchestration/references/planning.md:416` |
| MM-099 | commander | Финальная сшивка — волна синтеза, не текст оркестратора в своём контексте | commander doesn't stitch N arts himself | prompt | — | `skills/orchestration/references/planning.md:422` |
| MM-100 | commander | Волна перекрёстной сверки — только независимые источники; основной источник волн в границах запрещён by design | основной источник в cross-check → by design запрет | prompt | — | `skills/orchestration/references/planning.md:424` |
| MM-101 | commander | Partial-fail replan: зелёные не перезапускаются; только красные и зависимые | зелёный узел → не перезапускать | prompt | — | `skills/orchestration/references/planning.md:432` |
| MM-102 | commander | Анти-паттерн: premature decomposition / мелкая нарезка / гигант / нет сверки compass / каскад / полный restart /↑timeout | decomposition anti-patterns avoided | prompt | — | `skills/orchestration/references/planning.md:443` |
| MM-103 | commander | Анти-паттерн: резать до фиксации цели/критерия | premature decomposition → запрещено | prompt | — | `skills/orchestration/references/planning.md:445` |
| MM-104 | commander | Наблюдатель запускается командующим на КТ; запуск обычный НЕ --readonly (нужен Write метки) | observer not --readonly | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:4` |
| MM-230 | commander | Закрытие волны механизма дисциплины/hardening НЕ принимается без таблицы ожидание×факт на корпусе атак (оракул = journal/exit/чип, состояние); прогон только на дисциплинированном/послушном командующем = НЕ зачёт. Связь: dont-validaciya-sistemy-na-poslushnom-discipl (run_ref=owner-02-10), do-zelenyj-rabotaet-na-poligone-prinyat-vol; suite: tests/adversarial/** | закрытие волны дисциплины/hardening → таблица ожидание×факт на корпусе; оракул journal/exit; иначе не зачёт | mechanism | tests/adversarial suite (scenarios.json + report.md) | `skills/orchestration/references/planning.md:291` |
| MM-232 | commander | Серая зона без советников — fail-safe suspect; тихое OK запрещено | mechanical→молчание; fork неуверенный (mid/high и band=low)/defer/сбой канала→suspect; тихое OK только явный mechanical; шум — allowlist+expires_on | mechanism | orders_suspect+advisor-need-check+tests/test_orders_allowlist* | `skills/orchestration/SKILL.md:487` |

## general (27)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-105 | general | Пишущая волна кода: чекпоинт git-warden до и ревизия после; генерал обязан требовать чекпоинты | код-волна без git-warden → PROBLEMS/чип | mechanism | code_waves_no_gitwarden | `skills/orchestration/SKILL.md:207` |
| MM-106 | general | Генерал обязан требовать чекпоинты волн git-warden | волна кода без git-warden → PROBLEMS | mechanism | code_waves_no_gitwarden | `skills/orchestration/SKILL.md:214` |
| MM-107 | general | После значимой волны обязателен docs-keeper; генерал обязан требовать | значимая волна без docs-keeper → чип/PROBLEMS | mechanism | wave_no_docs | `skills/orchestration/SKILL.md:223` |
| MM-108 | general | Архитектура-контракты — всегда первая волна фронта | contracts first wave | prompt | — | `skills/orchestration/SKILL.md:418` |
| MM-109 | general | Подзадачи только через полковников; order в colonels/<cid>/order.md; сырьё — только raw-brief | генерал пишет код → нарушение с первого раза | prompt | — | `skills/orchestration/SKILL.md:431` |
| MM-110 | general | Руки генерала НОЛЬ: только обёртки/выжимки/write-compass/order полковникам/доклад; Write/Edit проекта = нарушение с первого раза | general Write/Edit project = violation | prompt | — | `skills/orchestration/SKILL.md:437` |
| MM-111 | general | Приёмка-по-журналу: сдача только при journal role=доменная; руки мимо обёрток не засчитываются | нет записей исполнителей → stalled | mechanism | health_red_chips | `skills/orchestration/SKILL.md:444` |
| MM-112 | general | Активный фронт без записей исполнителей за окно → stalled + новый генерал | нет journal исполнителей → stalled | prompt | — | `skills/orchestration/SKILL.md:448` |
| MM-113 | general | Короткие окна: 1 волна≈1 окно; resume только того же фронта; потолок ~2ч — штатный resume | вечное окно → запрещено | prompt | — | `skills/orchestration/SKILL.md:457` |
| MM-114 | general | Приёмка фронта: замер len(compass) ≤4000 | front compass >4000 → PROBLEMS | mechanism | write-compass.py | `skills/orchestration/SKILL.md:463` |
| MM-115 | general | Генералы волны слепы друг к другу НАПРОЧЬ; coordination только через командующего | перекрёстное чтение runs → запрещено | prompt | — | `skills/orchestration/SKILL.md:488` |
| MM-116 | general | Прямой запуск исполнителей генералом — только тривиальные замеры; NEVER для работ | работа ≠ замер → только через полковника | prompt | — | `skills/orchestration/SKILL.md:509` |
| MM-117 | general | Закрытие фронта: compass≤4000 + order + advisor + швы | front done criteria unmet → PROBLEMS/stalled | prompt | — | `skills/orchestration/references/FLOW.md:78` |
| MM-118 | general | order.md пишет командующий; генерал только читает | general never writes front order.md | prompt | — | `skills/orchestration/references/FLOW.md:85` |
| MM-119 | general | Генерал/полковник сырьё не читают — только raw-brief ≤15 строк вверх | чтение сырья командиром → запрещено | prompt | — | `skills/orchestration/references/FLOW.md:113` |
| MM-120 | general | Бегущие волны при новом генерале НЕ перезапускать; start без end = бежит | дубль бегущей волны → запрещено | prompt | — | `skills/orchestration/references/FLOW.md:115` |
| MM-121 | general | Границы волны: СВОИ пути + запрет чужих; назначение фронтов непересекающееся | пересечение файлов фронтов → ошибка графа | prompt | — | `skills/orchestration/references/planning.md:195` |
| MM-122 | general | САМОисполнение работы полковника/исполнителя запрещено — стоп и делегируй | general self-exec → stop+delegate | prompt | — | `skills/orchestration/references/planning.md:334` |
| MM-123 | general | Оркестратору по часам не рвать генерала — только по границам работ или дрейфу | таймер окна → не повод рвать руками | prompt | — | `skills/orchestration/references/planning.md:338` |
| MM-124 | general | Инструменты генерала исчерпывающие (а–д); всё остальное запрещено; Write/Edit проекта = нарушение с первого раза | Shell-правка кода → сразу стоп | prompt | — | `skills/orchestration/references/roles/meta/front-general.md:7` |
| MM-125 | general | Write/Edit/Shell правок проекта = нарушение с первого раза — остановись и запусти полковника | urge to edit → stop+delegate colonel | prompt | — | `skills/orchestration/references/roles/meta/front-general.md:13` |
| MM-126 | general | Чужие runs/вердикты параллельных фронтов НЕ читать; с параллельными НЕ координироваться — через командующего | координация с соседним генералом → запрещено | prompt | — | `skills/orchestration/references/roles/meta/front-general.md:16` |
| MM-127 | general | Перед order полковнику: jev need-advisor; выбор → advisor+scout; строка подход: или без советников обязательна | order без строки подхода → НЕ писать | mechanism | orders_without_basis | `skills/orchestration/references/roles/meta/front-general.md:20` |
| MM-128 | general | Возобновление: сверь journal start/end; не дублируй бегущее; память прошлого окна не предполагай | resume from journal not memory | prompt | — | `skills/orchestration/references/roles/meta/front-general.md:34` |
| MM-129 | general | НИКОГДА подглядывание к параллельным линиям; самовольная остановка по чужим результатам | координация/стоп по чужому → анти-паттерн | prompt | — | `skills/orchestration/references/roles/meta/front-general.md:55` |
| MM-231 | general | Закрытие волны механизма дисциплины/hardening НЕ принимается без таблицы ожидание×факт на корпусе атак (оракул = journal/exit/чип, состояние); прогон только на дисциплинированном/послушном командующем = НЕ зачёт. Связь: dont-validaciya-sistemy-na-poslushnom-discipl (run_ref=owner-02-10), do-zelenyj-rabotaet-na-poligone-prinyat-vol; suite: tests/adversarial/** | закрытие волны дисциплины/hardening → таблица ожидание×факт на корпусе; оракул journal/exit; иначе не зачёт | mechanism | tests/adversarial suite (scenarios.json + report.md) | `skills/orchestration/references/planning.md:291` |
| MM-233 | general | Серая зона без советников — fail-safe suspect; тихое OK запрещено | mechanical→молчание; fork неуверенный (mid/high и band=low)/defer/сбой канала→suspect; тихое OK только явный mechanical; шум — allowlist+expires_on | mechanism | orders_suspect+advisor-need-check+tests/test_orders_allowlist* | `skills/orchestration/SKILL.md:487` |

## colonel (9)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-130 | colonel | 1 полковник = 1 подзадача; NEVER порождать вложенных полковников; 2 круга → эскалация | вложенный полковник → запрещён | prompt | — | `skills/orchestration/SKILL.md:496` |
| MM-131 | colonel | Свита обязательна: advisor, plan critics, acceptance critics, raw-brief, git/docs/simplicity wardens | colonel suite roles present | prompt | — | `skills/orchestration/SKILL.md:501` |
| MM-132 | colonel | Значимая код-волна: simplicity-warden иначе PROBLEMS | simplicity gate after significant code | prompt | — | `skills/orchestration/references/FLOW.md:77` |
| MM-133 | colonel | colonels order пишет генерал; mini-compass пишет полковник через воронку | полковник пишет свой order → нарушение | prompt | — | `skills/orchestration/references/FLOW.md:87` |
| MM-134 | colonel | Рамки: 3–5 работ; order только читает; mini-compass ≤4000; не порождает полковников | полковник пишет свой order → запрещено | prompt | — | `skills/orchestration/references/planning.md:297` |
| MM-135 | colonel | Сам код/инфру руками не пишешь — командуешь cursor-исполнителями | полковник Write кода → запрещено | prompt | — | `skills/orchestration/references/roles/meta/front-colonel.md:5` |
| MM-136 | colonel | Не порождать вложенных полковников; сырьё самому не читать; чужие runs НЕ читать | вложенный полковник → НИКОГДА | prompt | — | `skills/orchestration/references/roles/meta/front-colonel.md:9` |
| MM-137 | colonel | Сырьё читают ТОЛЬКО raw-brief; код-волна → git-warden; значимая → docs+simplicity | код без git-warden → нарушение | mechanism | code_waves_no_gitwarden | `skills/orchestration/references/roles/meta/front-colonel.md:17` |
| MM-138 | colonel | НИКОГДА самому исполнять работы Write/Edit/Shell вместо делегирования | руки вместо ролей → анти-паттерн | prompt | — | `skills/orchestration/references/roles/meta/front-colonel.md:40` |

## executor (14)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-139 | executor | Код: правки через CLI run-exec; артефакт=дифф; критики=дифф+критерий | code wave uses run-exec + diff critics | prompt | — | `skills/orchestration/SKILL.md:201` |
| MM-140 | executor | Правки продукта — только через коммиты; мимо гита запрещено | правка продукта → только коммит | prompt | — | `skills/orchestration/SKILL.md:210` |
| MM-141 | executor | Сырой git commit -a / git add -A запрещён; только pathspec через commit_wave | commit via commit-wave.py pathspec | mechanism | commit-wave.py | `skills/orchestration/SKILL.md:217` |
| MM-142 | executor | Промт самодостаточен: цель/критерий; точные файлы; что НЕ трогать; путь/команда дословно; артефакт | промт без НЕ трогать → неполнота | prompt | — | `skills/orchestration/SKILL.md:253` |
| MM-143 | executor | --front вклеивает A2 owns+forbids; ORCH_FRONT в дочерние | front launch injects ownership block | mechanism | run-exec.py | `skills/orchestration/SKILL.md:478` |
| MM-144 | executor | Прогоны: --front или --no-front иначе FRONT_REQUIRED exit 8 (hierarchy≠off) | missing front flag → exit 8 | mechanism | FRONT_REQUIRED | `skills/orchestration/SKILL.md:534` |
| MM-145 | executor | Секреты в промте → SECRETS_IN_PROMPT exit 5; процесс не стартует | secrets in prompt → exit 5 | mechanism | SECRETS_IN_PROMPT | `skills/orchestration/references/FLOW.md:62` |
| MM-146 | executor | Одна маленькая задача = один агент; «A потом B» = две карточки (ловушка №4) | no multi-task cards for one agent | prompt | — | `skills/orchestration/references/planning.md:74` |
| MM-147 | executor | Oracle-класс: обязательна пара проба-green + проба-red на /tmp-полигоне | oracle diffs need green+red probes | prompt | — | `skills/orchestration/references/planning.md:286` |
| MM-148 | executor | Минимальное изменение; ГРАНИЦЫ дословно; результат строго в АРТЕФАКТ; приёмка замером по файлу | выход за ГРАНИЦЫ → анти-паттерн | prompt | — | `skills/orchestration/references/roles/code/coder.md:6` |
| MM-149 | executor | Артефакт строго в указанный путь; приёмка замером по нему | artifact path exact | prompt | — | `skills/orchestration/references/roles/code/coder.md:10` |
| MM-150 | executor | Процесс: прочитай целиком → diff → минимальная правка → тесты → показать diff | tests run before done | prompt | — | `skills/orchestration/references/roles/code/coder.md:12` |
| MM-151 | executor | Выполни без уточнений пока критерий зелёный; блокирует — запиши и завершись | вопросы вместо работы → нарушение | prompt | — | `skills/orchestration/references/roles/code/coder.md:15` |
| MM-152 | executor | Не переписывай чужой код под вкус; не добавляй зависимости без нужды; не меняй окружение без разрешения | pip без разрешения → анти-паттерн | prompt | — | `skills/orchestration/references/roles/code/coder.md:24` |

## critic (18)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-153 | critic | Критики волны — всегда шаблон критика-скептика/ревьюера; не задаёт вопросов и не видит лог | критик → шаблон из библиотеки, без лога исполнителя | prompt | — | `skills/orchestration/SKILL.md:59` |
| MM-154 | critic | Критики — всегда свежие read-only агенты того же режима, что и исполнители | критик → fresh readonly, тот же режим | prompt | — | `skills/orchestration/SKILL.md:99` |
| MM-155 | critic | Критик видит ТОЛЬКО дифф + критерий, никогда — лог/ход мыслей исполнителя | критик читает лог → нарушение | prompt | — | `skills/orchestration/SKILL.md:179` |
| MM-156 | critic | Замер-гигиена: SHA кода в выводе; не глушить вывод; ключ-мис = проба кривая | приёмочный замер → SHA+живые логи+схема | prompt | — | `skills/orchestration/SKILL.md:283` |
| MM-157 | critic | Формат проблем: ПРОБЛЕМА: место — суть — как чинить; нет проблем — только Вердикт: OK | отчёт критика → префикс ПРОБЛЕМА: или OK | prompt | — | `skills/orchestration/SKILL.md:306` |
| MM-158 | critic | На выжимках запрещена проверка «0 новых фактов»; ложные жалобы — grep по полным артефактам | UNVERIFIABLE на выжимках, не violation | prompt | — | `skills/orchestration/SKILL.md:316` |
| MM-159 | critic | Приёмка=функция: зелёный ⇔ приёмщик сам прогнал пробу; квитанция снимает probes_missing | нет пробы/квитанции → probes_missing | mechanism | probes_missing | `skills/orchestration/SKILL.md:450` |
| MM-160 | critic | probe-receipt.md tool-only; ручной Write/Edit = violation; dual-writer orchlib.write_probe_receipt | manual probe-receipt → violation | mechanism | write_probe_receipt | `skills/orchestration/references/planning.md:259` |
| MM-161 | critic | Вердикт без своего прогона недействителен; самоотчёт исполнителя ≠ оракул | критик без пробы → недействителен | mechanism | probes_missing | `skills/orchestration/references/planning.md:274` |
| MM-162 | critic | Снятие probes_missing только валидной квитанцией; дифф гасящий чип → chip_silenced | silencing chip without fix → chip_silenced | mechanism | chip_silenced | `skills/orchestration/references/planning.md:276` |
| MM-163 | critic | Критик обязан проверить: исключена ли истинная причина сигнала? CAUSE-CLEARED | глушение без причины → нарушение | mechanism | orch-lint | `skills/orchestration/references/planning.md:290` |
| MM-164 | critic | промт критика/аудитора/наблюдателя: артефакт+критерий; без карт локаций | карта в промте проверяющего → слепое пятно | prompt | — | `skills/orchestration/references/planning.md:321` |
| MM-165 | critic | Видишь только дифф и критерий, не ход автора; вопросов не задавать; ничего не править — только отчёт | критик правит код → нарушение | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:6` |
| MM-166 | critic | Вопросов не задавать; ничего не править — только отчёт | read-only critic; no questions | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:9` |
| MM-167 | critic | Каждой проблеме — файл:строка и серьёзность; пункты с префиксом ПРОБЛЕМА:; Вердикт первой строкой | проблема без ПРОБЛЕМА: → нарушение формата | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:12` |
| MM-168 | critic | Вердикт первой строкой; каждый пункт PROBLEMS с префиксом ПРОБЛЕМА:; Механика последней | ПРОБЛЕМА: prefix mandatory | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:14` |
| MM-169 | critic | Анти-паттерн: не выходи за ГРАНИЦЫ; не меняй; не выдумывай данные | правка/выдумка → анти-паттерн | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:16` |
| MM-170 | critic | Анти-паттерн: не пересказывай дифф; не пиши «в целом хорошо» без конкретики | общий OK без мест → анти-паттерн | prompt | — | `skills/orchestration/references/roles/code/code-reviewer.md:24` |

## observer (8)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-171 | observer | Наблюдатели: власти ноль — только ПРОБЛЕМА: главнокомандующему; 4 прицела; не исполняют | наблюдатель запускает волны → нарушение | prompt | — | `skills/orchestration/SKILL.md:408` |
| MM-172 | observer | SLA heartbeat ≤30 мин иначе гейт волны не зелёный | stale heartbeat → gate red | mechanism | observer-heartbeat | `skills/orchestration/references/FLOW.md:75` |
| MM-173 | observer | Власти ноль: не запускает исполнителей, не правит код/compass; писать только артефакт и heartbeat | observer правил compass → НИКОГДА | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:3` |
| MM-174 | observer | Писать разрешено только свой артефакт и observer-heartbeat.txt; НЕ сырые run.log | запись вне артефакта/heartbeat → запрещена | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:10` |
| MM-175 | observer | Hands-on сигнал: active+mtime растёт без journal runs → ПРОБЛЕМА руки генерала | hands-on detection → ПРОБЛЕМА | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:16` |
| MM-176 | observer | Правда докладов: 2–5 утверждений перезамерь; без свидетеля — проблема | нарратив без замера → ПРОБЛЕМА | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:19` |
| MM-177 | observer | Вердикт OK\|PROBLEMS\|BLOCKED; ПРОБЛЕМА: фронт\|Hands-on\|шов\|доклад\|цель — суть — что делать | отчёт без вердикта → не принято | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:23` |
| MM-178 | observer | НИКОГДА принимать доклад генерала без выборочного замера | доклад без замера → анти-паттерн | prompt | — | `skills/orchestration/references/roles/meta/front-observer.md:35` |

## prosecutor (7)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-179 | prosecutor | Канал prosecutor/ пишет прокурор; читает только командующий | генерал читает prosecutor/ → нарушение | prompt | — | `skills/orchestration/references/FLOW.md:91` |
| MM-180 | prosecutor | Подотчётен ТОЛЬКО командующему; власть ноль; писать ТОЛЬКО в sessions/.../prosecutor/ | доклад генералам → НИКОГДА | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:3` |
| MM-181 | prosecutor | 7 прицелов: cross-access / координация / дубли / пакет / compass мимо воронки / потеря приказа / без обоснования | прицел не закрыт → не OK | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:5` |
| MM-182 | prosecutor | Писать ТОЛЬКО в sessions/<sid>/prosecutor/ или указанный файл; не в общие места | запись в места генералов → запрещена | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:8` |
| MM-183 | prosecutor | Пакетная загрузка одному генералу — нарушение 1=1=1; пометь, не останавливай | пакет в приказе → ПРОБЛЕМА командующему | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:15` |
| MM-184 | prosecutor | НИКОГДА вмешиваться (стоп/правка/лечение); НИКОГДА сообщать генералам; НИКОГДА поверхностный вердикт | лечение дублей снизу → НИКОГДА | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:29` |
| MM-185 | prosecutor | journal end без front — by-design не ПРОБЛЕМА; старт после max_rounds при доке эскалации — by-design | end без front → не ПРОБЛЕМА | prompt | — | `skills/orchestration/references/roles/meta/front-prosecutor.md:36` |

## all (47)

| id | кому | текст | check | статус | механизм | источник |
|----|------|-------|-------|--------|----------|----------|
| MM-186 | all | Никогда не переходи на субагентов без явного «да» владельца в этой сессии | переход на subagents без да → запрещено | prompt | — | `skills/orchestration/SKILL.md:98` |
| MM-187 | all | Исчерпание лимита/квоты = стоп и доклад вверх; вышестоящий не замещает нижестоящего руками | руками сделал работу нижестоящего → запрещено | prompt | — | `skills/orchestration/SKILL.md:117` |
| MM-188 | all | Слепота: параллельные агенты волны стерильны друг к другу; подглядывание исключено; картину видит только оркестратор | агент читает чужой run → нарушение слепоты | prompt | — | `skills/orchestration/SKILL.md:148` |
| MM-189 | all | Промт проверяющего ТОЛЬКО: артефакт + критерий цели + адресат; ЗАПРЕЩЕНЫ карты файлов/grep/ожидаемые выводы | карта «где проверить» в промте → запрещено | prompt | — | `skills/orchestration/SKILL.md:154` |
| MM-190 | all | Манифест границ фронта — артефакт, давать целиком разрешено; карты поиска по системе запрещены | owns manifest OK; search maps forbidden | prompt | — | `skills/orchestration/SKILL.md:161` |
| MM-191 | all | Принцип снайпера: знаешь ТОЛЬКО свою задачу/критерий; «нет» → вниз с критерием; чужие нюансы не поднимать | разбор чужого уровня → запрещено | prompt | — | `skills/orchestration/SKILL.md:165` |
| MM-192 | all | Запрещено поднимать нюансы чужих уровней наверх и тащить детали вниз | контекст чужого уровня → не поднимать/не тащить | prompt | — | `skills/orchestration/SKILL.md:171` |
| MM-193 | all | Косяк волны: возврат ТОЛЬКО git-механиками (reset/revert), не чинить поверх | откат волны → reset/revert, не самодельный скрипт | prompt | — | `skills/orchestration/SKILL.md:211` |
| MM-194 | all | Сырой git commit -a / git add -A агентам запрещён; только pathspec через commit_wave | коммит агентом → только commit-wave.py pathspec | mechanism | commit-wave.py | `skills/orchestration/SKILL.md:217` |
| MM-195 | all | --no-verify в логе коммита → чип commit_no_verify | коммит с --no-verify → красный чип | mechanism | commit_no_verify | `skills/orchestration/SKILL.md:219` |
| MM-196 | all | Волна с ошибкой не принимается без DON'T-карточки (чип rules_no_retro) | ошибка волны без ретро-карточки → rules_no_retro | mechanism | rules_no_retro | `skills/orchestration/SKILL.md:230` |
| MM-197 | all | Первая строка рабочего промта: роль: <путь>; run-exec/run-cloud — передавай --role; extractor — первые 3 строки | шапка роли отсутствует (не смоук) → нарушение | prompt | — | `skills/orchestration/SKILL.md:245` |
| MM-198 | all | stall_after EXIT 124 retry; max_wall EXIT 125 без retry | timeout classes respect exit codes | mechanism | run-exec.py | `skills/orchestration/SKILL.md:268` |
| MM-199 | all | Глушить/ослаблять детектор без CAUSE-CLEARED — нарушение; цвет чипа ≠ критерий | detector weaken needs CAUSE-CLEARED | mechanism | orch-lint | `skills/orchestration/SKILL.md:281` |
| MM-200 | all | Замер-гигиена: SHA кода в выводе; не глушить вывод проверок; ключ-мис = кривая проба | >/dev/null на проверке → нарушение гигиены | prompt | — | `skills/orchestration/SKILL.md:283` |
| MM-201 | all | Вердикт: OK \| PROBLEMS \| BLOCKED + блок доказательств; оркестратор принимает ТОЛЬКО по вердикту+замеру | текст без доказательств → не принято | prompt | — | `skills/orchestration/SKILL.md:327` |
| MM-202 | all | Принцип лестницы: уровень сужает задачу; наверх ТОЛЬКО принятый замером результат выжимкой | вверх → только выжимка принятого, не сырьё | prompt | — | `skills/orchestration/SKILL.md:366` |
| MM-203 | all | Приёмка=функция: зелёный ⇔ приёмщик сам прогнал пробу; квитанция снимает probes_missing | нет квитанции → probes_missing | mechanism | probes_missing | `skills/orchestration/SKILL.md:450` |
| MM-204 | all | Compass фронта ≤4000 символов (замер len/wc -m) | compass>4000 → PROBLEMS | mechanism | write-compass.py | `skills/orchestration/SKILL.md:463` |
| MM-205 | all | kill --id точным run-id; дубль --id живой → exit 11; второй пишущий ран фронта → exit 13 + multi_write_front | второй writer → exit 13 | mechanism | multi_write_front | `skills/orchestration/SKILL.md:474` |
| MM-206 | all | Второй пишущий ран фронта → exit 13 + чип multi_write_front | два live-писателя одного фронта → отказ dual-writer | mechanism | multi_write_front | `skills/orchestration/SKILL.md:476` |
| MM-207 | all | Jev advisory; не решает за командира; запрещены critic-prefilter/kt-prefilter; не снимает probes_missing; разрешённые id — ровно 19 по routing/jev-table.json, вкл. must-check и advisor-need-check (advisory сверка волны против MUST; не разрешает старт/критиков/чипы) | Jev cannot close gates/HITL | prompt | — | `skills/orchestration/SKILL.md:485` |
| MM-208 | all | ЗАПРЕТЫ Jev: critic-prefilter, kt-prefilter; закрытие волн/probes_missing силой Jev; авто-Approve HITL | запрещённый jev-id → не вызывать | prompt | — | `skills/orchestration/SKILL.md:485` |
| MM-209 | all | План через критиков на каждом уровне; граф/фронт безусловно; задачи — если нетривиально | plan critic wave before start | prompt | — | `skills/orchestration/SKILL.md:517` |
| MM-210 | all | Эскалация: 2 неудачных круга любого уровня → доклад уровнем выше | 2 фейла → эскалация вверх, не третий круг | prompt | — | `skills/orchestration/SKILL.md:524` |
| MM-211 | all | Прогоны несут --front или --no-front; иначе hierarchy≠off → FRONT_REQUIRED exit 8 | запуск без front/no-front → exit 8 | mechanism | FRONT_REQUIRED | `skills/orchestration/SKILL.md:534` |
| MM-212 | all | warn_runs: FRONT_BUDGET_WARN продолжается; hard>0 used>hard → BUDGET_HARD exit 7; lock busy → exit 9 | BUDGET_HARD → exit 7 | mechanism | FRONT_BUDGET_WARN | `skills/orchestration/SKILL.md:547` |
| MM-213 | all | Компас ТОЛЬКО сессионный; НИКОГДА не пиши в общий .orchestration/compass.md | запись в общий compass → запрещено | mechanism | write-compass.py | `skills/orchestration/SKILL.md:594` |
| MM-214 | all | Лимиты compass: сессия ≤8500; фронт/полковник ≤4000; exit 2 — файл не пишется | превышение лимита → exit 2 + pending | mechanism | write-compass.py | `skills/orchestration/SKILL.md:605` |
| MM-215 | all | Воронка write-compass.py обязательна всем уровням; прямой Write/Edit в compass — нарушение (ловушка №48) | прямой Write compass → PreToolUse deny | mechanism | write-compass.py | `skills/orchestration/SKILL.md:609` |
| MM-216 | all | Гейт SECRETS_IN_PROMPT: паттерны ключей в промте → exit 5, процесс не стартовал | секрет в промте → exit 5 | mechanism | SECRETS_IN_PROMPT | `skills/orchestration/references/FLOW.md:62` |
| MM-217 | all | Закрытый фронт status∈cancelled\|rejected → FRONT_CLOSED exit 6 | запуск на closed front → exit 6 | mechanism | FRONT_CLOSED | `skills/orchestration/references/FLOW.md:67` |
| MM-218 | all | План любого уровня: без Вердикт OK критиков плана — не старт | план PROBLEMS → ремонт плана | mechanism | waves_no_critic | `skills/orchestration/references/FLOW.md:71` |
| MM-219 | all | Перед фиксацией split: jev need-split/split-quality; иначе «без советников: выбора нет» | split without advice needs explicit mark | prompt | — | `skills/orchestration/references/planning.md:47` |
| MM-220 | all | Одна маленькая задача = один агент; никогда «A, потом B, заодно C» в одном (ловушка №4) | пакет A+B+C одному → ловушка №4 | prompt | — | `skills/orchestration/references/planning.md:74` |
| MM-221 | all | Приёмка=функция: текст/артефакт/чип-в-норме доказательством НЕ является | зелёный чип ≠ приёмка | mechanism | probes_missing | `skills/orchestration/references/planning.md:246` |
| MM-222 | all | Блок пробы в карточке кода/фикса обязателен; нет → нудж + probes_missing | нет проба/оракул/полигон → probes_missing | mechanism | probes_missing | `skills/orchestration/references/planning.md:250` |
| MM-223 | all | narrative не самообъявляемый; артефакт-оракул narrative быть не может | класс narrative без основания → нарушение | prompt | — | `skills/orchestration/references/planning.md:255` |
| MM-224 | all | Квитанция probe-receipt.md tool-only; ручной Write/Edit = violation | ручная правка probe-receipt → violation | mechanism | probe-receipt.py | `skills/orchestration/references/planning.md:259` |
| MM-225 | all | Снятие probes_missing только валидной квитанцией; дифф гасящий чип → chip_silenced | гашение чипа без причины → chip_silenced | mechanism | chip_silenced | `skills/orchestration/references/planning.md:276` |
| MM-226 | all | orch-lint: подавления --exit-zero/noqa как способ погасить приёмку запрещены | подавление линта для приёмки → запрещено | mechanism | orch-lint | `skills/orchestration/references/planning.md:280` |
| MM-227 | all | Jev probe-sufficiency только advisory — никогда не разрешает/запрещает закрытие | probe-sufficiency → не гейт закрытия | prompt | — | `skills/orchestration/references/planning.md:282` |
| MM-228 | all | Oracle-класс: обязательна пара проба-green + проба-red на /tmp-полигоне | oracle-дифф без green+red → недостаточно | prompt | — | `skills/orchestration/references/planning.md:286` |
| MM-229 | all | Импровизированные роли с головы запрещены; только _index.md или фабрика | роль не из каталога → запрещена | prompt | — | `skills/orchestration/references/planning.md:304` |
| MM-234 | all | Смерть/нестарт надзорного рана → чип supervision_dead ≤60 с (обёртка/сторож/скан, дедуп) | kill надзора молча → чип ≤60 с | mechanism | supervision_dead+tests/test_supervision_dead.py | `skills/orchestration/references/planning.md:157` |
| MM-235 | all | Инвариант машинной секции приказа фронта без валидной квитанции §3 (cmd_sha256+front+generator) → чип invariants_not_run | инвариант приказа не прогнан → invariants_not_run | mechanism | invariants_not_run+tests/test_invariants_gate.py | `skills/orchestration/references/planning.md:292` |
| MM-236 | all | done при красных чипах волн → отказ close-гейта с перечнем; обход (vim/панель) → чип front_closed_red | закрытие с красным чипом → отказ с перечнем | mechanism | close_blockers/save_fronts+tests/test_close_gate.py | `skills/orchestration/references/planning.md:298` |

