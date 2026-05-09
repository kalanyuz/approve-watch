from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from approve_watch.config import KIND_OTHER, KIND_SHELL

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass(frozen=True)
class Match:
    command: str
    signature: str  # stable hash of the prompt block, used to deduplicate triggers
    kind: str       # KIND_SHELL or KIND_OTHER
    block: str      # full matched text (regex group 0); fed to the flight recorder


class Detector:
    """Two-tier detector. Tries the shell-command regex first; on no match,
    tries the generic "any TUI confirmation" regex. The kind on the returned
    Match drives the watcher's per-prompt timeout."""

    def __init__(self, shell_pattern: str, other_pattern: str) -> None:
        self._shell = re.compile(shell_pattern)
        self._other = re.compile(other_pattern)

    def match(self, raw: str) -> Match | None:
        text = strip_ansi(raw)
        m = self._shell.search(text)
        if m is not None:
            return self._build(m, kind=KIND_SHELL)
        m = self._other.search(text)
        if m is not None:
            return self._build(m, kind=KIND_OTHER)
        return None

    @staticmethod
    def _build(m: re.Match[str], kind: str) -> Match:
        try:
            command = m.group("command").strip()
        except IndexError:
            command = m.group(0).strip().splitlines()[-1]
        block = m.group(0)
        sig = hashlib.sha256(block.encode("utf-8", errors="replace")).hexdigest()[:16]
        return Match(command=command, signature=sig, kind=kind, block=block)
