from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROMPT_REGEX = (
    r"(?ms)"
    r"Run this command\?"
    r".*?Not\s+in\s+(?:team\s+)?allowlist:\s*"
    r"(?P<command>[^\n•]+?)"
    r"\s*(?:•|\n)"
    r".*?Skip\s*\(esc or n\)"
)

WATCHER_TIMEOUT_S = 3.2
DASHBOARD_TIMEOUT_S = 3.0
POLL_INTERVAL_S = 0.2
DECISION_POLL_S = 0.1


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
    prompt_regex: str = DEFAULT_PROMPT_REGEX
    source: str = "auto"  # "auto" | "tmux" | "cmux"


def load_config() -> Config:
    cfg_file = config_dir() / "config.toml"
    if not cfg_file.exists():
        return Config()
    with cfg_file.open("rb") as f:
        data = tomllib.load(f)
    return Config(
        prompt_regex=data.get("prompt_regex", DEFAULT_PROMPT_REGEX),
        source=data.get("source", "auto"),
    )
