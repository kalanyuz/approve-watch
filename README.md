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

```bash
cd approve-watch
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
```

## Use

```bash
approve-watch init-db          # create ~/.local/share/approve-watch/history.db
approve-watch watch &          # start the watcher in the background
approve-watch dash             # open the dashboard (separate terminal)
```

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

`coder/cmux`'s CLI surface varies by version, so the `cmux` source is a stub
in `src/approve_watch/sources/cmux.py`. Fill in the equivalent of `list-panes`,
`capture`, `send-enter`, and `send-reject` for cmux on your machine. The rest
of the system is unchanged.

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
