"""LOGIN: pick the server when asked, tap through the title screen, wait for what comes next."""

from __future__ import annotations

import time

from core.config import State
from core.state import Context
from states.story import PATCH_REGION, PATCH_STOP

TAP_POINT = (640, 400)
WAKE_POINT = (640, 400)
AFTER_CLICK_SECONDS = 2.0
SERVER_RETRY_SECONDS = 6.0
TITLE_RETRY_SECONDS = 5.0
SERVER_LOADING_SECONDS = 30.0
SERVER_NAME_LEFT = 352
SERVER_NAME_WIDTH = 200
SERVER_ROW_HEIGHT = 40
SERVER_ROWS_Y = (180, 267, 354, 442, 530, 618)
SERVER_SELECT_X = 886


def find_server_row(ctx: Context, server: str) -> int | None:
    """Return the y of a server's row by reading the names, whose order can change."""
    for y in SERVER_ROWS_Y:
        region = (SERVER_NAME_LEFT, y - SERVER_ROW_HEIGHT // 2, SERVER_NAME_WIDTH, SERVER_ROW_HEIGHT)
        if ctx.ocr.read(ctx.frame, region).casefold() == server.casefold():
            return y
    return None


def _select_server(ctx: Context) -> State:
    """Click Select on the reroll server. The game can ignore a click while it loads, so retry."""
    # This screen only shows without an account, so there is nothing left to reset.
    ctx.reset_pending = False
    if time.monotonic() - ctx.last_server_click < SERVER_RETRY_SECONDS:
        return State.LOGIN
    server = ctx.config.reroll.server
    y = find_server_row(ctx, server)
    if y is None:
        ctx.logger.warning("server %s not found on the Select Server screen", server)
        return State.LOGIN
    ctx.logger.info("selecting server %s", server)
    ctx.session.input.click(SERVER_SELECT_X, y)
    ctx.last_server_click = time.monotonic()
    ctx.wait(AFTER_CLICK_SECONDS)
    return State.LOGIN


def _tap_title(ctx: Context) -> State:
    """Tap once the title screen has finished loading, and again while it stays on screen.

    The background shows first, and a tap is lost until the buttons appear.
    """
    if ctx.find("title_ready") is None:
        return State.LOGIN
    if time.monotonic() - ctx.last_title_tap < TITLE_RETRY_SECONDS:
        return State.LOGIN
    ctx.logger.info("tapping the title screen")
    ctx.session.input.click(*TAP_POINT)
    ctx.last_title_tap = time.monotonic()
    ctx.title_tapped = True
    ctx.wait(AFTER_CLICK_SECONDS)
    return State.LOGIN


def login(ctx: Context) -> State:
    """Do one step of the way into the game. With a reset pending, stop at the lobby to do it."""
    if not ctx.attach():
        return State.LAUNCH
    # A tap meant for the title screen could land on this popup's buttons.
    if ctx.find("patch_update", PATCH_REGION) is not None:
        return ctx.halt(PATCH_STOP)
    # Checked before the title screen, whose template also matches behind this screen.
    if ctx.find("select_server") is not None:
        return _select_server(ctx)
    if ctx.find("title_screen") is not None:
        return _tap_title(ctx)
    if ctx.title_tapped and not ctx.reset_pending:
        # The title screen has gone, so this time the tap really worked.
        ctx.title_tapped = False
        ctx.logger.info("title screen passed")
        return State.STORY
    if ctx.find("lobby_menu") is not None:
        return State.RESET_ACCOUNT if ctx.reset_pending else State.STORY
    if ctx.reset_pending:
        # The lobby hides its menu after a while, and a click brings it back.
        ctx.session.input.click(*WAKE_POINT)
        ctx.wait(AFTER_CLICK_SECONDS)
        return State.LOGIN
    # Right after picking a server the game is loading its title screen: wait for it.
    if time.monotonic() - ctx.last_server_click < SERVER_LOADING_SECONDS:
        return State.LOGIN
    # Already past the title screen, for example when the bot starts during the intro.
    return State.STORY if ctx.logged_in_elsewhere() else State.LOGIN
