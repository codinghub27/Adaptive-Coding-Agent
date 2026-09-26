"""Shared fixtures for `tests/e2e`: reuse Phase 3's `db`-marked DB fixtures.

Mirrors `tests/graph/conftest.py`.
"""

from tests.memory.conftest import db_session, user_id

__all__ = ["db_session", "user_id"]
