---
id: dont-secrets-in-prompt
type: DON'T
кому: wrapper
когда: launch
категория: промты
trap: 43
---

## ловушка
Секрет (ключ/токен) вклеен в текст промта.

## признак
SECRETS_IN_PROMPT в логе, exit 5.

## обход
Секреты только в cursor.key / ENV; обёртки сканируют.
