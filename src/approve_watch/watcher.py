from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable
from pathlib import Path

from approve_watch.config import (
    DECISION_POLL_S,
    KIND_TIMEOUTS_S,
    POLL_INTERVAL_S,
    RUNAWAY_THRESHOLD_PER_MIN,
    RUNAWAY_WINDOW_MIN,
    Config,
    load_config,
)
from approve_watch.db import (
    claim_decision,
    connect,
    fetch_decision,
    insert_pending,
    is_alarmed,
    prompt_rate_per_minute,
    set_alarm,
)
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

# Multi-prompt drain: cursor-agent can show a queue of approval prompts
# back-to-back. After we answer one, the buffer often transitions directly
# to the next prompt (different command, different signature). Rather than
# return and wait for watch_loop's next poll (which can miss fast
# transitions), the post-decision loop notices the new prompt inline and
# we drain up to this many in a single _handle_pane call.
MAX_DRAIN_PER_PANE = 16


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
    default_decision_factory: Callable[[], tuple[int, str]] | None = None,
) -> tuple[int, str]:
    """Wait until the row has a decision or the deadline elapses, then
    claim a default decision. ``default_decision_factory`` is called at
    timeout-time so the caller can re-evaluate state (e.g. is the pane's
    runaway alarm still set?) at the moment of decision rather than at
    decision-await start. If omitted, defaults to (1, 'auto-watcher')."""
    while time.monotonic() < deadline:
        with connect(db_path) as conn:
            approved, decided_by = fetch_decision(conn, row_id)
        if approved is not None and decided_by is not None:
            return approved, decided_by
        await asyncio.sleep(DECISION_POLL_S)

    default_approved, default_by = (
        default_decision_factory() if default_decision_factory else (1, "auto-watcher")
    )
    with connect(db_path) as conn:
        if claim_decision(
            conn, row_id, approved=default_approved, decided_by=default_by
        ):
            return default_approved, default_by
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
    """Handle one or more prompts on ``pane``. Loops up to
    MAX_DRAIN_PER_PANE times so a queue of back-to-back prompts (e.g.
    cursor-agent batching three git commands) gets answered inside a
    single task rather than waiting for the next watch_loop poll between
    each — that gap dropped prompts on fast transitions in practice."""

    pane_source = f"{source.name}:{pane}"

    for _drain in range(MAX_DRAIN_PER_PANE):
        raw = await asyncio.to_thread(source.capture, pane)
        if not raw:
            return
        m = detector.match(raw)
        if m is None or m.signature in seen:
            return

        seen.add(m.signature)
        log.info(
            "pane %s prompt detected (%s, drain=%d): %s",
            pane, m.kind, _drain, m.command,
        )

        context = _capture_context(strip_ansi(raw), m.block)
        with connect(db_path) as conn:
            row_id = insert_pending(
                conn,
                command=m.command,
                source=pane_source,
                kind=m.kind,
                context=context,
            )
            # Trip the alarm if this pane is firing too fast. The DB
            # query already counts the row we just inserted, which is
            # what we want — the rate snapshot is "as of this prompt".
            rate = prompt_rate_per_minute(
                conn, pane_source, window_minutes=RUNAWAY_WINDOW_MIN
            )
            if rate >= RUNAWAY_THRESHOLD_PER_MIN:
                set_alarm(conn, pane_source, rate)
                log.warning(
                    "runaway-loop alarm: pane=%s rate=%.1f/min — "
                    "auto-decision flips to reject until cleared",
                    pane_source,
                    rate,
                )

        def default_decision() -> tuple[int, str]:
            # Re-check the alarm at timeout-time so a mid-await dismissal
            # from the dashboard immediately resumes auto-approval.
            with connect(db_path) as conn:
                if is_alarmed(conn, pane_source):
                    return 0, "auto-watcher-alarmed"
            return 1, "auto-watcher"

        timeout = (timeouts or KIND_TIMEOUTS_S).get(m.kind, KIND_TIMEOUTS_S[m.kind])
        approved, decided_by = await _await_decision(
            db_path,
            row_id,
            deadline=time.monotonic() + timeout,
            default_decision_factory=default_decision,
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

        # Watch the buffer for one of three outcomes:
        #   - cleared:   no prompt visible            → done, return
        #   - replaced:  new prompt with a different
        #                signature                    → drain it inline
        #   - stale:     same prompt still visible
        #                after the window             → keep sig in
        #                                              seen, return
        deadline = time.monotonic() + post_decision_timeout
        state = "stale"
        while time.monotonic() < deadline:
            await asyncio.sleep(0.1)
            post = await asyncio.to_thread(source.capture, pane)
            nxt = detector.match(post)
            if nxt is None:
                state = "cleared"
                break
            if nxt.signature != m.signature:
                state = "replaced"
                break

        if state == "stale":
            log.warning(
                "pane %s: prompt did not clear after keystroke; keeping "
                "sig %s in seen-cache to avoid double-approve",
                pane,
                m.signature,
            )
            return

        # Either cleared or replaced — the old sig is safe to retire.
        seen.discard(m.signature)
        if state == "cleared":
            return
        # state == "replaced": loop back and handle the new prompt.

    log.warning(
        "pane %s: drained %d prompts in one handler; further prompts will "
        "be picked up on the next watch_loop poll",
        pane,
        MAX_DRAIN_PER_PANE,
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
    detector = Detector(
        cfg.shell_regex,
        cfg.other_regex,
        cfg.dangerous_patterns,
        cfg.fast_patterns,
    )
    log.info(
        "watcher starting: source=%s, timeouts=%s, "
        "dangerous_patterns=%d, fast_patterns=%d",
        source.name,
        cfg.timeouts,
        len(cfg.dangerous_patterns),
        len(cfg.fast_patterns),
    )
    asyncio.run(watch_loop(source, detector, timeouts=cfg.timeouts))
