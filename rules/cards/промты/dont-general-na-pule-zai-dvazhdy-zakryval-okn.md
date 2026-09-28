---
id: dont-general-na-pule-zai-dvazhdy-zakryval-okn
type: DON'T
кому: general
когда: task-active
категория: промты
run-ref: agent-15-window-wait
---

## суть
Генерал на пуле ZAI дважды закрывал окно текстом «ожидаю уведомление/фоновые проверки» — субагент НЕ получает уведомлений, воля волн замирала до resume командующего. Причина: модель завершает ход на намерении ждать.

## как чинить
В роли/промте генерала ОБЯЗАТЕЛЕН поллинг Bash-циклами: for i in $(seq 1 60); do sleep 30; run-exec --id X --status | grep -q "pid_alive\": false && break; done — ожидать ТОЛЬКО внутри вызова инструмента; завершение окна без Вердикта = PROBLEMS.
