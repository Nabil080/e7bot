"""RESET_ACCOUNT: reset the game server from the settings screen, after reading what will be reset."""

from __future__ import annotations

import itertools
import re

from core.config import State
from core.state import Context
from states.story import EVENT_HEADER_REGION, LOBBY_REGION, QUIT_CANCEL_POINT, QUIT_REGION, SUMMON_HEADER_REGION
from states.summon import BACK_POINT

WAKE_POINT = (640, 400)
AFTER_ACTION_SECONDS = 1.5
MAX_WAKE_CLICKS = 8

TITLE_REGION = (480, 135, 320, 48)
SERVER_REGION = (480, 430, 320, 26)
NICKNAME_REGION = (480, 456, 320, 26)
FIELD_REGION = (505, 350, 266, 40)
FIELD_POINT = (638, 369)
FIELD_PLACEHOLDER = "tap here"
# Generated nicknames only use these, and without the limit Tesseract reads a final 0 as "0O".
NICKNAME_CHARACTERS = "abcdefghijklmnopqrstuvwxyz0123456789#"
NICKNAME_LABEL_CHARACTERS = NICKNAME_CHARACTERS + "N:"
# These look the same in the game's font, and OCR cannot be trusted to tell them apart.
LOOKALIKES = ("1li", "0o", "5s")
MAX_SPELLINGS = 16
NICKNAME_LENGTH = 6
RESET_LABEL = "reset server"
FINAL_BODY_REGION = (480, 333, 320, 28)
FINAL_BODY_TEXT = "reset the account"


def parse_server(text: str) -> str | None:
    """Return the server name from 'Current Server: Foundry'."""
    found = re.search(r"Current\s*Server\s*:\s*(\S+)", text, re.IGNORECASE)
    return found.group(1) if found else None


def parse_nickname(text: str) -> str | None:
    """Return a generated nickname, always epic7# and 6 characters, or None for any other reading.

    Tesseract can read one lookalike twice, as "o0" for an o: such a pair counts as one character.
    """
    found = re.search(r"Nickname\s*:\s*epic7#([a-z0-9]{6,8})\s*$", text)
    if not found:
        return None
    suffix = found.group(1)
    while len(suffix) > NICKNAME_LENGTH:
        pair = next((index for index in range(len(suffix) - 1) if _same_lookalike(suffix[index], suffix[index + 1])), None)
        if pair is None:
            return None
        suffix = suffix[:pair] + suffix[pair + 1:]
    return f"epic7#{suffix}"


def _same_lookalike(first: str, second: str) -> bool:
    """Return whether two different characters look the same in the game's font."""
    return first != second and any(first in group and second in group for group in LOOKALIKES)


def nickname_spellings(nickname: str) -> list[str]:
    """Return the ways to spell what OCR read, starting with the reading itself, then the closest ones."""
    prefix, suffix = nickname.split("#")
    options = [next((group for group in LOOKALIKES if character in group), character) for character in suffix]
    spellings = ["".join(letters) for letters in itertools.product(*options)]
    spellings.sort(key=lambda spelling: sum(a != b for a, b in zip(spelling, suffix)))
    return [f"{prefix}#{spelling}" for spelling in spellings[:MAX_SPELLINGS]]


def _click(ctx: Context, x: int, y: int) -> None:
    """Click and leave the game time to react."""
    ctx.session.input.click(x, y)
    ctx.wait(AFTER_ACTION_SECONDS)


def _fill_or_confirm(ctx: Context) -> State:
    """Handle the Reset Server dialog. Every check must pass, or nothing is clicked."""
    frame = ctx.frame
    title = ctx.ocr.read(frame, TITLE_REGION).casefold()
    if "delete" in title or RESET_LABEL not in title:
        return ctx.halt(f"refusing to continue: the dialog title reads {title!r}")

    server_text = ctx.ocr.read(frame, SERVER_REGION)
    server = parse_server(server_text)
    allowed = ctx.config.reroll.server
    if server != allowed:
        return ctx.halt(f"refusing to reset: server read as {server_text!r}, reroll server is {allowed}")

    reading = ctx.ocr.read(frame, NICKNAME_REGION, whitelist=NICKNAME_LABEL_CHARACTERS)
    nickname = parse_nickname(reading)
    if nickname is None:
        ctx.logger.warning("nickname not read from %r: reading again", reading)
        return State.RESET_ACCOUNT

    field = ctx.ocr.read(frame, FIELD_REGION)
    if not field or FIELD_PLACEHOLDER in field.casefold():
        spellings = nickname_spellings(nickname)
        if ctx.nickname_attempt >= len(spellings):
            return ctx.halt(f"the game refused all {len(spellings)} spellings of the nickname {nickname}")
        typed = spellings[ctx.nickname_attempt]
        # Shown in the window too: several tries are normal when the OCR mixes up 0/o or 1/l.
        ctx.logger.info(
            "typing the nickname %s for server %s (spelling %d of %d)",
            typed, server, ctx.nickname_attempt + 1, len(spellings),
        )
        _click(ctx, *FIELD_POINT)
        ctx.session.input.type_text(typed)
        ctx.wait(AFTER_ACTION_SECONDS)
        return State.RESET_ACCOUNT

    # The game only enables Confirm once the field matches its nickname. Reading the field back
    # is less reliable: OCR drops the j of epic7#j9mu7w on every try.
    # So a wrong spelling is harmless: it leaves Confirm disabled, and the next one gets its turn.
    confirm = ctx.find("reset_confirm")
    if confirm is None:
        # A wrong character cannot be fixed in place: start the dialog over.
        spellings = nickname_spellings(nickname)
        tried = spellings[min(ctx.nickname_attempt, len(spellings) - 1)]
        ctx.logger.warning("Confirm stays disabled for nickname %r: cancelling the dialog, trying the next spelling", tried)
        ctx.nickname_attempt += 1
        cancel = ctx.find("reset_cancel")
        if cancel is not None:
            _click(ctx, *cancel.center)
        return State.RESET_ACCOUNT

    ctx.logger.info("nickname accepted for server %s", server)
    _click(ctx, *confirm.center)
    return State.RESET_ACCOUNT


def _final_confirm(ctx: Context) -> State:
    """Answer the 'Are you sure' dialog, the point of no return."""
    body = ctx.ocr.read(ctx.frame, FINAL_BODY_REGION).casefold()
    if "delete" in body or FINAL_BODY_TEXT not in body:
        return ctx.halt(f"refusing to continue: the final dialog reads {body!r}")
    confirm = ctx.find("reset_final_confirm")
    if confirm is None:
        return State.RESET_ACCOUNT
    ctx.logger.warning("confirming the reset")
    ctx.reset_confirmed = True
    _click(ctx, *confirm.center)
    return State.RESET_ACCOUNT


def _click_reset_button(ctx: Context) -> State:
    """Open the Reset Server dialog. Delete Account sits right beside it, so check the label first."""
    button = ctx.find("settings_reset_server")
    if button is None:
        return State.RESET_ACCOUNT
    region = (button.x, button.y, button.width, button.height)
    label = ctx.ocr.read(ctx.frame, region).casefold()
    if RESET_LABEL not in label:
        return ctx.halt(f"refusing to click: the button reads {label!r}")
    _click(ctx, *button.center)
    return State.RESET_ACCOUNT


def reset_account(ctx: Context) -> State:
    """Do one step of the path: lobby, menu, settings, dialogs, then the Select Server screen."""
    if not ctx.config.reset.enabled:
        return ctx.halt("the reset is turned off in the settings: the account is left as it is")
    if ctx.goal in ("story", "climb", "theater", "arena", "shop", "penguins", "huche"):
        # Playing a kept account: nothing may erase it.
        return ctx.halt("never resetting a kept account: the account is left as it is")
    if not ctx.attach():
        return State.RECOVER

    if ctx.reset_confirmed:
        # Past Confirm, the only move is to wait: any click could land on the new account.
        if ctx.find("select_server") is None:
            return State.RESET_ACCOUNT
        ctx.reset_confirmed = False
        ctx.reset_pending = False
        ctx.start_new_account()
        ctx.resets_done += 1
        ctx.logger.info("reset %d done", ctx.resets_done)
        if ctx.on_reset is not None:
            ctx.on_reset()
        return ctx.halt("--once: one account done") if ctx.once else State.LOGIN

    # Checked first: the first dialog stays visible, dimmed, behind this one.
    if ctx.find("reset_final_body") is not None:
        return _final_confirm(ctx)
    if ctx.find("reset_dialog_title") is not None:
        return _fill_or_confirm(ctx)
    if ctx.find("settings_header") is not None:
        return _click_reset_button(ctx)
    # Checked before the gear and the menu button: these menus show lookalikes that do something else.
    if ctx.find("summon_header", SUMMON_HEADER_REGION) is not None or ctx.find("event_header", EVENT_HEADER_REGION) is not None:
        ctx.wake_clicks = 0
        _click(ctx, *BACK_POINT)
        return State.RESET_ACCOUNT
    gear = ctx.find("menu_settings")
    if gear is not None:
        ctx.wake_clicks = 0
        _click(ctx, *gear.center)
        return State.RESET_ACCOUNT
    if ctx.find("quit_popup", QUIT_REGION) is not None:
        _click(ctx, *QUIT_CANCEL_POINT)
        return State.RESET_ACCOUNT
    menu = ctx.find("lobby_menu")
    if menu is not None:
        ctx.wake_clicks = 0
        # The button still matches through the daily popups that pile up over the lobby, but
        # those cover Episode. A click would do nothing, and Esc closes them one by one.
        if ctx.find("episode", LOBBY_REGION) is None:
            ctx.session.input.press("esc")
            ctx.wait(AFTER_ACTION_SECONDS)
            return State.RESET_ACCOUNT
        _click(ctx, *menu.center)
        return State.RESET_ACCOUNT
    # Checked before the title screen, whose template also matches behind this screen.
    if ctx.find("select_server") is not None or ctx.find("title_screen") is not None:
        ctx.logger.info("not logged in: going through the login first")
        return State.LOGIN

    # The lobby hides its menu after a while, and a click brings it back. If it does not,
    # the game is somewhere else, such as a video: get to the lobby first, then come back.
    ctx.wake_clicks += 1
    if ctx.wake_clicks > MAX_WAKE_CLICKS:
        ctx.wake_clicks = 0
        if ctx.reset_from_saved_route:
            # A finished route saved before a restart, but the game plays a new account's intro:
            # the user reset it by hand. Resetting again would only throw a fresh account away.
            ctx.logger.info("this account is new, the saved route was an older one's: starting the route over")
            ctx.reset_pending = False
            ctx.reset_from_saved_route = False
            ctx.start_new_account()
            return State.STORY
        ctx.logger.info("the lobby is not in view: going through the intro before the reset")
        return State.STORY
    _click(ctx, *WAKE_POINT)
    return State.RESET_ACCOUNT
