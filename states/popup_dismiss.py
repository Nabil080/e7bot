"""POPUP_DISMISS: click the popup found by the preemption, then go back to the interrupted state."""

from core.config import State
from core.state import Context

AFTER_CLICK_SECONDS = 0.3


def popup_dismiss(ctx: Context) -> State:
    """Click the pending popup. The interrupted state keeps its watchdog timer."""
    match = ctx.pending_popup
    if match is not None and ctx.session is not None:
        ctx.logger.info("dismissing %s (score %.2f) at %s", match.name, match.score, match.center)
        ctx.session.input.click(*match.center)
        ctx.wait(AFTER_CLICK_SECONDS)
    ctx.pending_popup = None
    return ctx.interrupted or State.RECOVER
