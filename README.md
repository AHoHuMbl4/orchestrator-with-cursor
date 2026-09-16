# orchestrator-with-cursor

**English** | [Русский](README.ru.md)

Agent orchestration for **Claude Code, Codex CLI and Kimi Code**: every task runs
in a fresh executor subagent (or cursor-agent / Cursor Cloud), results are checked
by waves of fresh critics, and engine hooks periodically pull the agent back to
the task and parameters — by machine, not by prompt discipline.

**3 engines · 2-line install · no dependencies beyond python3**

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
| Kimi Code | `/reload` in a live session or restart kimi | `/orchestration` resolves |

All three: open **Settings** → `./panel.sh` → http://127.0.0.1:8765
(auto-picks a free port). The panel has an on/off toggle, task (compass),
executors per task, critics per diff, review rounds, timeout, course-check
interval, models, and the Cursor API token field.

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
panel/             settings panel (python3 stdlib, no dependencies)
bin/reground.py    course-check engine (hooks for all three engines)
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

## License

[MIT](LICENSE)
