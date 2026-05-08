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

Both `cmux`'s CLI surface and `cursor-agent`'s exact prompt text vary by version.
After install:

1. Run `cursor-agent` in a tmux pane and trigger a command-approval prompt.
2. From another shell:

   ```bash
   tmux capture-pane -p -t <pane> -S -50 > /tmp/cap.txt
   ```

3. Inspect `/tmp/cap.txt` and, if the watcher does not detect, override the regex:

   ```bash
   mkdir -p ~/.config/approve-watch
   cat > ~/.config/approve-watch/config.toml <<'EOF'
   prompt_regex = '''(?ms)<your regex here, with named group "command">'''
   EOF
   ```

4. If you run inside coder `cmux` rather than plain tmux, fill in
   `src/approve_watch/sources/cmux.py` with the equivalent of `list-panes`,
   `capture`, and `send-keys` for cmux. The rest of the system is unchanged.

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
