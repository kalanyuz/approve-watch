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
# Real cursor-agent web-fetch prompt — three options, with an "Always allow"
# domain-pin row sandwiched between (y) and Skip (esc or n).
PROMPT_WEB_FETCH = (
    "🌐 Web Fetch: https://www.llm-prices.com/current-v1.json\n"
    "\n"
    " Allow this web fetch?\n"
    "  → Fetch (y)\n"
    "    Always allow [www.llm-prices.com](https://www.llm-prices.com) (tab)\n"
    "    Skip (esc or n)\n"
)
PROMPT_WEB_FETCH_BOXED = (
    "▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄\n"
    " Allow this web fetch?\n"
    "  → Fetch (y)\n"
    "    Always allow [example.com](https://example.com) (tab)\n"
    "    Skip (esc or n)\n"
    "▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀\n"
)
# Real cursor-agent web-search prompt — verb is two words ("Allow search").
PROMPT_WEB_SEARCH = (
    "Allow this web search?\n"
    "→ Allow search (y)\n"
    "  Skip (esc or n)\n"
)
# Hypothetical multi-word verb with a parenthesised modifier clause.
PROMPT_MULTI_WORD_VERB_WITH_MODIFIER = (
    "Allow this thing?\n"
    "→ Allow always (once) (y)\n"
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


def test_other_tier_matches_web_fetch_prompt() -> None:
    """Web-fetch prompts have a 3-option menu (Fetch / Always allow / Skip);
    the regex must tolerate intermediate option lines between (y) and Skip."""
    m = make_detector().match(PROMPT_WEB_FETCH)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "Allow this web fetch?"


def test_other_tier_matches_boxed_web_fetch_prompt() -> None:
    m = make_detector().match(PROMPT_WEB_FETCH_BOXED)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "Allow this web fetch?"


def test_other_tier_matches_multi_word_verb_web_search() -> None:
    """Real cursor-agent web-search prompt: '→ Allow search (y)'.
    The verb is two words, which the previous regex's '\\S+' single-token
    verb couldn't accommodate."""
    m = make_detector().match(PROMPT_WEB_SEARCH)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "Allow this web search?"


def test_other_tier_matches_multi_word_verb_plus_modifier() -> None:
    """Multi-word verbs and parenthesised modifiers must coexist."""
    m = make_detector().match(PROMPT_MULTI_WORD_VERB_WITH_MODIFIER)
    assert m is not None
    assert m.kind == KIND_OTHER
    assert m.command == "Allow this thing?"


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
