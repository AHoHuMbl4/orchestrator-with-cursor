---
id: dont-ne-zapuskat-progony-s-front-f-debts-da-i
type: DON'T
кому: general
когда: retro
категория: процессы
run-ref: prosecutor-auto-F-DEBTS-6
---

## ловушка
Не запускать прогоны с front=F-DEBTS-DA: id нет в fronts.json (phantom + counter used=2).
Все прогоны линии F-DEBTS — только --front F-DEBTS; ключ автопрокурора = id фронта графа.

## признак
journal start с front вне fronts.json; counters/front-runs-F-DEBTS-DA.json жив при отсутствии id.
