from __future__ import annotations

from approve_watch.sources.base import PaneId

_CALIBRATE = (
    "CmuxSource is not implemented yet. coder/cmux exposes a CLI / Unix-socket IPC "
    "that varies by version. Run `cmux --help` on your machine, then fill in this "
    "file's list_panes / capture / send_enter / send_reject methods. The rest of "
    "approve-watch does not need to change. See README §Calibration."
)


class CmuxSource:
    """Stub for coder/cmux. See README §Calibration."""

    name = "cmux"

    def list_panes(self) -> list[PaneId]:
        raise NotImplementedError(_CALIBRATE)

    def capture(self, pane: PaneId) -> str:
        raise NotImplementedError(_CALIBRATE)

    def send_enter(self, pane: PaneId) -> None:
        raise NotImplementedError(_CALIBRATE)

    def send_reject(self, pane: PaneId) -> None:
        raise NotImplementedError(_CALIBRATE)
