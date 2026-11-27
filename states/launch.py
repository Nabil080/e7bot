"""LAUNCH: start the client once and wait for its window."""

import os

from core.config import State
from core.state import Context

POLL_SECONDS = 2.0


def launch(ctx: Context) -> State:
    """Start the configured command once, then go to LOGIN when the window appears."""
    if ctx.attach():
        ctx.launched = False
        return State.LOGIN
    if ctx.dry_run:
        return ctx.halt("dry run: the game is not open and will not be launched")
    if not ctx.launched:
        command = ctx.config.reset.launch_command
        ctx.logger.info("starting the client: %s", command)
        # startfile accepts both an exe path and a protocol URL.
        os.startfile(command)
        ctx.launched = True
    ctx.wait(POLL_SECONDS)
    return State.LAUNCH
