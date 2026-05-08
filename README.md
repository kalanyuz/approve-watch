# approve-watch

Watch `cursor-agent` (or any agent shell app) running inside `tmux` / coder `cmux`,
auto-approve command-approval prompts after a 3 s grace window, log everything to
SQLite, and (optionally) preempt the auto-approval from a Textual dashboard.

```
                 ┌──────────────────────────────────────┐
   cmux/tmux ───►│ Watcher (poll capture-pane → regex)  │
   pane          │   ↓ INSERT (approved=NULL)           │
                 │   ↓ wait ≤3.2s for decided_by≠NULL   │
                 │   ↓ send-keys (Enter | "n\n")        │
                 └─────────────┬────────────────────────┘
                               │ shared SQLite
                               ▼
                 ┌──────────────────────────────────────┐
                 │ Textual dashboard                    │
                 │  • polls DB every 200ms              │
                 │  • cards UPDATE approved + decided_by│
                 │    (UI countdown 3s = auto-decision) │
                 │  • charts read history               │
                 └──────────────────────────────────────┘
```

## Install

Requires Python 3.14 and [uv](https://docs.astral.sh/uv/).

```bash
cd approve-watch
uv sync                        # creates .venv, installs runtime + dev deps
```

`uv` will fetch CPython 3.14 automatically if it isn't already on the system.

## Use

```bash
uv run approve-watch init-db   # create ~/.local/share/approve-watch/history.db
uv run approve-watch watch &   # start the watcher in the background
uv run approve-watch dash      # open the dashboard (separate terminal)
```

Or activate the venv once and drop the `uv run` prefix:

```bash
source .venv/bin/activate
approve-watch dash
```

The watcher auto-discovers panes:

- If `cmux` is on `PATH`, it parses `cmux tree --all` and watches every
  `[terminal]` surface across all workspaces. No `--workspace` / `--surface`
  flags. Honors `CMUX_SOCKET_PATH` if set.
- Otherwise it falls back to `tmux list-panes -a` and watches every pane.
- Force one with `approve-watch watch --source tmux` or `--source cmux`.

When the regex matches, the watcher inserts a row with `approved=NULL`, waits
3.2 s, then sends `y` (approve) or `n` (reject) — single-key hotkeys that
match cursor-agent's TUI. The dashboard's 3 s countdown can preempt the
watcher.

## Calibration

The default regex was calibrated against this real cursor-agent prompt:

```
Run this command?
Not in allowlist: <command>  •  Not in team allowlist: <command>
→ Run (once) (y)
  Skip (esc or n)
```

If a future cursor-agent version changes the wording, override the regex:

```bash
mkdir -p ~/.config/approve-watch
cat > ~/.config/approve-watch/config.toml <<'EOF'
prompt_regex = '''(?ms)<your regex here, with named group "command">'''
EOF
```

To capture the live prompt for inspection while iterating:

```bash
tmux capture-pane -p -t <pane> -S -50 > /tmp/cap.txt
```

If your cmux version's tree output looks different and `parse_tree`
mis-discovers surfaces, paste the output of `cmux tree --all` into a
fresh test in `tests/test_cmux_parser.py` and adjust the regex set in
`src/approve_watch/sources/cmux.py`.

## Schema

```sql
CREATE TABLE approvals (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  asked_at    TEXT    NOT NULL,
  command     TEXT    NOT NULL,
  source      TEXT    NOT NULL,
  approved    INTEGER,           -- NULL pending, 1 approved, 0 rejected
  decided_at  TEXT,
  decided_by  TEXT,              -- 'auto-watcher' | 'auto-dashboard' | 'user'
  label       TEXT
);
```
