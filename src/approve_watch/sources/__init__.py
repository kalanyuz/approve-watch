from approve_watch.sources.base import PaneId, PaneSource
from approve_watch.sources.cmux import CmuxSource
from approve_watch.sources.tmux import TmuxSource

__all__ = ["PaneId", "PaneSource", "TmuxSource", "CmuxSource", "make_source"]


def make_source(name: str = "auto") -> PaneSource:
    """Factory honoring config: 'auto' | 'tmux' | 'cmux'."""
    import shutil

    if name == "tmux":
        return TmuxSource()
    if name == "cmux":
        return CmuxSource()
    if name == "auto":
        if shutil.which("cmux"):
            return CmuxSource()
        if shutil.which("tmux"):
            return TmuxSource()
        raise RuntimeError("No tmux or cmux binary found in PATH")
    raise ValueError(f"Unknown source: {name!r}")
