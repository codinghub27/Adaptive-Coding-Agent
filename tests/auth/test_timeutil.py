"""Fix 10 regression: there is exactly one `resolve_now`, shared by
`app.auth.security` and `app.db.auth` rather than each keeping its own copy.
"""

from datetime import UTC, datetime

import pytest

import app.auth.security as security_module
import app.db.auth as db_auth_module
from app.auth.timeutil import resolve_now


def test_security_and_db_auth_import_the_very_same_function() -> None:
    assert security_module.resolve_now is resolve_now
    assert db_auth_module.resolve_now is resolve_now


def test_resolve_now_defaults_to_current_utc_time() -> None:
    before = datetime.now(UTC)
    resolved = resolve_now(None)
    after = datetime.now(UTC)

    assert before <= resolved <= after
    assert resolved.tzinfo is not None


def test_resolve_now_passes_through_an_aware_datetime() -> None:
    aware = datetime(2026, 1, 1, tzinfo=UTC)
    assert resolve_now(aware) == aware


def test_resolve_now_rejects_a_naive_datetime() -> None:
    naive = datetime(2026, 1, 1)  # noqa: DTZ001 -- intentionally naive for this test
    with pytest.raises(ValueError, match="timezone-aware"):
        resolve_now(naive)
