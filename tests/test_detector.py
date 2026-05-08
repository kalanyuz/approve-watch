from __future__ import annotations

from approve_watch.config import DEFAULT_PROMPT_REGEX
from approve_watch.detector import Detector, strip_ansi


PROMPT_BOTH_CLAUSES = (
    "Run this command?\n"
    "Not in allowlist: git diff --cached -- ':!**/slack-activity/**'"
    " • Not in team allowlist: git diff --cached -- ':!**/slack-activity/**'\n"
    "→ Run (once) (y)\n"
    "  Skip (esc or n)\n"
)
PROMPT_TEAM_ONLY = (
    "Run this command?\n"
    "Not in team allowlist: rm -rf /tmp/foo\n"
    "→ Run (once) (y)\n"
    "  Skip (esc or n)\n"
)
PROMPT_SIMPLE = (
    "Run this command?\n"
    "Not in allowlist: ls -la\n"
    "→ Run (once) (y)\n"
    "  Skip (esc or n)\n"
)


def test_strip_ansi() -> None:
    raw = "\x1b[31mhello\x1b[0m world"
    assert strip_ansi(raw) == "hello world"


def test_default_pattern_matches_both_clauses_form() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m = d.match(PROMPT_BOTH_CLAUSES)
    assert m is not None
    assert m.command == "git diff --cached -- ':!**/slack-activity/**'"
    assert len(m.signature) == 16


def test_default_pattern_matches_team_only_form() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m = d.match(PROMPT_TEAM_ONLY)
    assert m is not None
    assert m.command == "rm -rf /tmp/foo"


def test_default_pattern_matches_simple_form() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m = d.match(PROMPT_SIMPLE)
    assert m is not None
    assert m.command == "ls -la"


def test_default_pattern_handles_ansi_styled_prompt() -> None:
    styled = (
        "\x1b[33mRun this command?\x1b[0m\n"
        "\x1b[2mNot in allowlist:\x1b[0m \x1b[1mecho hi\x1b[0m\n"
        "\x1b[7m→ Run (once) (y)\x1b[0m\n"
        "  Skip (esc or n)\n"
    )
    d = Detector(DEFAULT_PROMPT_REGEX)
    m = d.match(styled)
    assert m is not None
    assert m.command == "echo hi"


def test_no_match_when_no_prompt() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    assert d.match("just normal output\nnothing to approve\n") is None


def test_no_match_when_only_partial_prompt() -> None:
    """Prompt header without the Skip footer should not match — avoids
    triggering on cursor-agent's own message about its tooling."""
    d = Detector(DEFAULT_PROMPT_REGEX)
    text = "Run this command?\nNot in allowlist: ls\n(no footer here)"
    assert d.match(text) is None


def test_signature_is_stable_for_same_block() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m1 = d.match(PROMPT_SIMPLE)
    m2 = d.match(PROMPT_SIMPLE)
    assert m1 is not None and m2 is not None
    assert m1.signature == m2.signature


def test_signature_changes_for_different_command() -> None:
    d = Detector(DEFAULT_PROMPT_REGEX)
    m1 = d.match(PROMPT_SIMPLE)
    m2 = d.match(PROMPT_TEAM_ONLY)
    assert m1 is not None and m2 is not None
    assert m1.signature != m2.signature
