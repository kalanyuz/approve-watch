from __future__ import annotations

from approve_watch.config import (
    KIND_OTHER,
    KIND_SHELL,
    OTHER_PROMPT_REGEX,
    SHELL_COMMAND_REGEX,
)
from approve_watch.detector import Detector, strip_ansi


def make_detector() -> Detector:
    return Detector(SHELL_COMMAND_REGEX, OTHER_PROMPT_REGEX)


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
# Hypothetical "other" tier prompt — same hotkey footer, different verb.
PROMPT_DELETE = (
    "Delete this file?\n"
    "/tmp/some/file.txt\n"
    "→ Delete (y)\n"
    "  Skip (esc or n)\n"
)
PROMPT_EDIT = (
    "Edit this file?\n"
    "src/main.py\n"
    "→ Edit (once) (y)\n"
    "  Skip (esc or n)\n"
)


def test_strip_ansi() -> None:
    raw = "\x1b[31mhello\x1b[0m world"
    assert strip_ansi(raw) == "hello world"


def test_shell_pattern_matches_both_clauses_form() -> None:
    m = make_detector().match(PROMPT_BOTH_CLAUSES)
    assert m is not None
    assert m.kind == KIND_SHELL
    assert m.command == "git diff --cached -- ':!**/slack-activity/**'"
    assert len(m.signature) == 16


def test_shell_pattern_matches_team_only_form() -> None:
    m = make_detector().match(PROMPT_TEAM_ONLY)
    assert m is not None
    assert m.kind == KIND_SHELL
    assert m.command == "rm -rf /tmp/foo"


def test_shell_pattern_matches_simple_form() -> None:
    m = make_detector().match(PROMPT_SIMPLE)
    assert m is not None
    assert m.kind == KIND_SHELL
    assert m.command == "ls -la"


def test_shell_pattern_handles_ansi_styled_prompt() -> None:
    styled = (
        "\x1b[33mRun this command?\x1b[0m\n"
        "\x1b[2mNot in allowlist:\x1b[0m \x1b[1mecho hi\x1b[0m\n"
        "\x1b[7m→ Run (once) (y)\x1b[0m\n"
        "  Skip (esc or n)\n"
    )
    m = make_detector().match(styled)
    assert m is not None
    assert m.kind == KIND_SHELL
    assert m.command == "echo hi"


def test_other_tier_matches_delete_prompt() -> None:
    m = make_detector().match(PROMPT_DELETE)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "/tmp/some/file.txt"


def test_other_tier_matches_edit_prompt() -> None:
    m = make_detector().match(PROMPT_EDIT)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "src/main.py"


def test_shell_takes_precedence_over_other() -> None:
    """When a screen could match both regexes, the shell tier wins so we keep
    the fast 3.2s timeout for shell commands."""
    m = make_detector().match(PROMPT_SIMPLE)
    assert m is not None
    assert m.kind == KIND_SHELL


def test_no_match_when_no_prompt() -> None:
    assert make_detector().match("just normal output\nnothing to approve\n") is None


def test_no_match_when_only_partial_prompt() -> None:
    """Prompt header without the Skip footer should not match — avoids
    triggering on cursor-agent's own message about its tooling."""
    text = "Run this command?\nNot in allowlist: ls\n(no footer here)"
    assert make_detector().match(text) is None


def test_signature_is_stable_for_same_block() -> None:
    d = make_detector()
    m1 = d.match(PROMPT_SIMPLE)
    m2 = d.match(PROMPT_SIMPLE)
    assert m1 is not None and m2 is not None
    assert m1.signature == m2.signature


def test_signature_changes_for_different_command() -> None:
    d = make_detector()
    m1 = d.match(PROMPT_SIMPLE)
    m2 = d.match(PROMPT_TEAM_ONLY)
    assert m1 is not None and m2 is not None
    assert m1.signature != m2.signature
