"""BOOT: connect to the game window if it is open, otherwise have the game launched."""

from core.config import State
from core.state import Context


def boot(ctx: Context) -> State:
    """Start with the reset if one is asked for, else LOGIN. LAUNCH if the game is not open."""
    if not ctx.attach():
        return State.LAUNCH
    return State.RESET_ACCOUNT if ctx.reset_pending else State.LOGIN
