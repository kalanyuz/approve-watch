from __future__ import annotations

import re
import shutil
import subprocess

from approve_watch.sources.base import PaneId

_WORKSPACE_RE = re.compile(r"workspace:(\d+)")
_SURFACE_RE = re.compile(r"surface:(\d+)")
_TERMINAL_RE = re.compile(r"\[terminal\]")


def _split(pane: PaneId) -> tuple[str, str]:
    """``workspace:3/surface:9`` -> ("workspace:3", "surface:9")."""
    ws, sf = pane.split("/", 1)
    return ws, sf


def parse_tree(text: str) -> list[PaneId]:
    """Parse ``cmux tree --all`` output into ``workspace:N/surface:M`` ids.

    Walks lines top-to-bottom, tracks the most recent ``workspace:N`` seen on
    a header line, then emits an id for every line that mentions both
    ``surface:M`` and ``[terminal]``. Browser surfaces are skipped because the
    user's reference script notes that ``send`` / ``read-screen`` only work on
    ``[terminal]`` surfaces.
    """
    current_ws: str | None = None
    out: list[PaneId] = []
    for line in text.splitlines():
        ws_m = _WORKSPACE_RE.search(line)
        sf_m = _SURFACE_RE.search(line)
        if ws_m and sf_m is None:
            current_ws = f"workspace:{ws_m.group(1)}"
            continue
        if sf_m and _TERMINAL_RE.search(line) and current_ws is not None:
            out.append(f"{current_ws}/surface:{sf_m.group(1)}")
    return out


class CmuxSource:
    """Pane source backed by the coder/cmux CLI.

    Auto-discovers every ``[terminal]`` surface across all workspaces via
    ``cmux tree --all``. Read/write through ``cmux read-screen`` and
    ``cmux send``. Honors ``CMUX_SOCKET_PATH`` automatically because we
    inherit the parent environment.
    """

    name = "cmux"

    def __init__(self, capture_lines: int = 80) -> None:
        if not shutil.which("cmux"):
            raise RuntimeError("cmux not found in PATH")
        self._capture_lines = capture_lines

    def list_panes(self) -> list[PaneId]:
        out = subprocess.run(
            ["cmux", "tree", "--all"],
            capture_output=True,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return []
        return parse_tree(out.stdout)

    def capture(self, pane: PaneId) -> str:
        ws, sf = _split(pane)
        out = subprocess.run(
            [
                "cmux",
                "read-screen",
                "--workspace",
                ws,
                "--surface",
                sf,
                "--lines",
                str(self._capture_lines),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout if out.returncode == 0 else ""

    def send_approve(self, pane: PaneId) -> None:
        ws, sf = _split(pane)
        subprocess.run(
            ["cmux", "send", "--workspace", ws, "--surface", sf, "y"],
            check=False,
        )

    def send_reject(self, pane: PaneId) -> None:
        ws, sf = _split(pane)
        subprocess.run(
            ["cmux", "send", "--workspace", ws, "--surface", sf, "n"],
            check=False,
        )
