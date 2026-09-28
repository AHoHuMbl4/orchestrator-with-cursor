---
id: case-ispolnitel-docs-keeper-zavershilsya-end
type: CASE
кому: commander
когда: chip-red
категория: процессы
run-ref: prosecutor-auto-F-E2E-ALL-7
---

## суть
Исполнитель (docs-keeper) завершился end=PROBLEMS из-за mid-run чипа wave_no_docs: чип считается по mtime журнала, а end самого прогона ещё не записан — во время прогона чип виден, после exit он пуст. Волна принята законно: критик S1-crit=OK + чип после exit пуст (замер прокурора: 18/18). Урок: красный чип в ТЕКСТЕ исполнителя — не критерий; командующий меряет чип после завершения прогона (CAUSE-CLEARED).
