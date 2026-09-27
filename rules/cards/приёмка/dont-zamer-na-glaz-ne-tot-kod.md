---
id: dont-zamer-na-glaz-ne-tot-kod
type: DON'T
кому: commander
когда: acceptance
категория: приёмка
run-ref: F-C4-CASE-20260927
---

## ловушка
DON'T «замер на глаз: не тот код/глушёный вывод/ключи мимо схемы»

## чек-лист
(1) SHA = git -C <полигон> log -1 в выводе замера
(2) вывод не глушить; (3) ключи по схеме orch-lint/manifest (не None/None)
