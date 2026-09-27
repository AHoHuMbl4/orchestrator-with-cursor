---
id: dont-runtime-hit-in-repo
type: DON'T
кому: executor
когда: retro
категория: маршрутизация
run-ref: F-RULES-R4
---

## ловушка
Runtime-счётчики hit в коммиченых файлах репо (rules/manifest.json).

## признак
record_hit грязнит git; Jev-точки/архитектура теряют единый источник правды.

## обход
hit только в .orchestration/counters/rules-hits.json; manifest.hit игнорировать.
