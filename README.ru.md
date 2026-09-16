# orchestrator-with-cursor

[English](README.md) | **Русский**

Оркестрация агентов для **Claude Code, Codex CLI и Kimi Code**: любую задачу
исполняет свежий субагент с чистым контекстом (или cursor-agent / облако
Cursor), результат проверяют волны свежих критиков, а хуки движка периодически
возвращают агента к задаче и параметрам — машиной, мимо «мнения» модели.

**3 движка · установка в 2 строки · работает даже без python (режим «только скилл»)**

## Требования

- Один из агентов: Claude Code, Codex CLI или Kimi Code.
- **Python 3.6+** — нужен для хуков, панели настроек и скриптов.
  - Linux: `sudo apt install python3` (обычно уже есть)
  - macOS: `brew install python` или python.org
  - Windows: `winget install Python.Python.3.12` (при установке отметить «Add to PATH»)
- Питона нет вообще? Установщик всё равно поставит скилл (это просто
  инструкции) и честно сообщит, что пропущено; после установки python
  повторите установщик — он добавит хуки и панель.

## Быстрый старт

В рабочей папке (где запускаете claude / codex / kimi):

```bash
git clone https://github.com/AHoHuMbl4/orchestrator-with-cursor.git orchestration-kit
bash orchestration-kit/install-local.sh
```

Нет git — скачайте zip репозитория, распакуйте как `orchestration-kit/` и
выполните ту же вторую строку. Установщик идемпотентен, проверяет контрольные
суммы и не трогает чужие настройки (kimi-конфиг — с бэкапом `.bak-orch`).

| Движок | Первый запуск | Проверка |
|---|---|---|
| Claude Code | ничего — работает сразу | спросить агента «какие скиллы доступны?» → `orchestration` |
| Codex CLI | один раз: `/hooks` → доверить хуки orchestration (обязательный trust) | `$orchestration` доступен |
| Kimi Code | `/reload` в живой сессии или рестарт kimi | `/orchestration` доступен |

Настройки: `./panel.sh` → http://127.0.0.1:8765 (сам займёт свободный порт).
В панели: тумблер вкл/выкл, задача (compass), исполнители на задачу, критики,
круги ревью, таймаут, интервал сверки, модели, поле токена Cursor.

## Токен Cursor (для режима исполнителей «облако Cursor»)

1. Зайдите на **cursor.com** под своим аккаунтом → **Dashboard / Settings →
   API → API Keys** → **Create key** → скопируйте ключ (показывается один раз).
2. Нужен **платный план Cursor**; оплата — по токенам использованных моделей,
   а не за время.
3. Вставьте ключ в поле **«Токен Cursor»** на главном экране панели (панель
   сохранит его в `.orchestration/cursor.key` — файл в gitignore) — либо
   задайте переменную окружения `CURSOR_API_KEY`.
4. Переключите режим исполнителей на `cursor-cloud` — и оркестратор будет
   запускать исполнителей как облачных Cursor-агентов (локальная установка
   Cursor не нужна).

Не нужны облачные исполнители — пропустите: дефолтный режим (субагенты движка)
не требует никаких ключей.

## Как это работает

- Пишете задачу словами. Скилл `orchestration` превращает агента в
  оркестратора: декомпозиция → свежий исполнитель на под-задачу → N свежих
  критиков на результат (критик видит результат и критерий, а не ход мыслей
  исполнителя) → круги до схождения → приёмка замером.
- Хуки (Claude/Codex: SessionStart, UserPromptSubmit, PostToolUse; Kimi:
  UserPromptSubmit, SessionHeartbeat) вклеивают актуальные параметры при
  каждом сообщении и напоминают перечитать задачу каждые `reground.every_min`
  минут — агент не может тихо «забыть» режим или уйти в сторону.
- Режимы исполнителей: субагенты движка (дефолт), локальный
  `cursor-agent --model auto`, облако Cursor (токен — в панели).
- Параметры — в `.orchestration/params.json`, изменения хук доносит при
  следующем сообщении. Тумблер `orchestration.enabled=false` возвращает агенту
  прямую работу, хуки замолкают.
- Меню в чате: «меню» или `/orch-menu` (Claude).

## Ручная установка по движкам (без установщика)

| Движок | Путь скилла | Хуки |
|---|---|---|
| Claude Code | `.claude/skills/orchestration/` (проект) или `~/.claude/skills/` | `.claude/settings.json` (вмерживает установщик) |
| Codex CLI | `.agents/skills/` (репо) или `~/.agents/skills/` | `.codex/hooks.json` → доверить через `/hooks` |
| Kimi Code | `~/.kimi-code/skills/` или `~/.agents/skills/` | блок `[[hooks]]` в `~/.kimi-code/config.toml` |
| Cursor | `.cursor/skills/` или `~/.cursor/skills/` | — |

## Состав репозитория

```
install-local.sh   установщик (главный вход)
panel/             панель настроек (python3 stdlib, без зависимостей)
bin/reground.py    движок сверки курса (хуки трёх движков)
bin/run-exec.py    запуск локального cursor-agent (промт из файла, EXIT в лог)
bin/run-cloud.py   облачные исполнители Cursor Cloud Agents API
bin/menu.py        детерминированное меню параметров (валидация)
bin/discover.py    снимок доступных моделей/effort-уровней
skills/orchestration/  скилл (SKILL.md + references)
hooks/             сниппеты хуков Claude / Codex / Kimi
SHA256SUMS         контрольные суммы (проверяет установщик)
```

Подробности: [install-notes.md](install-notes.md) · Доктрина и ловушки:
[skills/orchestration/references/](skills/orchestration/references/)

Упаковка под plugin-marketplace (установка одной командой
`/plugin marketplace add` / `codex plugin marketplace add`) — в планах;
канонический путь сегодня — `install-local.sh`.

## Если что-то не работает

- **Kimi не видит скилл** — скиллы регистрируются при старте сессии: откройте
  **новую сессию** (или `/reload`) и вызовите `/skill:orchestration`. Description
  в SKILL.md должен быть YAML-безопасным (без «: » внутри значения) — в репо
  исправлено.
- **Хуки пропали после рестарта приложения** — команды хуков теперь используют
  **абсолютный путь к python**, поэтому переживают рестарт из другого окружения.
  Обновите установку повторным `install-local.sh` (он сам заменит свой старый
  блок в `~/.kimi-code/config.toml`). Хуки Kimi fail-open: упавший хук молчит;
  проверка вручную: `echo '{}' | <python> <kit>/bin/reground.py prompt-submit --engine kimi`.
- **Панель закрывается вместе с терминалом** — запускайте `./panel.sh --bg`
  (фоновый режим, лог в `panel.log`); для доступа по сети поставьте
  `panel.host = 0.0.0.0` в params.

## Лицензия

[MIT](LICENSE)
