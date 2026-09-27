---
id: dont-priemka-po-nalichiyu-artefakta-fajla-kvi
type: DON'T
кому: executor
когда: acceptance
категория: приёмка
run-ref: OWNER-INCIDENT-20260927
---

## ловушка
Приёмка по наличию артефакта/файла квитанции без проверки структуры §3 и своего прогона.

## признак
Квитанция-проза без cmd/exit или exit≠оракулу принята как снятие чипа.

## обход
Валидируй поля probe/cmd/exit/oracle_match/ts/critic_id/artifact; гоняй пробу сам.
