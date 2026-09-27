---
id: dont-avtoprokurorskie-id-prosecutor-auto-f-pe
type: DON'T
кому: wrapper
когда: retro
категория: запуск
run-ref: prosecutor-auto-F-DEBTS-2
---

## ловушка
Автопрокурорские id (prosecutor-auto-F-*) переиспользуются во втором state репо.
Коллизия с уже закрытыми id канона ломает journal/приёмку.

## признак
Один id start/end в project-.orchestration при закрытом том же id в каноне.

## обход
Один state=/root/.orchestration; ключ автопрокурора = id фронта графа, без dual-state.
