"""What the window shows of a run, read from the log lines it wrote: progress counts and a plain-words activity feed."""

from __future__ import annotations

import re
from dataclasses import dataclass

LINE = re.compile(r"^(\d\d:\d\d):\d\d (.*)$")
STAGE = re.compile(r"^stage (.*?)\s*(\d+)-(\d+)$")
ROUTE_STEP = re.compile(r"^route step (\d+) done")
ABYSS_FLOOR = re.compile(r"^abyss: floor (\d+) cleared")
FIGHT_LOST = re.compile(r"^(?:fight lost on .*?|abyss: lost fight), (\d+) in a row")
# The gear modes: a decision on a piece, a batch enhanced, a craft.
GEAR_DECISION = re.compile(r"^gear: (\w+) (\w+) \+(\d+) .* -> (would )?(enhance|keep|lock|sell|extract): ")
GEAR_BULK = re.compile(r"^gear: bulk enhanced (\d+) pieces to \+(\d+) for ([\d,]+) gold")
GEAR_CRAFT = re.compile(r"^gear: crafted (\d+) .* for ([\d,]+) gold")
GEAR_REMOVED = re.compile(r"^gear: (sold|extracted) (\d+) pieces")
# The account evaluation: a +15 piece read, a card left unread or skipped, a slot tab opened or done, a step.
ACCOUNT_PIECE = re.compile(r"^account: \w+ (\w+) \+15 ")
ACCOUNT_UNREAD = re.compile(r"^account: card \d+ unread \((.*)\)$")
ACCOUNT_SKIPPED = re.compile(r"^account: card \d+ skipped")
ACCOUNT_TAB = re.compile(r"^account: (\w+) tab$")
ACCOUNT_TAB_DONE = re.compile(r"^account: (\w+) tab done: (\d+) ")
ACCOUNT_STEPS = {
    "account: opening the inventory": "Opening the inventory",
    "account: setting the inventory filters": "Setting the filters",
    "account: computing the scores": "Computing the scores",
}
ACCOUNT_TABS = 6
# The Shop refresh: an item bought, with its amount and price.
HUCHE_BOUGHT = re.compile(r"^huche: bought (.+) for ([\d,]+) gold")
SHOP_BOUGHT = re.compile(r"^shop: bought (\d+) (Covenant Bookmarks|Mystic Medals) for ([\d,]+) gold")
SHOP_REFRESHED = re.compile(r"^shop: refreshed for \d+ skystones, (\d+) of (\d+)")
SHOP_PLANNED = re.compile(r"^shop: (\d+) refreshes planned")
SHOP_FOR_SALE = re.compile(r"^shop: (Covenant Bookmarks|Mystic Medals) for sale")
HUCHE_FOR_SALE = re.compile(r"^huche: (.+) for sale")
PENGUIN_PLAN = re.compile(r"^penguins: (\d+) (purchases|stigma) planned")
THEATER_ACT = re.compile(r"^theater: act started, story (\d+) season (\d+)")
# What the bot is busy with, from the last screen it saw: the first matching prefix wins.
DOING = (
    ("screen: fight", "Fighting"),
    ("screen: travelling", "Fighting"),
    ("screen: dialogue", "Dialogue"),
    ("screen: skip popup", "Dialogue"),
    ("screen: act title", "Story scene"),
    ("video skipped", "Video"),
    ("screen: stage list", "Choosing the stage"),
    ("screen: team", "Preparing the team"),
    ("screen: stage clear", "Stage won"),
    ("screen: stage results", "Stage rewards"),
    ("screen: lobby", "Lobby"),
    ("screen: summon", "Summons"),
    ("screen: gear inventory", "Gear: going through the inventory"),
    ("screen: gear enhance", "Gear: judging a piece"),
    ("screen: gear bulk", "Gear: bulk enhancement"),
    ("screen: gear workshop", "Gear: crafting"),
    ("screen: shop list", "Reading the Secret Shop"),
    ("screen: shop refresh", "Refreshing the shop"),
    ("screen: shop buy", "Buying"),
    ("screen: arena flag purchase", "Refilling the flags"),
    ("arena: 5 flags bought", "Flags refilled"),
    ("screen: arena result", "Reading the result"),
    ("screen: arena team", "Preparing the team"),
    ("screen: arena npc challenge", "Picking an opponent"),
    ("screen: arena opponents", "Picking an opponent"),
    ("screen: arena hero with no equipment", "Checking the team's gear"),
    ("arena: fight started", "Fighting"),
    ("arena: turning Quick Match off", "Turning Quick Match off"),
    ("screen: arena", "Arena menu"),
    ("screen: theater act list", "Choosing an act"),
    ("screen: theater reward", "Collecting the reward"),
    ("screen: theater no skip notice", "Watching the act"),
    ("screen: theater use ticket", "Using a ticket"),
    ("screen: theater ticket", "Buying a ticket"),
    ("screen: theater no ticket", "No ticket left"),
    ("screen: theater story page", "Story page"),
    ("screen: theater carousel", "Choosing the story"),
    ("theater: act started", "Watching the act"),
    ("theater: video skipped", "Skipping the video"),
    ("screen: abyss floors scrolling", "Finding the next floor"),
    ("screen: battle page", "Battle menu"),
    ("screen: milestone rewards", "Collecting milestone rewards"),
    ("screen: quick battling", "Quick battle"),
    ("screen: mailbox", "Mailbox"),
    ("screen: sanctuary", "Sanctuary"),
    ("screen: forest of souls", "Forest of Souls"),
    ("screen: growth altar", "Growth Altar"),
    ("screen: penguin purchase popup", "Buying 50 penguins"),
    ("screen: penguin result", "Penguins bought"),
    ("screen: labyrinth list", "Labyrinth list"),
    ("screen: labyrinth area", "Choosing Area 1"),
    ("screen: labyrinth team", "Entering the labyrinth"),
    ("screen: labyrinth room", "Opening the map"),
    ("screen: labyrinth map", "Finding the waypoint by Huche"),
    ("screen: waypoint popup", "Teleporting (3 morale)"),
    ("screen: labyrinth waypoint", "Going west"),
    ("screen: walking to huche", "Walking to Huche"),
    ("screen: huche buy popup", "Buying a charm"),
    ("screen: huche merchant", "Huche's shop"),
    ("screen: huche", "Opening Huche's shop"),
    ("screen: pause menu", "Leaving the labyrinth"),
    ("screen: yield popup", "Leaving the labyrinth"),
    ("screen: popup over the lobby", "Closing a popup"),
    ("screen: quit popup", "Closing the quit popup"),
)


@dataclass(frozen=True)
class Progress:
    """Counts of the current run, from its log lines only."""

    stages_won: int = 0
    fails_in_a_row: int = 0
    route_steps_done: int | None = None
    abyss_floor: int | None = None
    doing: str = ""
    fights_lost: int = 0
    first_stage: str = ""
    last_stage: str = ""
    floors_won: int = 0
    first_floor: int | None = None
    acts_watched: int = 0
    tickets_bought: int = 0
    arena_fights: int = 0
    arena_wins: int = 0
    gear_judged: int = 0
    gear_enhanced: int = 0
    gear_removed: int = 0
    gear_gold: int = 0
    shop_refreshes: int = 0
    shop_bookmarks: int = 0
    shop_medals: int = 0
    shop_gold: int = 0
    account_read: int = 0
    account_unread: int = 0
    account_skipped: int = 0
    account_tabs_done: int = 0
    # Where the run stands now, for the live line: the planned refreshes, the act's story and season.
    shop_planned: int | None = None
    theater_story: int | None = None
    theater_season: int | None = None
    # The penguin mode: purchases done, and the plan its first line said (purchases or stigma, and how many).
    penguin_purchases: int = 0
    penguin_kind: str | None = None
    penguin_amount: int = 0
    # Huche's shop: charms bought and the gold they cost.
    huche_bought: int = 0
    huche_gold: int = 0

    @property
    def account_doing(self) -> bool:
        """Whether the current step is one of the account evaluation's own."""
        return self.doing in ACCOUNT_STEPS.values() or self.doing.startswith("Reading the ")


def clean_episode(text: str) -> str:
    """Return the episode's name without the OCR noise: 'Episode3 (7)' becomes 'Episode 3'."""
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[^\w' ]", " ", text)
    text = re.sub(r"Episode\s*(\d+)", r"Episode \1", text)
    return " ".join(text.split())


def split_stage(stage: str) -> tuple[str, str] | None:
    """Return the episode and the chapter-stage of 'Episode 3 (2) 1-6', or None if it is not a stage."""
    found = re.match(r"^(.*?)\s*(\d+)-(\d+)$", stage or "")
    if found is None:
        return None
    return clean_episode(found.group(1)), f"{found.group(2)}-{found.group(3)}"


def read_progress(lines: list[str]) -> Progress:
    """Count what happened in these log lines, which start with the run."""
    won = 0
    fails = 0
    lost = 0
    first = ""
    last = ""
    floors = 0
    first_floor: int | None = None
    steps: int | None = None
    floor: int | None = None
    doing = ""
    acts = 0
    arena_fights = 0
    arena_wins = 0
    tickets = 0
    gear_judged = gear_enhanced = gear_removed = gear_gold = 0
    shop_refreshes = shop_bookmarks = shop_medals = shop_gold = 0
    account_read = account_unread = account_skipped = account_tabs = account_tabs_done = 0
    account_slot = ""
    shop_planned: int | None = None
    theater_story: int | None = None
    theater_season: int | None = None
    penguin_purchases = penguin_amount = 0
    huche_bought = huche_gold = 0
    penguin_kind: str | None = None
    for line in lines:
        found = LINE.match(line)
        if found is None:
            continue
        message = found.group(2)
        if message == "screen: stage clear":
            won += 1
            fails = 0
        elif (lost_fight := FIGHT_LOST.match(message)) is not None:
            fails = int(lost_fight.group(1))
            lost += 1
        elif (stage := STAGE.match(message)) is not None:
            last = message.removeprefix("stage ")
            first = first or last
        elif (step := ROUTE_STEP.match(message)) is not None:
            steps = int(step.group(1)) + 1
        elif (cleared := ABYSS_FLOOR.match(message)) is not None:
            floor = int(cleared.group(1))
            first_floor = floor if first_floor is None else first_floor
            floors += 1
            fails = 0
        elif message.startswith("theater: act watched"):
            acts += 1
        elif message.startswith("theater: one ticket bought"):
            tickets += 1
        elif message.startswith("arena: fight won") or message.startswith("arena: fight lost"):
            arena_fights += 1
            arena_wins += message.startswith("arena: fight won")
        elif (refreshed := SHOP_REFRESHED.match(message)) is not None:
            # The line carries the run's count: right even when the window has not read every line.
            shop_refreshes = int(refreshed.group(1))
            shop_planned = int(refreshed.group(2))
        elif (planned := SHOP_PLANNED.match(message)) is not None:
            shop_planned = int(planned.group(1))
        elif message.startswith("penguins: bought "):
            penguin_purchases += 1
        elif (charm := HUCHE_BOUGHT.match(message)) is not None:
            huche_bought += 1
            huche_gold += int(charm.group(2).replace(",", ""))
        elif (plan := PENGUIN_PLAN.match(message)) is not None:
            penguin_amount, penguin_kind = int(plan.group(1)), plan.group(2)
        elif (act := THEATER_ACT.match(message)) is not None:
            theater_story, theater_season = int(act.group(1)), int(act.group(2))
        elif (bought := SHOP_BOUGHT.match(message)) is not None:
            if bought.group(2) == "Covenant Bookmarks":
                shop_bookmarks += int(bought.group(1))
            else:
                shop_medals += int(bought.group(1))
            shop_gold += int(bought.group(3).replace(",", ""))
        elif GEAR_DECISION.match(message):
            gear_judged += 1
        elif (bulk := GEAR_BULK.match(message)) is not None:
            gear_enhanced += int(bulk.group(1))
            gear_gold += int(bulk.group(3).replace(",", ""))
        elif (craft := GEAR_CRAFT.match(message)) is not None:
            gear_gold += int(craft.group(2).replace(",", ""))
        elif (removed := GEAR_REMOVED.match(message)) is not None:
            gear_removed += int(removed.group(2))
        elif message.startswith("account: "):
            if ACCOUNT_PIECE.match(message):
                account_read += 1
            elif ACCOUNT_UNREAD.match(message):
                account_unread += 1
            elif ACCOUNT_SKIPPED.match(message):
                account_skipped += 1
            elif (tab := ACCOUNT_TAB.match(message)) is not None:
                account_tabs += 1
                account_slot = tab.group(1)
            elif ACCOUNT_TAB_DONE.match(message):
                account_tabs_done += 1
            step = next((text for prefix, text in ACCOUNT_STEPS.items() if message.startswith(prefix)), None)
            if step is not None:
                doing = step
            elif account_slot and not ACCOUNT_TAB_DONE.match(message):
                doing = f"Reading the {account_slot} tab ({account_tabs} of {ACCOUNT_TABS})"
            continue
        if (sale := SHOP_FOR_SALE.match(message)) is not None:
            doing = f"Buying {sale.group(1)}"
            continue
        if (sale := HUCHE_FOR_SALE.match(message)) is not None:
            doing = f"Buying the {sale.group(1)}"
            continue
        for prefix, text in DOING:
            if message.startswith(prefix):
                doing = text
                break
        else:
            if message.startswith("screen: "):
                # A screen with no wording of its own still shows the bot moving: say its name.
                doing = message.removeprefix("screen: ").capitalize()
    return Progress(
        won, fails, steps, floor, doing, lost, first, last, floors, first_floor, acts, tickets, arena_fights, arena_wins,
        gear_judged, gear_enhanced, gear_removed, gear_gold, shop_refreshes, shop_bookmarks, shop_medals, shop_gold,
        account_read, account_unread, account_skipped, account_tabs_done,
        shop_planned, theater_story, theater_season, penguin_purchases, penguin_kind, penguin_amount,
        huche_bought, huche_gold,
    )


def humanize(message: str) -> str | None:
    """Say a log message in plain words, or return None for one that says nothing to the user."""
    if (stage := STAGE.match(message)) is not None:
        return f"Stage {stage.group(2)}-{stage.group(3)} chosen"
    if message == "screen: stage clear":
        return "Stage won"
    if (lost := FIGHT_LOST.match(message)) is not None:
        return f"Fight lost ({lost.group(1)} in a row)"
    if (cleared := ABYSS_FLOOR.match(message)) is not None:
        return f"Abyss: floor {cleared.group(1)} won"
    if (step := ROUTE_STEP.match(message)) is not None:
        return f"Route: step {int(step.group(1)) + 1} done"
    plain = {
        "video skipped": "Video skipped",
        "screen: dialogue": "Dialogue skipped",
        "screen: friend request": "Friend request declined",
        "screen: hero announcement": "Hero announcement closed",
        "screen: rank up": "Rank up",
        "battle speed set to x2": "Battle speed set to x2",
        "auto battle turned on": "Auto battle on",
        "screen: connection error": "Connection lost, retrying",
        "quit popup: cancelling it": "Quit popup cancelled",
        "title screen passed": "Back in the game",
        "free summons claimed": "Free summons claimed",
    }
    if message in plain:
        return plain[message]
    if message.startswith("client drifted"):
        return "Game window set back to 1280×720"
    if message.startswith("watchdog expired"):
        return "Screen stuck: getting unstuck"
    if message.startswith("press F") and "to stop the bot" in message:
        return "Bot started"
    if message.startswith("stopped:"):
        return "Bot stopped"
    if message.startswith("penguins: bought "):
        return "50 penguins bought"
    if (charm := HUCHE_BOUGHT.match(message)) is not None:
        return f"{charm.group(1)} bought ({charm.group(2)} gold)"
    if message.startswith("theater: act started"):
        return "Theater: act started"
    if message.startswith("theater: act watched"):
        return "Theater: act done"
    if message.startswith("theater: one ticket bought"):
        return "Theater: ticket bought (500,000 gold)"
    if message.startswith("theater: story") and "next story" in message:
        return "Theater: next story"
    if message.startswith("theater: story") and "next season" in message:
        return "Theater: next season"
    if message.startswith("theater: no ticket"):
        return "Theater: no tickets left, free acts only"
    if message.startswith("arena: fight started"):
        return "Arena: quick match started" if "quick" in message else "Arena: fight started"
    if message.startswith("arena: fight won"):
        return "Arena: fight won"
    if message.startswith("arena: fight lost"):
        return "Arena: fight lost"
    if message.startswith("arena: no Quick Match left") or message.startswith("arena: Quick Match cannot"):
        return "Arena: no quick matches left, normal fights"
    if message.startswith("arena: 5 flags bought with") and "skystones" in message:
        return "Arena: 5 flags refilled with 30 skystones"
    if message.startswith("arena: 5 flags bought"):
        return "Arena: 5 flags refilled with friendship"
    if (refresh := re.match(r"shop: refreshed for \d+ skystones, (\d+) of (\d+)", message)) is not None:
        return f"Shop: refresh {refresh.group(1)} of {refresh.group(2)}"
    if (bought := SHOP_BOUGHT.match(message)) is not None:
        return f"Shop: {bought.group(1)} {bought.group(2)} bought ({bought.group(3)} gold)"
    if (decision := GEAR_DECISION.match(message)) is not None:
        grade, slot, level, would, action = decision.groups()
        return f"Gear: {grade} {slot} +{level}, {'would ' if would else ''}{action}"
    if (bulk := GEAR_BULK.match(message)) is not None:
        return f"Gear: {bulk.group(1)} pieces enhanced to +{bulk.group(2)} ({bulk.group(3)} gold)"
    if (craft := GEAR_CRAFT.match(message)) is not None:
        return f"Gear: {craft.group(1)} pieces crafted ({craft.group(2)} gold)"
    if (removed := GEAR_REMOVED.match(message)) is not None:
        return f"Gear: {removed.group(2)} pieces {removed.group(1)}"
    if message.startswith("account: "):
        return account_event(message)
    if message == "gear: locked":
        return "Gear: piece locked"
    if message.startswith("out of energy: one leaf used"):
        return "Energy refilled with a leaf"
    if message.startswith("stopped: cannot start"):
        return "Cannot start: inventory full"
    if message.startswith("the selected stage is locked"):
        return "Stage locked: picked the one before"
    return None


def unread_reason(why: str) -> str:
    """Say why the card reader gave up on a card in plain words: 'line 4: crit_chance 0.54 vs health 0.30' is a stat."""
    if (line := re.match(r"line (\d+)", why)) is not None:
        return f"stat line {line.group(1)} unclear"
    if why.startswith("level"):
        return "level unclear"
    if why.startswith("set"):
        return "set unclear"
    if why.startswith("main"):
        return "main stat unclear"
    return why


def account_event(message: str) -> str | None:
    """Say a step of the account evaluation in plain words: the tabs and the cards it could not read."""
    step = next((text for prefix, text in ACCOUNT_STEPS.items() if message.startswith(prefix)), None)
    if step is not None:
        return f"Account: {step[0].lower()}{step[1:]}"
    if (tab := ACCOUNT_TAB.match(message)) is not None:
        return f"Account: {tab.group(1)} tab opened"
    if (done := ACCOUNT_TAB_DONE.match(message)) is not None:
        count = int(done.group(2))
        return f"Account: {done.group(1)} tab done, {count} +15 piece{'' if count == 1 else 's'}"
    if (unread := ACCOUNT_UNREAD.match(message)) is not None:
        return f"Account: a card left unread ({unread_reason(unread.group(1))})"
    return None


def activity(lines: list[str], count: int = 5) -> list[tuple[str, str]]:
    """Return the last events worth a glance as (HH:MM, text), newest first, without repeats in a row."""
    events: list[tuple[str, str]] = []
    for line in lines:
        found = LINE.match(line)
        if found is None:
            continue
        text = humanize(found.group(2))
        if text is None or (events and events[-1][1] == text):
            continue
        events.append((found.group(1), text))
    return list(reversed(events[-count:]))
