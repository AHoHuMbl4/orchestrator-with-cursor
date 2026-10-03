---
id: dont-summy-xargs-ne-stdin
type: DON'T
кому: commander
когда: release
категория: запуск
run-ref: 050ce29
---

## суть
Пересбор SHA256SUMS пайпом «git ls-files | sort | sha256sum > SHA256SUMS» БЕЗ аргументов = sha256sum хеширует STDIN (весь список имён одной строкой «-»), манифест из 293+ строк схлопывается в одну; install-local на клоне падает EXIT=1. Ловушка мигрирует через lossy-запись команды в handoff/памяти командующего (живой прецедент 050ce29, пойман клон-гейтом до раздачи).

## как чинить
Канон пересбора: `git -c core.quotepath=off ls-files | grep -v '^SHA256SUMS$' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS`. Доказательство после КАЖДОГО пересбора: свежий `git clone` → `sha256sum -c` (все OK) → `HOME=<tmp> bash install-local.sh` → EXIT=0. Команду в handoff/доки — только целиком, с `xargs`.
