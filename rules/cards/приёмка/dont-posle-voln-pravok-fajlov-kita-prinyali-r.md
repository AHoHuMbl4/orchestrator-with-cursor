---
id: dont-posle-voln-pravok-fajlov-kita-prinyali-r
type: DON'T
кому: executor
когда: retro
категория: приёмка
run-ref: F-DEBTS-DB-W2c
---

## ловушка
После волн правок файлов кита приняли работу без сверки rules/manifest.json и корневого SHA256SUMS.

## признак
install-local падает на `sha256sum -c SHA256SUMS` (хеш manifest или новой карточки не совпадает).

## обход
До приёмки: обновить SHA256SUMS для нового .md карточки и ./rules/manifest.json; `sha256sum -c` → OK.
