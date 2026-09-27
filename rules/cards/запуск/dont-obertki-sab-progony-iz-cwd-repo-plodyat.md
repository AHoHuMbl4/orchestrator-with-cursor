---
id: dont-obertki-sab-progony-iz-cwd-repo-plodyat
type: DON'T
кому: colonel
когда: retro
категория: запуск
run-ref: prosecutor-auto-F-DEBTS-4
---

## ловушка
Обёртки/саб-прогоны из cwd репо плодят параллельный state repo/.orchestration.
Journal/counters раскалываются: приёмка части id только в project-state.

## признак
Канон /root/.orchestration vs project .orchestration: разные used/счётчики; ACC/FIX/автопрокуроры живут не в том journal.

## обход
Все run-exec и саб-прогоны запускать из cwd=/root (канон state).
Не вести второй .orchestration в дереве репо.
