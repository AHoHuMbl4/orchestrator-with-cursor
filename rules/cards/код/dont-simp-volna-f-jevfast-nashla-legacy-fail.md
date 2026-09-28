---
id: dont-simp-volna-f-jevfast-nashla-legacy-fail
type: DON'T
кому: executor
когда: retro
категория: код
run-ref: F-JEVFAST-SIMP
---

## суть
SIMP-волна F-JEVFAST нашла legacy fail-open БЕЗ fallback (мёртвая ветка): у fail-open пути обязан быть явный fallback-результат, иначе «молчаливое ничего» выглядит как ответ. Починено в волне (dbbec1e), но ретро-карточка не была заведена сразу — чип rules_no_retro поймал.

## как чинить
Каждая волна с PROBLEMS-end обязана закрывать цикл карточкой в той же волне (ретро-шаг — не опционально); fail-open всегда с явным fallback в выводе.
