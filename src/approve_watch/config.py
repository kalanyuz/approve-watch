from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

KIND_SHELL = "shell_command"
KIND_OTHER = "other"
KIND_DANGEROUS = "dangerous"

# Tier 3 (post-classification): if the captured `command` text matches any
# of these heuristic patterns we re-tag the row as "dangerous". The watcher
# uses a much longer timeout for this tier — effectively "manual decision
# only" — so a slip-of-the-attention can't auto-approve something
# destructive. The list is conservative; users can add or remove patterns
# via [dangerous_patterns] in ~/.config/approve-watch/config.toml.
DANGEROUS_PATTERNS: list[str] = [
    # Recursive filesystem destruction targeting roots / system dirs.
    # `/tmp/...` is intentionally NOT flagged — that's what /tmp is for.
    r"\brm\s+-[a-zA-Z]*[rRf][a-zA-Z]*\s+/\s*(?:$|[\s;&|])",
    r"\brm\s+-[a-zA-Z]*[rRf][a-zA-Z]*\s+(?:~|\$HOME)(?:/|\s|$)",
    r"\brm\s+-[a-zA-Z]*[rRf][a-zA-Z]*\s+/(?:etc|usr|bin|sbin|var|opt|boot|root|lib|lib64|sys|proc|dev)\b",
    r":\(\)\s*\{\s*:\|:&\s*\}\s*;",                 # fork bomb
    # Shell history / dotfile / credential paths.
    r"~/\.ssh\b",
    r"\bid_(?:rsa|ed25519|ecdsa|dsa)\b",
    r"~/\.aws/credentials",
    r"\.netrc\b",
    # Privilege escalation.
    r"\bsudo\b",
    r"\bchmod\s+(?:-R\s+)?[07]{3,4}\s+",
    r"\bchown\s+(?:-R\s+)?root\b",
    # Force / rewriting git history on shared refs.
    r"\bgit\s+push\s+(?:[^\s]+\s+)?--force\b",
    r"\bgit\s+push\s+(?:[^\s]+\s+)?-f\b",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+rebase\b.*--root",
    # Arbitrary code from the network.
    r"\bcurl\b[^\n|]*\|\s*(?:sh|bash|zsh|sudo)",
    r"\bwget\b[^\n|]*\|\s*(?:sh|bash|zsh|sudo)",
    # SQL nukes.
    r"\bDROP\s+(?:DATABASE|TABLE|SCHEMA)\b",
    r"\bTRUNCATE\s+TABLE\b",
    r"\bDELETE\s+FROM\b(?!.*\bWHERE\b)",            # DELETE without WHERE
    # System destruction.
    r"\bmkfs(?:\.\w+)?\b",
    r"\bdd\s+if=.*\bof=/dev/[sh]d[a-z]",
    r">\s*/dev/sd[a-z]",
    # Container / k8s destructive ops.
    r"\bkubectl\s+delete\s+(?:--all|namespace|ns)\b",
    r"\bdocker\s+system\s+prune\s+(?:-a\s+)?--volumes",
]

# Tier 1: shell commands. Anchored on cursor-agent's "Run this command?" header
# so we only fast-approve actual shell commands.
SHELL_COMMAND_REGEX = (
    r"(?ms)"
    r"Run this command\?"
    r".*?Not\s+in\s+(?:team\s+)?allowlist:\s*"
    r"(?P<command>[^\n•]+?)"
    r"\s*(?:•|\n)"
    r".*?Skip\s*\(esc or n\)"
)

# Tier 2: every other cursor-agent confirmation that uses the same hotkey
# footer (Delete, Edit, Web Fetch, Web Search, etc.). Captures the line
# directly above the "→ <verb …> ... (y)" choice as the action description,
# tolerates multi-word verbs (e.g. "Allow search"), zero or more
# parenthesised modifier clauses (e.g. "(once)"), and any number of
# intermediate option lines (e.g. "Always allow … (tab)") between that
# choice and the trailing "Skip (esc or n)" footer. The shell-command
# regex is tried first, so this only fires when that one didn't match.
OTHER_PROMPT_REGEX = (
    r"(?ms)"
    r"(?P<command>[^\n]+?)"
    r"\s*\n\s*"
    r"→\s*[^()\n]+?(?:\s*\([^)]+\))*\s*\(y\)"
    r".{0,500}?"
    r"Skip\s*\(esc or n\)"
)

# Per-kind timeouts. The dashboard's countdown is slightly shorter than the
# watcher's so when both are running the dashboard's auto-decision wins,
# but if the dashboard isn't running the watcher still resolves the prompt.
KIND_TIMEOUTS_S: dict[str, float] = {
    KIND_SHELL: 3.2,
    KIND_OTHER: 3601.0,      # 1 hour + 1s
    KIND_DANGEROUS: 86401.0, # 24 hours + 1s — effectively manual-only
}
KIND_DASHBOARD_TIMEOUTS_S: dict[str, float] = {
    KIND_SHELL: 3.0,
    KIND_OTHER: 3600.0,      # 1 hour
    KIND_DANGEROUS: 86400.0, # 24 hours
}

POLL_INTERVAL_S = 0.2
DECISION_POLL_S = 0.1

# Runaway-loop alarm: when a single pane's prompt rate exceeds
# RUNAWAY_THRESHOLD_PER_MIN averaged across the last RUNAWAY_WINDOW_MIN
# minutes, the watcher flips to auto-rejecting on that pane until the
# user dismisses the alarm. Catches "agent stuck in a retry loop"
# situations early, before they spend significant compute.
RUNAWAY_THRESHOLD_PER_MIN = 10.0
RUNAWAY_WINDOW_MIN = 2

# Backwards-compat aliases used by older test code.
WATCHER_TIMEOUT_S = KIND_TIMEOUTS_S[KIND_SHELL]
DASHBOARD_TIMEOUT_S = KIND_DASHBOARD_TIMEOUTS_S[KIND_SHELL]
DEFAULT_PROMPT_REGEX = SHELL_COMMAND_REGEX


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    p = Path(base) / "approve-watch"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "approve-watch"


def db_path() -> Path:
    override = os.environ.get("APPROVE_WATCH_DB")
    if override:
        return Path(override)
    return data_dir() / "history.db"


@dataclass(frozen=True)
class Config:
    shell_regex: str = SHELL_COMMAND_REGEX
    other_regex: str = OTHER_PROMPT_REGEX
    source: str = "auto"  # "auto" | "tmux" | "cmux"
    timeouts: dict[str, float] = field(
        default_factory=lambda: dict(KIND_TIMEOUTS_S)
    )
    dashboard_timeouts: dict[str, float] = field(
        default_factory=lambda: dict(KIND_DASHBOARD_TIMEOUTS_S)
    )
    dangerous_patterns: list[str] = field(
        default_factory=lambda: list(DANGEROUS_PATTERNS)
    )


def load_config() -> Config:
    cfg_file = config_dir() / "config.toml"
    if not cfg_file.exists():
        return Config()
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    timeouts = dict(KIND_TIMEOUTS_S)
    timeouts.update({k: float(v) for k, v in (data.get("timeouts") or {}).items()})
    dash = dict(KIND_DASHBOARD_TIMEOUTS_S)
    dash.update({k: float(v) for k, v in (data.get("dashboard_timeouts") or {}).items()})
    user_dangerous = data.get("dangerous_patterns")
    return Config(
        shell_regex=data.get("shell_regex", data.get("prompt_regex", SHELL_COMMAND_REGEX)),
        other_regex=data.get("other_regex", OTHER_PROMPT_REGEX),
        source=data.get("source", "auto"),
        timeouts=timeouts,
        dashboard_timeouts=dash,
        dangerous_patterns=(
            list(user_dangerous) if user_dangerous is not None
            else list(DANGEROUS_PATTERNS)
        ),
    )
