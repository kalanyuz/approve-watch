from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Iterable
from pathlib import Path

from approve_watch.config import (
    DECISION_POLL_S,
    KIND_TIMEOUTS_S,
    POLL_INTERVAL_S,
    Config,
    load_config,
)
from approve_watch.db import claim_decision, connect, fetch_decision, insert_pending
from approve_watch.detector import Detector, strip_ansi
from approve_watch.sources import make_source
from approve_watch.sources.base import PaneId, PaneSource

log = logging.getLogger("approve_watch.watcher")

# Flight recorder: how many lines of pane context to store on either side
# of the matched prompt block. Plenty to reconstruct what cursor-agent was
# trying to do, small enough that 256 LRU rows × ~2 KB stays under 1 MB.
CONTEXT_LINES_BEFORE = 12
CONTEXT_LINES_AFTER = 4
CONTEXT_MAX_BYTES = 8192


def _capture_context(raw: str, matched_block: str) -> str:
    """Return the matched prompt block plus a few surrounding lines from
    the captured pane buffer, capped at CONTEXT_MAX_BYTES so stuck-flush
    pane buffers can't bloat the DB. ``raw`` is post-strip_ansi text from
    the detector; ``matched_block`` is the full text of the regex match
    (m.group(0))."""
    if not matched_block:
        return raw[-CONTEXT_MAX_BYTES:]
    idx = raw.find(matched_block)
    if idx < 0:
        return matched_block[:CONTEXT_MAX_BYTES]
    pre = raw[:idx].splitlines()[-CONTEXT_LINES_BEFORE:]
    post_start = idx + len(matched_block)
    post = raw[post_start:].splitlines()[:CONTEXT_LINES_AFTER]
    body = "\n".join([*pre, matched_block.rstrip("\n"), *post])
    if len(body) > CONTEXT_MAX_BYTES:
        body = body[-CONTEXT_MAX_BYTES:]
    return body


class _SignatureCache:
    """Bounded LRU of prompt signatures we've already acted on."""

    def __init__(self, capacity: int = 256) -> None:
        self._d: OrderedDict[str, float] = OrderedDict()
        self._cap = capacity

    def __contains__(self, sig: str) -> bool:
        return sig in self._d

    def add(self, sig: str) -> None:
        self._d[sig] = time.monotonic()
        self._d.move_to_end(sig)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)

    def discard(self, sig: str) -> None:
        self._d.pop(sig, None)


async def _await_decision(
    db_path: Path | None,
    row_id: int,
    deadline: float,
) -> tuple[int, str]:
    """Wait until the row has a decision or the deadline elapses, then claim
    auto-approval if we time out. Returns (approved, decided_by)."""
    while time.monotonic() < deadline:
        with connect(db_path) as conn:
            approved, decided_by = fetch_decision(conn, row_id)
        if approved is not None and decided_by is not None:
            return approved, decided_by
        await asyncio.sleep(DECISION_POLL_S)

    with connect(db_path) as conn:
        if claim_decision(conn, row_id, approved=1, decided_by="auto-watcher"):
            return 1, "auto-watcher"
        approved, decided_by = fetch_decision(conn, row_id)
        assert approved is not None and decided_by is not None
        return approved, decided_by


async def _handle_pane(
    source: PaneSource,
    pane: PaneId,
    detector: Detector,
    seen: _SignatureCache,
    db_path: Path | None,
    timeouts: dict[str, float] | None = None,
    post_decision_timeout: float = 5.0,
) -> None:
    raw = await asyncio.to_thread(source.capture, pane)
    if not raw:
        return
    m = detector.match(raw)
    if m is None or m.signature in seen:
        return

    seen.add(m.signature)
    log.info("pane %s prompt detected (%s): %s", pane, m.kind, m.command)

    context = _capture_context(strip_ansi(raw), m.block)
    with connect(db_path) as conn:
        row_id = insert_pending(
            conn,
            command=m.command,
            source=f"{source.name}:{pane}",
            kind=m.kind,
            context=context,
        )

    timeout = (timeouts or KIND_TIMEOUTS_S).get(m.kind, KIND_TIMEOUTS_S[m.kind])
    approved, decided_by = await _await_decision(
        db_path, row_id, deadline=time.monotonic() + timeout
    )
    log.info(
        "row %s decided: approved=%s by=%s -> sending keystroke",
        row_id,
        approved,
        decided_by,
    )
    if approved == 1:
        await asyncio.to_thread(source.send_approve, pane)
    else:
        await asyncio.to_thread(source.send_reject, pane)

    # Only retire the signature once we observe the prompt is gone (or
    # replaced by a different-sig prompt). If the buffer still shows the
    # same prompt when this window closes — slow repaint, dropped
    # keystroke, etc. — keep the signature in `seen` so the next poll
    # cycle can't insert a second row and send a second keystroke. The
    # LRU bound (256 entries) is the only thing that retires it then.
    deadline = time.monotonic() + post_decision_timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        post = await asyncio.to_thread(source.capture, pane)
        nxt = detector.match(post)
        if nxt is None or nxt.signature != m.signature:
            seen.discard(m.signature)
            return
    log.warning(
        "pane %s: prompt did not clear after keystroke; keeping sig %s in"
        " seen-cache to avoid double-approve",
        pane,
        m.signature,
    )


async def watch_loop(
    source: PaneSource,
    detector: Detector,
    db_path: Path | None = None,
    pane_filter: Iterable[PaneId] | None = None,
    stop: asyncio.Event | None = None,
    timeouts: dict[str, float] | None = None,
    post_decision_timeout: float = 5.0,
) -> None:
    """Run forever (or until ``stop`` is set), polling each pane in parallel.

    Each pane has at most one in-flight decision task at a time.
    """
    seen = _SignatureCache()
    in_flight: dict[PaneId, asyncio.Task[None]] = {}
    stop = stop or asyncio.Event()

    while not stop.is_set():
        try:
            panes = await asyncio.to_thread(source.list_panes)
        except Exception as e:  # pragma: no cover
            log.warning("list_panes failed: %s", e)
            panes = []

        if pane_filter is not None:
            wanted = set(pane_filter)
            panes = [p for p in panes if p in wanted]

        for pane in panes:
            task = in_flight.get(pane)
            if task is not None and not task.done():
                continue
            in_flight[pane] = asyncio.create_task(
                _handle_pane(
                    source, pane, detector, seen, db_path, timeouts,
                    post_decision_timeout,
                ),
                name=f"approve-watch:{pane}",
            )

        # Reap finished tasks
        for pane, task in list(in_flight.items()):
            if task.done():
                exc = task.exception()
                if exc is not None:
                    log.exception("pane %s handler crashed", pane, exc_info=exc)
                in_flight.pop(pane, None)

        try:
            await asyncio.wait_for(stop.wait(), timeout=POLL_INTERVAL_S)
        except asyncio.TimeoutError:
            pass

    for task in in_flight.values():
        task.cancel()
    for task in in_flight.values():
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


def run(config: Config | None = None) -> None:
    cfg = config or load_config()
    source = make_source(cfg.source)
    detector = Detector(cfg.shell_regex, cfg.other_regex)
    log.info(
        "watcher starting: source=%s, timeouts=%s", source.name, cfg.timeouts
    )
    asyncio.run(watch_loop(source, detector, timeouts=cfg.timeouts))
