---
id: do-klon-tmp-target-proverka
type: DO
кому: executor
когда: acceptance
категория: запуск
run-ref: F-DEBTS-DB6
---

## правило
Золотой паттерн проверки установщика: клон в /tmp → TARGET туда → проверка.

## пример
Канон: git 39cfc88 (гард TARGET∈KIT), 14faae5 (тест-режим без живых конфигов).
bash /tmp/<clone>/install-local.sh из cwd=/tmp/<target>.
