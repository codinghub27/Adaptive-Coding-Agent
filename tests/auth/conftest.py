"""Shared fixtures for `tests/auth`: reuse Phase 3's `db`-marked DB fixtures,
plus a cheap bcrypt cost factor for every test in this package.
"""

import pytest

from app.auth import security as security_module
from tests.memory.conftest import db_session

__all__ = ["db_session"]


@pytest.fixture(autouse=True)
def _fast_bcrypt(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use a cheap bcrypt cost factor so password hashing in these tests is fast."""
    monkeypatch.setattr(security_module, "BCRYPT_ROUNDS", 4)
