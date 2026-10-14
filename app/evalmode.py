"""`EVAL_MODE` request helpers shared by the routes that honour it.

`EVAL_MODE` is evaluation instrumentation (`Settings.eval_mode`): off by
default and refused at startup when `APP_ENV=prod`. These helpers are the one
place a route asks "is it on?" and "may this request move the clock?".
"""

from datetime import datetime

from fastapi import HTTPException, Request

from app.config import Settings

__all__ = ["eval_clock", "eval_mode"]


def eval_mode(request: Request) -> bool:
    """True when this app runs with `EVAL_MODE` on."""
    settings = getattr(request.app.state, "settings", None)
    return isinstance(settings, Settings) and settings.eval_mode


def eval_clock(request: Request, eval_now: datetime | None) -> datetime | None:
    """The clock override this request may use: `eval_now` under `EVAL_MODE`,
    a 403 anywhere else, so a client can never move the clock of a real
    deployment."""
    if eval_now is not None and not eval_mode(request):
        raise HTTPException(status_code=403, detail="eval_now requires EVAL_MODE")
    return eval_now
