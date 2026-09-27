---
id: dont-compass-todo-posle-gw-post-ok
type: DON'T
кому: general
когда: retro
категория: процессы
run-ref: prosecutor-auto-F-FRESH-7
---

## ловушка
Фронтовый compass остаётся на TODO W0→…→GW-POST после OK сдачи волн/GW-POST (mtime компаса старше journal end).

## надо
После OK волны/GW-POST перезаписать compass через write-compass.py: факт сдачи, следующий шаг или закрытие, ссылка на order.md.
