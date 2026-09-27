---
id: dont-bg-timeout
type: DON'T
кому: wrapper
когда: launch
категория: запуск
trap: 19
---

## ловушка
Фоновый лаунчер сгорел на своём таймауте.

## признак
Длинный прогон в Bash-фоне движка; pid/exit неизвестны.

## обход
Дольше ~8 мин — run-exec --detach или yield-after.
