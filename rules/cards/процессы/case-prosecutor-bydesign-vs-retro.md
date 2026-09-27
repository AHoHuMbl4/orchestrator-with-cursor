---
id: case-prosecutor-bydesign-vs-retro
type: CASE
кому: commander
когда: chip-red
категория: процессы
run-ref: prosecutor-auto-F-E2E-ALL-6
---

## суть
Системная причина перезарядки rules_no_retro: автопрокурор помечал by-design факты кита (end без front; старт после max_rounds=3 при задокументированной эскалации) как ПРОБЛЕМА → каждый аудит закрывающегося фронта перезаряжал чип. Устранено: в роль meta/front-prosecutor добавлен блок «By-design факты кита (не ПРОБЛЕМА)» (коммит волны FIX4-S1); урок задокументирован. Ссылки: case-end-records-front-on-start, case-e2e-audit-retro-2.
