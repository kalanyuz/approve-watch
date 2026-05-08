from __future__ import annotations

from typing import Protocol, runtime_checkable

PaneId = str  # opaque, source-specific (e.g. "demo:0.1" for tmux, "workspace:N/surface:M" for cmux)


@runtime_checkable
class PaneSource(Protocol):
    """Abstract over a terminal multiplexer that holds running agent panes.

    Implementations only need to read recent output from a pane and inject the
    two keystroke sequences the watcher uses: an approve key and a reject key.
    For cursor-agent's TUI those are literal "y" and "n" — single characters
    that the TUI consumes as hotkeys without an Enter.
    """

    name: str

    def list_panes(self) -> list[PaneId]: ...
    def capture(self, pane: PaneId) -> str: ...
    def send_approve(self, pane: PaneId) -> None: ...
    def send_reject(self, pane: PaneId) -> None: ...
