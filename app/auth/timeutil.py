"""A single, shared `resolve_now` used by both `app.auth.security` and `app.db.auth`.

Kept in one place so "what does an unset `now` default to, and what happens if
a caller passes a naive one" has exactly one answer across token issuance and
refresh-token persistence.
"""

from datetime import UTC, datetime

__all__ = ["resolve_now"]


def resolve_now(now: datetime | None) -> datetime:
    """Return `now`, defaulting to the current UTC time.

    Raises `ValueError` if an explicit `now` is naive (no tzinfo): callers
    (tests fabricating expired/backdated timestamps) must be explicit about
    the timezone.
    """
    if now is None:
        return datetime.now(UTC)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    return now
