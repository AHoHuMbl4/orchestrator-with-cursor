# MUSTMAP holes — status=prompt (MM-C1)

Всего prompt-дыр: 150
Маршрут фикса: детектор/чип в orchlib → **MM-C2**; гейт/вклейка в reground → **MM-C3**.
Трёхполный вердикт: (а) детектор/чип | (б) гейт/вклейка | (в) принимаемый риск.

## MM-001 → commander
- text: Читай planning.md перед нарезкой нетривиальной и при каждом replan
- check: planning.md before nontrivial split
- source: `skills/orchestration/SKILL.md:11`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «planning.md before nontrivial split»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-001` в компас/промт `commander` из skills/orchestration/SKILL.md:11; post-wave critic сверяет check «planning.md before nontrivial split»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-003 → commander
- text: Изоляция state: 1 проект = 1 папка с .orchestration; чужое дерево = подхват чужого state; STATE БЕЗ ПРОЕКТА — остановиться
- check: хук STATE БЕЗ ПРОЕКТА → стоп и развести папки
- source: `skills/orchestration/SKILL.md:24`
- вердикт: **(а)** детектор/чип: session-entry / state-isolation check — сигнал: старт без session-entry или cross-project state; порог: HARD; где: orchlib|run-exec; маршрут → MM-C2 (если упирается в run-exec — эскалация владения)
- вердикт: **(б)** гейт/вклейка: reground MUST-блок commander «истина на диске / session-entry»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-004 → commander
- text: HANDOFF .orchestration/handoff.md ≤2000 символов; обновляет командующий на границах волн и перед закрытием сессии
- check: границы волн/закрытие → handoff обновлён ≤2000
- source: `skills/orchestration/SKILL.md:30`
- вердикт: **(а)** детектор/чип: session-entry / state-isolation check — сигнал: старт без session-entry или cross-project state; порог: HARD; где: orchlib|run-exec; маршрут → MM-C2 (если упирается в run-exec — эскалация владения)
- вердикт: **(б)** гейт/вклейка: reground MUST-блок commander «истина на диске / session-entry»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-006 → commander
- text: Иерархия промтов — не импровизируй; каскад домен→поддомен→роль только по _index.md, не листать ~146 файлов
- check: выбор роли → только _index.md каскадом
- source: `skills/orchestration/SKILL.md:43`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «выбор роли → только _index.md каскадом»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-006` в компас/промт `commander` из skills/orchestration/SKILL.md:43; post-wave critic сверяет check «выбор роли → только _index.md каскадом»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-007 → commander
- text: Не листай ~146 файлов ролей — только каталог _index.md
- check: поиск роли → только _index.md
- source: `skills/orchestration/SKILL.md:51`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «поиск роли → только _index.md»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-007` в компас/промт `commander` из skills/orchestration/SKILL.md:51; post-wave critic сверяет check «поиск роли → только _index.md»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-008 → commander
- text: Роли нет — фабрика по _template.md + строка в _index.md; роль остаётся навсегда
- check: нет роли → фабрика по шаблону и _index.md
- source: `skills/orchestration/SKILL.md:54`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «нет роли → фабрика по шаблону и _index.md»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-008` в компас/промт `commander` из skills/orchestration/SKILL.md:54; post-wave critic сверяет check «нет роли → фабрика по шаблону и _index.md»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-009 → commander
- text: В промте роли обязательно заполняй АРТЕФАКТ — приёмка замером по нему
- check: промт роли → поле АРТЕФАКТ заполнено
- source: `skills/orchestration/SKILL.md:57`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «промт роли → поле АРТЕФАКТ заполнено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-009` в компас/промт `commander` из skills/orchestration/SKILL.md:57; post-wave critic сверяет check «промт роли → поле АРТЕФАКТ заполнено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-010 → commander
- text: Критики волны — всегда шаблон критика из библиотеки; критик не задаёт вопросов и не видит лог исполнителя
- check: критик видит лог → нарушение
- source: `skills/orchestration/SKILL.md:59`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-012 → commander
- text: Режим auto: роль code/ → код-задача; иначе не-код; объяви владельцу одной строкой
- check: старт пачки → объявить режим одной строкой
- source: `skills/orchestration/SKILL.md:70`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «старт пачки → объявить режим одной строкой»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-012` в компас/промт `commander` из skills/orchestration/SKILL.md:70; post-wave critic сверяет check «старт пачки → объявить режим одной строкой»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-014 → commander
- text: Кураторство контекста в промте local-cursor НЕ нужно — агент сам читает/правит ФС
- check: лишнее кураторство CLI → не нужно
- source: `skills/orchestration/SKILL.md:79`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «лишнее кураторство CLI → не нужно»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-014` в компас/промт `commander` из skills/orchestration/SKILL.md:79; post-wave critic сверяет check «лишнее кураторство CLI → не нужно»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-016 → commander
- text: Cloud-код-протокол — только явный fallback при недоступности CLI, не авто-выбор
- check: код через cloud → только явный fallback
- source: `skills/orchestration/SKILL.md:86`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «код через cloud → только явный fallback»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-016` в компас/промт `commander` из skills/orchestration/SKILL.md:86; post-wave critic сверяет check «код через cloud → только явный fallback»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-021 → commander
- text: Молчаливый переход на субагентов запрещён всегда; вопросы недоступны → пауза≥10м + доклад текстом + разреженные ретраи
- check: молча subagents → запрещено
- source: `skills/orchestration/SKILL.md:113`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «молча subagents → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-021` в компас/промт `commander` из skills/orchestration/SKILL.md:113; post-wave critic сверяет check «молча subagents → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-022 → commander
- text: Молчаливый переход на субагентов запрещён всегда
- check: нет явного да → субагенты не включаются
- source: `skills/orchestration/SKILL.md:116`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-023 → commander
- text: Авто-режим без вопросов: Assumptions → compass ДО волн → раздел Допущения в финале; on_cursor_fail ask → субагенты НЕ молча
- check: вопросы недоступны → Assumptions+доклад, не silent switch
- source: `skills/orchestration/SKILL.md:122`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «вопросы недоступны → Assumptions+доклад, не silent switch»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-023` в компас/промт `commander` из skills/orchestration/SKILL.md:122; post-wave critic сверяет check «вопросы недоступны → Assumptions+доклад, не silent switch»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-025 → commander
- text: Любая задача через fresh-исполнителя; сам не исполняешь; напрямую — только мета-вопросы о сессии
- check: задача владельца → только через исполнителя
- source: `skills/orchestration/SKILL.md:137`
- вердикт: **(а)** детектор/чип: session-entry / state-isolation check — сигнал: старт без session-entry или cross-project state; порог: HARD; где: orchlib|run-exec; маршрут → MM-C2 (если упирается в run-exec — эскалация владения)
- вердикт: **(б)** гейт/вклейка: reground MUST-блок commander «истина на диске / session-entry»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-027 → commander
- text: Промт проверяющего содержит ТОЛЬКО артефакт+критерий+адресат; ЗАПРЕЩЕНЫ карты «где проверить»
- check: промт критика → без карт локаций/grep-директив
- source: `skills/orchestration/SKILL.md:155`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- вердикт: **(б)** гейт/вклейка: reground вставляет MUST-строку адресату `commander` перед run; маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-028 → commander
- text: Механика не опциональна: роль ТОЛЬКО через _index.md; промт ТОЛЬКО из шаблона (исключение — ремонтный follow-up)
- check: пропуск шаблона вне follow-up → ошибка курса
- source: `skills/orchestration/SKILL.md:182`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- вердикт: **(б)** гейт/вклейка: reground вставляет MUST-строку адресату `commander` перед run; маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-029 → commander
- text: Оптимизировать механику запрещено; шаг невозможен → отступление в compass + доклад; строка Механика: в отчёте волны
- check: нет строки Механика: → критики ловят
- source: `skills/orchestration/SKILL.md:185`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-030 → commander
- text: Критики исследований проверяют утверждения с URL; слабые — выкинуть или пометить
- check: утверждение без URL → выкинуть/пометить
- source: `skills/orchestration/SKILL.md:197`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-032 → commander
- text: Косяк волны: возврат ТОЛЬКО git-механиками (reset/revert), не чинить поверх
- check: чинить поверх чекпоинта → запрещено
- source: `skills/orchestration/SKILL.md:211`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «чинить поверх чекпоинта → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-032` в компас/промт `commander` из skills/orchestration/SKILL.md:211; post-wave critic сверяет check «чинить поверх чекпоинта → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-033 → commander
- text: Пересбор SHA256SUMS — не зона commit_wave, только командующий
- check: агент пересобрал SHA256SUMS → вне зоны
- source: `skills/orchestration/SKILL.md:219`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «агент пересобрал SHA256SUMS → вне зоны»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-033` в компас/промт `commander` из skills/orchestration/SKILL.md:219; post-wave critic сверяет check «агент пересобрал SHA256SUMS → вне зоны»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-035 → commander
- text: При старте иерархии командующий создаёт PROJECT.md; docs-keeper ведёт перезаписью, не дописыванием
- check: нет PROJECT.md при иерархии → создать
- source: `skills/orchestration/SKILL.md:239`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «нет PROJECT.md при иерархии → создать»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-035` в компас/промт `commander` из skills/orchestration/SKILL.md:239; post-wave critic сверяет check «нет PROJECT.md при иерархии → создать»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-036 → commander
- text: Готов = процесс завершился И критерий подтверждён замером; со слов исполнителя — никогда
- check: принятие со слов → запрещено
- source: `skills/orchestration/SKILL.md:262`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «принятие со слов → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-036` в компас/промт `commander` из skills/orchestration/SKILL.md:262; post-wave critic сверяет check «принятие со слов → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-037 → commander
- text: Фейл — один перезапуск; второй подряд — разбери или доложи; завис повторно — стоп ветки
- check: второй фейл без доклада → нарушение
- source: `skills/orchestration/SKILL.md:264`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «второй фейл без доклада → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-037` в компас/промт `commander` из skills/orchestration/SKILL.md:264; post-wave critic сверяет check «второй фейл без доклада → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-038 → commander
- text: Завис — стоп, разбор, перезапуск; повторный завис — стоп ветки, доклад
- check: повторный stall → стоп ветки
- source: `skills/orchestration/SKILL.md:266`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «повторный stall → стоп ветки»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-038` в компас/промт `commander` из skills/orchestration/SKILL.md:266; post-wave critic сверяет check «повторный stall → стоп ветки»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-039 → commander
- text: stall_after EXIT 124 retry; max_wall EXIT 125 без retry; частые таймауты = дробить, не поднимать лимиты
- check: таймауты → replan мельче
- source: `skills/orchestration/SKILL.md:268`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «таймауты → replan мельче»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-039` в компас/промт `commander` из skills/orchestration/SKILL.md:268; post-wave critic сверяет check «таймауты → replan мельче»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-040 → commander
- text: Частые таймауты = ошибка декомпозиции: дробить, а не поднимать лимиты
- check: частые timeout → replan мельче, не ↑timeout
- source: `skills/orchestration/SKILL.md:279`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «частые timeout → replan мельче, не ↑timeout»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-040` в компас/промт `commander` из skills/orchestration/SKILL.md:279; post-wave critic сверяет check «частые timeout → replan мельче, не ↑timeout»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-041 → commander
- text: Замер-гигиена: SHA в выводе; не глушить вывод проверок; ключ-мис = кривая проба
- check: measurement prints SHA; no >/dev/null on checks
- source: `skills/orchestration/SKILL.md:283`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-042 → commander
- text: Перед пачкой — фундамент planning.md; неоднозначность → СТОП/вопросы или Assumptions
- check: пачка без фундамента → не стартовать
- source: `skills/orchestration/SKILL.md:287`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «пачка без фундамента → не стартовать»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-042` в компас/промт `commander` из skills/orchestration/SKILL.md:287; post-wave critic сверяет check «пачка без фундамента → не стартовать»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-044 → commander
- text: Авто при превышении порога: Assumptions, не вечный стоп
- check: auto over threshold → Assumptions continue
- source: `skills/orchestration/SKILL.md:295`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «auto over threshold → Assumptions continue»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-044` в компас/промт `commander` из skills/orchestration/SKILL.md:295; post-wave critic сверяет check «auto over threshold → Assumptions continue»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-045 → commander
- text: Параллельный залп — только на независимых задачах; пишущие — по одной на область
- check: пересечение файлов пишущих → сериализация
- source: `skills/orchestration/SKILL.md:298`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «пересечение файлов пишущих → сериализация»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-045` в компас/промт `commander` из skills/orchestration/SKILL.md:298; post-wave critic сверяет check «пересечение файлов пишущих → сериализация»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-046 → commander
- text: Синтез N артефактов — отдельная волна, не оркестратор; report-synthesizer; synthesizer только reconciliation
- check: оркестратор сам сшил отчёт → нарушение
- source: `skills/orchestration/SKILL.md:309`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «оркестратор сам сшил отчёт → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-046` в компас/промт `commander` из skills/orchestration/SKILL.md:309; post-wave critic сверяет check «оркестратор сам сшил отчёт → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-047 → commander
- text: Ремонтный follow-up — тому же agent-id; единственный случай без шаблона; >2 ремонтных кругов → стоп и доклад
- check: 3+ repair → стоп/доклад
- source: `skills/orchestration/SKILL.md:333`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «3+ repair → стоп/доклад»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-047` в компас/промт `commander` из skills/orchestration/SKILL.md:333; post-wave critic сверяет check «3+ repair → стоп/доклад»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-048 → commander
- text: hierarchy=off — иерархия ЗАПРЕЩЕНА, всегда плоский режим
- check: hierarchy=off → фронты не создавать
- source: `skills/orchestration/SKILL.md:350`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «hierarchy=off → фронты не создавать»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-048` в компас/промт `commander` из skills/orchestration/SKILL.md:350; post-wave critic сверяет check «hierarchy=off → фронты не создавать»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-049 → commander
- text: Приёмка уровня: командир читает ТОЛЬКО отчёт-выжимку; сырьё и нижний код не читаются
- check: командир уровня → нет чтения сырья
- source: `skills/orchestration/SKILL.md:372`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «командир уровня → нет чтения сырья»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-049` в компас/промт `commander` из skills/orchestration/SKILL.md:372; post-wave critic сверяет check «командир уровня → нет чтения сырья»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-051 → commander
- text: Без OK наблюдателя следующая волна не стартует
- check: observer OK gate before next wave
- source: `skills/orchestration/SKILL.md:391`
- вердикт: **(а)** детектор/чип `observer-heartbeat`: сигнал: observer heartbeat просрочен; порог: WARN; где: panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-053 → commander
- text: Инспектор вызовов на КТ: вход journal.jsonl; отчёт прокурору и владельцу
- check: КТ без инспектора при запуске → пробел процесса
- source: `skills/orchestration/SKILL.md:404`
- вердикт: **(а)** детектор/чип `fronts_no_prosecutor`: сигнал: фронт без prosecutor-контура; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-062 → commander
- text: Чек-лист статуса: развилки/руки/компас — директива генералу, не делать самому
- check: командующий чинит compass фронта сам → нарушение
- source: `skills/orchestration/SKILL.md:487`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «командующий чинит compass фронта сам → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-062` в компас/промт `commander` из skills/orchestration/SKILL.md:487; post-wave critic сверяет check «командующий чинит compass фронта сам → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-068 → commander
- text: Качество > токены: не режь осознанную сложность ради экономии
- check: don't cut quality for token save
- source: `skills/orchestration/SKILL.md:554`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «don't cut quality for token save»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-068` в компас/промт `commander` из skills/orchestration/SKILL.md:554; post-wave critic сверяет check «don't cut quality for token save»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-069 → commander
- text: Зависшее окно: stalled НЕ cancelled; новый генерал: order+compass+журнал; бегущие не перезапускать
- check: stalled resume = acceptance window
- source: `skills/orchestration/SKILL.md:559`
- вердикт: **(а)** детектор/чип `general_resume_chain`: сигнал: general resume-chain нарушение; порог: WARN; где: orchlib|panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-070 → commander
- text: По умолчанию НИЧЕГО не спрашивай у владельца; меню — только если позвал «меню»
- check: лишние вопросы меню → нарушение
- source: `skills/orchestration/SKILL.md:570`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «лишние вопросы меню → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-070` в компас/промт `commander` из skills/orchestration/SKILL.md:570; post-wave critic сверяет check «лишние вопросы меню → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-071 → commander
- text: Пишущая задача parallel_per_task=1 + критики; spike — отдельное решение не по дефолту
- check: spike по дефолту → запрещено
- source: `skills/orchestration/SKILL.md:577`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-073 → commander
- text: COMPASS ПРЕВЫШЕН фронта: НЕ ужимать самому; директива генералу через воронку; повтор → stalled
- check: overflow → order general rewrite
- source: `skills/orchestration/SKILL.md:643`
- вердикт: **(а)** детектор/чип `COMPASS_OVERFLOW`: сигнал: компас > лимита; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-078 → commander
- text: order.md пишет командующий; генерал RO; compass фронта пишет генерал через воронку
- check: генерал пишет order фронта → нарушение
- source: `skills/orchestration/references/FLOW.md:85`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «генерал пишет order фронта → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-078` в компас/промт `commander` из skills/orchestration/references/FLOW.md:85; post-wave critic сверяет check «генерал пишет order фронта → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-079 → commander
- text: Нет измеримого критерия — не декомпозируй: сначала добудь критерий
- check: no measurable done → no split
- source: `skills/orchestration/references/planning.md:9`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «no measurable done → no split»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-079` в компас/промт `commander` из skills/orchestration/references/planning.md:9; post-wave critic сверяет check «no measurable done → no split»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-080 → commander
- text: Запиши в compass цель, критерий, границы и TODO-чеклист до нарезки
- check: compass has goal+criterion+TODO before split
- source: `skills/orchestration/references/planning.md:13`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «compass has goal+criterion+TODO before split»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-080` в компас/промт `commander` из skills/orchestration/references/planning.md:13; post-wave critic сверяет check «compass has goal+criterion+TODO before split»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-082 → commander
- text: Никогда не задавай вопросов исполнителям: нерешённое — владельцу или Assumptions
- check: вопрос исполнителю → запрещено
- source: `skills/orchestration/references/planning.md:29`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «вопрос исполнителю → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-082` в компас/промт `commander` из skills/orchestration/references/planning.md:29; post-wave critic сверяет check «вопрос исполнителю → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-083 → commander
- text: Перед разбиением jev need-split/split-quality; низкий quality → пересобрать; иначе без советников: выбора нет
- check: split без jev/пометки → нарушение
- source: `skills/orchestration/references/planning.md:47`
- вердикт: **(а)** детектор/чип `advisors_without_scouts`: сигнал: advisors без scouts; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-084 → commander
- text: Низкий split-quality → пересобрать до критиков плана
- check: low split-quality → re-split
- source: `skills/orchestration/references/planning.md:53`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-085 → commander
- text: Артефакт всегда обязателен кроме чисто мета-диалога
- check: non-meta tasks need artifact path
- source: `skills/orchestration/references/planning.md:56`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «non-meta tasks need artifact path»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-085` в компас/промт `commander` из skills/orchestration/references/planning.md:56; post-wave critic сверяет check «non-meta tasks need artifact path»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-087 → commander
- text: Пишущая задача — всегда ОДИН исполнитель; parallel_per_task только READ-ONLY
- check: writers: parallel_per_task=1
- source: `skills/orchestration/references/planning.md:69`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «writers: parallel_per_task=1»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-087` в компас/промт `commander` из skills/orchestration/references/planning.md:69; post-wave critic сверяет check «writers: parallel_per_task=1»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-088 → commander
- text: depends_on в state НЕ заводим — рёбра в TODO compass
- check: no machine depends_on in task state
- source: `skills/orchestration/references/planning.md:89`
- вердикт: **(а)** детектор/чип: session-entry / state-isolation check — сигнал: старт без session-entry или cross-project state; порог: HARD; где: orchlib|run-exec; маршрут → MM-C2 (если упирается в run-exec — эскалация владения)
- вердикт: **(б)** гейт/вклейка: reground MUST-блок commander «истина на диске / session-entry»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-089 → commander
- text: Валидация декомпозиции: ведёт к критерию / нет дыр / нет дублей / самодостаточна; иначе переделай
- check: хоть один «нет» → переделка до запуска
- source: `skills/orchestration/references/planning.md:95`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «хоть один «нет» → переделка до запуска»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-089` в компас/промт `commander` из skills/orchestration/references/planning.md:95; post-wave critic сверяет check «хоть один «нет» → переделка до запуска»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-090 → commander
- text: Фундамент до волн обязателен: gate→декомпозиция→роли→критерии критиков→прогноз
- check: пачка без §3.6 → не стартовать
- source: `skills/orchestration/references/planning.md:107`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-091 → commander
- text: PROJECT.md обновляется перезаписью, не дописыванием; расхождение = PROBLEMS
- check: docs-keeper сверка → расхождение PROBLEMS
- source: `skills/orchestration/references/planning.md:142`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «docs-keeper сверка → расхождение PROBLEMS»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-091` в компас/промт `commander` из skills/orchestration/references/planning.md:142; post-wave critic сверяет check «docs-keeper сверка → расхождение PROBLEMS»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-095 → commander
- text: narrative не самообъявляемый; артефакт-оракул narrative быть не может
- check: narrative class restricted
- source: `skills/orchestration/references/planning.md:255`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «narrative class restricted»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-095` в компас/промт `commander` из skills/orchestration/references/planning.md:255; post-wave critic сверяет check «narrative class restricted»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-096 → commander
- text: Ремонт плана = новая волна критиков плана (не ремонтный follow-up артефакта)
- check: follow-up на план → запрещено
- source: `skills/orchestration/references/planning.md:354`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-098 → commander
- text: Между волнами gate: критерии зелёные замером + сверка с compass
- check: волна без замера/сверки → не закрывать
- source: `skills/orchestration/references/planning.md:416`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «волна без замера/сверки → не закрывать»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-098` в компас/промт `commander` из skills/orchestration/references/planning.md:416; post-wave critic сверяет check «волна без замера/сверки → не закрывать»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-099 → commander
- text: Финальная сшивка — волна синтеза, не текст оркестратора в своём контексте
- check: commander doesn't stitch N arts himself
- source: `skills/orchestration/references/planning.md:422`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «commander doesn't stitch N arts himself»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-099` в компас/промт `commander` из skills/orchestration/references/planning.md:422; post-wave critic сверяет check «commander doesn't stitch N arts himself»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-100 → commander
- text: Волна перекрёстной сверки — только независимые источники; основной источник волн в границах запрещён by design
- check: основной источник в cross-check → by design запрет
- source: `skills/orchestration/references/planning.md:424`
- вердикт: **(а)** детектор/чип `orders_without_basis`: сигнал: приказ/волна без basis; порог: HARD при отсутствии _order_has_basis; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-101 → commander
- text: Partial-fail replan: зелёные не перезапускаются; только красные и зависимые
- check: зелёный узел → не перезапускать
- source: `skills/orchestration/references/planning.md:432`
- вердикт: **(а)** детектор/чип `health_red_chips`: сигнал: red chips без разбора; порог: WARN; где: orchlib|panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-102 → commander
- text: Анти-паттерн: premature decomposition / мелкая нарезка / гигант / нет сверки compass / каскад / полный restart /↑timeout
- check: decomposition anti-patterns avoided
- source: `skills/orchestration/references/planning.md:443`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «decomposition anti-patterns avoided»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-102` в компас/промт `commander` из skills/orchestration/references/planning.md:443; post-wave critic сверяет check «decomposition anti-patterns avoided»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-103 → commander
- text: Анти-паттерн: резать до фиксации цели/критерия
- check: premature decomposition → запрещено
- source: `skills/orchestration/references/planning.md:445`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «premature decomposition → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-103` в компас/промт `commander` из skills/orchestration/references/planning.md:445; post-wave critic сверяет check «premature decomposition → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-104 → commander
- text: Наблюдатель запускается командующим на КТ; запуск обычный НЕ --readonly (нужен Write метки)
- check: observer not --readonly
- source: `skills/orchestration/references/roles/meta/front-observer.md:4`
- вердикт: **(а)** детектор/чип `observer-heartbeat`: сигнал: observer heartbeat просрочен; порог: WARN; где: panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-108 → general
- text: Архитектура-контракты — всегда первая волна фронта
- check: contracts first wave
- source: `skills/orchestration/SKILL.md:418`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-108` в блок MUST для `general` (источник skills/orchestration/SKILL.md:418); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-109 → general
- text: Подзадачи только через полковников; order в colonels/<cid>/order.md; сырьё — только raw-brief
- check: генерал пишет код → нарушение с первого раза
- source: `skills/orchestration/SKILL.md:431`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-109` в блок MUST для `general` (источник skills/orchestration/SKILL.md:431); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-110 → general
- text: Руки генерала НОЛЬ: только обёртки/выжимки/write-compass/order полковникам/доклад; Write/Edit проекта = нарушение с первого раза
- check: general Write/Edit project = violation
- source: `skills/orchestration/SKILL.md:437`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-110` в блок MUST для `general` (источник skills/orchestration/SKILL.md:437); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-112 → general
- text: Активный фронт без записей исполнителей за окно → stalled + новый генерал
- check: нет journal исполнителей → stalled
- source: `skills/orchestration/SKILL.md:448`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-112` в блок MUST для `general` (источник skills/orchestration/SKILL.md:448); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-113 → general
- text: Короткие окна: 1 волна≈1 окно; resume только того же фронта; потолок ~2ч — штатный resume
- check: вечное окно → запрещено
- source: `skills/orchestration/SKILL.md:457`
- вердикт: **(а)** детектор/чип `general_resume_chain`: сигнал: general resume-chain нарушение; порог: WARN; где: orchlib|panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-115 → general
- text: Генералы волны слепы друг к другу НАПРОЧЬ; coordination только через командующего
- check: перекрёстное чтение runs → запрещено
- source: `skills/orchestration/SKILL.md:488`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-115` в блок MUST для `general` (источник skills/orchestration/SKILL.md:488); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-116 → general
- text: Прямой запуск исполнителей генералом — только тривиальные замеры; NEVER для работ
- check: работа ≠ замер → только через полковника
- source: `skills/orchestration/SKILL.md:509`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-116` в блок MUST для `general` (источник skills/orchestration/SKILL.md:509); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-117 → general
- text: Закрытие фронта: compass≤4000 + order + advisor + швы
- check: front done criteria unmet → PROBLEMS/stalled
- source: `skills/orchestration/references/FLOW.md:78`
- вердикт: **(а)** детектор/чип `advisors_without_scouts`: сигнал: advisors без scouts; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-118 → general
- text: order.md пишет командующий; генерал только читает
- check: general never writes front order.md
- source: `skills/orchestration/references/FLOW.md:85`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-118` в блок MUST для `general` (источник skills/orchestration/references/FLOW.md:85); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-119 → general
- text: Генерал/полковник сырьё не читают — только raw-brief ≤15 строк вверх
- check: чтение сырья командиром → запрещено
- source: `skills/orchestration/references/FLOW.md:113`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `general` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-119` в блок MUST для `general` (источник skills/orchestration/references/FLOW.md:113); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-120 → general
- text: Бегущие волны при новом генерале НЕ перезапускать; start без end = бежит
- check: дубль бегущей волны → запрещено
- source: `skills/orchestration/references/FLOW.md:115`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-120` в блок MUST для `general` (источник skills/orchestration/references/FLOW.md:115); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-121 → general
- text: Границы волны: СВОИ пути + запрет чужих; назначение фронтов непересекающееся
- check: пересечение файлов фронтов → ошибка графа
- source: `skills/orchestration/references/planning.md:195`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-121` в блок MUST для `general` (источник skills/orchestration/references/planning.md:195); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-122 → general
- text: САМОисполнение работы полковника/исполнителя запрещено — стоп и делегируй
- check: general self-exec → stop+delegate
- source: `skills/orchestration/references/planning.md:334`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-122` в блок MUST для `general` (источник skills/orchestration/references/planning.md:334); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-123 → general
- text: Оркестратору по часам не рвать генерала — только по границам работ или дрейфу
- check: таймер окна → не повод рвать руками
- source: `skills/orchestration/references/planning.md:338`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-123` в блок MUST для `general` (источник skills/orchestration/references/planning.md:338); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-124 → general
- text: Инструменты генерала исчерпывающие (а–д); всё остальное запрещено; Write/Edit проекта = нарушение с первого раза
- check: Shell-правка кода → сразу стоп
- source: `skills/orchestration/references/roles/meta/front-general.md:7`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-124` в блок MUST для `general` (источник skills/orchestration/references/roles/meta/front-general.md:7); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-125 → general
- text: Write/Edit/Shell правок проекта = нарушение с первого раза — остановись и запусти полковника
- check: urge to edit → stop+delegate colonel
- source: `skills/orchestration/references/roles/meta/front-general.md:13`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-125` в блок MUST для `general` (источник skills/orchestration/references/roles/meta/front-general.md:13); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-126 → general
- text: Чужие runs/вердикты параллельных фронтов НЕ читать; с параллельными НЕ координироваться — через командующего
- check: координация с соседним генералом → запрещено
- source: `skills/orchestration/references/roles/meta/front-general.md:16`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `general` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-126` в блок MUST для `general` (источник skills/orchestration/references/roles/meta/front-general.md:16); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-128 → general
- text: Возобновление: сверь journal start/end; не дублируй бегущее; память прошлого окна не предполагай
- check: resume from journal not memory
- source: `skills/orchestration/references/roles/meta/front-general.md:34`
- вердикт: **(а)** детектор/чип `general_resume_chain`: сигнал: general resume-chain нарушение; порог: WARN; где: orchlib|panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-129 → general
- text: НИКОГДА подглядывание к параллельным линиям; самовольная остановка по чужим результатам
- check: координация/стоп по чужому → анти-паттерн
- source: `skills/orchestration/references/roles/meta/front-general.md:55`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-129` в блок MUST для `general` (источник skills/orchestration/references/roles/meta/front-general.md:55); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-130 → colonel
- text: 1 полковник = 1 подзадача; NEVER порождать вложенных полковников; 2 круга → эскалация
- check: вложенный полковник → запрещён
- source: `skills/orchestration/SKILL.md:496`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-130` в блок MUST для `colonel` (источник skills/orchestration/SKILL.md:496); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-131 → colonel
- text: Свита обязательна: advisor, plan critics, acceptance critics, raw-brief, git/docs/simplicity wardens
- check: colonel suite roles present
- source: `skills/orchestration/SKILL.md:501`
- вердикт: **(а)** детектор/чип `advisors_without_scouts`: сигнал: advisors без scouts; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-132 → colonel
- text: Значимая код-волна: simplicity-warden иначе PROBLEMS
- check: simplicity gate after significant code
- source: `skills/orchestration/references/FLOW.md:77`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-132` в блок MUST для `colonel` (источник skills/orchestration/references/FLOW.md:77); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-133 → colonel
- text: colonels order пишет генерал; mini-compass пишет полковник через воронку
- check: полковник пишет свой order → нарушение
- source: `skills/orchestration/references/FLOW.md:87`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-133` в блок MUST для `colonel` (источник skills/orchestration/references/FLOW.md:87); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-134 → colonel
- text: Рамки: 3–5 работ; order только читает; mini-compass ≤4000; не порождает полковников
- check: полковник пишет свой order → запрещено
- source: `skills/orchestration/references/planning.md:297`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-134` в блок MUST для `colonel` (источник skills/orchestration/references/planning.md:297); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-135 → colonel
- text: Сам код/инфру руками не пишешь — командуешь cursor-исполнителями
- check: полковник Write кода → запрещено
- source: `skills/orchestration/references/roles/meta/front-colonel.md:5`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-135` в блок MUST для `colonel` (источник skills/orchestration/references/roles/meta/front-colonel.md:5); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-136 → colonel
- text: Не порождать вложенных полковников; сырьё самому не читать; чужие runs НЕ читать
- check: вложенный полковник → НИКОГДА
- source: `skills/orchestration/references/roles/meta/front-colonel.md:9`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-136` в блок MUST для `colonel` (источник skills/orchestration/references/roles/meta/front-colonel.md:9); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-138 → colonel
- text: НИКОГДА самому исполнять работы Write/Edit/Shell вместо делегирования
- check: руки вместо ролей → анти-паттерн
- source: `skills/orchestration/references/roles/meta/front-colonel.md:40`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-138` в блок MUST для `colonel` (источник skills/orchestration/references/roles/meta/front-colonel.md:40); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-139 → executor
- text: Код: правки через CLI run-exec; артефакт=дифф; критики=дифф+критерий
- check: code wave uses run-exec + diff critics
- source: `skills/orchestration/SKILL.md:201`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-140 → executor
- text: Правки продукта — только через коммиты; мимо гита запрещено
- check: правка продукта → только коммит
- source: `skills/orchestration/SKILL.md:210`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-140` в блок MUST для `executor` (источник skills/orchestration/SKILL.md:210); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-142 → executor
- text: Промт самодостаточен: цель/критерий; точные файлы; что НЕ трогать; путь/команда дословно; артефакт
- check: промт без НЕ трогать → неполнота
- source: `skills/orchestration/SKILL.md:253`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-142` в блок MUST для `executor` (источник skills/orchestration/SKILL.md:253); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-146 → executor
- text: Одна маленькая задача = один агент; «A потом B» = две карточки (ловушка №4)
- check: no multi-task cards for one agent
- source: `skills/orchestration/references/planning.md:74`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-146` в блок MUST для `executor` (источник skills/orchestration/references/planning.md:74); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-147 → executor
- text: Oracle-класс: обязательна пара проба-green + проба-red на /tmp-полигоне
- check: oracle diffs need green+red probes
- source: `skills/orchestration/references/planning.md:286`
- вердикт: **(а)** детектор/чип `probes_missing`: сигнал: code-волна без probe-receipt; порог: WARN→HARD по политике фронта; где: orchlib+run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-148 → executor
- text: Минимальное изменение; ГРАНИЦЫ дословно; результат строго в АРТЕФАКТ; приёмка замером по файлу
- check: выход за ГРАНИЦЫ → анти-паттерн
- source: `skills/orchestration/references/roles/code/coder.md:6`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-148` в блок MUST для `executor` (источник skills/orchestration/references/roles/code/coder.md:6); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-149 → executor
- text: Артефакт строго в указанный путь; приёмка замером по нему
- check: artifact path exact
- source: `skills/orchestration/references/roles/code/coder.md:10`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `executor` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-149` в блок MUST для `executor` (источник skills/orchestration/references/roles/code/coder.md:10); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-150 → executor
- text: Процесс: прочитай целиком → diff → минимальная правка → тесты → показать diff
- check: tests run before done
- source: `skills/orchestration/references/roles/code/coder.md:12`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-150` в блок MUST для `executor` (источник skills/orchestration/references/roles/code/coder.md:12); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-151 → executor
- text: Выполни без уточнений пока критерий зелёный; блокирует — запиши и завершись
- check: вопросы вместо работы → нарушение
- source: `skills/orchestration/references/roles/code/coder.md:15`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-151` в блок MUST для `executor` (источник skills/orchestration/references/roles/code/coder.md:15); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-152 → executor
- text: Не переписывай чужой код под вкус; не добавляй зависимости без нужды; не меняй окружение без разрешения
- check: pip без разрешения → анти-паттерн
- source: `skills/orchestration/references/roles/code/coder.md:24`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-152` в блок MUST для `executor` (источник skills/orchestration/references/roles/code/coder.md:24); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-153 → critic
- text: Критики волны — всегда шаблон критика-скептика/ревьюера; не задаёт вопросов и не видит лог
- check: критик → шаблон из библиотеки, без лога исполнителя
- source: `skills/orchestration/SKILL.md:59`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-154 → critic
- text: Критики — всегда свежие read-only агенты того же режима, что и исполнители
- check: критик → fresh readonly, тот же режим
- source: `skills/orchestration/SKILL.md:99`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-155 → critic
- text: Критик видит ТОЛЬКО дифф + критерий, никогда — лог/ход мыслей исполнителя
- check: критик читает лог → нарушение
- source: `skills/orchestration/SKILL.md:179`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-156 → critic
- text: Замер-гигиена: SHA кода в выводе; не глушить вывод; ключ-мис = проба кривая
- check: приёмочный замер → SHA+живые логи+схема
- source: `skills/orchestration/SKILL.md:283`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-157 → critic
- text: Формат проблем: ПРОБЛЕМА: место — суть — как чинить; нет проблем — только Вердикт: OK
- check: отчёт критика → префикс ПРОБЛЕМА: или OK
- source: `skills/orchestration/SKILL.md:306`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-158 → critic
- text: На выжимках запрещена проверка «0 новых фактов»; ложные жалобы — grep по полным артефактам
- check: UNVERIFIABLE на выжимках, не violation
- source: `skills/orchestration/SKILL.md:316`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-158` в блок MUST для `critic` (источник skills/orchestration/SKILL.md:316); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-164 → critic
- text: промт критика/аудитора/наблюдателя: артефакт+критерий; без карт локаций
- check: карта в промте проверяющего → слепое пятно
- source: `skills/orchestration/references/planning.md:321`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- вердикт: **(б)** гейт/вклейка: reground вставляет MUST-строку адресату `critic` перед run; маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-165 → critic
- text: Видишь только дифф и критерий, не ход автора; вопросов не задавать; ничего не править — только отчёт
- check: критик правит код → нарушение
- source: `skills/orchestration/references/roles/code/code-reviewer.md:6`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-166 → critic
- text: Вопросов не задавать; ничего не править — только отчёт
- check: read-only critic; no questions
- source: `skills/orchestration/references/roles/code/code-reviewer.md:9`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-167 → critic
- text: Каждой проблеме — файл:строка и серьёзность; пункты с префиксом ПРОБЛЕМА:; Вердикт первой строкой
- check: проблема без ПРОБЛЕМА: → нарушение формата
- source: `skills/orchestration/references/roles/code/code-reviewer.md:12`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `critic` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-167` в блок MUST для `critic` (источник skills/orchestration/references/roles/code/code-reviewer.md:12); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-168 → critic
- text: Вердикт первой строкой; каждый пункт PROBLEMS с префиксом ПРОБЛЕМА:; Механика последней
- check: ПРОБЛЕМА: prefix mandatory
- source: `skills/orchestration/references/roles/code/code-reviewer.md:14`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `critic` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-168` в блок MUST для `critic` (источник skills/orchestration/references/roles/code/code-reviewer.md:14); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-169 → critic
- text: Анти-паттерн: не выходи за ГРАНИЦЫ; не меняй; не выдумывай данные
- check: правка/выдумка → анти-паттерн
- source: `skills/orchestration/references/roles/code/code-reviewer.md:16`
- вердикт: **(в)** Принимаемый риск: семантическое/стилевое требование роли; жёсткий детектор даст ложные HARD и сломает легитимные краткие ответы. Остаётся в промте роли + critic-pass.
- итог: принимаемый риск (механика не вводится)

## MM-170 → critic
- text: Анти-паттерн: не пересказывай дифф; не пиши «в целом хорошо» без конкретики
- check: общий OK без мест → анти-паттерн
- source: `skills/orchestration/references/roles/code/code-reviewer.md:24`
- вердикт: **(в)** Принимаемый риск: семантическое/стилевое требование роли; жёсткий детектор даст ложные HARD и сломает легитимные краткие ответы. Остаётся в промте роли + critic-pass.
- итог: принимаемый риск (механика не вводится)

## MM-171 → observer
- text: Наблюдатели: власти ноль — только ПРОБЛЕМА: главнокомандующему; 4 прицела; не исполняют
- check: наблюдатель запускает волны → нарушение
- source: `skills/orchestration/SKILL.md:408`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-171` в блок MUST для `observer` (источник skills/orchestration/SKILL.md:408); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-173 → observer
- text: Власти ноль: не запускает исполнителей, не правит код/compass; писать только артефакт и heartbeat
- check: observer правил compass → НИКОГДА
- source: `skills/orchestration/references/roles/meta/front-observer.md:3`
- вердикт: **(а)** детектор/чип `observer-heartbeat`: сигнал: observer heartbeat просрочен; порог: WARN; где: panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-174 → observer
- text: Писать разрешено только свой артефакт и observer-heartbeat.txt; НЕ сырые run.log
- check: запись вне артефакта/heartbeat → запрещена
- source: `skills/orchestration/references/roles/meta/front-observer.md:10`
- вердикт: **(а)** детектор/чип `observer-heartbeat`: сигнал: observer heartbeat просрочен; порог: WARN; где: panel
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-175 → observer
- text: Hands-on сигнал: active+mtime растёт без journal runs → ПРОБЛЕМА руки генерала
- check: hands-on detection → ПРОБЛЕМА
- source: `skills/orchestration/references/roles/meta/front-observer.md:16`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-175` в блок MUST для `observer` (источник skills/orchestration/references/roles/meta/front-observer.md:16); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-176 → observer
- text: Правда докладов: 2–5 утверждений перезамерь; без свидетеля — проблема
- check: нарратив без замера → ПРОБЛЕМА
- source: `skills/orchestration/references/roles/meta/front-observer.md:19`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-176` в блок MUST для `observer` (источник skills/orchestration/references/roles/meta/front-observer.md:19); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-177 → observer
- text: Вердикт OK|PROBLEMS|BLOCKED; ПРОБЛЕМА: фронт|Hands-on|шов|доклад|цель — суть — что делать
- check: отчёт без вердикта → не принято
- source: `skills/orchestration/references/roles/meta/front-observer.md:23`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `observer` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-177` в блок MUST для `observer` (источник skills/orchestration/references/roles/meta/front-observer.md:23); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-178 → observer
- text: НИКОГДА принимать доклад генерала без выборочного замера
- check: доклад без замера → анти-паттерн
- source: `skills/orchestration/references/roles/meta/front-observer.md:35`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-178` в блок MUST для `observer` (источник skills/orchestration/references/roles/meta/front-observer.md:35); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-179 → prosecutor
- text: Канал prosecutor/ пишет прокурор; читает только командующий
- check: генерал читает prosecutor/ → нарушение
- source: `skills/orchestration/references/FLOW.md:91`
- вердикт: **(а)** детектор/чип `fronts_no_prosecutor`: сигнал: фронт без prosecutor-контура; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-180 → prosecutor
- text: Подотчётен ТОЛЬКО командующему; власть ноль; писать ТОЛЬКО в sessions/.../prosecutor/
- check: доклад генералам → НИКОГДА
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:3`
- вердикт: **(а)** детектор/чип `fronts_no_prosecutor`: сигнал: фронт без prosecutor-контура; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-181 → prosecutor
- text: 7 прицелов: cross-access / координация / дубли / пакет / compass мимо воронки / потеря приказа / без обоснования
- check: прицел не закрыт → не OK
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:5`
- вердикт: **(а)** детектор/чип `orders_without_basis`: сигнал: приказ/волна без basis; порог: HARD при отсутствии _order_has_basis; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-182 → prosecutor
- text: Писать ТОЛЬКО в sessions/<sid>/prosecutor/ или указанный файл; не в общие места
- check: запись в места генералов → запрещена
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:8`
- вердикт: **(а)** детектор/чип `fronts_no_prosecutor`: сигнал: фронт без prosecutor-контура; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-183 → prosecutor
- text: Пакетная загрузка одному генералу — нарушение 1=1=1; пометь, не останавливай
- check: пакет в приказе → ПРОБЛЕМА командующему
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:15`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-183` в блок MUST для `prosecutor` (источник skills/orchestration/references/roles/meta/front-prosecutor.md:15); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-184 → prosecutor
- text: НИКОГДА вмешиваться (стоп/правка/лечение); НИКОГДА сообщать генералам; НИКОГДА поверхностный вердикт
- check: лечение дублей снизу → НИКОГДА
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:29`
- вердикт: **(а)** детектор/чип (лёгкий): post-run проверка артефакта адресата `prosecutor` (наличие вердикт-строки / запрет write paths); где: orchlib post-check; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-184` в блок MUST для `prosecutor` (источник skills/orchestration/references/roles/meta/front-prosecutor.md:29); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-185 → prosecutor
- text: journal end без front — by-design не ПРОБЛЕМА; старт после max_rounds при доке эскалации — by-design
- check: end без front → не ПРОБЛЕМА
- source: `skills/orchestration/references/roles/meta/front-prosecutor.md:36`
- вердикт: **(б)** гейт/вклейка: reground вшивает императив `MM-185` в блок MUST для `prosecutor` (источник skills/orchestration/references/roles/meta/front-prosecutor.md:36); сигнал нарушения — отсутствие маркера/чеклиста в artifact; порог: WARN на post-run critic. Маршрут фикса → MM-C3
- итог: закрываемая {б}; фикс: reground→MM-C3

## MM-186 → all
- text: Никогда не переходи на субагентов без явного «да» владельца в этой сессии
- check: переход на subagents без да → запрещено
- source: `skills/orchestration/SKILL.md:98`
- вердикт: **(а)** детектор/чип: session-entry / state-isolation check — сигнал: старт без session-entry или cross-project state; порог: HARD; где: orchlib|run-exec; маршрут → MM-C2 (если упирается в run-exec — эскалация владения)
- вердикт: **(б)** гейт/вклейка: reground MUST-блок commander «истина на диске / session-entry»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-187 → all
- text: Исчерпание лимита/квоты = стоп и доклад вверх; вышестоящий не замещает нижестоящего руками
- check: руками сделал работу нижестоящего → запрещено
- source: `skills/orchestration/SKILL.md:117`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «руками сделал работу нижестоящего → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-187` в компас/промт `all` из skills/orchestration/SKILL.md:117; post-wave critic сверяет check «руками сделал работу нижестоящего → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-188 → all
- text: Слепота: параллельные агенты волны стерильны друг к другу; подглядывание исключено; картину видит только оркестратор
- check: агент читает чужой run → нарушение слепоты
- source: `skills/orchestration/SKILL.md:148`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-189 → all
- text: Промт проверяющего ТОЛЬКО: артефакт + критерий цели + адресат; ЗАПРЕЩЕНЫ карты файлов/grep/ожидаемые выводы
- check: карта «где проверить» в промте → запрещено
- source: `skills/orchestration/SKILL.md:154`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «карта «где проверить» в промте → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-189` в компас/промт `all` из skills/orchestration/SKILL.md:154; post-wave critic сверяет check «карта «где проверить» в промте → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-190 → all
- text: Манифест границ фронта — артефакт, давать целиком разрешено; карты поиска по системе запрещены
- check: owns manifest OK; search maps forbidden
- source: `skills/orchestration/SKILL.md:161`
- вердикт: **(а)** детектор/чип `owns.py`: сигнал: запись вне owns; порог: HARD; где: orchlib/owns
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-191 → all
- text: Принцип снайпера: знаешь ТОЛЬКО свою задачу/критерий; «нет» → вниз с критерием; чужие нюансы не поднимать
- check: разбор чужого уровня → запрещено
- source: `skills/orchestration/SKILL.md:165`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «разбор чужого уровня → запрещено»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-191` в компас/промт `all` из skills/orchestration/SKILL.md:165; post-wave critic сверяет check «разбор чужого уровня → запрещено»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-192 → all
- text: Запрещено поднимать нюансы чужих уровней наверх и тащить детали вниз
- check: контекст чужого уровня → не поднимать/не тащить
- source: `skills/orchestration/SKILL.md:171`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «контекст чужого уровня → не поднимать/не тащить»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-192` в компас/промт `all` из skills/orchestration/SKILL.md:171; post-wave critic сверяет check «контекст чужого уровня → не поднимать/не тащить»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-193 → all
- text: Косяк волны: возврат ТОЛЬКО git-механиками (reset/revert), не чинить поверх
- check: откат волны → reset/revert, не самодельный скрипт
- source: `skills/orchestration/SKILL.md:211`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «откат волны → reset/revert, не самодельный скрипт»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-193` в компас/промт `all` из skills/orchestration/SKILL.md:211; post-wave critic сверяет check «откат волны → reset/revert, не самодельный скрипт»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-197 → all
- text: Первая строка рабочего промта: роль: <путь>; run-exec/run-cloud — передавай --role; extractor — первые 3 строки
- check: шапка роли отсутствует (не смоук) → нарушение
- source: `skills/orchestration/SKILL.md:245`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «шапка роли отсутствует (не смоук) → нарушение»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-197` в компас/промт `all` из skills/orchestration/SKILL.md:245; post-wave critic сверяет check «шапка роли отсутствует (не смоук) → нарушение»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-200 → all
- text: Замер-гигиена: SHA кода в выводе; не глушить вывод проверок; ключ-мис = кривая проба
- check: >/dev/null на проверке → нарушение гигиены
- source: `skills/orchestration/SKILL.md:283`
- вердикт: **(а)** детектор/чип `SECRETS_IN_PROMPT`: сигнал: секрет в промте; порог: HARD; где: orchlib|run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-201 → all
- text: Вердикт: OK | PROBLEMS | BLOCKED + блок доказательств; оркестратор принимает ТОЛЬКО по вердикту+замеру
- check: текст без доказательств → не принято
- source: `skills/orchestration/SKILL.md:327`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «текст без доказательств → не принято»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-201` в компас/промт `all` из skills/orchestration/SKILL.md:327; post-wave critic сверяет check «текст без доказательств → не принято»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-202 → all
- text: Принцип лестницы: уровень сужает задачу; наверх ТОЛЬКО принятый замером результат выжимкой
- check: вверх → только выжимка принятого, не сырьё
- source: `skills/orchestration/SKILL.md:366`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «вверх → только выжимка принятого, не сырьё»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-202` в компас/промт `all` из skills/orchestration/SKILL.md:366; post-wave critic сверяет check «вверх → только выжимка принятого, не сырьё»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-207 → all
- text: Jev advisory; не решает за командира; запрещены critic-prefilter/kt-prefilter; не снимает probes_missing
- check: Jev cannot close gates/HITL
- source: `skills/orchestration/SKILL.md:483`
- вердикт: **(а)** детектор/чип `probes_missing`: сигнал: code-волна без probe-receipt; порог: WARN→HARD по политике фронта; где: orchlib+run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-208 → all
- text: ЗАПРЕТЫ Jev: critic-prefilter, kt-prefilter; закрытие волн/probes_missing силой Jev; авто-Approve HITL
- check: запрещённый jev-id → не вызывать
- source: `skills/orchestration/SKILL.md:485`
- вердикт: **(а)** детектор/чип `probes_missing`: сигнал: code-волна без probe-receipt; порог: WARN→HARD по политике фронта; где: orchlib+run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-209 → all
- text: План через критиков на каждом уровне; граф/фронт безусловно; задачи — если нетривиально
- check: plan critic wave before start
- source: `skills/orchestration/SKILL.md:517`
- вердикт: **(а)** детектор/чип `waves_no_critic`: сигнал: волна без критика где канон требует; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-210 → all
- text: Эскалация: 2 неудачных круга любого уровня → доклад уровнем выше
- check: 2 фейла → эскалация вверх, не третий круг
- source: `skills/orchestration/SKILL.md:524`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «2 фейла → эскалация вверх, не третий круг»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-210` в компас/промт `all` из skills/orchestration/SKILL.md:524; post-wave critic сверяет check «2 фейла → эскалация вверх, не третий круг»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-219 → all
- text: Перед фиксацией split: jev need-split/split-quality; иначе «без советников: выбора нет»
- check: split without advice needs explicit mark
- source: `skills/orchestration/references/planning.md:47`
- вердикт: **(а)** детектор/чип `advisors_without_scouts`: сигнал: advisors без scouts; порог: WARN; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-220 → all
- text: Одна маленькая задача = один агент; никогда «A, потом B, заодно C» в одном (ловушка №4)
- check: пакет A+B+C одному → ловушка №4
- source: `skills/orchestration/references/planning.md:74`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «пакет A+B+C одному → ловушка №4»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-220` в компас/промт `all` из skills/orchestration/references/planning.md:74; post-wave critic сверяет check «пакет A+B+C одному → ловушка №4»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-223 → all
- text: narrative не самообъявляемый; артефакт-оракул narrative быть не может
- check: класс narrative без основания → нарушение
- source: `skills/orchestration/references/planning.md:255`
- вердикт: **(а)** детектор/чип `orders_without_basis`: сигнал: приказ/волна без basis; порог: HARD при отсутствии _order_has_basis; где: orchlib
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-227 → all
- text: Jev probe-sufficiency только advisory — никогда не разрешает/запрещает закрытие
- check: probe-sufficiency → не гейт закрытия
- source: `skills/orchestration/references/planning.md:282`
- вердикт: **(а)** детектор/чип `probes_missing`: сигнал: code-волна без probe-receipt; порог: WARN→HARD по политике фронта; где: orchlib+run-exec
- итог: закрываемая {а}; фикс: orchlib→MM-C2

## MM-228 → all
- text: Oracle-класс: обязательна пара проба-green + проба-red на /tmp-полигоне
- check: oracle-дифф без green+red → недостаточно
- source: `skills/orchestration/references/planning.md:286`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «oracle-дифф без green+red → недостаточно»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-228` в компас/промт `all` из skills/orchestration/references/planning.md:286; post-wave critic сверяет check «oracle-дифф без green+red → недостаточно»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

## MM-229 → all
- text: Импровизированные роли с головы запрещены; только _index.md или фабрика
- check: роль не из каталога → запрещена
- source: `skills/orchestration/references/planning.md:292`
- вердикт: **(а)** детектор/чип (кандидат): сигнал по check «роль не из каталога → запрещена»; порог: WARN; где: orchlib; маршрут → MM-C2
- вердикт: **(б)** гейт/вклейка: reground добавляет MUST `MM-229` в компас/промт `all` из skills/orchestration/references/planning.md:292; post-wave critic сверяет check «роль не из каталога → запрещена»; маршрут → MM-C3
- итог: закрываемая {а, б}; фикс: orchlib→MM-C2; reground→MM-C3

---

## эскалация: требует расширения владения

Дыры, чей фикс упирается вне текущего owns волны (write-compass / run-exec / routing / rules / SHA256SUMS / journal):

- MM-003: вне owns → run-exec — «Изоляция state: 1 проект = 1 папка с .orchestration; чужое дерево = подхват чужо»
- MM-004: вне owns → run-exec — «HANDOFF .orchestration/handoff.md ≤2000 символов; обновляет командующий на грани»
- MM-023: вне owns → write-compass — «Авто-режим без вопросов: Assumptions → compass ДО волн → раздел Допущения в фина»
- MM-025: вне owns → run-exec — «Любая задача через fresh-исполнителя; сам не исполняешь; напрямую — только мета-»
- MM-029: вне owns → write-compass — «Оптимизировать механику запрещено; шаг невозможен → отступление в compass + докл»
- MM-033: вне owns → SHA256SUMS — «Пересбор SHA256SUMS — не зона commit_wave, только командующий»
- MM-053: вне owns → journal — «Инспектор вызовов на КТ: вход journal.jsonl; отчёт прокурору и владельцу»
- MM-062: вне owns → write-compass — «Чек-лист статуса: развилки/руки/компас — директива генералу, не делать самому»
- MM-069: вне owns → write-compass — «Зависшее окно: stalled НЕ cancelled; новый генерал: order+compass+журнал; бегущи»
- MM-073: вне owns → write-compass — «COMPASS ПРЕВЫШЕН фронта: НЕ ужимать самому; директива генералу через воронку; по»
- MM-078: вне owns → write-compass — «order.md пишет командующий; генерал RO; compass фронта пишет генерал через ворон»
- MM-080: вне owns → write-compass — «Запиши в compass цель, критерий, границы и TODO-чеклист до нарезки»
- MM-088: вне owns → run-exec, write-compass — «depends_on в state НЕ заводим — рёбра в TODO compass»
- MM-098: вне owns → write-compass — «Между волнами gate: критерии зелёные замером + сверка с compass»
- MM-102: вне owns → write-compass — «Анти-паттерн: premature decomposition / мелкая нарезка / гигант / нет сверки com»
- MM-110: вне owns → write-compass — «Руки генерала НОЛЬ: только обёртки/выжимки/write-compass/order полковникам/докла»
- MM-112: вне owns → journal — «Активный фронт без записей исполнителей за окно → stalled + новый генерал»
- MM-117: вне owns → write-compass — «Закрытие фронта: compass≤4000 + order + advisor + швы»
- MM-128: вне owns → journal — «Возобновление: сверь journal start/end; не дублируй бегущее; память прошлого окн»
- MM-133: вне owns → write-compass — «colonels order пишет генерал; mini-compass пишет полковник через воронку»
- MM-134: вне owns → write-compass — «Рамки: 3–5 работ; order только читает; mini-compass ≤4000; не порождает полковни»
- MM-139: вне owns → run-exec — «Код: правки через CLI run-exec; артефакт=дифф; критики=дифф+критерий»
- MM-173: вне owns → write-compass — «Власти ноль: не запускает исполнителей, не правит код/compass; писать только арт»
- MM-175: вне owns → journal — «Hands-on сигнал: active+mtime растёт без journal runs → ПРОБЛЕМА руки генерала»
- MM-181: вне owns → write-compass — «7 прицелов: cross-access / координация / дубли / пакет / compass мимо воронки / »
- MM-185: вне owns → journal — «journal end без front — by-design не ПРОБЛЕМА; старт после max_rounds при доке э»
- MM-186: вне owns → run-exec — «Никогда не переходи на субагентов без явного «да» владельца в этой сессии»
- MM-197: вне owns → run-exec — «Первая строка рабочего промта: роль: <путь>; run-exec/run-cloud — передавай --ro»
