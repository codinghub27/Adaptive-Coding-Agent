"""The application clock, with an evaluation-only override.

`now()` is `datetime.now(UTC)` unless a request running under `EVAL_MODE` set
an override for its own context (`override`). The override exists so a replay
can place a turn on a given day: skill decay, "last seen" stamps on mistakes
and spaced recall all read the clock, and a thirty-day journey cannot be
measured in thirty real days.

The override is a `ContextVar`: it applies to the request that set it and the
tasks that request starts, never to another request. Nothing outside
`app.graph.api` sets it, and that module refuses it unless `EVAL_MODE` is on.
"""

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

__all__ = ["now", "override"]

_override: ContextVar[datetime | None] = ContextVar("clock_override", default=None)


def now() -> datetime:
    """The current moment (timezone-aware UTC), or this context's override."""
    moment = _override.get()
    return moment if moment is not None else datetime.now(UTC)


@contextmanager
def override(moment: datetime | None) -> Generator[None]:
    """Run the enclosed block with `now()` fixed at `moment` (`None`: no change)."""
    if moment is None:
        yield
        return
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    token = _override.set(moment.astimezone(UTC))
    try:
        yield
    finally:
        _override.reset(token)
