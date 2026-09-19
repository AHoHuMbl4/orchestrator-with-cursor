# orchestrator-with-cursor

[English](README.md) | **Русский**

Оркестрация агентов для **Claude Code, Codex CLI и Kimi Code**: любую задачу
исполняет свежий исполнитель (**dual-path**: локальный CLI Cursor для код-задач —
прямой доступ к файлам; Cursor Cloud для исследований/без ФС — или субагенты
движка), результат проверяют волны свежих критиков, а хуки движка периодически
возвращают агента к задаче и параметрам — машиной, мимо «мнения» модели.

**3 движка · установка в 2 строки · работает даже без python (режим «только скилл»)**

## Как работает после установки

Просто напишите задачу агенту. Система делает всё сама:

```
Вы: "найди топ-10 бизнес-моделей в edTech"

Агент (автоматически):
  1. Хук вклеивает параметры (машина, не промт)
  2. Compass сессии авто-сеется из шаблона (sessions/<id>/compass.md)
  3. Скилл загружается: агент становится оркестратором
  4. Планирование: декомпозиция → TODO в compass сессии → валидация
  5. Каскадный выбор роли: домен→поддомен→роль (146 узких специалистов)
  6. Исполнитель (dual-path): код (роль из code/) → локальный cursor-agent
     через `run-exec.py` (прямая ФС); исследования/без ФС → Cursor Cloud через
     `run-cloud.py` — или субагенты движка после явного «да» владельца
  7. Критики: 3 свежих скептика проверяют каждый результат
  8. Расхождение? → волна расхождений → арбитры → синтезатор
  9. Приёмка: только замером (файл существует, тест зелёный, URL проверен)
 10. Доклад вам с источниками
```

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

Windows (PowerShell 5.1+ или pwsh), из рабочей папки, куда скопирован/склонирован
репозиторий:

```powershell
git clone -c core.autocrlf=false https://github.com/AHoHuMbl4/orchestrator-with-cursor.git orchestration-kit
powershell -ExecutionPolicy Bypass -File orchestration-kit\install-local.ps1
```

Если суммы не сходятся при установке — вы склонировали с конвертацией концов строк; переклонируйте с `-c core.autocrlf=false`.

Токен и state: панель показывает абсолютный путь к ключу; `.orchestration` ищется от текущей папки вверх — запускайте движок и панель из одной рабочей папки проекта.

`-Global` — уровень пользователя. Повторный запуск безопасен (идемпотентен).

Нет git — скачайте zip репозитория, распакуйте как `orchestration-kit/` и
выполните ту же вторую строку. Установщик идемпотентен, проверяет контрольные
суммы и не трогает чужие настройки (kimi-конфиг — с бэкапом `.bak-orch`).

| Движок | Первый запуск | Проверка |
|---|---|---|
| Claude Code | ничего — работает сразу | спросить агента «какие скиллы доступны?» → `orchestration` |
| Codex CLI | один раз: `/hooks` → доверить хуки orchestration (обязательный trust) | `$orchestration` доступен |
| Kimi Code | `/reload` в живой сессии или рестарт kimi | `/skill:orchestration` доступен |

Настройки: `./panel.sh` → http://127.0.0.1:8765 (сам займёт свободный порт).
В панели: селектор сессий (compass сессий — в основной зоне), тумблер вкл/выкл,
исполнители на задачу (дефолт 3), критики, круги ревью, таймаут, `retry_on_fail`, интервал
сверки, модели, токен Cursor; **«Фронты»** (волны, статусы, JSON-редактор);
**«Расширенные»** — общий стартовый шаблон (`.orchestration/compass.md`,
сохранение с подтверждением, кнопка восстановления стандартного).

## Токен Cursor (для облака / не-код исполнителей)

1. Зайдите на **cursor.com** под своим аккаунтом → **Dashboard / Settings →
   API → API Keys** → **Create key** → скопируйте ключ (показывается один раз).
2. Нужен **платный план Cursor**; оплата — по токенам использованных моделей,
   а не за время.
3. Вставьте ключ в поле **«Токен Cursor»** на главном экране панели (панель
   сохранит его в `.orchestration/cursor.key` — файл в gitignore) — либо
   задайте переменную окружения `CURSOR_API_KEY`.
4. **Dual-path:** код-задачи — локальный CLI (`run-exec.py` + `cursor-agent` в
   PATH). Не-код / исследования — Cursor Cloud через `run-cloud.py` при наличии
   ключа (`auto` или `cursor-cloud`). Явный `execution.executor` в params
   перекрывает авто-маршрутизацию.

Нет ключа и нет локального бинарника для нужного пути? Дефолтный `auto`
**останавливается и спрашивает** (без молчаливого fallback). Субагенты
движка — только после явного «да» (или `execution.executor = subagents`).

## Как это работает

- Пишете задачу словами. Скилл `orchestration` превращает агента в
  оркестратора: декомпозиция → свежий исполнитель на под-задачу → N свежих
  критиков на результат (критик видит результат и критерий, а не ход мыслей
  исполнителя) → круги до схождения → приёмка замером.
- Модель compass: `.orchestration/compass.md` — **общий стартовый шаблон**
  (правка только в панели, **«Расширенные»**, с подтверждением и кнопкой
  восстановления стандартного). Каждая сессия при первом сообщении автоматически
  получает `sessions/<id>/compass.md`; рабочая задача пишется туда. CLI
  `menu.py` шаблон не пишет (`--global-template` → отказ, exit 2).
- Хуки (Claude/Codex: SessionStart, UserPromptSubmit, PostToolUse; Kimi:
  UserPromptSubmit, SessionHeartbeat) вклеивают актуальные параметры при
  каждом сообщении и напоминают перечитать задачу каждые `reground.every_min`
  минут — агент не может тихо «забыть» режим или уйти в сторону.
- Режимы исполнителей (**dual-path**): `auto` маршрутизирует по домену роли —
  код (`code/`) → локальный CLI через `run-exec.py`, если `cursor-agent` в
  PATH; не-код → Cursor Cloud через `run-cloud.py` при наличии ключа; иначе
  стоп и вопрос. Явный `execution.executor` (`local-cursor` / `cursor-cloud` /
  `subagents`) перекрывает авто. Локальный CLI: прямой доступ к файлам
  проекта. Облако: исследования / без ФС, артефакт текстом.
- Параметры — в `.orchestration/params.json`, изменения хук доносит при
  следующем сообщении. Тумблер `orchestration.enabled=false` возвращает агенту
  прямую работу, хуки замолкают.
- Режим иерархии: off (плоский, один оркестратор) / auto (эвристика доктрины) / on (предпочитать фронты для мультиблочных задач) — `orchestration.hierarchy`, по умолчанию auto. Полный выключатель оркестрации — прежний тумблер вкл/выкл (`orchestration.enabled`).
- Предстарт-порог пачки: оркестратор спрашивает перед запуском пачек больше N прогонов (`execution.ask_before_runs`, по умолчанию 20).
- Меню в чате: «меню» или `/orch-menu` (Claude).

## Иерархия больших проектов

Большие проекты — цепочка: командующий → генералы-наблюдатели + генералы фронтов
(умные субагенты движка) → полковники/исполнители (Cursor). Граф фронтов —
`.orchestration/fronts.json`, продвижение волнами по deps. План каждого уровня
идёт через критиков. Панель показывает дерево фронтов (волны, статусы,
JSON-редактор).

## Итоги прогонов и надёжность

Отчёты исполнителей/критиков заканчиваются строкой
`Вердикт: OK | PROBLEMS | BLOCKED` и доказательствами. Облачные прогоны
(`run-cloud.py`) пишут логи в `.orchestration/sessions/<sid>/runs/<id>/`;
приёмка — по artifacts/status и строке вердикта, не по сводке исполнителя.

Машиночитаемый статус при наличии лога:

```bash
python3 orchestration-kit/bin/verdict.py .orchestration/sessions/<sid>/runs/<id>/run.log
```

Поля JSON: `exit`, `verdict`, `report_present`, `retries`.
Автоперезапуск — см. `execution.retry_on_fail` в params (по умолчанию 1).

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
install.sh         альтернатива: режим «kit внутри репо»
commands/          файлы команды /orch-menu (Claude, Codex)
panel/             панель настроек (python3 stdlib, без зависимостей)
params.json, compass.md   шаблоны по умолчанию (сеются в .orchestration/)
bin/               orchlib (ядро), reground (сверка курса, хуки),
                   menu (валидированное меню), discover (снимок моделей),
                   run-exec (локальный cursor-agent для кода),
                   run-cloud (Cursor Cloud Agents API)
skills/orchestration/  скилл (SKILL.md + references)
hooks/             сниппеты хуков Claude / Codex / Kimi
SHA256SUMS         контрольные суммы (проверяет установщик)
bin/run-exec.py    локальный CLI для код-задач (промт из файла → лог/EXIT/retry)
bin/run-cloud.py   облачные исполнители Cursor Cloud Agents API
                   (create → poll → artifacts)
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

## Охват: все сессии или одна папка?

По умолчанию установщик ставит **в папку** (Claude Code и Codex получают
скилл/хуки/команду только в ней; Kimi всегда на уровне пользователя). Запуск
с `--global` ставит части Claude/Codex на **уровень пользователя**
(`~/.claude`, `~/.codex`) — скилл и хуки работают в **каждой сессии в любой
папке** этой машины. Параметры (`.orchestration/`) остаются per-folder
сознательно: у каждого проекта свои настройки пачки и шаблон compass.

```bash
bash orchestration-kit/install-local.sh --global
```

На Windows то же через `-Global`:
`powershell -ExecutionPolicy Bypass -File orchestration-kit\install-local.ps1 -Global`.

Оба режима идемпотентны и аккуратно мержатся с существующими конфигами.

## Если что-то не работает

### Codex: хуки не срабатывают после установки
Codex требует явного доверия: запустите `/hooks` в Codex CLI → просмотрите и
доверьте хуки orchestration. Это модель безопасности Codex, не баг. Без trust
скиллы работают, но хуки сверки молчат.

### Python не установлен
Скилл работает как инструкции, но хуки/панель/скрипты пропускаются с
предупреждением. Установите Python 3.6+ и повторите: `bash orchestration-kit/install-local.sh`

### Cursor недоступен
Агент останавливается и спрашивает явно по недостающему пути: нет
`cursor-agent` для кода или нет ключа API для облака/не-кода. Варианты:
(а) продолжить на субагентах движка — расход квоты основного агента;
(б) установить `cursor-agent` / вставить ключ Cursor в панели;
(в) только для кода — явный cloud-fallback. Никогда не молчит.

### Старая версия Claude Code
`--permission-prompts none` требует v2.1.259+. На старых — `--permission-mode dontAsk`.

### GLM Code
Не четвёртый движок. GLM Coding Plan — бэкенд для Claude Code / Codex
(ANTHROPIC_BASE_URL + токен GLM). Скиллы и хуки работают без изменений.

### Удаление
```bash
bash orchestration-kit/uninstall.sh          # из этой папки
bash orchestration-kit/uninstall.sh --global  # пользовательский уровень
bash orchestration-kit/uninstall.sh --all     # и .orchestration/
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -Global
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -All
```

### Прочее

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

### Windows
Хуки Claude — в **exec-форме** (`command` + `args`): python запускается напрямую,
**без зависимости от Git Bash**. Для Codex/Kimi: лучше путь к kit **без пробелов**
и лаунчер `py` (`py -3 …`); пути с пробелами могут ломать spawn через cmd.exe.

## Лицензия

[MIT](LICENSE)
