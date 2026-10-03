---
id: dont-glavkom-pravit-produkt-sam
type: DON'T
кому: commander
когда: any
категория: приёмка
run-ref: W-S2 A5-live (192.168.56.49, /root/adv-ws-poly)
---

## суть
Главком правит tracked-файлы продукта руками вне волн — до чипа kit_dirty_outside_wave был НЕВИДИМ: live-факт W-S2 (A5-red, доля проскоков 1/3) — сигнал отсутствовал полностью (health-ключа нет, commander_hands_active false).

## как чинить
Правки продукта — только через волны/git-warden. Оракул = чип kit_dirty_outside_wave (tracked-правка вне журнал-фикс-точки код-волны → красный список файлов); при сигнале — стоп и ревью «руки ли это». Живой повтор закрыл дыру: 0/3, квитанция ADV-TC4-PROBE. Связь: dont-komandir-rabotaet-rukami-vmesto-fronta; MM-175 (наблюдательский сигнал) — чип делает класс детектируемым состоянием.
