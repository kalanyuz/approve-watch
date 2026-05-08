# approve-watch

Watch `cursor-agent` (or any agent shell app) running inside `tmux` / coder `cmux`,
auto-approve its confirmation prompts on a tier-aware timeout, log everything
to SQLite, and (optionally) preempt the auto-approval from a Textual dashboard.

```
                 ┌──────────────────────────────────────────────┐
   cmux/tmux ───►│ Watcher (capture pane → 2 regexes)           │
   pane          │   ↓ classify shell_command vs other          │
                 │   ↓ INSERT (approved=NULL, kind=…)           │
                 │   ↓ wait per-kind for decided_by≠NULL        │
                 │   ↓ send-keys: y (approve) | n (reject)      │
                 └────────────────────┬─────────────────────────┘
                                      │ shared SQLite
                                      ▼
                 ┌──────────────────────────────────────────────┐
                 │ Textual dashboard                            │
                 │  • polls DB every 200ms                      │
                 │  • per-card countdown matches the row's kind │
                 │  • Y/N buttons UPDATE approved + decided_by  │
                 │  • top row: hourly-24h + daily-30d charts    │
                 └──────────────────────────────────────────────┘
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

## Tiers (auto-approve scope)

cursor-agent uses the same hotkey footer (`(y)` / `Skip (esc or n)`) for
several confirmation types. approve-watch classifies each match into one of
two tiers and applies a different timeout to each:

| Kind            | Matches                                | Watcher timeout | Dashboard countdown |
| --------------- | -------------------------------------- | ---------------:| -------------------:|
| `shell_command` | "Run this command? … Not in allowlist" |          3.2 s  |              3.0 s  |
| `other`         | Anything else with the same footer (Delete, Edit, …) |    1 h + 1 s |             1 h     |

Defaults are conservative: shell prompts auto-approve fast; everything else
waits an hour by default so you have plenty of time to veto from the
dashboard. Override per kind via `~/.config/approve-watch/config.toml`:

```toml
[timeouts]
shell_command = 3.2
other         = 3600

[dashboard_timeouts]
shell_command = 3.0
other         = 3600
```

You can also override the regexes (`shell_regex`, `other_regex`) the same
way if your cursor-agent version's wording changes.

## Calibration

The shell-command regex was calibrated against this real cursor-agent prompt:

```
Run this command?
Not in allowlist: <command>  •  Not in team allowlist: <command>
→ Run (once) (y)
  Skip (esc or n)
```

The "other" regex captures whatever line precedes `→ <Verb> (y)` plus the
shared `Skip (esc or n)` footer.

To capture the live prompt for inspection while iterating:

```bash
tmux capture-pane -p -t <pane> -S -50 > /tmp/cap.txt   # tmux
cmux read-screen --workspace workspace:N --surface surface:M --lines 80   # cmux
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
  kind        TEXT    NOT NULL DEFAULT 'shell_command',
  approved    INTEGER,           -- NULL pending, 1 approved, 0 rejected
  decided_at  TEXT,
  decided_by  TEXT,              -- 'auto-watcher' | 'auto-dashboard' | 'user'
  label       TEXT
);
```
