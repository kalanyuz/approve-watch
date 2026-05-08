from __future__ import annotations

import shutil
import subprocess

from approve_watch.sources.base import PaneId


class TmuxSource:
    """Pane source backed by the tmux(1) CLI."""

    name = "tmux"

    def __init__(self, capture_lines: int = 50) -> None:
        if not shutil.which("tmux"):
            raise RuntimeError("tmux not found in PATH")
        self._capture_lines = capture_lines

    def list_panes(self) -> list[PaneId]:
        out = subprocess.run(
            [
                "tmux",
                "list-panes",
                "-aF",
                "#{session_name}:#{window_index}.#{pane_index}",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return []
        return [line.strip() for line in out.stdout.splitlines() if line.strip()]

    def capture(self, pane: PaneId) -> str:
        out = subprocess.run(
            ["tmux", "capture-pane", "-p", "-t", pane, "-S", f"-{self._capture_lines}"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout if out.returncode == 0 else ""

    def send_enter(self, pane: PaneId) -> None:
        subprocess.run(["tmux", "send-keys", "-t", pane, "Enter"], check=False)

    def send_reject(self, pane: PaneId) -> None:
        subprocess.run(["tmux", "send-keys", "-t", pane, "n", "Enter"], check=False)
