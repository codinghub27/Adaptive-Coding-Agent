"""Fix 1+2 regression: `/auth/refresh` and `/auth/logout` serialize per-user
refresh-token mutations by taking a row lock on the user (`lock_user`)
*before* any read-then-mutate on that user's refresh tokens.

(a) Deterministic unit tests proving the lock is taken before the
    subsequent read/mutate, by spying on call order. Fast and always run.
(b) A real two-connection test: an actual rotation and an actual logout of
    the *same* login session, run concurrently against two independent
    `AsyncSession`s from the engine -- not the single rolled-back
    `db_session` fixture, which is one transaction/connection and can't
    model two genuinely concurrent, independently-committing transactions.

    A naive version of this test (just running both concurrently and
    checking the final state) turned out **not** to reliably exercise the
    race: with two fast local connections, the loser of the race typically
    doesn't even start its own work until the winner has already committed,
    so the final state looks correct regardless of whether the user lock is
    actually taken -- verified by temporarily removing `.with_for_update()`
    from `lock_user` and re-running it, which still passed. So instead this
    test deliberately slows down the rotation's write (via a monkeypatched
    delay applied only to `create_refresh_token`, well after it has taken
    the user lock) and asserts, from real wall-clock timestamps recorded on
    each side's real, independent connection, that the logout's own
    `lock_user` call did not return until the rotation had held the lock for
    (most of) that delay -- i.e. that it was genuinely blocked at the
    Postgres level, not just scheduled to run afterwards. Explicitly cleans
    up (deletes the user it creates) in a `finally`, since it commits for
    real rather than relying on a rolled-back fixture.
"""

import asyncio
import time
from collections.abc import Callable
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.auth.routes as routes_module
from app.auth.routes import logout as logout_route
from app.auth.routes import refresh as refresh_route
from app.auth.security import issue_refresh_token
from app.config import Settings, get_settings
from app.db.auth import create_refresh_token, create_user
from app.db.models import RefreshToken, User
from app.db.session import create_engine, create_session_factory
from app.schemas.auth import LogoutRequest, RefreshRequest
from tests.auth.helpers import PASSWORD, make_handle

MakeSettings = Callable[..., Settings]


def _spy(monkeypatch: pytest.MonkeyPatch, name: str, calls: list[str]) -> None:
    """Wrap `app.auth.routes.<name>` to append `name` to `calls` on each call,
    in call order, then delegate to the original implementation.
    """
    original = getattr(routes_module, name)

    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        calls.append(name)
        return await original(*args, **kwargs)

    monkeypatch.setattr(routes_module, name, wrapper)


# --------------------------------------------------------------------------
# (a) deterministic: lock ordering
# --------------------------------------------------------------------------


@pytest.mark.db
async def test_refresh_locks_user_before_reading_and_rotating(
    make_settings: MakeSettings, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(refresh_reuse_grace_seconds=0)
    user = await create_user(db_session, make_handle(), PASSWORD)
    session_id = uuid4()
    issued = issue_refresh_token(user_id=user.id, session_id=session_id, settings=settings)
    await create_refresh_token(
        db_session,
        token_id=issued.jti,
        user_id=user.id,
        session_id=session_id,
        token=issued.token,
        expires_at=issued.expires_at,
    )
    await db_session.flush()

    calls: list[str] = []
    _spy(monkeypatch, "lock_user", calls)
    _spy(monkeypatch, "get_refresh_token", calls)
    _spy(monkeypatch, "revoke_refresh_token_by_id", calls)

    await refresh_route(
        response=Response(),
        body=RefreshRequest(refresh_token=issued.token),
        session=db_session,
        settings=settings,
    )

    assert calls[:2] == ["lock_user", "get_refresh_token"]
    assert "revoke_refresh_token_by_id" in calls


@pytest.mark.db
async def test_refresh_reuse_locks_user_before_revoke_all(
    make_settings: MakeSettings, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings(refresh_reuse_grace_seconds=0)
    user = await create_user(db_session, make_handle(), PASSWORD)
    session_id = uuid4()
    issued = issue_refresh_token(user_id=user.id, session_id=session_id, settings=settings)
    await create_refresh_token(
        db_session,
        token_id=issued.jti,
        user_id=user.id,
        session_id=session_id,
        token=issued.token,
        expires_at=issued.expires_at,
    )
    await db_session.flush()

    # Rotate once so the presented token becomes `revoked_reason="rotated"`.
    await refresh_route(
        response=Response(),
        body=RefreshRequest(refresh_token=issued.token),
        session=db_session,
        settings=settings,
    )

    calls: list[str] = []
    _spy(monkeypatch, "lock_user", calls)
    _spy(monkeypatch, "get_refresh_token", calls)
    _spy(monkeypatch, "revoke_all_for_user", calls)

    with pytest.raises(HTTPException):
        await refresh_route(
            response=Response(),
            body=RefreshRequest(refresh_token=issued.token),
            session=db_session,
            settings=settings,
        )

    assert calls.index("lock_user") < calls.index("get_refresh_token")
    assert calls.index("lock_user") < calls.index("revoke_all_for_user")


@pytest.mark.db
async def test_logout_locks_user_before_revoking_session(
    make_settings: MakeSettings, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    user = await create_user(db_session, make_handle(), PASSWORD)
    session_id = uuid4()
    issued = issue_refresh_token(user_id=user.id, session_id=session_id, settings=settings)
    await create_refresh_token(
        db_session,
        token_id=issued.jti,
        user_id=user.id,
        session_id=session_id,
        token=issued.token,
        expires_at=issued.expires_at,
    )
    await db_session.flush()

    calls: list[str] = []
    _spy(monkeypatch, "get_refresh_token", calls)
    _spy(monkeypatch, "lock_user", calls)
    _spy(monkeypatch, "revoke_session", calls)

    await logout_route(body=LogoutRequest(refresh_token=issued.token), session=db_session)

    assert calls == ["get_refresh_token", "lock_user", "revoke_session"]


# --------------------------------------------------------------------------
# (b) real concurrency: two independent connections
# --------------------------------------------------------------------------


async def _cleanup_user(factory: async_sessionmaker[AsyncSession], user_id: Any) -> None:
    async with factory() as cleanup_session:
        user = await cleanup_session.get(User, user_id)
        if user is not None:
            await cleanup_session.delete(user)
            await cleanup_session.commit()


@pytest.mark.db
async def test_concurrent_rotate_and_logout_serialize_via_the_user_lock(
    make_settings: MakeSettings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real rotate and a real logout of the *same* session, run concurrently
    against two independent, real (non-rolled-back) sessions -- and proven to
    genuinely serialize at the Postgres level, not just happen to finish in a
    consistent order.

    The rotation's write (`create_refresh_token`) is deliberately slowed
    down by `DELAY` seconds, well after it has taken the user lock via
    `lock_user`. If the logout's own `lock_user` call is genuinely blocked
    waiting for that lock (real Postgres row-lock contention across two
    independent connections), it cannot return until at least `DELAY`
    seconds after the rotation acquired it.
    """
    settings = make_settings(refresh_reuse_grace_seconds=0)
    engine = create_engine(get_settings())
    factory = create_session_factory(engine)

    delay_seconds = 0.5
    original_create_refresh_token = routes_module.create_refresh_token

    async def _slow_create_refresh_token(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(delay_seconds)
        return await original_create_refresh_token(*args, **kwargs)

    monkeypatch.setattr(routes_module, "create_refresh_token", _slow_create_refresh_token)

    lock_acquired_at: dict[str, float] = {}
    original_lock_user = routes_module.lock_user

    async def _timed_lock_user(session: AsyncSession, user_id: UUID) -> Any:
        task = asyncio.current_task()
        assert task is not None
        result = await original_lock_user(session, user_id)
        lock_acquired_at[task.get_name()] = time.monotonic()
        return result

    monkeypatch.setattr(routes_module, "lock_user", _timed_lock_user)

    user_id = None
    try:
        async with factory() as setup_session:
            user = await create_user(setup_session, make_handle(), PASSWORD)
            user_id = user.id
            login_session_id = uuid4()
            issued = issue_refresh_token(
                user_id=user.id, session_id=login_session_id, settings=settings
            )
            await create_refresh_token(
                setup_session,
                token_id=issued.jti,
                user_id=user.id,
                session_id=login_session_id,
                token=issued.token,
                expires_at=issued.expires_at,
            )
            await setup_session.commit()

        async def _rotate() -> object:
            async with factory() as session_a:
                try:
                    return await refresh_route(
                        response=Response(),
                        body=RefreshRequest(refresh_token=issued.token),
                        session=session_a,
                        settings=settings,
                    )
                except HTTPException as exc:
                    return exc

        async def _logout() -> object:
            async with factory() as session_b:
                return await logout_route(
                    body=LogoutRequest(refresh_token=issued.token), session=session_b
                )

        rotate_task = asyncio.create_task(_rotate(), name="rotate")
        # A small head start so `rotate` reliably reaches (and holds) the
        # user lock before `logout` attempts to acquire it.
        await asyncio.sleep(0.05)
        logout_task = asyncio.create_task(_logout(), name="logout")

        await asyncio.gather(rotate_task, logout_task)

        assert lock_acquired_at.keys() == {"rotate", "logout"}
        gap = lock_acquired_at["logout"] - lock_acquired_at["rotate"]
        assert gap >= delay_seconds * 0.8, (
            f"logout's lock_user returned only {gap:.3f}s after rotate's -- "
            f"expected it to be blocked for close to the {delay_seconds}s delay"
        )

        async with factory() as check_session:
            rows = (
                (
                    await check_session.execute(
                        select(RefreshToken).where(RefreshToken.user_id == user.id)
                    )
                )
                .scalars()
                .all()
            )
            assert rows, "expected at least the original refresh-token row"
            assert all(row.revoked for row in rows), [
                (row.id, row.revoked, row.revoked_reason) for row in rows
            ]
    finally:
        if user_id is not None:
            await _cleanup_user(factory, user_id)
        await engine.dispose()
