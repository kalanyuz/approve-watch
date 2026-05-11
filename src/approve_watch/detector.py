from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from approve_watch.config import KIND_DANGEROUS, KIND_OTHER, KIND_SHELL

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
    """Three-tier detector with two post-classification passes. Tries the
    shell-command regex first; on no match, tries the generic "any TUI
    confirmation" regex. After either matches, two heuristic passes run
    against the captured command / block:

      1. ``dangerous_patterns``: any hit re-tags the row as
         ``dangerous``, which the watcher treats as effectively
         manual-only (24h timeout).
      2. ``fast_patterns``: any hit on an ``other``-tier match
         demotes it to ``shell_command`` so it auto-approves in 3.2s
         instead of an hour. The dangerous check runs first, so a
         ``Write to ~/.ssh/id_rsa`` stays dangerous even though it
         matches a fast pattern.

    The kind on the returned Match drives all per-prompt timeouts."""

    def __init__(
        self,
        shell_pattern: str,
        other_pattern: str,
        dangerous_patterns: list[str] | None = None,
        fast_patterns: list[str] | None = None,
    ) -> None:
        self._shell = re.compile(shell_pattern)
        self._other = re.compile(other_pattern)
        self._dangerous = [
            re.compile(p, re.IGNORECASE) for p in (dangerous_patterns or [])
        ]
        self._fast = [
            re.compile(p, re.IGNORECASE) for p in (fast_patterns or [])
        ]

    # Number of pre-match lines folded into the fast-pattern scan so the
    # question header (e.g. "Write to this file?", which sits one line
    # above the matched block in the OTHER regex) is reachable.
    FAST_PRE_LINES = 3

    def match(self, raw: str) -> Match | None:
        text = strip_ansi(raw)
        m = self._shell.search(text)
        if m is not None:
            return self._build(m, kind=KIND_SHELL, full_text=text)
        m = self._other.search(text)
        if m is not None:
            return self._build(m, kind=KIND_OTHER, full_text=text)
        return None

    def _is_dangerous(self, command: str) -> bool:
        return any(p.search(command) for p in self._dangerous)

    def _is_fast(self, context: str) -> bool:
        return any(p.search(context) for p in self._fast)

    def _build(self, m: re.Match[str], kind: str, full_text: str) -> Match:
        try:
            command = m.group("command").strip()
        except IndexError:
            command = m.group(0).strip().splitlines()[-1]
        block = m.group(0)
        # Dangerous heuristics override the base tier first…
        if self._is_dangerous(command):
            kind = KIND_DANGEROUS
        # …then fast-patterns may demote an `other` match. The question
        # header (e.g. "Write to this file?") sits a line above the
        # matched block in the OTHER regex, so widen the scan to include
        # a few lines before the match start.
        elif kind == KIND_OTHER and self._fast:
            pre_lines = full_text[: m.start()].splitlines()[-self.FAST_PRE_LINES :]
            context = "\n".join(pre_lines) + "\n" + block
            if self._is_fast(context):
                kind = KIND_SHELL
        sig = hashlib.sha256(block.encode("utf-8", errors="replace")).hexdigest()[:16]
        return Match(command=command, signature=sig, kind=kind, block=block)
