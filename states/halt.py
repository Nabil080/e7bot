"""HALT: the final state, which reports why the bot stopped and releases the game."""

from core.config import State
from core.state import Context


def halt(ctx: Context) -> State:
    """Log the stop reason and close the session."""
    ctx.logger.info("HALT: %s", ctx.halt_reason or "no reason given")
    ctx.detach()
    return State.HALT
