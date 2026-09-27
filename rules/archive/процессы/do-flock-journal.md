---
id: do-flock-journal
type: DO
кому: wrapper
когда: post-tool
категория: процессы
---

## правило
journal_append под flock; save_fronts через lockdir (F-LOCKS).

## пример
bin/orchlib.py journal_append+fcntl; коммит 54d3308.
