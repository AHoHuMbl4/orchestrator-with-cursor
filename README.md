# orchestrator-with-cursor

**English** | [Русский](README.ru.md)

Agent orchestration for **Claude Code, Codex CLI and Kimi Code**: every task runs
in a fresh executor subagent (or cursor-agent / Cursor Cloud), results are checked
by waves of fresh critics, and engine hooks periodically pull the agent back to
the task and parameters — by machine, not by prompt discipline.

**3 engines · 2-line install · works even without python (skills-only mode)**

## What happens when you give a task

After installation, just tell your agent any task. The system handles the rest:

```
You: "find top-10 business models in edTech subscriptions"

Agent (automatically):
  1. Hook injects session parameters (machine, not prompt)
  2. Skill loads: agent becomes orchestrator
  3. Planning: decompose → TODO checklist in compass → validate
  4. Role selection: cascade domain→subdomain→role (137 narrow specialists)
  5. Executor: cursor-agent on Cursor quota (never main engine quota)
  6. Critics: 3 fresh skeptics check every result (never see executor's reasoning)
  7. Mismatch? → mismatch wave → fresh arbiters → synthesis
  8. Acceptance: by measurement only (file exists, test green, URL verified)
  9. Report to you with sources
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

No git? Download the repo zip, unpack as `orchestration-kit/`, run the same
second line. The installer is idempotent, verifies file checksums and never
touches settings it doesn't own (kimi config gets a `.bak-orch` backup).

| Engine | First run | Verify |
|---|---|---|
| Claude Code | nothing — works right away | ask the agent "which skills are available?" → `orchestration` |
| Codex CLI | once: `/hooks` → trust the orchestration hooks (mandatory trust gate) | `$orchestration` resolves |
| Kimi Code | `/reload` in a live session or restart kimi | `/skill:orchestration` resolves |

All three: open **Settings** → `./panel.sh` → http://127.0.0.1:8765
(auto-picks a free port). The panel has an on/off toggle, task (compass),
executors per task, critics per diff, review rounds, timeout, course-check
interval, models, and the Cursor API token field.

## Cursor API token (for the "Cursor cloud" executor mode)

1. Log in at **cursor.com**, open **cursor.com/dashboard → API Keys** →
   **New API Key** → copy it (shown once).
2. Requires a **paid Cursor plan**; billing is per model tokens actually used,
   not per wall-clock time.
3. Paste the key into the **"Cursor API token"** field on the panel's main
   screen (the panel stores it in `.orchestration/cursor.key`, which is
   gitignored) — or set the `CURSOR_API_KEY` environment variable instead.
4. Switch executor mode to `cursor-cloud` and the orchestrator will run
   executors as Cursor Cloud Agents (no local install needed).

Don't need cloud executors? Skip this — the default executor mode (engine
subagents) needs no keys at all.

## How it works

- You write a task in plain words. The `orchestration` skill turns the agent
  into an orchestrator: decompose → fresh executor per subtask → N fresh
  critics per result (critics see the result + acceptance criterion, never the
  executor's reasoning) → rounds until convergence → acceptance by measurement.
- Hooks (Claude/Codex: SessionStart, UserPromptSubmit, PostToolUse; Kimi:
  UserPromptSubmit, SessionHeartbeat) inject current parameters with every
  message and remind to re-read the task every `reground.every_min` minutes —
  the agent cannot quietly forget the mode or drift off course.
- Executor modes: engine subagents (default), local `cursor-agent --model auto`,
  Cursor Cloud Agents API (token from the panel).
- Settings live in `.orchestration/params.json`; hooks deliver changes with the
  next message. The `orchestration.enabled=false` toggle switches the agent
  back to direct work; hooks go silent.
- In-chat menu: say "меню" / `menu` or `/orch-menu` (Claude).

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
                   run-exec (local cursor-agent), run-cloud (Cursor Cloud)
skills/orchestration/  the skill (SKILL.md + references)
hooks/             hook snippets for Claude / Codex / Kimi
SHA256SUMS         checksums, verified by the installer
bin/run-exec.py    local cursor-agent runner (prompt from file, EXIT in log)
bin/run-cloud.py   Cursor Cloud Agents API runner
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
Agent stops and asks explicitly: "Cursor unavailable. (a) continue on engine
subagents — uses main quota; (b) install cursor-agent; (c) add Cursor API key
in panel." Never silently falls back.

### Old Claude Code version
`--permission-prompts none` requires v2.1.259+. On older versions use
`--permission-mode dontAsk` (already in the skill as fallback).

### GLM Code
Not a 4th engine. GLM Coding Plan is a **backend** for Claude Code / Codex
(set `ANTHROPIC_BASE_URL` + `ANTHROPIC_AUTH_TOKEN` to GLM endpoint). Skills
and hooks work unchanged.

### Kimi: skill not listed** — skills register at session start; open a **new
  session** (or `/reload`) and invoke `/skill:orchestration`. The description in
  SKILL.md must stay YAML-safe (no `: ` inside the value) — fixed in this repo.
- **Hooks stopped after app restart** — hook commands now use the **absolute
  python path**, so they survive restarts from a different environment. Re-run
  `install-local.sh` to upgrade an existing install (it replaces its own old
  hook block in `~/.kimi-code/config.toml`). Kimi hooks are fail-open: a
  failing hook is silent, test manually with
  `echo '{}' | <python> <kit>/bin/reground.py prompt-submit --engine kimi`.
- **Panel closes with the terminal** — run `./panel.sh --bg` (background mode,
  log in `panel.log`); for LAN access set `panel.host` to `0.0.0.0` in params.

## Uninstall

```bash
bash orchestration-kit/uninstall.sh          # remove from this folder
bash orchestration-kit/uninstall.sh --global  # remove user-level
bash orchestration-kit/uninstall.sh --all     # also delete .orchestration/
```

Safe: preserves foreign hooks/settings, creates backups, idempotent.

## License

[MIT](LICENSE)
