"""What the window reads of a run from its log lines."""

from core.panel import climb_summary, format_duration, route_line, run_metrics, story_summary
from core.progress import Progress, activity, clean_episode, humanize, read_progress, split_stage
from core.stats import Stats
from core.status import StatusSnapshot

STORY_RUN = [
    "22:31:20 press F10 at any time to stop the bot",
    "22:31:34 stage Episode 3 (2) 2-4",
    "22:31:36 screen: dialogue",
    "22:31:37 screen: dialogue",
    "22:33:15 screen: stage clear",
    "22:33:18 screen: friend request",
    "22:33:22 stage Episode 3 (2) 2-5",
    "22:34:10 fight lost on Episode 3 (2) 2-5, 1 in a row",
    "22:34:30 fight lost on Episode 3 (2) 2-5, 2 in a row",
    "22:34:40 state enter STORY frame=x",
    "22:35:02 screen: fight: waiting",
]


def test_the_story_counts_wins_and_the_losses_since_the_last_win():
    progress = read_progress(STORY_RUN)
    assert progress.stages_won == 1
    assert progress.fails_in_a_row == 2
    assert progress.doing == "Fighting"


def test_a_win_clears_the_losses():
    assert read_progress([*STORY_RUN, "22:36:00 screen: stage clear"]).fails_in_a_row == 0


def test_the_route_and_the_abyss_are_followed():
    progress = read_progress([
        "15:43:14 route step 1 done: story to 6th World Episode 3-3",
        "16:10:00 abyss: floor 37 cleared",
        "16:11:00 abyss: floor 38 cleared",
    ])
    assert progress.route_steps_done == 2
    assert progress.abyss_floor == 38


def test_the_activity_is_in_french_newest_first_without_noise_or_repeats():
    events = activity(STORY_RUN, 4)
    assert events == [
        ("22:34", "Fight lost (2 in a row)"),
        ("22:34", "Fight lost (1 in a row)"),
        ("22:33", "Stage 2-5 chosen"),
        ("22:33", "Friend request declined"),
    ]
    assert ("22:31", "Dialogue skipped") in activity(STORY_RUN, 10)
    assert sum(text == "Dialogue skipped" for _, text in activity(STORY_RUN, 10)) == 1


def test_episode_names_lose_the_ocr_noise():
    assert clean_episode("Episode3 (7)") == "Episode 3"
    assert clean_episode("6th World Episode") == "6th World Episode"
    assert split_stage("Episode 3 (2) 10-9") == ("Episode 3", "10-9")
    assert split_stage("") is None


def test_durations_read_at_a_glance():
    assert format_duration(None) == "—"
    assert format_duration(45) == "45 s"
    assert format_duration(100) == "1 min 40"
    assert format_duration(3540) == "59 min"
    assert format_duration(3900) == "1 h 05"


def test_story_numbers_divide_the_time_by_the_wins():
    snapshot = StatusSnapshot(True, "STORY", "", "", 0, 0, 300.0)
    numbers = run_metrics("story", snapshot, Progress(stages_won=3, fails_in_a_row=1))
    assert numbers == [("3", "stages won"), ("1", "loss in a row"), ("1 min 40", "per stage")]


def test_the_route_line_gives_an_end_time_only_for_an_account_begun_in_this_run():
    stats = Stats()
    stats.add_run(3600)
    begun = StatusSnapshot(True, "ROUTE", "", "", 1, 5, 0.0, stats=stats, account_started_at=0.0)
    resumed = StatusSnapshot(True, "ROUTE", "", "", 0, 5, 0.0, stats=stats)
    assert route_line(Progress(route_steps_done=23), begun).startswith("Route: step 24 of 37 · done around ")
    assert route_line(Progress(route_steps_done=23), resumed) == "Route: step 24 of 37"


def test_the_story_summary_says_where_the_last_session_went():
    stats = Stats()
    line, numbers = story_summary(read_progress(STORY_RUN), 600.0, stats)
    assert line == "Last session: Episode 3, 2-4 to 2-5"
    assert numbers == [("1", "stage won"), ("2", "fights lost"), ("10 min", "time")]
    stats.story_stages_won = 4
    assert story_summary(Progress(), 0.0, stats)[1][0] == ("4", "stages won")


def test_the_climb_summary_gives_the_floors_of_the_last_climb():
    lines = ["10:00:00 abyss: floor 46 cleared", "10:03:00 abyss: floor 47 cleared", "10:05:00 abyss: lost fight, 1 in a row"]
    progress = read_progress(lines)
    assert (progress.floors_won, progress.first_floor, progress.abyss_floor, progress.fights_lost) == (2, 46, 47, 1)
    line, numbers = climb_summary(progress, 400.0, Stats())
    assert line == "Last climb: floors 46 to 47"
    assert numbers == [("2", "floors won"), ("1", "fight lost"), ("6 min 40", "time")]


def test_a_chest_name_stands_for_its_moonlight_hero():
    from core.heroes import chest_rank, load_ranking, rank_of
    ranking = load_ranking()
    assert rank_of("Lisette", ranking) == 1
    assert chest_rank("Charles", ranking) == rank_of("Closer Charles", ranking)
    assert chest_rank("Lisette", ranking) == 1
    assert chest_rank("Tenebria", ranking) == rank_of("Specter Tenebria", ranking)
    assert chest_rank("Tenebria", ranking, "light") == rank_of("Witch of the Mere Tenebria", ranking)
    assert chest_rank("Tenebria", ranking, "dark") == rank_of("Specter Tenebria", ranking)
    assert chest_rank("Iseria", ranking, "light") == rank_of("Monarch of the Sword Iseria", ranking)
    assert chest_rank("Iseria", ranking, "dark") == rank_of("Briar Witch Iseria", ranking)
    assert chest_rank("Nobody", ranking) is None


def test_the_theater_counts_acts_and_tickets():
    lines = [
        "15:07:15 theater: act watched, 1 in this run",
        "15:07:18 theater: one ticket bought with 500,000 gold, 1 in this run",
        "15:10:42 theater: act watched, 2 in this run",
    ]
    progress = read_progress(lines)
    assert (progress.acts_watched, progress.tickets_bought) == (2, 1)
    assert humanize("theater: act watched, 2 in this run") == "Theater: act done"


def test_the_gear_lines_count_and_read_in_plain_words():
    lines = [
        "10:00:01 gear: epic boots +0 speed set, main speed: crit_chance 5 -> enhance: to +3: score potential 50",
        "10:00:02 gear: rare weapon +0 ? set, main attack: health 100 -> would sell: heroic piece without speed",
        "10:00:03 gear: epic ring +3 hit set, main effectiveness: speed 4 -> enhance to +6, in a later batch",
        "10:00:04 gear: bulk enhanced 16 pieces to +3 for 384,000 gold",
        "10:00:05 gear: sold 3 pieces",
        "10:00:06 gear: crafted 10 wyvern weapon for 210,000 gold, 10 in this run",
        "10:00:07 screen: gear bulk",
    ]
    progress = read_progress(lines)
    assert (progress.gear_judged, progress.gear_enhanced, progress.gear_removed, progress.gear_gold) == (2, 16, 3, 594_000)
    assert progress.doing == "Gear: bulk enhancement"
    assert [text for _, text in activity(lines, 10)] == [
        "Gear: 10 pieces crafted (210,000 gold)",
        "Gear: 3 pieces sold",
        "Gear: 16 pieces enhanced to +3 (384,000 gold)",
        "Gear: rare weapon +0, would sell",
        "Gear: epic boots +0, enhance",
    ]


# The account evaluation, from the log of a real run, with the steps the scan now writes.
ACCOUNT_RUN = [
    "12:16:12 screen: lobby",
    "12:16:12 account: opening the inventory",
    "12:16:14 screen: gear inventory",
    "12:16:15 account: setting the inventory filters: worn pieces shown, highest level first",
    "12:16:24 account: weapon tab",
    "12:16:33 account: epic weapon +15 critical set (level 88), main attack: crit_chance 13, crit_damage 22, speed 4, attack_percent 9",
    "12:16:34 account: card 28 unread (line 4: crit_chance 0.54 vs health 0.30)",
    "12:16:35 account: card 29 skipped (item level 70)",
    "12:16:40 account: weapon tab done: 1 +15 piece (under +15)",
    "12:16:40 account: helmet tab",
    "12:16:50 account: epic helmet +15 speed set, main health: speed 11, health_percent 14, defense_percent 8, effectiveness 9",
]


def test_the_account_scan_counts_its_cards_and_says_its_step():
    progress = read_progress(ACCOUNT_RUN)
    assert (progress.account_read, progress.account_unread, progress.account_skipped) == (2, 1, 1)
    assert progress.account_tabs_done == 1
    assert progress.doing == "Reading the helmet tab (2 of 6)"
    assert progress.gear_judged == 0, "the gear modes' count stays theirs"
    steps = [read_progress(ACCOUNT_RUN[:end]).doing for end in (2, 4, 5)]
    assert steps == ["Opening the inventory", "Setting the filters", "Reading the weapon tab (1 of 6)"]
    assert read_progress([*ACCOUNT_RUN, "12:18:00 account: computing the scores"]).doing == "Computing the scores"


def test_the_account_scan_events_are_its_tabs_and_unread_cards():
    events = [text for _, text in activity(ACCOUNT_RUN, 20)]
    assert events == [
        "Account: helmet tab opened",
        "Account: weapon tab done, 1 +15 piece",
        "Account: a card left unread (stat line 4 unclear)",
        "Account: weapon tab opened",
        "Account: setting the filters",
        "Account: opening the inventory",
    ]


def test_the_account_run_shows_read_unread_and_skipped():
    progress = read_progress(ACCOUNT_RUN)
    snapshot = StatusSnapshot(True, "ROUTE", "", "", 0, 0, 30.0, 0, 0, "")
    assert run_metrics("gear_account", snapshot, progress) == [("2", "+15 pieces read"), ("1", "unread"), ("1", "other item levels")]


def test_the_shop_refresh_shows_its_plan_the_refresh_and_the_item_being_bought():
    from core.panel import shop_line

    progress = read_progress([
        "10:00:00 shop: 100 refreshes planned",
        "10:00:01 screen: lobby",
        "10:00:05 shop: refreshed for 3 skystones, 36 of 100",
        "10:00:06 screen: shop list top",
        "10:00:07 shop: Covenant Bookmarks for sale on the top page, buying",
    ])
    assert progress.doing == "Buying Covenant Bookmarks"
    detail, bar = shop_line(progress)
    assert detail == "Refresh 37 of 100"
    assert bar == {"text": "36 of 100 refreshes · 108 of 300 skystones", "fraction": 0.36}


def test_the_shop_refresh_before_its_first_refresh_has_no_bar_without_a_plan():
    from core.panel import shop_line

    assert shop_line(read_progress([])) == ("On the way to the Secret Shop", None)


def test_arena_and_theater_screens_say_what_the_bot_does():
    assert read_progress(["10:00:00 screen: arena npc challenge"]).doing == "Picking an opponent"
    assert read_progress(["10:00:00 screen: arena flag purchase"]).doing == "Refilling the flags"
    assert read_progress(["10:00:00 arena: fight started"]).doing == "Fighting"
    assert read_progress(["10:00:00 screen: theater ticket purchase"]).doing == "Buying a ticket"


def test_the_theater_line_names_the_story_season_and_act():
    from core.panel import theater_line

    progress = read_progress(["10:00:00 theater: act watched, 1 in this run", "10:00:05 theater: act started, story 2 season 3"])
    assert (progress.theater_story, progress.theater_season) == (2, 3)
    assert theater_line(progress) == "Story 2, season 3 · act 2"


def test_a_screen_with_no_wording_still_shows_its_name():
    assert read_progress(["10:00:00 screen: lobby", "10:00:02 screen: guild check in"]).doing == "Guild check in"
