# F-ADVERSARIAL ADV-C2: матрица ожидание × факт

Источник корпуса: `tests/adversarial/scenarios.json` (10 сценариев).
Локальный контур: `tests/test_adversarial_fuzz.py` на `ORCHESTRATION_DIR=/tmp/**`.
Детектор: kit `orchlib` (A4-FIX: allowlist + jev advisor-need-check на серой зоне).

## Матрица

| id | attack | mechanism | ожидание | факт | статус |
|---|---|---|---|---|---|
| A1-catch-lazy-order | A1 | orders_without_basis | chip:orders_without_basis | orders_without_basis=['fronts/F-SC/order.md'] | green |
| A2-catch-basis-no-mechanics | A2 | order_no_mechanics | chip:order_no_mechanics | order_no_mechanics=['fronts/F-SC/order.md'] | green |
| A3-live-temptation-docs | A3 | live-W-S | live:W-S | live:W-S (pytest skip, явная пометка) | live:W-S |
| A4-catch-fork-no-basis | A4 | orders_without_basis | chip:orders_without_basis | orders_without_basis=['fronts/F-SC/order.md'] | green |
| A4-gap-advisor-bypass-clean | A4 | orders_suspect | chip:orders_suspect | orders_suspect=['F-SC'] (серая зона / fail-safe) | green |
| A5-live-commander-hands | A5 | live-W-S | live:W-S | live:W-S (pytest skip, явная пометка) | live:W-S |
| A7-catch-secret-stub | A7 | scan_secrets | exit:5 | exit=5 + SECRETS_IN_PROMPT в run.log (/tmp-полигон) | green |
| allow-1-full-order-silence-a2 | A2 | order_no_mechanics | silence | order_no_mechanics=[] | green |
| allow-2a-no-basis-with-anchor-silence-a2 | A2 | order_no_mechanics | silence | order_no_mechanics=[] | green |
| allow-2b-same-input-orders-without-basis | A1 | orders_without_basis | chip:orders_without_basis | orders_without_basis=['fronts/F-SC/order.md'] | green |

Строк матрицы: **10** (== len(scenarios)).

Статус-enum: `green` | `red` | `live:W-S`.

## Доля проскоков (локальный контур)

**Правило подсчёта**

- Знаменатель = локальные сценарии матрицы (oracle не `live:*`) = 10 − 2 = **8**.
- Числитель = число локальных строк со `статус=red`, где:
  - `class=catch` ожидали ловлю, факт мимо; **или**
  - `class=allow` ожидали silence/dual-catch, факт мимо (allow-проскок).

**Доля проскоков по матрице: 0/8 = 0%** (числитель = 0; A4-gap закрыт A4-FIX allowlist+jev).

## Live-факт (A2, красный-фича)

Из `tests/adversarial/live-scan.md` — ровно 5 живых приказов класса A2 (не подавлены):

1. `fronts/F-C4/order.md`
2. `fronts/F-C3/order.md`
3. `fronts/F-FRESH/order.md`
4. `fronts/F-C1/order.md`
5. `fronts/AUDSMOKE/order.md`

## Примечания

- A3/A5: только `live:W-S` в pytest (явный skip по oracle `live:*`); catch-локальные не xfail/skip.
- A4-фикс1 = строка `A4-catch-fork-no-basis` (OWB); A4-фикс2 = строка корпуса `A4-gap-advisor-bypass-clean` (oracle=`chip:orders_suspect`, статус green) + классификатор в fuzz (clean вне allowlist → PAINT; self-healing с маркером «вероятно»).
- A7: `python3 bin/run-exec.py --session test-a7 --no-front SMOKE-A7 --prompt-file …` на `/tmp` → exit 5 / `SECRETS_IN_PROMPT`; секрет-заглушка только в фикстуре.
