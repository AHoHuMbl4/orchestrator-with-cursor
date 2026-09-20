# orchestrator-with-cursor

**English** | [Русский](README.ru.md)

Agent orchestration for **Claude Code, Codex CLI and Kimi Code**: every task runs
in a fresh executor (**dual-path**: local Cursor CLI for code tasks — direct file
access; Cursor Cloud for research/no-FS — or engine subagents), results are checked
by waves of fresh critics, and engine hooks periodically pull the agent back to
the task and parameters — by machine, not by prompt discipline.

**3 engines · 2-line install · works even without python (skills-only mode)**

## What happens when you give a task

After installation, just tell your agent any task. The system handles the rest:

```
You: "find top-10 business models in edTech subscriptions"

Agent (automatically):
  1. Hook injects session parameters (machine, not prompt)
  2. Session compass auto-seeded from template (sessions/<id>/compass.md)
  3. Skill loads: agent becomes orchestrator
  4. Planning: decompose → TODO checklist in session compass → validate
  5. Role selection: cascade domain→subdomain→role (149 narrow specialists)
  6. Executor (dual-path): code (role under code/) → local cursor-agent via
     `run-exec.py` (direct FS); research/no-FS → Cursor Cloud via `run-cloud.py`
     — or engine subagents after explicit owner "yes"
  7. Critics: 3 fresh skeptics check every result (never see executor's reasoning)
  8. Mismatch? → mismatch wave → fresh arbiters → synthesis
  9. Acceptance: by measurement only (file exists, test green, URL verified)
 10. Report to you with sources
```

## Requirements

- One of the agents: Claude Code, Codex CLI or Kimi Code.
- **Python 3.6+** — required for hooks, settings panel and scripts.
  - Linux: `sudo apt install python3` (usually preinstalled)
  - macOS: `brew install python` or python.org
  - Windows: `winget install Python.Python.3.12` (check "Add to PATH" in the installer)
- No python at all? The installer still deploys the skill (it's plain
  instructions) and clearly reports what was skipped; re-run it after
  installing python to add hooks and the panel.

## Quick Start

In your working folder (where you run claude / codex / kimi):

```bash
git clone https://github.com/AHoHuMbl4/orchestrator-with-cursor.git orchestration-kit
bash orchestration-kit/install-local.sh
```

Windows (PowerShell 5.1+ or pwsh), from the working folder where the repo is
cloned/copied:

```powershell
git clone -c core.autocrlf=false https://github.com/AHoHuMbl4/orchestrator-with-cursor.git orchestration-kit
powershell -ExecutionPolicy Bypass -File orchestration-kit\install-local.ps1
```

If checksums fail during install, you cloned with line-ending conversion; re-clone with `-c core.autocrlf=false`.

Token & state: the panel shows the absolute key path; `.orchestration` resolves from the current folder upward — run engine and panel from the same project folder.

Add `-Global` for user-level install. Re-runs are safe (idempotent).

No git? Download the repo zip, unpack as `orchestration-kit/`, run the same
second line. The installer is idempotent, verifies file checksums and never
touches settings it doesn't own (kimi config gets a `.bak-orch` backup).

| Engine | First run | Verify |
|---|---|---|
| Claude Code | nothing — works right away | ask the agent "which skills are available?" → `orchestration` |
| Codex CLI | once: `/hooks` → trust the orchestration hooks (mandatory trust gate) | `$orchestration` resolves |
| Kimi Code | `/reload` in a live session or restart kimi | `/skill:orchestration` resolves |

All three: open **Settings** → `./panel.sh` → http://127.0.0.1:8765
(auto-picks a free port). The panel has a session selector (session compasses
only in the main zone), on/off toggle, executors per task (default 3), critics per diff,
review rounds, timeout, `retry_on_fail`, course-check interval, models, Cursor
API token, **Fronts** (waves, statuses, JSON editor), and **Advanced** for the
shared starter template (`.orchestration/compass.md` — confirm to save;
restore-default button).

## Cursor API token (for Cursor Cloud / non-code executors)

1. Log in at **cursor.com**, open **cursor.com/dashboard → API Keys** →
   **New API Key** → copy it (shown once).
2. Requires a **paid Cursor plan**; billing is per model tokens actually used,
   not per wall-clock time.
3. Paste the key into the **"Cursor API token"** field on the panel's main
   screen (the panel stores it in `.orchestration/cursor.key`, which is
   gitignored) — or set the `CURSOR_API_KEY` environment variable instead.
4. **Dual-path:** code tasks use local CLI (`run-exec.py` + `cursor-agent` on
   PATH). Non-code / research uses Cursor Cloud via `run-cloud.py` when a key
   is present (`auto` or `cursor-cloud`). Explicit `execution.executor` in
   params overrides auto-routing.

No key and no local binary for the needed path? Default `auto` **stops and asks**
(never silent fallback). Choose engine subagents only after an explicit "yes"
(or set `execution.executor = subagents`).

## How it works

- You write a task in plain words. The `orchestration` skill turns the agent
  into an orchestrator: decompose → fresh executor per subtask → N fresh
  critics per result (critics see the result + acceptance criterion, never the
  executor's reasoning) → rounds until convergence → acceptance by measurement.
- Compass model: `.orchestration/compass.md` is the **shared starter template**
  (edit only in the panel **Advanced**, with confirm + restore-default). Each
  session auto-gets `sessions/<id>/compass.md` on the first message; the agent
  writes the working task there. CLI `menu.py` refuses template writes
  (`--global-template` → exit 2).
- Hooks (Claude/Codex: SessionStart, UserPromptSubmit, PostToolUse; Kimi:
  UserPromptSubmit, SessionHeartbeat) inject current parameters with every
  message and remind to re-read the task every `reground.every_min` minutes —
  the agent cannot quietly forget the mode or drift off course.
- Executor modes (**dual-path**): `auto` routes by role domain — code
  (`code/`) → local CLI via `run-exec.py` if `cursor-agent` is on PATH;
  non-code → Cursor Cloud via `run-cloud.py` if a key is present; otherwise
  stop & ask. Explicit `execution.executor` (`local-cursor` / `cursor-cloud` /
  `subagents`) overrides auto. Local CLI: direct project file access. Cloud:
  research / no-FS, artifact as text.
- Settings live in `.orchestration/params.json`; hooks deliver changes with the
  next message. The `orchestration.enabled=false` toggle switches the agent
  back to direct work; hooks go silent.
- Hierarchy mode: off (flat, single orchestrator) / auto (doctrine heuristic) / on (prefer fronts for multi-block tasks) — `orchestration.hierarchy`, default auto. Full orchestration off remains the existing on/off toggle (`orchestration.enabled`).
- Pre-batch threshold: orchestrator asks before launching batches larger than N runs (`execution.ask_before_runs`, default 20).
- In-chat menu: say "меню" / `menu` or `/orch-menu` (Claude).

## Hierarchy for large projects

Large projects run as a command chain: commander → observer generals + front
generals (smart engine subagents) → colonels/executors (Cursor). The front graph
lives in `.orchestration/fronts.json` and advances in dependency waves. Each
level plans through critics before execution. The panel shows the front tree
(waves, statuses, JSON editor).

## Run outcomes & reliability

Executor/critic reports end with `Вердикт: OK` / `Вердикт: PROBLEMS: <list>` /
`Вердикт: BLOCKED: <reason>` plus evidence. Cloud runs (`run-cloud.py`) write session logs under
`.orchestration/sessions/<sid>/runs/<id>/`; accept by artifacts/status and
verdict line, never by the executor's summary alone.

Machine-readable status when a run log is present:

```bash
python3 orchestration-kit/bin/verdict.py .orchestration/sessions/<sid>/runs/<id>/run.log
```

JSON fields: `exit`, `verdict`, `report_present`, `retries`.
Auto-restart policy: see `execution.retry_on_fail` in params (default 1).

## Manual per-engine paths (if you prefer no installer)

| Engine | Skill path | Hooks |
|---|---|---|
| Claude Code | `.claude/skills/orchestration/` (project) or `~/.claude/skills/` | `.claude/settings.json` (merged by installer) |
| Codex CLI | `.agents/skills/` (repo) or `~/.agents/skills/` | `.codex/hooks.json` → trust via `/hooks` |
| Kimi Code | `~/.kimi-code/skills/` or `~/.agents/skills/` | `[[hooks]]` block in `~/.kimi-code/config.toml` |
| Cursor | `.cursor/skills/` or `~/.cursor/skills/` | — |

## Repository layout

```
install-local.sh   installer (main entry)
install.sh         alternative: kit-inside-repo mode
commands/          /orch-menu command files (Claude, Codex)
panel/             settings panel (python3 stdlib, no dependencies)
params.json, compass.md   default templates (seeded into .orchestration/)
bin/               orchlib (core), reground (course-check hooks),
                   menu (validated settings), discover (model snapshot),
                   run-exec (local cursor-agent for code),
                   run-cloud (Cursor Cloud Agents API)
skills/orchestration/  the skill (SKILL.md + references)
hooks/             hook snippets for Claude / Codex / Kimi
SHA256SUMS         checksums, verified by the installer
bin/run-exec.py    local Cursor CLI runner for code tasks (prompt file → log/EXIT/retry)
bin/run-cloud.py   Cursor Cloud Agents API runner (create → poll → artifacts)
bin/menu.py        deterministic settings menu (validated writes)
bin/discover.py    snapshot of available models/effort levels
skills/orchestration/  the skill (SKILL.md + references)
hooks/             hook snippets for Claude / Codex / Kimi
SHA256SUMS         checksums, verified by the installer
```

Details: [install-notes.md](install-notes.md) · Doctrine & traps:
[skills/orchestration/references/](skills/orchestration/references/)

Plugin-marketplace packaging (one-command install via
`/plugin marketplace add` / `codex plugin marketplace add`) is on the roadmap;
`install-local.sh` is the canonical path today.

## Scope: all sessions or one folder?

By default the installer targets **this folder** (Claude Code and Codex get
skill/hooks/command only here; Kimi is always user-level). With `--global`,
Claude/Codex parts go to the **user level** (`~/.claude`, `~/.codex`) — skill
and hooks work in **every session in any folder** on this machine. Params
(`.orchestration/`) stay per-folder on purpose: each project keeps its own
batch settings and compass template.

```bash
bash orchestration-kit/install-local.sh --global
```

On Windows the same via `-Global`:
`powershell -ExecutionPolicy Bypass -File orchestration-kit\install-local.ps1 -Global`.

Both modes are idempotent and merge carefully with existing configs.

## Troubleshooting

### Codex: hooks don't fire after install
Codex requires explicit trust: run `/hooks` in Codex CLI → review and trust the
orchestration hooks. This is Codex's security model, not a bug. Without trust,
skills work but course-check hooks stay silent.

### Python not installed
Skill works as instructions (plain text), but hooks/panel/scripts are skipped
with a clear warning. Install Python 3.6+ and re-run the installer:
`bash orchestration-kit/install-local.sh`

### Cursor not available
Agent stops and asks explicitly for the missing path: no `cursor-agent` for
code, or no API key for cloud/non-code. Options: (a) continue on engine
subagents — uses main quota; (b) install `cursor-agent` / add Cursor API key
in panel; (c) for code only — explicit cloud fallback. Never silently falls back.

### Old Claude Code version
`--permission-prompts none` requires v2.1.259+. On older versions use
`--permission-mode dontAsk` (already in the skill as fallback).

### GLM Code
Not a 4th engine. GLM Coding Plan is a **backend** for Claude Code / Codex
(set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` to GLM endpoint). Skills
and hooks work unchanged.

### Kimi: skill not listed
Skills register at session start; open a **new session** (or `/reload`) and
invoke `/skill:orchestration`. The description in SKILL.md must stay YAML-safe
(no `: ` inside the value) — fixed in this repo.

### Hooks stopped after app restart
Hook commands use the **absolute python path**, so they survive restarts from a
different environment. Re-run `install-local.sh` to upgrade an existing install
(it replaces its own old hook block in `~/.kimi-code/config.toml`). Kimi hooks
are fail-open: a failing hook is silent, test manually with
`echo '{}' | <python> <kit>/bin/reground.py prompt-submit --engine kimi`.

### Panel closes with the terminal
Run `./panel.sh --bg` (background mode, log in `panel.log`); for LAN access set
`panel.host` to `0.0.0.0` in params.

### Windows notes
Claude hooks use **exec form** (`command` + `args`) — they spawn python
directly and do **not** depend on Git Bash. For Codex/Kimi: prefer a kit path
**without spaces** and the `py` launcher (`py -3 …`); paths with spaces can
break cmd.exe-style hook spawn.

## Uninstall

```bash
bash orchestration-kit/uninstall.sh          # remove from this folder
bash orchestration-kit/uninstall.sh --global  # remove user-level
bash orchestration-kit/uninstall.sh --all     # also delete .orchestration/
```

Windows:

```powershell
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -Global
powershell -ExecutionPolicy Bypass -File orchestration-kit\uninstall.ps1 -All
```

Safe: preserves foreign hooks/settings, creates backups, idempotent.

## License

[MIT](LICENSE)
