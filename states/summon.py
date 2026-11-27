"""SUMMON: claim the free summons of the event, pull them all on Covenant Summon, then keep or reset.

Like STORY, each tick reads the screen and does what it asks. Nothing is ever pulled without the
Free label in view, and an account is only reset once every free summon has been read.
"""

from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path

import cv2

from core.capture import is_blank
from core.config import State
from core.heroes import UNRANKED, identify_hero, load_moonlight_heroes, load_ranking, rank_of
from core.state import Card, Context
from core.vision import Region
from states.story import (
    EVENT_HEADER_REGION,
    AFTER_CLICK_SECONDS,
    LETTER_REGION,
    LOBBY_REGION,
    LOBBY_STABLE_SECONDS,
    POPUP_TAPS,
    QUIT_CANCEL_POINT,
    QUIT_REGION,
    RESULTS_REGION,
    SUMMON_HEADER_REGION,
    Tap,
    UNKNOWN_WAIT_SECONDS,
    click_once,
    do_tap,
    letter,
    seen,
)

BACK_POINT = (38, 34)
RESULT_BACK_POINT = (156, 661)
BOOK_POINT = (1085, 500)
COVENANT_TAB_POINT = (108, 640)
WISH_LIST_POINT = (730, 558)
WISH_CONFIRM_POINT = (747, 460)
WISH_CLOSE_POINT = (640, 694)
SUMMON_ONE_POINT = (880, 660)
SUMMON_TEN_POINT = (1136, 660)
SUMMON_AGAIN_POINT = (1150, 661)
LIST_DRAG_FROM = (120, 620)
LIST_DRAG_TO = (120, 200)
MAX_LIST_DRAGS = 6
BUY_BOOKMARKS_REGION = (479, 74, 324, 52)
BUY_CLOSE_POINT = (640, 694)
DATE_CHANGED_REGION = (530, 194, 222, 56)
DATE_CLOSE_POINT = (640, 480)
NOTICE_QUESTION_REGION = (335, 290, 610, 46)
NOTICE_CONFIRM_POINT = (747, 460)
# From the face, this lands on the bubble whichever of its two sizes it has.
NOTICE_CLICK_OFFSET = (90, 20)
ARTIFACT_DETAILS_REGION = (745, 52, 180, 44)
ASIDE_POINT = (1240, 400)

EVENT_ICON_REGION = (10, 335, 100, 60)
EVENT_LABEL_REGION = (10, 350, 100, 28)
EVENT_LABEL_WHITE_LEVEL = 210
EVENT_POINT = (57, 355)
SUMMON_ENTRY_REGION = (1120, 430, 130, 50)
SUPPORT_TAB_REGION = (0, 80, 260, 620)
SUPPORT_TITLE_REGION = (1010, 185, 245, 66)
BOOK_DONE_REGION = (1050, 465, 75, 65)
REVEAL_SKIP_REGION = (1180, 12, 90, 48)
REVEAL_NEXT_REGION = (1040, 636, 170, 50)
AGAIN_FREE_REGION = (975, 640, 75, 46)
RESULT_BACK_REGION = (120, 640, 80, 44)
COVENANT_TAB_REGION = (40, 605, 170, 75)
WISH_EMPTY_REGION = (730, 538, 88, 40)
FREE_ONE_REGION = (795, 640, 70, 44)
FREE_TEN_REGION = (1048, 640, 70, 44)
FREE_ALL_REGION = (1140, 592, 78, 36)
WISH_TITLE_REGION = (105, 44, 240, 52)
WISH_SLOT_REGION = (140, 190, 62, 62)
WISH_APPLY_REGION = (250, 600, 206, 44)
WISH_CONFIRM_REGION = (420, 302, 440, 48)

# The ten cards of Summon Results always sit at these places, as (left, top).
CARD_SLOTS = (
    (100, 166), (100, 290), (100, 415),
    (472, 105), (472, 225), (472, 350), (472, 475),
    (845, 166), (845, 290), (845, 415),
)
CARD_HERO_STARS = (8, 38, 122, 34)
CARD_ARTIFACT_STARS = (105, 25, 110, 32)
CARD_ELEMENT = (40, 4, 44, 40)
CARD_HERO_NAME = (8, 64, 240, 34)
SINGLE_HERO_STARS = (0, 72, 500, 46)
SINGLE_ARTIFACT_STARS = (20, 115, 150, 35)
SINGLE_NAME = (24, 62, 205, 56)
NAME_WHITE_LEVEL = 200
FIVE_STARS = 5
# A sparkle runs over the stars and can hide some on one frame: keep the most seen over a while.
READS_PER_RESULT = 6
# Toasts such as a finished mission cover a card for a few seconds: read again before stopping.
MAX_UNREADABLE_RETRIES = 5
UNREADABLE_WAIT_SECONDS = 1.5

SUMMON_TAPS = POPUP_TAPS + (
    Tap("hero joined", "ok_button", (560, 440, 160, 120)),
    Tap("skip tutorial", "skip_tutorial", (1080, 10, 170, 48)),
    Tap("tap to close", "tap_to_close", (540, 430, 200, 290)),
)


def _click(ctx: Context, x: int, y: int) -> State:
    click_once(ctx, x, y)
    return State.SUMMON


def _stars(ctx: Context, name: str, region: Region) -> int:
    return len(ctx.library.find_every(ctx.frame, name, region))


def _is_moonlight(ctx: Context, slot: tuple[int, int]) -> bool:
    """Return whether a card shows the element of Moonlight heroes, either the moon or the sun."""
    region = _offset(slot, CARD_ELEMENT)
    return any(ctx.library.find_every(ctx.frame, name, region) for name in ("element_moon", "element_sun"))


def _offset(slot: tuple[int, int], region: Region) -> Region:
    """Return a region given relative to a card, in game coordinates."""
    return (slot[0] + region[0], slot[1] + region[1], region[2], region[3])


def _read_name(ctx: Context, region: Region) -> str:
    """Read a hero's name, white on artwork."""
    return " ".join(ctx.ocr.read(ctx.frame, region, white_level=NAME_WHITE_LEVEL).split())


def _merge(ctx: Context, cards: list[Card]) -> None:
    """Add one reading to the current result, keeping the most stars seen per card."""
    progress = ctx.summon
    if not progress.current:
        progress.current = cards
    else:
        for known, card in zip(progress.current, cards):
            known.hero_stars = max(known.hero_stars, card.hero_stars)
            known.artifact_stars = max(known.artifact_stars, card.artifact_stars)
            known.moonlight = known.moonlight or card.moonlight
    progress.reads += 1


def _save_result(ctx: Context) -> Path:
    """Keep a picture of the result, to look at later."""
    folder = ctx.config.logging.log_dir / "results"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{datetime.now():%Y%m%d_%H%M%S}.png"
    cv2.imwrite(str(path), ctx.frame)
    return path


def _only_empty_slots_at_the_end(cards: list[Card]) -> bool:
    """Return whether the unreadable cards are the last ones, with no star of either kind seen."""
    unreadable = [index for index, card in enumerate(cards) if not card.readable()]
    if not unreadable or unreadable != list(range(len(cards) - len(unreadable), len(cards))):
        return False
    return all(cards[index].hero_stars == 0 and cards[index].artifact_stars == 0 for index in unreadable)


def _finish_reading(ctx: Context, names: list[str], stay: State = State.SUMMON) -> State | None:
    """Judge a result that has been read enough times. Returns HALT if it cannot be trusted.

    A card hidden for a moment, as by a mission toast, is read again first: `stay` is then returned.
    """
    progress = ctx.summon
    for card, name in zip(progress.current, names):
        card.name = name
    path = _save_result(ctx)
    ctx.logger.info("summon result: %s (%s)", ", ".join(str(card) for card in progress.current), path)
    unreadable = [str(index + 1) for index, card in enumerate(progress.current) if not card.readable()]
    if ctx.on_result is not None:
        read = progress.cards_read + (0 if unreadable else len(progress.current))
        ctx.on_result(read, ctx.config.summon.expected_pulls, str(path))
    if unreadable and progress.unread_retries < MAX_UNREADABLE_RETRIES:
        progress.unread_retries += 1
        ctx.logger.warning("card(s) %s unreadable, reading the result again (%d of %d)",
                           ", ".join(unreadable), progress.unread_retries, MAX_UNREADABLE_RETRIES)
        progress.current, progress.reads = [], 0
        ctx.wait(UNREADABLE_WAIT_SECONDS)
        return stay
    progress.unread_retries = 0
    if unreadable and _only_empty_slots_at_the_end(progress.current):
        # A last pull of fewer than ten leaves its last places empty: no stars ever showed there.
        ctx.logger.info("place(s) %s empty: a result of %d", ", ".join(unreadable), len(progress.current) - len(unreadable))
        progress.current = progress.current[:len(progress.current) - len(unreadable)]
        unreadable = []
    if unreadable:
        # Without knowing what this card is, a reset could throw away the hero being looked for.
        return ctx.halt(f"could not read summon result card(s) {', '.join(unreadable)}: see {path}")
    if ctx.on_cards is not None:
        ctx.on_cards(progress.current)
    for card in progress.current:
        if card.hero_stars == FIVE_STARS and card.moonlight:
            ctx.logger.warning("5-star Moonlight: %s", card)
            progress.moonlight_5star.append(card.name)
        elif card.hero_stars == FIVE_STARS and not card.element_known:
            progress.unsure.append(str(card))
    progress.cards_read += len(progress.current)
    progress.current = []
    progress.reads = 0
    progress.result_done = True
    ctx.save_route_pulls()
    return None


def _results(ctx: Context) -> State:
    """Read the ten cards of a Summon Results screen, then pull again while it is free."""
    seen(ctx, "summon results")
    progress = ctx.summon
    progress.pulling = False
    if not progress.result_done:
        _merge(ctx, [
            Card(
                _stars(ctx, "star_card", _offset(slot, CARD_HERO_STARS)),
                _stars(ctx, "star_artifact", _offset(slot, CARD_ARTIFACT_STARS)),
                _is_moonlight(ctx, slot),
            )
            for slot in CARD_SLOTS
        ])
        if progress.reads < READS_PER_RESULT:
            return State.SUMMON
        names = [
            _read_name(ctx, _offset(slot, CARD_HERO_NAME)) if card.hero_stars else ""
            for slot, card in zip(CARD_SLOTS, progress.current)
        ]
        stop = _finish_reading(ctx, names)
        if stop is not None:
            return stop
    if ctx.find("again_free", AGAIN_FREE_REGION) is not None:
        return _pull(ctx, *SUMMON_AGAIN_POINT)
    return _click(ctx, *RESULT_BACK_POINT)


def _single_result(ctx: Context) -> State:
    """Read the result of a single summon, which has no results screen. Back leads to the x10."""
    seen(ctx, "summon: one result")
    progress = ctx.summon
    progress.pulling = False
    if not progress.result_done:
        _merge(ctx, [Card(
            _stars(ctx, "star_single", SINGLE_HERO_STARS),
            _stars(ctx, "star_artifact", SINGLE_ARTIFACT_STARS),
            element_known=False,
        )])
        if progress.reads < READS_PER_RESULT:
            return State.SUMMON
        name = _read_name(ctx, SINGLE_NAME) if progress.current[0].hero_stars else ""
        stop = _finish_reading(ctx, [name])
        if stop is not None:
            return stop
    return _click(ctx, *RESULT_BACK_POINT)


def _count_run(ctx: Context) -> None:
    """Report the time of an account played since its reset. A resumed one would skew the average."""
    if ctx.on_run_done is not None and ctx.account_started_at is not None:
        ctx.on_run_done(time.monotonic() - ctx.account_started_at)
    ctx.account_started_at = None


def _decide(ctx: Context) -> str | None:
    """Return why the account must not be reset, or None. In doubt it is kept: a reset cannot be undone."""
    progress = ctx.summon
    rules = ctx.config.summon
    if progress.unsure:
        return f"account kept: {', '.join(progress.unsure)}"
    if len(progress.moonlight_5star) >= rules.min_moonlight_5star:
        return "account kept: " + ", ".join(f"{name} ML 5*" for name in progress.moonlight_5star)
    if not rules.stop_heroes and not rules.top_rules:
        return None
    known = load_moonlight_heroes()
    heroes = []
    for reading in progress.moonlight_5star:
        hero = identify_hero(reading, known)
        if hero is None:
            return f"account kept: {reading} ML 5*, a name that could not be matched to a hero"
        heroes.append(hero)
    ranking = load_ranking()
    for rule in rules.top_rules:
        # A hero missing from the ranking counts as rank 0, above them all.
        ranked = [hero for hero in heroes if (rank_of(hero, ranking) or UNRANKED) <= rule.rank]
        if len(ranked) >= rule.count:
            return "account kept: " + ", ".join(f"{hero} ML 5*" for hero in ranked)
    found = [hero for hero in heroes if hero in rules.stop_heroes]
    return "account kept: " + ", ".join(f"{name} ML 5*" for name in found) if found else None


def _no_free_left(ctx: Context) -> State:
    """Decide what the free summons were worth. Anything unexpected stops the bot before a reset."""
    progress = ctx.summon
    if ctx.goal == "abyss":
        # The long route decides after its own pulls: hand back to it.
        if progress.cards_read == 0:
            ctx.logger.warning("no free event summon to pull: they may have expired at the daily reset")
        else:
            ctx.logger.info("event's free summons pulled, %d results read so far", progress.cards_read)
        progress.event_done = True
        return State.ROUTE
    reason = _decide(ctx)
    if reason is not None:
        _count_run(ctx)
        return ctx.halt(reason)
    expected = ctx.config.summon.expected_pulls
    if progress.cards_read != expected:
        return ctx.halt(f"no free summon left but {progress.cards_read} of {expected} results were read: not resetting")
    ctx.logger.info("nothing to keep in %d summons", progress.cards_read)
    _count_run(ctx)
    progress.finished = True
    return _click(ctx, *BACK_POINT)


def _summon_menu(ctx: Context) -> State:
    """On the summon menu, get to Covenant Summon. Only a visible Free label allows a pull."""
    seen(ctx, "summon menu")
    ctx.summon.pulling = False
    if ctx.summon.finished:
        return _click(ctx, *BACK_POINT)
    if ctx.find("covenant_tab", COVENANT_TAB_REGION) is None:
        return _click(ctx, *COVENANT_TAB_POINT)
    if ctx.find("wish_empty", WISH_EMPTY_REGION) is not None:
        return _click(ctx, *WISH_LIST_POINT)
    if ctx.find("free_x1", FREE_ONE_REGION) is None or ctx.find("free_x10", FREE_TEN_REGION) is None:
        return _no_free_left(ctx)
    # The x10 opens up after one single summon, which is when this counter stops showing them all.
    # A result counts as done until the next pull starts: a first click may only silence the hero.
    ctx.summon.result_done = False
    if ctx.find("free_all", FREE_ALL_REGION) is not None:
        return _pull(ctx, *SUMMON_ONE_POINT)
    return _pull(ctx, *SUMMON_TEN_POINT)


def _wish_list(ctx: Context) -> State:
    """Covenant Summon refuses to pull without a wish list: apply the recommended one, then close."""
    seen(ctx, "wish list")
    if ctx.find("wish_slot_empty", WISH_SLOT_REGION) is None:
        return _click(ctx, *WISH_CLOSE_POINT)
    apply = ctx.find("wish_apply", WISH_APPLY_REGION)
    return _click(ctx, *apply.center) if apply is not None else State.SUMMON


def _event(ctx: Context) -> State:
    """In the event menu, find Summon Support, claim its free summons, and leave."""
    seen(ctx, "event menu")
    progress = ctx.summon
    if progress.free_claimed or ctx.find("support_book_done", BOOK_DONE_REGION) is not None:
        if not progress.free_claimed:
            if progress.book_clicked:
                ctx.logger.info("free summons claimed")
            else:
                # Already done when the page opened: nothing new, maybe gone with the daily reset.
                ctx.logger.warning("Summon Support had nothing to claim")
        progress.free_claimed = True
        return _click(ctx, *BACK_POINT)
    if ctx.find("support_title", SUPPORT_TITLE_REGION) is not None:
        progress.book_clicked = True
        return _click(ctx, *BOOK_POINT)
    tab = ctx.find("support_tab", SUPPORT_TAB_REGION)
    if tab is not None:
        return _click(ctx, *tab.center)
    if progress.list_drags >= MAX_LIST_DRAGS:
        if ctx.goal == "abyss":
            ctx.logger.info("Summon Support is not in the event list: no free summons to pull")
            progress.event_done = True
            return State.ROUTE
        return ctx.halt("Summon Support is not in the event list: has the event ended?")
    progress.list_drags += 1
    ctx.session.input.drag(*LIST_DRAG_FROM, *LIST_DRAG_TO)
    ctx.wait(AFTER_CLICK_SECONDS)
    return State.SUMMON


def _reads_event(ctx: Context) -> bool:
    """Return whether the lobby's Event label reads, whatever the artwork behind it."""
    text = ctx.ocr.read(ctx.frame, EVENT_LABEL_REGION, white_level=EVENT_LABEL_WHITE_LEVEL)
    return "event" in "".join(text.split()).casefold()


def _lobby(ctx: Context) -> State:
    """Once the lobby has settled, reset the account, or go claim or pull the free summons."""
    seen(ctx, "lobby")
    now = time.monotonic()
    if ctx.lobby_since is None:
        ctx.lobby_since = now
    if now - ctx.lobby_since < LOBBY_STABLE_SECONDS:
        return State.SUMMON
    ctx.lobby_since = None
    progress = ctx.summon
    if progress.finished:
        return State.RESET_ACCOUNT
    if progress.free_claimed:
        template, region = "summon_entry", SUMMON_ENTRY_REGION
    else:
        template, region = "event_icon", EVENT_ICON_REGION
    entry = ctx.find(template, region)
    if entry is None and template == "event_icon" and _reads_event(ctx):
        # The lobby's artwork changes behind the label and spoils the template: the word still reads.
        return _click(ctx, *EVENT_POINT)
    if entry is None:
        # Something covers it. If that lasts, the watchdog ends in a stop, never in a reset.
        ctx.logger.warning("%s is not on the lobby", template)
        return _unknown(ctx)
    return _click(ctx, *entry.center)


def _unknown(ctx: Context) -> State:
    """Leave a screen that stays unrecognized with Esc, which closes popups and goes back.

    Never a click: the lobby's neighbours include shops. During a pull nothing is sent at all,
    as its animations end on their own. If they do not, the watchdog stops the bot.
    """
    if is_blank(ctx.frame) or ctx.summon.pulling:
        return State.SUMMON
    now = time.monotonic()
    if ctx.unknown_since is None:
        ctx.unknown_since = now
    elif now - ctx.unknown_since >= UNKNOWN_WAIT_SECONDS:
        ctx.unknown_since = None
        ctx.session.input.press("esc")
        ctx.wait(AFTER_CLICK_SECONDS)
    return State.SUMMON


def _pull(ctx: Context, x: int, y: int) -> State:
    """Click a summon button. Nothing else is sent until its result shows."""
    ctx.summon.pulling = True
    return _click(ctx, x, y)


def summon(ctx: Context) -> State:
    """Do one step, checking the screens from the most specific to the least."""
    if not ctx.attach():
        return State.RECOVER

    lobby = ctx.find("episode", LOBBY_REGION)
    if lobby is None:
        ctx.lobby_since = None

    if ctx.find("quit_popup", QUIT_REGION) is not None:
        seen(ctx, "quit popup")
        return _click(ctx, *QUIT_CANCEL_POINT)
    # Free summons expire when the day changes, with the Free label still showing until the next
    # screen. Past that, a click on it pays: never go on, and never reset on what was not pulled.
    if ctx.find("buy_bookmarks", BUY_BOOKMARKS_REGION) is not None:
        _click(ctx, *BUY_CLOSE_POINT)
        return ctx.halt("the game asks to buy bookmarks: the free summons are gone, not resetting")
    if ctx.find("date_changed", DATE_CHANGED_REGION) is not None:
        _click(ctx, *DATE_CLOSE_POINT)
        return ctx.halt("the day changed during the summons: the unused ones expired, not resetting")
    # This bubble stays for hours, switching messages, and it can hide a card or some of its stars.
    if ctx.find("notice_question", NOTICE_QUESTION_REGION) is not None:
        seen(ctx, "notices off")
        return _click(ctx, *NOTICE_CONFIRM_POINT)
    notice = ctx.find("notice_avatar")
    if notice is not None:
        seen(ctx, "notice bubble")
        x, y = notice.center
        return _click(ctx, x + NOTICE_CLICK_OFFSET[0], y + NOTICE_CLICK_OFFSET[1])
    # Its window leaves enough of the results title showing to pass for the results screen.
    if ctx.find("artifact_details", ARTIFACT_DETAILS_REGION) is not None:
        seen(ctx, "artifact details")
        return _click(ctx, *ASIDE_POINT)
    # Before the taps: this list has a Tap to close of its own, and would get closed unfilled.
    if ctx.find("wish_confirm", WISH_CONFIRM_REGION) is not None:
        seen(ctx, "wish list confirm")
        return _click(ctx, *WISH_CONFIRM_POINT)
    if ctx.find("wish_title", WISH_TITLE_REGION) is not None:
        return _wish_list(ctx)

    if do_tap(ctx, SUMMON_TAPS) is not None:
        return State.SUMMON
    if ctx.find("letter_close", LETTER_REGION) is not None:
        letter(ctx)
        return State.SUMMON

    # Skip first: a dimmed Next also shows while a hero speaks, and would go through them one by one.
    skip = ctx.find("reveal_skip", REVEAL_SKIP_REGION)
    if skip is not None:
        seen(ctx, "summon: skip")
        ctx.summon.result_done = False
        return _click(ctx, *skip.center)
    if ctx.find("summon_results", RESULTS_REGION) is not None:
        return _results(ctx)
    # Skip stops on what is rare, and the results screen comes right after.
    next_button = ctx.find("reveal_next", REVEAL_NEXT_REGION)
    if next_button is not None:
        seen(ctx, "summon: rare result")
        return _click(ctx, *next_button.center)
    if ctx.find("result_back", RESULT_BACK_REGION) is not None:
        return _single_result(ctx)

    if ctx.find("summon_header", SUMMON_HEADER_REGION) is not None:
        return _summon_menu(ctx)
    if ctx.find("event_header", EVENT_HEADER_REGION) is not None:
        return _event(ctx)
    if lobby is not None:
        return _lobby(ctx)
    return _unknown(ctx)
