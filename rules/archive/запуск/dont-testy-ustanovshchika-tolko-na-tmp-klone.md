---
id: dont-testy-ustanovshchika-tolko-na-tmp-klone
type: DON'T
кому: executor
когда: retro
категория: запуск
run-ref: F-DEBTS-DB6-PLAN3-C1
---

## ловушка
Тесты установщика/скриптов с TARGET — только на /tmp-клоне.
Установка из живого репо сеёт артефакты и рвёт суммы.

## признак
git status живого репо грязный после install-local/теста;
SHA256SUMS/артефакты появились в дереве кита.

## обход
git clone репо в /tmp → TARGET туда → проверка.
Живой репо не использовать как TARGET/cwd установки.
