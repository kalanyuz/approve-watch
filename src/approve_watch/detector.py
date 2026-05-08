from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


@dataclass(frozen=True)
class Match:
    command: str
    signature: str  # stable hash of the prompt block, used to deduplicate triggers


class Detector:
    def __init__(self, pattern: str) -> None:
        self._re = re.compile(pattern)

    def match(self, raw: str) -> Match | None:
        text = strip_ansi(raw)
        m = self._re.search(text)
        if not m:
            return None
        try:
            command = m.group("command").strip()
        except IndexError:
            command = m.group(0).strip().splitlines()[-1]
        block = m.group(0)
        sig = hashlib.sha256(block.encode("utf-8", errors="replace")).hexdigest()[:16]
        return Match(command=command, signature=sig)
