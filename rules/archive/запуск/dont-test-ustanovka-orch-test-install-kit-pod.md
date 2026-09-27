---
id: dont-test-ustanovka-orch-test-install-kit-pod
type: DON'T
кому: executor
когда: retro
категория: запуск
run-ref: F-DEBTS-DB
---

## ловушка
Тест-установка (ORCH_TEST_INSTALL / KIT под /tmp|/var/tmp) не должна регистрировать хуки в живых конфигах движков.
Не удаляй каталоги, на которые ссылаются конфиги — сначала grep ссылок.

## признак
install-local из /tmp или с ORCH_TEST_INSTALL=1 пишет ~/.claude|~/.codex|~/.kimi-code.
rm -rf /tmp/proof* без grep по конфигам и ~/.agents/**.

## обход
В тест-режиме только проверка + строка TEST INSTALL; TARGET и живые конфиги не трогать.
Перед удалением: grep scope → нет ссылок → rm; есть ссылки → не удалять.
