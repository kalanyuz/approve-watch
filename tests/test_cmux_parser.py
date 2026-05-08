from __future__ import annotations

from approve_watch.sources.cmux import parse_tree


def test_picks_terminal_surfaces_and_skips_browser() -> None:
    tree = """\
workspace:1 "main"
├─ surface:1 [browser] https://...
└─ surface:2 [terminal] tty=/dev/pts/3
workspace:3 "scratch"
└─ surface:9 [terminal] tty=/dev/pts/7
"""
    assert parse_tree(tree) == [
        "workspace:1/surface:2",
        "workspace:3/surface:9",
    ]


def test_workspace_header_alone_does_not_emit() -> None:
    tree = """\
workspace:5 empty workspace
"""
    assert parse_tree(tree) == []


def test_surfaces_without_terminal_tag_skipped() -> None:
    tree = """\
workspace:2
└─ surface:11 [browser] foo
└─ surface:12  bar (no tag)
"""
    assert parse_tree(tree) == []


def test_indentation_variants() -> None:
    tree = """\
workspace:7 nested
  tab:1 "shell"
    surface:21 [terminal] tty=/dev/pts/0
  tab:2 "secondary"
    surface:22 [terminal] tty=/dev/pts/1
"""
    assert parse_tree(tree) == [
        "workspace:7/surface:21",
        "workspace:7/surface:22",
    ]


def test_surface_before_workspace_is_orphan_and_dropped() -> None:
    tree = """\
surface:99 [terminal] orphan
workspace:1
└─ surface:1 [terminal] keeper
"""
    assert parse_tree(tree) == ["workspace:1/surface:1"]


def test_empty_input() -> None:
    assert parse_tree("") == []


def test_real_cmux_tree_output() -> None:
    """Captured from a real ``cmux tree --all`` invocation."""
    tree = (
        "window window:1 [current] ◀ active\n"
        "└── workspace workspace:1 \"~\" [selected] ◀ active\n"
        "    ├── pane pane:1\n"
        "    │   └── surface surface:1 [terminal] \"End Mark\" [selected] tty=ttys003\n"
        "    ├── pane pane:2\n"
        "    │   └── surface surface:2 [terminal] \"Small Point\" [selected] tty=ttys000\n"
        "    ├── pane pane:3 [focused] ◀ active\n"
        "    │   └── surface surface:3 [terminal] \"Small Point\" [selected] ◀ active tty=ttys001\n"
        "    └── pane pane:4\n"
        "        └── surface surface:4 [terminal] \"Cursor Agent\" [selected] tty=ttys002\n"
    )
    assert parse_tree(tree) == [
        "workspace:1/surface:1",
        "workspace:1/surface:2",
        "workspace:1/surface:3",
        "workspace:1/surface:4",
    ]
