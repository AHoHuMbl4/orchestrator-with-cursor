---
id: do-scan-secrets
type: DO
кому: wrapper
когда: launch
категория: промты
---

## правило
Единый сканер секретов — orchlib.scan_secrets до старта обёртки.

## пример
bin/orchlib.py:scan_secrets; run-exec/run-cloud зовут его (F-SEC 0f78577, 6de0d9a).
