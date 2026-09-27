---
id: dont-evristika-puti-vmesto-yavnogo-flaga
type: DON'T
кому: wrapper
когда: launch
категория: процессы
run-ref: INCIDENT-P14-20260927
---

## ловушка
Не угадывай режим/поведение по пути или cwd (напр. /tmp).

## надо
Только явный env/флаг (ORCH_TEST_INSTALL=1 и т.п.); иначе свежие установки ломаются.
