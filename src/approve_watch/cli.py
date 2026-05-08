from __future__ import annotations

import logging
import sys

import click

from approve_watch.config import db_path, load_config
from approve_watch.db import init_db


@click.group()
@click.option("-v", "--verbose", is_flag=True, help="Enable debug logging.")
def main(verbose: bool) -> None:
    """approve-watch: watch agent panes, log approvals, dashboard them."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


@main.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite DB at ~/.local/share/approve-watch/history.db."""
    p = init_db()
    click.echo(f"Initialized {p}")


@main.command("watch")
@click.option(
    "--source",
    type=click.Choice(["auto", "tmux", "cmux"]),
    default=None,
    help="Override config source.",
)
def watch_cmd(source: str | None) -> None:
    """Run the watcher daemon (foreground)."""
    from dataclasses import replace

    from approve_watch.watcher import run

    cfg = load_config()
    if source is not None:
        cfg = replace(cfg, source=source)
    init_db()
    run(cfg)


@main.command("dash")
def dash_cmd() -> None:
    """Open the Textual approval dashboard."""
    from approve_watch.dashboard.app import ApproveWatchApp

    init_db()
    ApproveWatchApp().run()


@main.command("path")
def path_cmd() -> None:
    """Print the database path."""
    click.echo(str(db_path()))


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
