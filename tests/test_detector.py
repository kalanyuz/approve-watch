from __future__ import annotations

from approve_watch.config import DEFAULT_PROMPT_REGEX
from approve_watch.detector import Detector, strip_ansi


def test_strip_ansi() -> None:
    raw = "\x1b[31mhello\x1b[0m world"
    assert strip_ansi(raw) == "hello world"


def test_default_pattern_matches_typical_prompt() -> None:
    text = (
        "Some preamble line\n"
        "Run this command in your shell?\n"
        "$ git push origin main --force\n"
        "(y/N): "
    )
    d = Detector(DEFAULT_PROMPT_REGEX)
    m = d.match(text)
    assert m is not None
    assert m.command == "git push origin main --force"
    assert len(m.signature) == 16


def test_no_match_when_no_prompt() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    assert d.match("just normal output\nnothing to approve\n") is None


def test_signature_is_stable_for_same_block() -> None:
    text = (
        "Run this command in your shell?\n"
        "$ ls -la\n"
        "(y/N) "
    )
    d = Detector(DEFAULT_PROMPT_REGEX)
    m1 = d.match(text)
    m2 = d.match(text)
    assert m1 is not None and m2 is not None
    assert m1.signature == m2.signature


def test_signature_changes_for_different_command() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m1 = d.match("Run this command?\n$ a\n(y/N)")
    m2 = d.match("Run this command?\n$ b\n(y/N)")
    assert m1 is not None and m2 is not None
    assert m1.signature != m2.signature
