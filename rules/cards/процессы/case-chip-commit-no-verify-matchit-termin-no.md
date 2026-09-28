---
id: case-chip-commit-no-verify-matchit-termin-no
type: CASE
кому: commander
когда: chip-red
категория: процессы
run-ref: commit_no_verify-knownlimit
---

## суть
Чип commit_no_verify матчит термин --no-verify в ТЕКСТАХ (доки/артефакты доктрин описывают флаг) — ложные срабатывания на документацию; git-история без обходных коммитов (замер: HEAD доков чист).

## как чинить
План: матч только по journal/tool-call событиям реального git-вызова; до фикса текстовые срабатывания закрываются замером git-истории.
