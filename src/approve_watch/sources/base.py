from __future__ import annotations

from typing import Protocol, runtime_checkable

PaneId = str  # opaque, source-specific (e.g. "demo:0.1" for tmux, "<uuid>" for cmux)


@runtime_checkable
class PaneSource(Protocol):
    """Abstract over a terminal multiplexer that holds running agent panes.

    Implementations only need to read recent output from a pane and inject the
    two keystroke sequences the watcher uses: an Enter (approve) and an
    "n"+Enter (reject).
    """

    name: str

    def list_panes(self) -> list[PaneId]: ...
    def capture(self, pane: PaneId) -> str: ...
    def send_enter(self, pane: PaneId) -> None: ...
    def send_reject(self, pane: PaneId) -> None: ...
