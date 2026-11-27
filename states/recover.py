"""RECOVER: try to get the game unstuck, then retry the interrupted state a limited number of times."""

from core.config import State
from core.state import Context

BACK_TEMPLATE = "back_button"


def recover(ctx: Context) -> State:
    """Press Esc, click back if visible, wait, and retry. Gives up after the configured retries."""
    target = ctx.interrupted or State.BOOT
    limit = ctx.config.watchdog.recover_max_retries
    count = ctx.recover_counts.get(target, 0) + 1
    ctx.recover_counts[target] = count
    if count > limit:
        if target is State.RESET_ACCOUNT:
            return ctx.halt(f"RESET_ACCOUNT still failing after {limit} retries")
        if target is State.SUMMON:
            # The account may hold what the reroll is after: never reset it because of a stuck screen.
            return ctx.halt(f"SUMMON still failing after {limit} retries: not resetting")
        if ctx.story_only:
            # Pushing the story of an account: only rerolling may erase one.
            return ctx.halt(f"{target.value} still failing after {limit} retries: not resetting")
        ctx.logger.warning("%s still failing after %d retries, resetting the account", target.value, limit)
        return State.RESET_ACCOUNT

    ctx.logger.info("recovering %s, attempt %d of %d", target.value, count, limit)
    if ctx.session is None:
        return State.BOOT
    ctx.session.input.press("esc")
    back = ctx.find(BACK_TEMPLATE)
    if back is not None:
        ctx.session.input.click(*back.center)
    ctx.wait(ctx.config.watchdog.recover_wait_seconds)
    return target
