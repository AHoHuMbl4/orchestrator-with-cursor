---
id: dont-avtoprokuror-volny-zanimaet-pishuschij-r
type: DON'T
кому: commander
когда: chip-red
категория: запуск
run-ref: prosecutor-auto-F-MUSTCHECK-8
---

## суть
Автопрокурор волны занимает пишущий ран фронта → параллельные пишущие прогоны (критики с артефактами, docs-verify) ловят exit 13/multi_write_front — надзор БЛОКИРУЕТ работу, хотя прокурор/наблюдатель по доктрине не конкурируют с волнами. Повторялось audit-3..8 F-MUSTCHECK.

## как чинить
План-фикс: multi_write_front-чип и пишущий ран исключают надзорные роли (meta/front-prosecutor, meta/front-observer) — их артефакты в закрытом канале; командирам до фикса: пишущие волны фронта — после end прокурора, ретраи без kill живого пишущего запрещены.
