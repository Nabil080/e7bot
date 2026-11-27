"""STORY: read the screen and do what it asks, whichever order the game shows things in.

Videos, dialogues, tutorials, rewards, fights and the stage list all go through here,
so the same rules carry the intro and any later part of the story.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import cv2
import numpy as np

from core.capture import is_blank
from core.config import State
from core.state import Context
from core.vision import Region

Point = tuple[int, int]

SKIP_CONFIRM_POINT = (748, 467)
QUIT_CANCEL_POINT = (532, 460)
LETTER_CLOSE_POINT = (640, 693)
REWARD_CLOSE_POINT = (640, 530)
ANNOUNCEMENT_CONFIRM_POINT = (1142, 662)
CENTER_POINT = (640, 400)
FRIEND_CANCEL_POINT = (532, 527)
TRY_AGAIN_POINT = (1180, 661)
FAILED_REGION = (720, 122, 210, 72)
# A lost story stage shows FAILED in the middle instead, over Orbis Guide and Help. Its Try again is where the
# first skill sits, so unseen, the skill click restarted the stage without the loss being counted.
STORY_FAILED_REGION = (500, 270, 280, 56)
MAX_FAILS_IN_A_ROW = 10
# Pushing the story of a kept account: a stage lost this often needs a stronger team, not more tries.
MAX_STORY_FAILS_IN_A_ROW = 3
AUTO_ASSIGN_CONFIRM_POINT = (747, 495)
SPEED_POINT = (1073, 33)
AUTO_POINT = (1127, 33)
AFTER_CLICK_SECONDS = 0.3
# A screen can stay in view a moment after its click worked: this avoids clicking it a second time.
SAME_CLICK_SECONDS = 1.0
SAME_CLICK_PIXELS = 12
AFTER_SKILL_SECONDS = 0.5
CONNECTION_RETRY_SECONDS = 15.0

TITLE_REGION = (1038, 16, 226, 60)
SELECT_SERVER_REGION = (535, 62, 210, 60)
QUIT_REGION = (452, 300, 376, 48)
LETTER_REGION = (570, 675, 138, 38)
STAMP_REGION = (602, 433, 74, 70)
LOBBY_REGION = (1118, 158, 128, 56)
SUMMON_LOCK_REGION = (1236, 436, 38, 40)
PATCH_REGION = (525, 125, 230, 55)
SPEED_REGION = (1050, 10, 50, 50)
AUTO_ICON_REGION = (1102, 12, 50, 42)
PATCH_STOP = "the game offers a patch: choose Proceed yourself, then check the bot still recognizes the screens"
EVENT_HEADER_REGION = (55, 10, 90, 45)
SUMMON_HEADER_REGION = (55, 10, 130, 45)
RESULTS_REGION = (20, 26, 305, 56)
HOURGLASS_REGION = (12, 55, 42, 44)
PAUSE_MENU_REGION = (586, 164, 108, 54)
YIELD_POPUP_REGION = (430, 282, 420, 82)
STORY_LOG_REGION = (1072, 14, 54, 46)
PAUSE_REGION = (1228, 14, 44, 50)
TRAVEL_BAR_REGION = (20, 126, 48, 48)
READY_REGION = (1050, 634, 130, 56)
VIEW_STORY_REGION = (1040, 634, 152, 56)
START_QUEST_REGION = (1076, 634, 144, 56)
# The team screen of a stage played before: Start with its energy cost, Team CP on the left. Not clicked by the story.
TEAM_CP_REGION = (250, 626, 120, 28)
START_TEXT_REGION = (1070, 636, 130, 46)
START_POINT = (1131, 658)
# The Select Supporter screen some stages show before the team: its title and its Select Team button.
SUPPORTER_TITLE_REGION = (200, 70, 300, 50)
SELECT_TEAM_REGION = (1000, 630, 280, 70)
SELECT_TEAM_POINT = (1140, 657)
EPISODE_REGION = (60, 16, 230, 32)
CHAPTER_REGION = (56, 92, 200, 34)
STAGE_NUMBER_REGION = (96, 212, 22, 44)
# A locked stage greys Ready out: its green, saturation about 140, drops to about 80.
READY_GREEN_SATURATION = 110
# The stage list's selected node glows teal, in this column. The node above it sits this far up.
STAGE_NODES_REGION = (0, 70, 95, 550)
GLOW_MIN_PIXELS_PER_ROW = 6
STAGE_NODE_SPACING = 100
STAGE_NODE_X = 52
STAGE_NODE_TOP_Y = 125
MAX_LOCKED_STAGE_PICKS = 3
# A cleared stage carries a green tick at the bottom right of its node, about this far below the node's center.
TICK_REGION = (60, 70, 30, 550)
TICK_MIN_WIDTH = 4
TICK_MIN_PEAK = 9
TICK_MIN_ROWS = 6
TICK_ROW_GAP = 6
TICK_BELOW_NODE = 18
# The selected node's glow, left of the node. A cleared stage glows greener than a new one, so the test is looser.
GLOW_REGION = (0, 70, 30, 550)
# The list ends above the chapter's progress bar: a node lower than this is out of view.
STAGE_LIST_BOTTOM = 585
# Dragging the list up brings the next nodes into view. Short enough that the selected node, low in the list
# when a scroll is needed, stays in view.
STAGE_LIST_DRAG = ((STAGE_NODE_X, 520), (STAGE_NODE_X, 300))
# The arrow to the next chapter is the rightmost white mark in this band. Long names wrap, so it stays near x 250.
CHAPTER_ARROW_REGION = (150, 92, 130, 34)
CHAPTER_ARROW_WHITE = 100
CHAPTER_ARROW_SHARE = 0.6
# The stage list's tree button opens Select Episode. A pin marks where the story stands: on the episode's row in the
# list on the left (blue; the world episode's is orange), then on the chapter's card on the right.
EPISODE_TREE_POINT = (940, 662)
EPISODE_SELECT_REGION = (56, 10, 200, 50)
EPISODE_PINS_REGION = (20, 110, 70, 590)
CHAPTER_PINS_REGION = (380, 280, 900, 420)
EPISODE_ROW_X = 180
# The card's pin sits at its bottom left: its middle is up and to the right.
CHAPTER_CARD_OFFSET = (98, -48)
EPISODE_LIST_DRAG = ((EPISODE_ROW_X, 600), (EPISODE_ROW_X, 260))
CHAPTER_MAP_DRAG = ((1150, 480), (500, 480))
MAX_EPISODE_SCROLLS = 3
# One trip to Select Episode per stage started: a pin that leads to a cleared stage again falls back to the walk.
MAX_EPISODE_TRIPS = 1
EPISODE_STEP_WAIT_SECONDS = 1.5
# Cleared stages are walked through one by one: the next node (or a scroll to it) twice, then the next chapter
# twice, then the bot stops. A moved selection starts the count again.
CLEARED_STEP_WAIT_SECONDS = 1.0
NEXT_NODE_TRIES = 2
MAX_CLEARED_STEPS = 4

HERO_ADD_REGION = (930, 200, 90, 400)
SLOT_PLUS_REGION = (300, 250, 420, 280)
EMPTY_SLOT_POINT = (587, 425)
HERO_LIST_LEFT = 900
# Starting a stage calls the server, so leave it some time before deciding it did not work.
START_QUEST_WAIT_SECONDS = 4.0
START_QUEST_TRIES = 2               # clicks on Start Quest before the stage is thought to want a hero
# A hero clicked this many times in the list without a plus showing is already in the team: the team is full, and
# Start Quest failed for another reason (seen once: "This Hero has already been selected" for three minutes).
MAX_HERO_LIST_CLICKS = 4

# These texts are white, over artwork that changes with every stage.
EPISODE_WHITE_LEVEL = 150
CHAPTER_WHITE_LEVEL = 170
STAGE_WHITE_LEVEL = 180

SKILL_SPACING = 99
FIRST_SKILL_POINT = (1026, 660)
FIRST_SKILL_RIM_REGION = (965, 640, 22, 50)
FIRST_CANNOT_USE_REGION = (985, 636, 78, 40)
FIRST_COOLDOWN_REGION = (985, 640, 90, 38)
SKILL_COUNT = 3
# Skills slide in when a turn starts: wait until they are in place before reading them.
SKILLS_SETTLE_SECONDS = 0.3

YIELD_POINT = (640, 275)
RETURN_TO_GAME_POINT = (640, 502)
YIELD_END_POINT = (748, 460)
YIELD_CANCEL_POINT = (532, 460)

# The lobby shows briefly before the event letter, so it has to stay for a while to count.
LOBBY_STABLE_SECONDS = 3.0
# Screens fade into each other, so give the game some time before reacting to an unknown one.
UNKNOWN_WAIT_SECONDS = 0.8
# A skill's full-screen animation hides the fight's interface for this long at most.
SKILL_ANIMATION_SECONDS = 8.0
FIGHT_SCREENS = ("fight", "tag hint", "travelling")

SKIP_KEY = "esc"
MAX_HOME_PRESSES = 3
# Some moments of a fight ignore Esc, such as when the game wants a new skill used.
MAX_LEAVE_PRESSES = 5
ARM_SECONDS = 0.3
SKIP_ICON_REGION = (580, 300, 120, 120)
# The icon shows 0.3 s into the hold. Without it by then, this is not a video being skipped.
ICON_WAIT_SECONDS = 1.0
ICON_GONE_FRAMES = 2
POLL_SECONDS = 0.05
# The Auto icon spins while Auto is on: 0.67 of its white pixels change between two reads, against 0 when off.
AUTO_WHITE_LEVEL = 225
AUTO_MOVING_RATIO = 0.2
AUTO_READ_SECONDS = 0.3
# The icon also stops for about 1.5 s on a fight's finishing blow, with Auto on: a click then turned Auto off
# for the next fight. Only a longer stillness means Auto is off.
AUTO_STILL_SECONDS = 3.0
FIGHT_SETTINGS_CHAPTER = 3
SPEED_ICON_REGION = (1058, 18, 30, 32)
# The white of an unlocked button reaches 255, the grey of a locked one about 160.
UNLOCKED_WHITE = 220


@dataclass(frozen=True)
class Tap:
    """A screen recognized by a template and passed with one click on it, or on a fixed point."""

    screen: str
    template: str
    region: Region
    point: Point | None = None
    wait_after: float = 0.0


# Checked before the event letter, whose Tap to close they can show at the same place.
POPUP_TAPS = (
    # Recognized by its other button, the only one not to click: it leads to the paid banners.
    Tap("hero announcement", "go_to_summon", (1056, 566, 176, 50), ANNOUNCEMENT_CONFIRM_POINT),
    # Each tap asks the server again: no need to keep at it when it does not answer.
    Tap("connection error", "connection_error", (378, 302, 525, 46), (640, 445), CONNECTION_RETRY_SECONDS),
    Tap("skip popup", "skip_confirm_text", (455, 302, 370, 46), SKIP_CONFIRM_POINT),
    # A support friend helped in the fight: never send the request, Cancel it.
    Tap("friend request", "friend_request", (425, 218, 430, 88), FRIEND_CANCEL_POINT),
    # Shown before a fight against a much stronger enemy: fight anyway, and not be asked again today.
    Tap("battle guide", "battle_guide_skip", (772, 506, 162, 52)),
    Tap("auto assign", "auto_assign", (540, 190, 200, 60), AUTO_ASSIGN_CONFIRM_POINT),
    Tap("reward", "reward_title", (505, 140, 270, 58), REWARD_CLOSE_POINT),
)
# From the most specific to the least: popups show over the screens below them.
TAPS = (
    Tap("hero joined", "ok_button", (560, 440, 160, 120)),
    Tap("skip tutorial", "skip_tutorial", (1080, 10, 170, 48)),
    Tap("dialogue", "skip_story", (1170, 14, 100, 48)),
    Tap("tap to close", "tap_to_close", (540, 430, 200, 290)),
    Tap("stage clear", "clear_banner", (516, 400, 290, 80)),
    Tap("stage results", "result_confirm", (1090, 630, 170, 60)),
    Tap("rank up", "rank_up", (580, 195, 120, 50), CENTER_POINT),
    # The game waits for this during a fight, and no Skip Tutorial shows for it.
    Tap("tag hint", "tag_hint", (240, 620, 290, 58), (70, 640)),
)


ENERGY_TITLE_REGION = (485, 120, 310, 85)
# The leaf option sits on the left of the popup; the skystone one on the right is never clicked.
ENERGY_LEAF_REGION = (380, 305, 235, 135)
ENERGY_BUY_POINT = (746, 550)
# The user allowed leaves, which are common. This many in one run means something is off.
MAX_LEAVES_PER_RUN = 20

def refill_energy(ctx: Context) -> State | None:
    """Answer the Insufficient Energy popup with one leaf. Returns None when it is not in view.

    Never skystones: the leaf option is clicked first, which picks it, and Buy only follows on the
    next tick if the leaf is still where it was picked. Without a leaf option, the bot stops.
    """
    if ctx.find("energy_title", ENERGY_TITLE_REGION) is None:
        ctx.leaf_picked = False
        return None
    seen(ctx, "insufficient energy")
    leaf = ctx.find("energy_leaf", ENERGY_LEAF_REGION)
    if leaf is None:
        return ctx.halt("out of energy and no leaf to use: nothing was bought")
    if ctx.leaves_used >= MAX_LEAVES_PER_RUN:
        return ctx.halt(f"out of energy after {ctx.leaves_used} leaves in this run: nothing more was bought")
    if not ctx.leaf_picked:
        ctx.leaf_picked = True
        return _click(ctx, *leaf.center)
    ctx.leaf_picked = False
    ctx.leaves_used += 1
    if ctx.on_leaf is not None:
        ctx.on_leaf()
    ctx.logger.info("out of energy: one leaf used, %d in this run", ctx.leaves_used)
    return _click(ctx, *ENERGY_BUY_POINT)


# The popup offering to revive the team for skystones after a lost fight. Not captured yet: the regions
# are wide on purpose and can be tightened once the templates exist. Without its title template the
# popup is never recognized, so nothing is ever bought.
REVIVE_TITLE_REGION = (200, 60, 880, 420)
# The skystone drawn on the Revive button, which is clicked; the Give up button next to it.
REVIVE_BUTTON_REGION = (200, 300, 880, 400)
REVIVE_GIVE_UP_REGION = (200, 300, 880, 400)
# The popup grows in: it is answered once it has been in view this long.
REVIVE_SETTLE_SECONDS = 1.0
# After Revive, the popup has this long to go away. If it stays, the bot stops rather than pay twice.
REVIVE_CLOSE_SECONDS = 5.0
# Without the Give up template, Esc declines; this many tries without effect stop the bot.
MAX_REVIVE_ESCAPES = 3
# A new fight starts on these screens: the revives of the last one no longer count.
FIGHT_END_SCREENS = ("team", "fight failed", "stage clear", "stage results", "stage list")


def allowed_revives(ctx: Context) -> int:
    """Return the skystone revives one fight may use: only the story mode of a kept account spends any."""
    return ctx.config.story.max_revives if ctx.goal == "story" else 0


def revive_popup(ctx: Context) -> State | None:
    """Answer the skystone revive offered after a lost fight. Returns None when it is not in view.

    Revive is clicked only while this fight has revives left under the setting, and only on the button
    that shows a skystone. Otherwise, or if the popup cannot be read, Give up is chosen and the game
    goes on to its FAILED screen, which counts the defeat as any other.
    """
    if ctx.find("story_revive_title", REVIVE_TITLE_REGION) is None:
        ctx.revive_seen_at = None
        ctx.revive_clicked_at = None
        ctx.revive_escapes = 0
        return None
    seen(ctx, "revive popup")
    now = time.monotonic()
    if ctx.revive_clicked_at is not None:
        if now - ctx.revive_clicked_at < REVIVE_CLOSE_SECONDS:
            return State.STORY
        return ctx.halt("the revive popup stayed after Revive: stopping rather than paying again")
    if ctx.revive_seen_at is None:
        ctx.revive_seen_at = now
    if now - ctx.revive_seen_at < REVIVE_SETTLE_SECONDS:
        return State.STORY
    allowed = allowed_revives(ctx)
    button = ctx.find("story_revive_skystone", REVIVE_BUTTON_REGION)
    if ctx.revives_this_fight < allowed and button is not None:
        ctx.revives_this_fight += 1
        ctx.revive_clicked_at = now
        ctx.logger.info("fight lost on %s: skystone revive %d of %d", ctx.stage, ctx.revives_this_fight, allowed)
        if ctx.on_story_revive is not None:
            ctx.on_story_revive()
        return _click(ctx, *button.center)
    if ctx.revives_this_fight < allowed:
        reason = "no skystone found on the Revive button"
    elif allowed:
        reason = f"the {allowed} revives of this fight are used"
    else:
        reason = "revives are turned off"
    give_up = ctx.find("story_revive_give_up", REVIVE_GIVE_UP_REGION)
    if give_up is not None:
        ctx.logger.info("revive declined: %s", reason)
        return _click(ctx, *give_up.center)
    if ctx.revive_escapes >= MAX_REVIVE_ESCAPES:
        return ctx.halt("the revive popup does not close: give up the fight yourself")
    ctx.revive_escapes += 1
    # The next try waits for the popup to have had time to close.
    ctx.revive_seen_at = now
    ctx.logger.info("revive declined with Esc: %s", reason)
    ctx.session.input.press(SKIP_KEY)
    ctx.wait(AFTER_CLICK_SECONDS)
    return State.STORY


def first_number(text: str) -> str:
    found = re.search(r"\d+", text)
    return found.group() if found else "?"


def read_stage(ctx: Context) -> str:
    """Read the episode's name, then chapter-stage, from the stage list: 6th World Episode 2-2."""
    # The help icon after the name reads as (?), (2) or (7): only the name counts.
    episode = re.sub(r"\(.*?\)|[?()]", " ", ctx.ocr.read(ctx.frame, EPISODE_REGION, white_level=EPISODE_WHITE_LEVEL))
    episode = " ".join(episode.split())
    chapter = ctx.ocr.read(ctx.frame, CHAPTER_REGION, single_line=False, white_level=CHAPTER_WHITE_LEVEL)
    found = re.search(r"Chapter\s*(\d+)", chapter)
    stage = ctx.ocr.read(
        ctx.frame, STAGE_NUMBER_REGION, single_line=False, whitelist="0123456789.", white_level=STAGE_WHITE_LEVEL
    )
    return f"{episode or '?'} {found.group(1) if found else '?'}-{first_number(stage)}"


def seen(ctx: Context, screen: str) -> None:
    """Note a recognized screen. A new screen counts as progress for the watchdog."""
    ctx.unknown_since = None
    ctx.unknown_tapped = False
    if not screen.startswith(FIGHT_SCREENS):
        ctx.fight_seen_at = None
    if screen in FIGHT_END_SCREENS:
        ctx.revives_this_fight = 0
    if screen != ctx.screen:
        if screen == "stage clear" and ctx.goal == "story" and ctx.on_story_won is not None:
            ctx.on_story_won()
        if screen != "team":
            ctx.start_clicked_at = None
            ctx.start_quest_clicks = 0
        ctx.logger.info("screen: %s", screen)
        ctx.screen = screen
        ctx.progressed = True


def click_once(ctx: Context, x: int, y: int) -> None:
    """Click, unless this same point was just clicked. The next tick sees whether the screen changed."""
    now = time.monotonic()
    if ctx.last_click is not None:
        last_x, last_y, at = ctx.last_click
        if now - at < SAME_CLICK_SECONDS and abs(x - last_x) <= SAME_CLICK_PIXELS and abs(y - last_y) <= SAME_CLICK_PIXELS:
            return
    ctx.last_click = (x, y, now)
    ctx.session.input.click(x, y)
    ctx.wait(AFTER_CLICK_SECONDS)


def _click(ctx: Context, x: int, y: int) -> State:
    click_once(ctx, x, y)
    return State.STORY


def do_tap(ctx: Context, taps: tuple[Tap, ...]) -> State | None:
    """Click the first of these screens in view. Returns None if none of them is."""
    for tap in taps:
        match = ctx.find(tap.template, tap.region)
        if match is not None:
            seen(ctx, tap.screen)
            state = _click(ctx, *(tap.point or match.center))
            if tap.wait_after:
                ctx.wait(tap.wait_after)
            return state
    return None


def _back_to_lobby(ctx: Context) -> State:
    """Leave a menu with Esc. A reset, or carrying on with an account, starts from the lobby."""
    ctx.session.input.press(SKIP_KEY)
    ctx.wait(AFTER_CLICK_SECONDS)
    return State.STORY


def letter(ctx: Context) -> State:
    """Claim the gift on the event letter, then close it."""
    stamp = ctx.find("event_stamp", STAMP_REGION)
    if stamp is not None:
        seen(ctx, "event stamp")
        return _click(ctx, *stamp.center)
    seen(ctx, "event letter")
    return _click(ctx, *LETTER_CLOSE_POINT)


def ready_is_greyed(ctx: Context) -> bool:
    """Return whether Ready shows in grey, as it does on a locked stage."""
    x, y, width, height = READY_REGION
    hsv = cv2.cvtColor(ctx.frame[y:y + height, x:x + width], cv2.COLOR_BGR2HSV)
    return float(hsv[..., 1].mean()) < READY_GREEN_SATURATION


def selected_node_y(ctx: Context) -> int | None:
    """Return the height of the selected stage node, found by its teal glow, or None without one."""
    x, y, width, height = STAGE_NODES_REGION
    area = ctx.frame[y:y + height, x:x + width].astype(np.int16)
    blue, green, red = area[..., 0], area[..., 1], area[..., 2]
    teal = (blue > 80) & (blue - red > 40) & (green - red > 35)
    rows = np.nonzero(teal.sum(axis=1) >= GLOW_MIN_PIXELS_PER_ROW)[0]
    return None if len(rows) == 0 else y + int(rows.mean())


def stage_ticks(ctx: Context) -> list[int]:
    """Return the height of each green "cleared" tick in the stage list, from top to bottom."""
    x, y, width, height = TICK_REGION
    area = ctx.frame[y:y + height, x:x + width].astype(np.int16)
    blue, green, red = area[..., 0], area[..., 1], area[..., 2]
    tick = (green > 150) & (green - blue > 90) & (green - red > 25)
    widths = tick.sum(axis=1)
    rows = np.nonzero(widths >= TICK_MIN_WIDTH)[0]
    groups: list[list[int]] = []
    for row in rows:
        # The tick's two strokes can show as two bands a few rows apart.
        if groups and row - groups[-1][-1] <= TICK_ROW_GAP:
            groups[-1].append(int(row))
        else:
            groups.append([int(row)])
    # The selected node's bright rim passes the colour test too, but only as a thin arc: the tick is wider.
    return [
        y + (group[0] + group[-1]) // 2
        for group in groups
        if group[-1] - group[0] + 1 >= TICK_MIN_ROWS and widths[group[0]:group[-1] + 1].max() >= TICK_MIN_PEAK
    ]


def selected_glow_y(ctx: Context) -> int | None:
    """Return the height of the selected node from its glow, new or cleared, or None without one."""
    x, y, width, height = GLOW_REGION
    area = ctx.frame[y:y + height, x:x + width].astype(np.int16)
    blue, green, red = area[..., 0], area[..., 1], area[..., 2]
    glow = (green - red > 25) & (blue - red > 15) & (green > 70)
    rows = np.nonzero(glow.sum(axis=1) >= 8)[0]
    return None if len(rows) == 0 else y + int(rows.mean())


def selected_tick_y(ctx: Context) -> int | None:
    """Return the height of the selected node's tick if the selected stage is cleared, otherwise None."""
    node = selected_glow_y(ctx)
    if node is None:
        return None
    # The glow's middle can sit well above the node when the artwork behind it is warm.
    return next((tick for tick in stage_ticks(ctx) if -10 <= tick - node <= 55), None)


def chapter_arrow(ctx: Context) -> Point | None:
    """Return the arrow to the next chapter: the rightmost white mark right of the chapter's name."""
    x, y, width, height = CHAPTER_ARROW_REGION
    area = ctx.frame[y:y + height, x:x + width]
    grey = area.min(axis=2)
    # The menu can be a little dimmed while it fades in: white is judged against the brightest mark in view.
    if grey.max() < CHAPTER_ARROW_WHITE:
        return None
    white = (grey >= CHAPTER_ARROW_SHARE * grey.max()).sum(axis=0) >= 3
    columns = np.nonzero(white)[0]
    return x + int(columns[-1]) - 4, y + height // 2


def _cleared_stage(ctx: Context, stage: str, tick: int) -> State:
    """The game selected a stage played before: walk down the list to the first one not cleared yet.

    The episode button can open an episode at its first stage, and replaying it would loop on it for ever.
    """
    seen(ctx, "stage list: cleared stage")
    if ctx.episode_trips < MAX_EPISODE_TRIPS:
        ctx.episode_trips += 1
        ctx.episode_picked = False
        ctx.episode_scrolls = 0
        ctx.logger.info("stage %s is cleared: looking for the story's pin in Select Episode", stage)
        click_once(ctx, *EPISODE_TREE_POINT)
        ctx.wait(EPISODE_STEP_WAIT_SECONDS)
        return State.STORY
    # The name is read at a fixed place, so the tick's height tells a moved selection or a scrolled list apart too.
    where = f"{stage} at {tick // 20}"
    if where != ctx.cleared_stage:
        ctx.cleared_stage = where
        ctx.cleared_stage_steps = 0
    step = ctx.cleared_stage_steps
    if step >= MAX_CLEARED_STEPS:
        return ctx.halt(f"story: stage {stage} is cleared and no later stage could be selected: pick it yourself")
    ctx.cleared_stage_steps += 1
    next_node = tick - TICK_BELOW_NODE + STAGE_NODE_SPACING
    if step < NEXT_NODE_TRIES and next_node <= STAGE_LIST_BOTTOM:
        ctx.logger.info("stage %s is cleared: selecting the next one", stage)
        click_once(ctx, STAGE_NODE_X, next_node)
    elif step < NEXT_NODE_TRIES:
        ctx.logger.info("stage %s is cleared: scrolling the list", stage)
        (x1, y1), (x2, y2) = STAGE_LIST_DRAG
        ctx.session.input.drag(x1, y1, x2, y2)
    else:
        arrow = chapter_arrow(ctx)
        if arrow is None:
            return ctx.halt(f"story: stage {stage} is cleared and no next chapter shows: pick the next stage yourself")
        ctx.logger.info("stage %s ends its chapter: opening the next chapter", stage)
        click_once(ctx, *arrow)
    ctx.wait(CLEARED_STEP_WAIT_SECONDS)
    return State.STORY


def story_pins(ctx: Context, region: Region) -> list[tuple[Point, bool]]:
    """Return each pin in a region, top to bottom, with whether it is blue rather than orange."""
    pins = []
    for match in ctx.library.find_every(ctx.frame, "story_pin", region):
        x, y = match.x, match.y
        patch = ctx.frame[y:y + match.height, x:x + match.width].astype(np.int16)
        blue = float(patch[..., 0].mean()) > float(patch[..., 2].mean())
        pins.append((match.center, blue))
    return sorted(pins, key=lambda pin: pin[0][1])


def _scroll_or_halt(ctx: Context, drag: tuple[Point, Point], what: str) -> State:
    """Scroll to look further for a pin, or stop once scrolling has not shown one."""
    if ctx.episode_scrolls >= MAX_EPISODE_SCROLLS:
        return ctx.halt(f"story: no pin shows {what} in Select Episode: pick the next stage yourself")
    ctx.episode_scrolls += 1
    (x1, y1), (x2, y2) = drag
    ctx.session.input.drag(x1, y1, x2, y2)
    ctx.wait(EPISODE_STEP_WAIT_SECONDS)
    return State.STORY


def _select_episode(ctx: Context) -> State:
    """Open the episode, then the chapter, that carry the story's pin: the stage list then opens on its stage."""
    seen(ctx, "select episode")
    if ctx.reset_pending or ctx.going_home or ctx.episode_trips == 0:
        # Not opened by the bot for a cleared stage: nothing to pick here.
        return _back_to_lobby(ctx)
    if not ctx.episode_picked:
        pins = story_pins(ctx, EPISODE_PINS_REGION)
        # The world episode keeps an orange pin once cleared: a blue one is where the story goes on.
        pins.sort(key=lambda pin: not pin[1])
        if not pins:
            return _scroll_or_halt(ctx, EPISODE_LIST_DRAG, "on any episode")
        (_, y), blue = pins[0]
        ctx.logger.info("Select Episode: opening the %s pinned episode", "blue" if blue else "orange")
        ctx.episode_picked = True
        ctx.episode_scrolls = 0
        click_once(ctx, EPISODE_ROW_X, y)
        ctx.wait(EPISODE_STEP_WAIT_SECONDS)
        return State.STORY
    pins = story_pins(ctx, CHAPTER_PINS_REGION)
    if not pins:
        return _scroll_or_halt(ctx, CHAPTER_MAP_DRAG, "on any chapter")
    (x, y), _ = pins[0]
    ctx.logger.info("Select Episode: opening the pinned chapter")
    dx, dy = CHAPTER_CARD_OFFSET
    click_once(ctx, x + dx, y + dy)
    ctx.wait(EPISODE_STEP_WAIT_SECONDS)
    return State.STORY


def _locked_stage(ctx: Context) -> State:
    """The game can select the stage after the one to play, still locked: select the one above it."""
    seen(ctx, "stage list: locked stage")
    node = selected_node_y(ctx)
    if node is None or ctx.locked_stage_picks >= MAX_LOCKED_STAGE_PICKS:
        return ctx.halt("the selected stage stays locked: pick the next stage to play yourself")
    ctx.locked_stage_picks += 1
    ctx.logger.info("the selected stage is locked: selecting the one above it")
    return _click(ctx, STAGE_NODE_X, max(STAGE_NODE_TOP_Y, node - STAGE_NODE_SPACING))


def _stage_list(ctx: Context, button: Point, greyed: bool = False) -> State:
    """Note which stage is selected, stop there if asked, otherwise start it."""
    seen(ctx, "stage list")
    if ctx.reset_pending or ctx.going_home:
        return _back_to_lobby(ctx)
    if greyed:
        return _locked_stage(ctx)
    ctx.locked_stage_picks = 0
    stage = read_stage(ctx)
    if "?" in stage:
        # Not clicking: this could be the stage to stop at. The watchdog covers a list that stays unreadable.
        return State.STORY
    if stage != ctx.stage:
        ctx.fails_in_a_row = 0
        ctx.stage = stage
        ctx.logger.info("stage %s", stage)
        if ctx.on_stage is not None:
            ctx.on_stage(stage)
    if stage == ctx.config.reroll.stop_at_stage:
        return ctx.halt(f"reached stage {stage}: over to you")
    if ctx.story_target is not None and stage == ctx.story_target:
        ctx.logger.info("story reached %s: back to the route", stage)
        ctx.story_reached = stage
        ctx.story_target = None
        return State.ROUTE
    tick = selected_tick_y(ctx)
    if tick is not None:
        return _cleared_stage(ctx, stage, tick)
    ctx.cleared_stage = None
    ctx.cleared_stage_steps = 0
    ctx.episode_trips = 0
    return _click(ctx, *button)


def _lobby(ctx: Context, episode: Point) -> State:
    """Once the lobby has settled, do the pending reset or go on with the story."""
    seen(ctx, "lobby")
    now = time.monotonic()
    if ctx.lobby_since is None:
        ctx.lobby_since = now
    if now - ctx.lobby_since < LOBBY_STABLE_SECONDS:
        return State.STORY
    ctx.lobby_since = None
    ctx.going_home = False
    if ctx.reset_pending:
        return State.RESET_ACCOUNT
    # On the long route, the story only runs while a step asks for it.
    if ctx.goal in ("abyss", "climb", "theater", "arena", "shop", "penguins", "huche", "gear_roll", "gear_sort", "gear_craft", "gear_account") and ctx.story_target is None:
        return State.ROUTE
    if not ctx.story_only and ctx.find("summon_lock", SUMMON_LOCK_REGION) is None:
        return State.SUMMON
    return _click(ctx, *episode)


def _add_hero(ctx: Context) -> State | None:
    """Do one step of adding the first hero of the list: empty slot, hero, plus on the slot.

    Returns None once done. The plus going away is what shows the hero was placed.
    """
    plus = ctx.find("slot_plus", SLOT_PLUS_REGION)
    if plus is not None:
        ctx.placing_hero = True
        ctx.route_data.pop("hero_list_clicks", None)
        return _click(ctx, *plus.center)
    if ctx.placing_hero:
        ctx.placing_hero = False
        ctx.adding_hero = False
        ctx.hero_added_on = ctx.stage
        ctx.logger.info("hero added to the team on stage %s", ctx.stage)
        return None
    heroes = [match for match in ctx.library.find_every(ctx.frame, "hero_add") if match.x >= HERO_LIST_LEFT]
    if heroes:
        clicks = ctx.route_data.get("hero_list_clicks", 0) + 1
        ctx.route_data["hero_list_clicks"] = clicks
        if clicks > MAX_HERO_LIST_CLICKS:
            ctx.route_data.pop("hero_list_clicks", None)
            ctx.adding_hero = False
            ctx.hero_added_on = ctx.stage
            ctx.logger.info("the hero list takes no hero (the team is full): starting the quest as it is")
            return None
        return _click(ctx, *min(heroes, key=lambda match: match.y).center)
    return _click(ctx, *EMPTY_SLOT_POINT)


def _team(ctx: Context, start_quest: Point) -> State:
    """Start the quest. If the team screen stays, the stage wants one more hero: add one, once."""
    seen(ctx, "team")
    if ctx.reset_pending or ctx.going_home:
        return _back_to_lobby(ctx)
    if ctx.adding_hero:
        state = _add_hero(ctx)
        if state is not None:
            return state
        ctx.start_clicked_at = None
    now = time.monotonic()
    if ctx.start_clicked_at is None:
        ctx.start_clicked_at = now
        # A new screen: the supporter's button, at the same place, was the last click, and the guard
        # against double clicks dropped Start Quest.
        ctx.last_click = None
        return _click(ctx, *start_quest)
    if now - ctx.start_clicked_at < START_QUEST_WAIT_SECONDS:
        return State.STORY
    ctx.start_clicked_at = None
    ctx.start_quest_clicks += 1
    # A click while the heroes still flash in is ignored: try again before blaming the team.
    if ctx.start_quest_clicks < START_QUEST_TRIES:
        ctx.logger.info("Start Quest did not work: clicking it again")
        return State.STORY
    ctx.start_quest_clicks = 0
    if ctx.hero_added_on != ctx.stage:
        ctx.logger.info("Start Quest did not work: adding a hero")
        ctx.adding_hero = True
    return State.STORY


def _leave_stage(ctx: Context) -> bool:
    """Press Esc to open the pause menu if the bot wants out. Returns False to play on instead."""
    if not (ctx.reset_pending or ctx.going_home) or ctx.leave_presses >= MAX_LEAVE_PRESSES:
        return False
    ctx.leave_presses += 1
    if ctx.leave_presses == MAX_LEAVE_PRESSES:
        ctx.logger.warning("Esc does not open the pause menu here: playing the stage to its end")
    seen(ctx, "leaving the stage")
    _back_to_lobby(ctx)
    return True


def _shifted(region: Region, slot: int) -> Region:
    """Return a region of the first skill moved to another skill slot."""
    return (region[0] + slot * SKILL_SPACING, *region[1:])


def usable_skills(ctx: Context) -> list[Point]:
    """Return the points of the skills in view that can be used, the rightmost first."""
    points = []
    for slot in reversed(range(SKILL_COUNT)):
        if ctx.find("skill_rim", _shifted(FIRST_SKILL_RIM_REGION, slot)) is None:
            continue
        if ctx.find("skill_cooldown", _shifted(FIRST_COOLDOWN_REGION, slot)) is not None:
            continue
        if ctx.find("skill_cannot_use", _shifted(FIRST_CANNOT_USE_REGION, slot)) is None:
            points.append((FIRST_SKILL_POINT[0] + slot * SKILL_SPACING, FIRST_SKILL_POINT[1]))
    return points


def _fight(ctx: Context) -> State:
    """On our turn, double click the rightmost skill. While the turn goes on, the next ones, in a loop.

    The game can ignore a click, such as when it wants a new skill used, so any skill may need a retry.
    """
    if _leave_stage(ctx):
        return State.STORY
    skills = usable_skills(ctx)
    if not skills:
        ctx.skills_tried = 0
        seen(ctx, "fight: waiting")
        return State.STORY
    if ctx.screen != "fight: our turn":
        seen(ctx, "fight: our turn")
        ctx.wait(SKILLS_SETTLE_SECONDS)
        return State.STORY
    ctx.session.input.double_click(*skills[ctx.skills_tried % len(skills)])
    ctx.skills_tried += 1
    ctx.wait(AFTER_SKILL_SECONDS)
    return State.STORY


def _auto_is_off(ctx: Context) -> bool:
    """Return whether the Auto icon stayed still for AUTO_STILL_SECONDS, over reads a moment apart."""
    now = time.monotonic()
    if now - ctx.auto_read_at < AUTO_READ_SECONDS:
        return False
    x, y, width, height = AUTO_ICON_REGION
    icon = cv2.cvtColor(ctx.frame[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY) > AUTO_WHITE_LEVEL
    previous = ctx.auto_icon
    ctx.auto_icon = icon
    ctx.auto_read_at = now
    if previous is None:
        return False
    changed = np.count_nonzero(icon ^ previous) / max(1, np.count_nonzero(icon | previous))
    if changed >= AUTO_MOVING_RATIO:
        ctx.auto_still_since = None
        return False
    if ctx.auto_still_since is None:
        ctx.auto_still_since = now
    if now - ctx.auto_still_since < AUTO_STILL_SECONDS:
        return False
    ctx.auto_still_since = None
    ctx.auto_icon = None
    return True


def fight_settings_unlocked(stage: str) -> bool:
    """Return whether x2 and Auto can be used: they unlock with chapter 3, before it the fights stay manual."""
    found = re.search(r"(\d+)-\d+$", stage or "")
    return found is not None and int(found.group(1)) >= FIGHT_SETTINGS_CHAPTER


def _fight_settings(ctx: Context) -> bool:
    """Put the fight on x2 and Auto. Returns whether it clicked. The game keeps both from one fight to the next."""
    if not ctx.story_only or not fight_settings_unlocked(ctx.stage):
        return False
    # Locked, the buttons show in grey: the same shape, but no white in them.
    x, y, width, height = SPEED_ICON_REGION
    if int(cv2.cvtColor(ctx.frame[y:y + height, x:x + width], cv2.COLOR_BGR2GRAY).max()) < UNLOCKED_WHITE:
        return False
    if ctx.find("speed_x1", SPEED_REGION) is not None:
        ctx.logger.info("battle speed set to x2")
        click_once(ctx, *SPEED_POINT)
        return True
    if _auto_is_off(ctx):
        ctx.logger.info("auto battle turned on")
        click_once(ctx, *AUTO_POINT)
        return True
    return False


def _fight_failed(ctx: Context) -> State:
    """Try a lost fight again. After MAX_FAILS_IN_A_ROW on the long route, start a new account."""
    if ctx.screen != "fight failed":
        ctx.fails_in_a_row += 1
        ctx.logger.info("fight lost on %s, %d in a row", ctx.stage, ctx.fails_in_a_row)
        if ctx.goal == "story" and ctx.on_story_lost is not None:
            ctx.on_story_lost()
    seen(ctx, "fight failed")
    if ctx.goal == "abyss" and ctx.fails_in_a_row >= MAX_FAILS_IN_A_ROW:
        ctx.logger.warning("%d lost fights in a row: starting a new account", ctx.fails_in_a_row)
        ctx.fails_in_a_row = 0
        ctx.story_target = None
        ctx.reset_pending = True
        return State.RESET_ACCOUNT
    if ctx.goal == "story" and ctx.fails_in_a_row >= MAX_STORY_FAILS_IN_A_ROW:
        return ctx.halt(f"{ctx.fails_in_a_row} lost fights in a row on {ctx.stage}: the team needs to get stronger")
    return _click(ctx, *TRY_AGAIN_POINT)


def _pause_menu(ctx: Context) -> State:
    """Yield the fight to go back to the lobby. Otherwise this menu has no reason to stay open."""
    seen(ctx, "pause menu")
    ctx.leave_presses = 0
    if ctx.reset_pending or ctx.going_home:
        return _click(ctx, *YIELD_POINT)
    return _click(ctx, *RETURN_TO_GAME_POINT)


def _yield_popup(ctx: Context) -> State:
    """Answer the warning before yielding, in the same way."""
    seen(ctx, "yield popup")
    if ctx.reset_pending or ctx.going_home:
        return _click(ctx, *YIELD_END_POINT)
    return _click(ctx, *YIELD_CANCEL_POINT)


def skip_video(ctx: Context) -> bool:
    """Try to skip a video and return whether the game confirmed it.

    The first press only arms the skip, the second one must be held. While it is
    held the game shows an icon, which goes away when the skip is done.
    """
    session = ctx.session
    session.input.press(SKIP_KEY)
    ctx.wait(ARM_SECONDS)
    seen = False
    misses = 0
    session.input.key_down(SKIP_KEY)
    start = time.monotonic()
    try:
        while (elapsed := time.monotonic() - start) < ctx.config.reroll.skip_hold_seconds:
            frame = session.capture.grab()
            if ctx.library.find(frame, "skip_icon", SKIP_ICON_REGION) is not None:
                seen = True
                misses = 0
            elif seen:
                misses += 1
                if misses >= ICON_GONE_FRAMES:
                    return True
            elif elapsed >= ICON_WAIT_SECONDS:
                return False
            ctx.wait(POLL_SECONDS)
    finally:
        session.input.key_up(SKIP_KEY)
    return False


def _unknown(ctx: Context) -> State:
    """Nothing recognized: wait a bit, then try a video skip, then tap, which also wakes a lobby."""
    ctx.screen = "unknown"
    if is_blank(ctx.frame):
        ctx.unknown_since = None
        return State.STORY
    now = time.monotonic()
    if ctx.fight_seen_at is not None and now - ctx.fight_seen_at < SKILL_ANIMATION_SECONDS:
        ctx.unknown_since = None
        return State.STORY
    if ctx.unknown_since is None:
        ctx.unknown_since = now
    if now - ctx.unknown_since < UNKNOWN_WAIT_SECONDS:
        return State.STORY
    ctx.unknown_since = None
    # Started on a menu, such as a hero's page. Esc leaves it, but it would not skip a video.
    if ctx.going_home and ctx.home_presses < MAX_HOME_PRESSES:
        ctx.home_presses += 1
        ctx.logger.info("going back to the lobby")
        return _back_to_lobby(ctx)
    # Most unknown screens are videos, and a video only ever goes away this way.
    if not ctx.unknown_tapped:
        ctx.unknown_tapped = True
        if skip_video(ctx):
            ctx.unknown_tapped = False
            ctx.logger.info("video skipped")
        else:
            ctx.logger.info("unknown screen: no video to skip here")
        ctx.wait(AFTER_CLICK_SECONDS)
        return State.STORY
    ctx.unknown_tapped = False
    ctx.logger.info("unknown screen: tapping")
    return _click(ctx, *CENTER_POINT)


def story(ctx: Context) -> State:
    """Do one step, checking the screens from the most specific to the least."""
    if not ctx.attach():
        return State.RECOVER

    # It shows over the title screen, and the screens may differ once it is installed.
    if ctx.find("patch_update", PATCH_REGION) is not None:
        return ctx.halt(PATCH_STOP)

    # Checked first and in this order: the title template also matches behind Select Server.
    if ctx.find("select_server", SELECT_SERVER_REGION) is not None:
        return State.LOGIN
    if ctx.find("title_screen", TITLE_REGION) is not None:
        return State.LOGIN

    lobby = ctx.find("episode", LOBBY_REGION)
    if lobby is None:
        ctx.lobby_since = None

    # Never answer this one with Confirm: it would close the game.
    if ctx.find("quit_popup", QUIT_REGION) is not None:
        seen(ctx, "quit popup")
        ctx.logger.warning("quit popup: cancelling it")
        return _click(ctx, *QUIT_CANCEL_POINT)

    # On the long route and the climb, the story only runs while a step asks for it: the route knows its own screens.
    # A pending reset is the exception: the story is what takes the game back to the lobby for it.
    if ctx.goal in ("abyss", "climb", "theater", "arena", "shop", "penguins", "huche", "gear_roll", "gear_sort", "gear_craft", "gear_account") and ctx.story_target is None and not ctx.reset_pending:
        return State.ROUTE

    state = refill_energy(ctx)
    if state is not None:
        return state
    # It shows over the fight, whose hourglass and pause button stay in view behind it.
    state = revive_popup(ctx)
    if state is not None:
        return state
    state = do_tap(ctx, POPUP_TAPS)
    if state is not None:
        return state
    # Before the event letter: this menu shows the same Tap to close at the same place.
    if ctx.find("pause_menu", PAUSE_MENU_REGION) is not None:
        return _pause_menu(ctx)
    if ctx.find("yield_popup", YIELD_POPUP_REGION) is not None:
        return _yield_popup(ctx)
    if ctx.find("letter_close", LETTER_REGION) is not None:
        return letter(ctx)
    if ctx.find("abyss_failed", FAILED_REGION) is not None or ctx.find("story_failed", STORY_FAILED_REGION) is not None:
        return _fight_failed(ctx)
    state = do_tap(ctx, TAPS)
    if state is not None:
        return state

    start_quest = ctx.find("start_quest", START_QUEST_REGION)
    if start_quest is not None:
        return _team(ctx, start_quest.center)
    # A stage played before shows "Start" with its energy cost instead of "Start Quest". The story never replays
    # one: the game opened on a cleared stage, the next one is out of this list (a next episode).
    if "team cp" in ctx.ocr.read(ctx.frame, TEAM_CP_REGION).casefold() and ctx.ocr.read(ctx.frame, START_TEXT_REGION).strip().casefold() == "start":
        seen(ctx, "team: stage cleared already")
        return ctx.halt(
            f"story: the stage the game opens ({ctx.stage or 'unknown'}) is cleared already; the next one is in another "
            "chapter or episode, which the bot cannot open yet. Start the next stage by hand, then restart the bot"
        )

    if ctx.find("episode_select", EPISODE_SELECT_REGION) is not None:
        return _select_episode(ctx)

    ready = ctx.find("stage_ready", READY_REGION)
    if ready is not None:
        return _stage_list(ctx, ready.center, greyed=ready_is_greyed(ctx))
    # Between the stage list and the team, some stages ask for a supporter: none is needed, Select Team goes on.
    if "select team" in ctx.ocr.read(ctx.frame, SELECT_TEAM_REGION).casefold() and "supporter" in ctx.ocr.read(ctx.frame, SUPPORTER_TITLE_REGION).casefold():
        seen(ctx, "select supporter")
        if ctx.reset_pending or ctx.going_home:
            return _back_to_lobby(ctx)
        return _click(ctx, *SELECT_TEAM_POINT)
    button = ctx.find("view_story", VIEW_STORY_REGION)
    if button is not None:
        return _stage_list(ctx, button.center)

    if lobby is not None:
        return _lobby(ctx, lobby.center)

    if ctx.find("hourglass", HOURGLASS_REGION) is not None:
        ctx.fight_seen_at = time.monotonic()
        if _fight_settings(ctx):
            return State.STORY
        return _fight(ctx)

    # The skip button shows once the scene behind an Act title starts. A tap gets there sooner.
    if ctx.find("story_log", STORY_LOG_REGION) is not None:
        seen(ctx, "act title")
        return _click(ctx, *CENTER_POINT)

    # With the minimap showing, the team is walking to the next wave by itself.
    if ctx.find("travel_bar", TRAVEL_BAR_REGION) is not None:
        if not _leave_stage(ctx):
            seen(ctx, "travelling")
        return State.STORY

    # Inside a stage, the hourglass hides during attack animations.
    if ctx.find("pause_hud", PAUSE_REGION) is not None:
        ctx.fight_seen_at = time.monotonic()
        if _fight_settings(ctx):
            return State.STORY
        if not _leave_stage(ctx):
            seen(ctx, "fight: animation")
        return State.STORY

    # A run can start on these, such as after a stop in the middle of the summons.
    if not ctx.reset_pending and not ctx.story_only and (
        ctx.find("summon_results", RESULTS_REGION) is not None
        or ctx.find("summon_header", SUMMON_HEADER_REGION) is not None
        or ctx.find("event_header", EVENT_HEADER_REGION) is not None
    ):
        ctx.going_home = False
        return State.SUMMON

    return _unknown(ctx)
