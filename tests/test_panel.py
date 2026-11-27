"""What the bot window says and does, tested on its logic without showing it."""

import json
import logging
import threading
from pathlib import Path

import pytest

from core.config import load_config
from core.advisor import Advice, ScreenAdvice
from core.panel import (
    ATTENTION, DELETE_ARMED_LABEL, DELETE_LABEL, ERASE_CONFIRM_SECONDS, GOAL_BY_KEY, GOALS, IDLE, INVALID_SETTING, KEPT, KEPT_GOALS,
    PICK_MODE_LABEL, RUNNING, Panel, band, clean_hero_name, describe, describe_stop, export_log, gear_summary, run_metrics, stat_sections,
)
from core.heroes import identify_hero, load_moonlight_heroes
from core.progress import Progress
from core.settings import Settings, load_settings
from core.state import Card
from core.stats import Stats, load_stats, load_total_rerolls, save_stats, save_total_rerolls
from core.status import BotStatus, LogFeed, StatusSnapshot

ROOT = Path(__file__).parent.parent


def snapshot(state: str, stage: str = "", read: int = 0, expected: int = 0) -> StatusSnapshot:
    return StatusSnapshot(True, state, stage, "", 0, 0, 0.0, read, expected, "")


@pytest.mark.parametrize(
    ("reading", "name"),
    [
        ("Briar Witch Iseria \u2018KE", "Briar Witch Iseria"),
        ("Ragnvald N", "Ragnvald"),
        ("Kluri Neg!", "Kluri"),
        ("ae-KARINA", "ae-KARINA"),
    ],
)
def test_ocr_noise_is_removed_from_a_name(reading, name):
    assert clean_hero_name(reading) == name


def test_a_kept_account_is_good_news_with_a_clean_name():
    display = describe_stop("account kept: Briar Witch Iseria \u2018KE 5*")
    assert display.kind == KEPT
    assert display.detail == "Briar Witch Iseria 5\u2605"


@pytest.mark.parametrize(
    "reason",
    [
        "the game offers a patch: choose Proceed yourself, then check the bot still recognizes the screens",
        "the day changed during the summons: the unused ones expired, not resetting",
        "the game asks to buy bookmarks: the free summons are gone, not resetting",
        "could not read summon result card(s) 4: see logs/results/x.png",
        "no free summon left but 11 of 21 results were read: not resetting",
        "Summon Support is not in the event list: has the event ended?",
        "SUMMON still failing after 3 retries: not resetting",
    ],
)
def test_every_stop_that_needs_a_look_says_the_account_is_safe(reason):
    display = describe_stop(reason)
    assert display.kind == ATTENTION
    assert "erased" in display.detail


def test_an_unknown_reason_is_shown_as_is():
    display = describe_stop("something new")
    assert display.kind == ATTENTION
    assert display.detail == "something new"


def test_being_stopped_by_the_user_is_not_an_alarm():
    assert describe_stop("stopped by the user").kind == IDLE
    assert "Start" in describe_stop("stopped by the user").detail
    assert describe_stop("").kind == IDLE


def test_a_run_says_where_it_is():
    assert "Chapter 2" in describe(snapshot("STORY", "6th World Episode 2-2")).detail
    story = describe(snapshot("STORY", "Episode3 (7) 1-6"))
    assert (story.title, story.detail) == ("Episode 3", "Chapter 1 · stage 1-6")
    assert describe(snapshot("SUMMON", read=11, expected=21)).detail == "11 of 21 pulls read"
    assert describe(snapshot("RESET_ACCOUNT")).kind == RUNNING
    assert describe(snapshot("RECOVER")).kind == ATTENTION


def test_status_keeps_the_result_until_a_new_account_starts():
    status = BotStatus()
    status.set_result(11, 21, "logs/results/x.png")
    assert status.snapshot().pulls_read == 11
    status.add_reroll()
    assert status.snapshot().pulls_read == 0
    assert status.snapshot().result_image == ""


class FakeClock:
    """A clock the tests move by hand, for the clicks that wear off."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def make_panel(folder: Path, settings: Settings) -> Panel:
    """A window's logic over a temporary folder, whose bot only notes how it was started."""
    config = load_config(ROOT / "config.yaml")
    runs = []
    built = Panel(
        config.window, settings, folder / "settings.json", BotStatus(), LogFeed(), threading.Event(),
        lambda s, reset, goal: runs.append((reset, goal)), logging.getLogger("test_panel"), log_dir=folder, clock=FakeClock(),
    )
    built.runs = runs
    return built


@pytest.fixture
def panel(tmp_path):
    """A window aimed at STOVE, on the reroll mode, idle."""
    built = make_panel(tmp_path, Settings(client="stove", server="Foundry", pre_action_delay_min_ms=150, pre_action_delay_max_ms=600, skip_hold_seconds=5.0))
    built.pick_mode("reroll_abyss")
    return built


def finish(panel: Panel, result: str = "") -> None:
    """End the run the panel started, as the bot thread does."""
    if panel.worker is not None:
        panel.worker.join(1)
    panel.status.finished(result)


def test_erasing_an_account_takes_two_clicks(panel):
    assert panel.erase() is False
    assert panel.runs == []
    assert panel.view()["erase_label"] == DELETE_ARMED_LABEL
    assert panel.erase() is True
    finish(panel)
    assert panel.runs == [(True, "abyss")]
    assert panel.view()["erase_label"] == DELETE_LABEL


def test_the_first_click_on_reset_wears_off(panel):
    panel.erase()
    panel.clock.now += ERASE_CONFIRM_SECONDS + 0.1
    assert panel.view()["erase_armed"] is False
    panel.erase()
    assert panel.runs == []


def test_continue_takes_one_click_and_never_resets(panel):
    assert panel.start() is True
    finish(panel)
    assert panel.runs == [(False, "abyss")]


def test_the_window_has_its_five_tabs_and_opens_on_home(panel):
    assert panel.options()["tabs"] == ["Home", "Stats", "Log", "Settings", "Help"]


def test_help_has_short_steps_for_every_mode(panel):
    from core.panel import GOALS

    help_pages = dict(panel.options()["help"])
    assert list(help_pages)[0] == "Start here"
    # Every mode the rail offers has its page, named as the rail names it.
    assert {goal.label for goal in GOALS} <= set(help_pages)
    # Plain bullet points: a handful each, one short sentence or two.
    for steps in help_pages.values():
        assert 2 <= len(steps) <= 6
        assert all(len(step) <= 130 for step in steps)


def test_statistics_tab_shows_what_was_counted(panel):
    panel.status.add_cards([Card(hero_stars=5, moonlight=True), Card(hero_stars=4)])
    sections = dict(section for _, tab in panel.view()["stats"] for section in tab)
    assert sections["Pulls"][0][0] == "1"
    assert sections["Pulls"][4][0] == "1"
    assert sections["Rerolls"][2][0] == "2"
    panel.status.add_theater(acts=2, tickets=1, seconds=300)
    sections = dict(section for _, tab in panel.view()["stats"] for section in tab)
    assert sections["Moonlight Theater"][0][0] == "2"
    assert sections["Moonlight Theater"][2][0] == "500k"


def test_a_run_shows_its_events_in_plain_words(panel):
    panel.run_from = panel.feed.added
    panel.feed.lines.extend(["10:00:00 stage Episode 3 (2) 2-2", "10:00:01 state enter STORY", "10:00:02 screen: stage clear"])
    panel.feed.added += 3
    panel.status.started()
    shown = panel.view()["activity"]
    assert [tuple(event) for event in shown[:2]] == [("10:00", "Stage won"), ("10:00", "Stage 2-2 chosen")]
    assert len(shown) == 2
    assert any("state enter" in line for line in panel.log_lines(0)["lines"])


def test_the_log_sends_only_the_new_lines(panel):
    panel.feed.lines.extend(["a", "b"])
    panel.feed.added += 2
    first = panel.log_lines(0)
    assert (first["lines"], first["added"], first["reset"]) == (["a", "b"], 2, False)
    panel.feed.lines.append("c")
    panel.feed.added += 1
    assert panel.log_lines(2)["lines"] == ["c"]
    assert panel.log_lines(3)["lines"] == []
    assert panel.log_lines(99)["reset"] is True


def test_during_a_run_only_stop_shows(panel):
    panel.status.started()
    shown = panel.view()
    assert shown["running"]
    assert not shown["can_start"] and not shown["can_erase"]
    panel.stop()
    assert panel.stop_event.is_set()


def test_the_result_button_shows_only_with_a_picture(panel):
    assert panel.view()["band"]["image"] is False
    # The reroll ran: its result shows on its own mode.
    panel.run_goal = "abyss"
    panel.status.set_result(21, 21, "x.png")
    panel.status.finished("account kept: Zio ML 5*")
    assert panel.view()["band"]["image"] is True
    opened = []
    panel.open_path = opened.append
    panel.open_image()
    assert opened and opened[0].name == "x.png"


def test_stopped_the_buttons_to_start_work_and_stop_does_not_show(panel):
    shown = panel.view()
    assert not shown["running"]
    assert shown["can_erase"] and shown["can_start"]


def test_the_modes_sit_under_four_categories_with_one_gear_menu():
    from core.panel import GOALS

    assert [goal.category for goal in GOALS] == ["Reroll"] * 2 + ["Progression"] * 3 + ["Tools"] * 4 + ["Gear"] * 5
    assert [goal.key for goal in GOALS if goal.menu == "gear_work"] == ["gear_craft", "gear_roll", "gear_sort"]


def test_the_modes_are_offered_and_one_click_picks_one(panel):
    assert [goal.name for goal in GOALS] == [
        "Summon reroll", "Abyss 45 reroll", "Story", "Abyss", "Theater", "Arena", "Shop refresh", "Penguins", "Huche shop",
        "Craft gear", "Enhance gear", "Sell gear", "Account evaluation", "Gear overlay",
    ]
    assert [goal["key"] for goal in panel.options()["goals"]] == [goal.key for goal in GOALS]
    assert panel.view()["goal"] == "reroll_abyss"
    assert "45" in panel.view()["description"]
    panel.pick_mode("story")
    assert panel.view()["goal"] == "story"
    panel.pick_mode("reroll_abyss")
    assert panel.view()["goal"] == "reroll_abyss"


@pytest.mark.parametrize("mode", ["story", "climb"])
def test_a_kept_account_mode_never_offers_to_erase_it(panel, mode):
    panel.pick_mode(mode)
    shown = panel.view()
    assert not shown["can_erase"]
    assert shown["can_start"]
    panel.erase()
    panel.erase()
    assert panel.worker is None


def test_during_a_run_the_menu_can_be_browsed_without_touching_the_run(panel):
    panel.pick_mode("story")
    panel.start()
    assert panel.view()["on_run"] and panel.view()["run_name"] == "Story"
    panel.back()
    shown = panel.view()
    assert shown["goal"] == "" and shown["running"] and not shown["on_run"]
    panel.pick_mode("arena")
    shown = panel.view()
    assert shown["goal"] == "arena" and not shown["on_run"] and not shown["can_start"]
    assert panel.run_goal == "story" and panel.start() is False
    panel.show_run()
    assert panel.view()["goal"] == "story" and panel.view()["on_run"]
    finish(panel)
    panel.show_run()
    assert panel.view()["on_run"] is False


def test_a_running_kept_mode_says_it_never_erases(panel):
    panel.pick_mode("story")
    panel.start()
    assert panel.view()["kept_note"] == "This mode never erases the account."
    finish(panel)


def test_heroes_hidden_by_the_search_stay_chosen(panel):
    panel.set_hero("Zio", True)
    panel.set_hero("Briar Witch Iseria", True)
    panel.set_hero("Not a hero", True)
    assert panel.read_settings().stop_heroes == ["Briar Witch Iseria", "Zio"]
    panel.set_hero("Zio", False)
    assert panel.read_settings().stop_heroes == ["Briar Witch Iseria"]


def test_the_settings_are_saved_as_soon_as_they_are_valid(panel):
    panel.set_setting("story_max_revives", 3)
    assert json.loads(panel.settings_path.read_text(encoding="utf-8"))["story_max_revives"] == 3
    assert panel.set_setting("story_max_revives", 99) != ""
    assert json.loads(panel.settings_path.read_text(encoding="utf-8"))["story_max_revives"] == 3
    assert panel.view()["invalid"] == {"story_max_revives": panel.view()["invalid"]["story_max_revives"]}


def test_an_invalid_setting_stops_the_start_with_a_message(panel):
    panel.set_setting("pre_action_delay_min_ms", -5)
    assert panel.start() is False
    assert panel.view()["band"]["detail"] == INVALID_SETTING
    panel.set_setting("pre_action_delay_min_ms", 100)
    assert panel.start() is True
    finish(panel)


def test_settings_cannot_change_during_a_run(panel):
    panel.status.started()
    assert panel.set_setting("story_max_revives", 2) != ""
    assert panel.draft["story_max_revives"] == 0
    assert panel.set_setting("no_such_setting", 1).startswith("Unknown")


def test_the_top_rules_are_read(panel):
    assert panel.set_top_rule(1, 2, 10) == ""
    assert panel.read_settings().top_rules == [(0, 5), (2, 10)]
    assert panel.set_top_rule(0, 9, 10) != ""
    assert panel.read_settings() is None


def test_a_role_is_turned_on_and_given_its_own_efficiency(panel):
    from core.settings import gear_policy, apply_settings
    from core.gear import ALL_ROLES, role_target

    assert panel.set_role("tank", True, 85) == ""
    settings = panel.read_settings()
    assert "tank" in settings.gear_roles and settings.gear_role_efficiency == {"tank": 85.0}
    policy = gear_policy(apply_settings(load_config(Path(__file__).parent.parent / "config.yaml"), settings).gear)
    assert "tank" in policy.roles and role_target(policy, "tank") == 85
    # Back to auto: a niche role asks 15 points over the general efficiency.
    assert panel.set_role("tank", True, "") == ""
    policy = gear_policy(apply_settings(load_config(Path(__file__).parent.parent / "config.yaml"), panel.read_settings()).gear)
    assert role_target(policy, "tank") == policy.efficiency + 15 and role_target(policy, "damage") == policy.efficiency
    assert panel.set_role("tank", False, "") == ""
    assert set(panel.read_settings().gear_roles) == set(ALL_ROLES) - {"tank"}
    assert panel.set_role("tank", True, 150) != "" and panel.read_settings() is None
    assert panel.set_role("nobody", True) == "Unknown role"
    assert [row["key"] for row in panel.options()["roles"]][:4] == ["damage", "damage_sure_crit", "damage_nocrit", "damage_slow"]


def test_the_band_names_the_open_mode_and_leads_back(panel):
    view = panel.view()
    assert (view["band"]["title"], view["band"]["detail"], view["band"]["back"]) == (view["goal_name"], "Ready to start", True)
    panel.back()
    view = panel.view()
    assert view["band"]["title"] == "Ready to start" and not view["band"]["back"]


def test_a_goal_from_the_command_line_is_ticked(panel):
    panel.back()
    panel.select_goal("abyss")
    assert panel.view()["goal"] == "reroll_abyss"


def test_a_goal_without_a_working_choice_changes_nothing(panel):
    panel.select_goal("no_such_goal")
    panel.select_goal("moonlight_theater")
    assert panel.view()["goal"] == "reroll_abyss"


def test_the_event_summons_box_is_saved_with_the_settings(panel):
    panel.set_setting("event_summons", False)
    assert panel.read_settings().event_summons is False
    panel.set_setting("event_summons", True)
    assert panel.read_settings().event_summons is True


def test_the_reset_box_is_saved_and_applied(panel):
    from core.settings import apply_settings
    panel.set_setting("reset_account", False)
    settings = panel.read_settings()
    assert settings.reset_account is False
    assert apply_settings(load_config(ROOT / "config.yaml"), settings).reset.enabled is False


@pytest.mark.parametrize("mode", ["gear_craft", "gear_sort", "gear_roll"])
def test_the_gear_modes_can_be_picked_and_never_erase_the_account(panel, mode):
    panel.pick_mode(mode)
    shown = panel.view()
    assert shown["goal"] == mode
    assert GOAL_BY_KEY[mode].bot_goal == mode
    assert mode in KEPT_GOALS
    assert not shown["can_erase"]
    assert shown["stats_tab"] == "Gear"
    assert shown["settings_tab"] == "Gear"


def test_the_gear_run_settings_are_read_from_the_window(panel):
    panel.show_gear_page("Run")
    assert panel.view()["gear_page"] == "Run"
    for name, value in (
        ("gear_dry_run", True), ("gear_screenshots", True), ("gear_only_new", True), ("gear_max_pieces", 25),
        ("gear_max_gold_millions", 2.5), ("gear_craft_hunt", "banshee"), ("gear_craft_slot", "ring"),
        ("gear_craft_pieces", 30), ("gear_craft_then", "sort"),
    ):
        assert panel.set_setting(name, value) == ""
    settings = panel.read_settings()
    assert settings.gear_dry_run and settings.gear_screenshots and settings.gear_only_new
    assert settings.gear_max_pieces == 25 and settings.gear_max_gold_millions == 2.5
    assert (settings.gear_craft_hunt, settings.gear_craft_slot) == ("banshee", "ring")
    assert settings.gear_craft_pieces == 30 and settings.gear_craft_then == "sort"
    choices = panel.options()
    assert {"key": "banshee", "label": "Banshee"} in choices["hunts"]
    assert {"key": "sort", "label": "Remove the bad ones"} in choices["craft_then"]


def test_the_gear_folder_is_made_and_opened(panel):
    opened = []
    panel.open_path = opened.append
    panel.open_gear_folder()
    assert opened == [(panel.log_dir / "gear").resolve()] and opened[0].is_dir()


def test_the_moonlight_list_follows_the_ranking(panel):
    heroes = panel.options()["heroes"]
    assert heroes[:3] == ["Lisette", "Rhianna and Luciella", "Lady of the Scales"]
    assert heroes[-3:] == ["Ainz Ooal Gown", "Bunny Girl Great Detective Moro", "Renoa"]


def test_the_gear_rules_are_read_from_the_window(panel):
    panel.set_setting("gear_chase", "speed")
    panel.set_setting("gear_min_speed_speed_set", 20)
    panel.set_setting("gear_max_level", 12)
    panel.set_setting("gear_new_bad_action", "sell")
    settings = panel.read_settings()
    assert (settings.gear_chase, settings.gear_min_speed_speed_set, settings.gear_max_level, settings.gear_new_bad_action) == ("speed", 20, 12, "sell")


def test_the_stats_follow_the_home_order_and_show_one_mode_at_a_time(panel):
    assert panel.options()["stats_tabs"] == ["Reroll", "Story", "Abyss", "Theater", "Arena", "Shop", "Penguins", "Huche", "Gear"]
    panel.pick_mode("theater")
    assert panel.view()["stats_tab"] == "Theater"
    panel.show_stats("Abyss")
    assert panel.view()["stats_tab"] == "Abyss"
    panel.show_stats("Nothing")
    assert panel.view()["stats_tab"] == "Abyss"


def test_the_settings_are_in_tabs_and_follow_the_mode(panel):
    assert panel.options()["settings_tabs"] == ["Reroll", "Theater", "Arena", "Gear", "Advanced"]
    panel.pick_mode("theater")
    assert panel.view()["settings_tab"] == "Theater"
    panel.show_settings("Gear")
    assert panel.view()["settings_tab"] == "Gear"


def test_the_ticket_settings_are_read(panel):
    panel.set_setting("theater_buy_tickets", False)
    panel.set_setting("theater_min_gold_millions", 12)
    settings = panel.read_settings()
    assert (settings.theater_buy_tickets, settings.theater_min_gold_millions) == (False, 12)


def test_the_arena_settings_are_read(panel):
    for name, value in (("arena_quick_match", False), ("arena_attack_players", True), ("arena_friendship_refill", True), ("arena_skystone_refill", True), ("arena_target_fights", 40)):
        panel.set_setting(name, value)
    settings = panel.read_settings()
    assert (settings.arena_quick_match, settings.arena_attack_players, settings.arena_friendship_refill, settings.arena_skystone_refill, settings.arena_target_fights) == (False, True, True, True, 40)


def test_the_story_revive_setting_is_read(panel):
    assert panel.read_settings().story_max_revives == 0
    panel.set_setting("story_max_revives", 2)
    assert panel.read_settings().story_max_revives == 2


def test_the_stats_are_reset_only_after_a_second_click(panel):
    save_stats(Stats(theater_acts=4), panel.state_path)
    save_total_rerolls(9, panel.state_path)
    panel.status.add_theater(acts=4)
    panel.reset_stats()
    assert load_stats(panel.state_path).theater_acts == 4
    assert panel.view()["reset_stats_armed"]
    panel.reset_stats()
    assert load_stats(panel.state_path) == Stats()
    assert load_total_rerolls(panel.state_path) == 0
    assert panel.status.snapshot().stats == Stats()
    assert panel.view()["reset_stats_note"] == "Stats reset."


def test_the_first_click_on_reset_stats_wears_off(panel):
    panel.reset_stats()
    panel.clock.now += 6
    shown = panel.view()
    assert not shown["reset_stats_armed"] and shown["reset_stats_note"] == "Reset cancelled."


RULES = ["gear_efficiency", "gear_stone_min_efficiency", "gear_chase"]


def test_a_page_reset_puts_back_only_its_settings_after_a_second_click(panel):
    panel.set_setting("gear_efficiency", 80)
    panel.set_setting("gear_stone_min_efficiency", 90)
    panel.set_setting("theater_min_gold_millions", 50)
    panel.set_role("burner", False, 70)
    panel.reset_settings("Gear › Rules", RULES)
    assert panel.read_settings().gear_efficiency == 80
    assert panel.view()["reset_settings_armed"] == "Gear › Rules"
    panel.reset_settings("Gear › Rules", RULES)
    saved = load_settings(panel.settings_path, load_config(ROOT / "config.yaml"))
    assert saved.gear_efficiency == 65 and saved.gear_stone_min_efficiency == 100
    # Other pages stay: Theater, and the roles on Gear › Roles.
    assert saved.theater_min_gold_millions == 50
    assert "burner" not in saved.gear_roles and saved.gear_role_efficiency == {"burner": 70}
    assert panel.view()["reset_settings_note"] == ["Gear › Rules", "Back to the defaults."]


def test_a_page_reset_clears_its_errors_and_never_touches_the_client(panel):
    panel.set_setting("gear_efficiency", 300)
    panel.reset_settings("Gear › Rules", RULES + ["client"])
    panel.reset_settings("Gear › Rules", RULES + ["client"])
    assert panel.view()["invalid"] == {}
    assert panel.read_settings().client == "stove"


def test_a_page_reset_wears_off_and_another_page_asks_again(panel):
    panel.set_setting("gear_efficiency", 80)
    panel.reset_settings("Gear › Rules", RULES)
    panel.reset_settings("Theater", ["theater_min_gold_millions"])
    assert panel.view()["reset_settings_armed"] == "Theater"
    panel.clock.now += 6
    assert panel.view()["reset_settings_armed"] == ""
    panel.reset_settings("Gear › Rules", RULES)
    assert panel.read_settings().gear_efficiency == 80


def test_without_a_client_the_window_asks_and_does_not_start(tmp_path):
    bare = make_panel(tmp_path, Settings())
    bare.pick_mode("reroll_abyss")
    assert bare.view()["needs_client"] is True
    assert bare.start() is False
    assert bare.worker is None
    assert bare.view()["place"] == "No client chosen"


def test_choosing_a_client_aims_the_window_and_saves_it(panel):
    titles = []
    panel.on_title = titles.append
    assert panel.choose_client("steam")
    assert titles == ["E7 Bot · Steam"] and panel.view()["title"] == "E7 Bot · Steam"
    assert panel.window_config.process_name == "EpicSeven_Steam.exe"
    assert panel.state_path.name == "state_steam.json"
    assert json.loads(panel.settings_path.read_text(encoding="utf-8"))["client"] == "steam"
    assert panel.view()["place"] == "Steam · server Foundry"
    assert not panel.choose_client("epic")


def test_the_client_cannot_change_during_a_run(panel):
    panel.start()
    assert not panel.choose_client("steam")
    finish(panel)
    assert panel.client == "stove"


def test_a_new_window_opens_on_the_menu_with_no_mode_and_start_greyed(tmp_path):
    fresh = make_panel(tmp_path, Settings(client="stove"))
    shown = fresh.view()
    assert shown["goal"] == "" and shown["description"] == PICK_MODE_LABEL
    assert not shown["can_start"] and not shown["can_erase"]
    assert fresh.start() is False
    fresh.erase()
    assert fresh.worker is None and not fresh.view()["erase_armed"]


def test_the_back_arrow_returns_to_the_menu_and_keeps_the_settings(panel):
    panel.pick_mode("theater")
    panel.set_setting("theater_min_gold_millions", 12)
    assert panel.view()["goal_name"] == "Theater"
    panel.back()
    shown = panel.view()
    assert shown["goal"] == ""
    assert shown["settings"]["theater_min_gold_millions"] == 12
    assert shown["settings_tab"] == "Theater"


def test_the_route_shows_a_bar_during_the_abyss_route(panel):
    panel.start()
    panel.status.set_state("ROUTE")
    panel.feed.lines.append("10:00:00 route step 9 done")
    panel.feed.added += 1
    route = panel.view()["band"]["route"]
    assert route is not None and route["text"].startswith("Route: step")
    assert 0 <= route["fraction"] <= 1
    finish(panel)


def test_a_stopped_story_shows_the_last_session(panel):
    panel.pick_mode("story")
    panel.start()
    panel.feed.lines.extend(["10:00:00 stage Episode 3 (2) 2-2", "10:00:02 screen: stage clear"])
    panel.feed.added += 2
    panel.view()
    finish(panel, "stopped by the user")
    shown = panel.view()
    assert shown["session"].startswith("Last session")
    assert shown["numbers"][0] == ("1", "stage won")


def test_exporting_the_log_says_where_it_went(panel, tmp_path):
    (tmp_path / "bot.log").write_text("line\n", encoding="utf-8")
    target = tmp_path / "out.txt"
    panel.ask_save_path = lambda name: str(target)
    panel.export_journal()
    assert target.read_text(encoding="utf-8") == "line\n"
    assert panel.view()["export_note"] == f"Log exported: {target}"
    panel.ask_save_path = lambda name: None
    panel.export_journal()
    assert panel.view()["export_note"] == f"Log exported: {target}"


def test_the_view_can_be_sent_to_the_page(panel):
    json.dumps(panel.view())
    json.dumps(panel.options())


def test_a_kept_moonlight_says_so():
    assert describe_stop("account kept: Briar Witch Iseria \u2018KE ML 5*").detail == "Briar Witch Iseria 5\u2605 Moonlight"


def test_stats_sort_the_cards():
    stats = Stats()
    stats.add_cards([
        Card(hero_stars=5, moonlight=True),
        Card(hero_stars=5),
        Card(hero_stars=4, moonlight=True),
        Card(hero_stars=4),
        Card(hero_stars=3, moonlight=True),
        Card(artifact_stars=5),
        Card(artifact_stars=4),
    ])
    assert stats.summons == 7
    assert (stats.heroes_5, stats.moonlight_5, stats.moonlight_4, stats.normal_4, stats.artifacts_5) == (2, 1, 1, 1, 1)


def test_average_run_time():
    stats = Stats()
    assert stats.average_run_seconds() is None
    stats.add_run(100)
    stats.add_run(200)
    assert stats.average_run_seconds() == 150


def test_no_finished_run_shows_a_dash_instead_of_a_zero():
    tiles = {caption: value for _, section in stat_sections(Stats(), 0) for value, caption in section}
    assert tiles["per account"] == "—"
    assert tiles["win rate"] == "—"


def test_the_stats_show_rates_and_the_theater_gold():
    stats = Stats(theater_acts=4, theater_tickets=3, theater_seconds=600, story_stages_won=9, story_fights_lost=1, summons=200, heroes_5=3)
    sections = dict(stat_sections(stats, 12))
    theater = {caption: value for value, caption in sections["Moonlight Theater"]}
    assert theater["gold on tickets"] == "1.5M"
    assert theater["per act"] == "2 min 30"
    story = {caption: value for value, caption in sections["Push the story"]}
    assert story["win rate"] == "90%"
    draws = {caption: value for value, caption in sections["Pulls"]}
    assert draws["5★ rate"] == "2%"
    assert all(len(tiles) <= 6 for tiles in sections.values())


def test_stats_and_reroll_count_share_their_file(tmp_path):
    path = tmp_path / "state.json"
    save_total_rerolls(15, path)
    stats = Stats(summons=21)
    save_stats(stats, path)
    save_total_rerolls(16, path)
    assert load_total_rerolls(path) == 16
    assert load_stats(path).summons == 21


def test_an_old_or_broken_state_file_gives_empty_stats(tmp_path):
    path = tmp_path / "state.json"
    path.write_text('{"total_rerolls": 15}', encoding="utf-8")
    assert load_stats(path) == Stats()
    assert load_total_rerolls(path) == 15
    path.write_text("not json", encoding="utf-8")
    assert load_stats(path) == Stats()


def test_export_puts_the_whole_log_in_order(tmp_path):
    (tmp_path / "bot.log.2").write_text("oldest\n", encoding="utf-8")
    (tmp_path / "bot.log.1").write_text("older\n", encoding="utf-8")
    (tmp_path / "bot.log").write_text("newest\n", encoding="utf-8")
    destination = tmp_path / "out.txt"
    export_log(tmp_path, destination)
    assert destination.read_text(encoding="utf-8").split() == ["oldest", "older", "newest"]


def test_journal_starts_with_what_the_log_file_holds(tmp_path):
    path = tmp_path / "bot.log"
    path.write_text(
        "2026-11-05 15:28:52,901 INFO    e7bot: reset 1 done\n"
        "2026-11-05 15:28:53,000 DEBUG   e7bot: 1 click(s)\n"
        "2026-11-05 15:28:54,000 WARNING e7bot: careful\n",
        encoding="utf-8",
    )
    feed = LogFeed()
    feed.load_file(path)
    assert list(feed.lines) == ["15:28:52 reset 1 done", "15:28:54 careful"]


def test_identifying_a_hero_read_with_noise_or_wrong_letters():
    names = ["Briar Witch Iseria", "Dark Corvus", "Zio"]
    assert identify_hero("Briar Witch Iseria \u2018KE", names) == "Briar Witch Iseria"
    assert identify_hero("Briar Witoh Iseria", names) == "Briar Witch Iseria"
    assert identify_hero("Zio ~", names) == "Zio"
    assert identify_hero("Corvus", names) is None
    assert identify_hero("xq#7", names) is None


def test_the_default_rule_is_one_moonlight():
    settings = Settings()
    assert (settings.min_moonlight_5star, settings.stop_heroes) == (1, [])


def test_the_list_has_the_recent_heroes_missing_from_the_official_file():
    names = load_moonlight_heroes(ROOT / "data" / "moonlight_heroes.json")
    assert "Rhianna and Luciella" in names
    assert "Lisette" in names
    assert identify_hero("Rhianna and Luciella _~", names) == "Rhianna and Luciella"


def test_a_cut_name_is_known_when_only_one_hero_starts_with_it():
    names = load_moonlight_heroes()
    assert identify_hero("Sylvan S¢", names) == "Sylvan Sage Vivian"
    assert identify_hero("Sylv", names) is None
    assert identify_hero("Dark C", names) in (None, "Dark Corvus")


def test_the_climb_stops_say_what_to_do():
    weak = describe_stop("3 lost fights in a row on abyss floor 52: the team needs to get stronger")
    assert (weak.kind, weak.title) == (ATTENTION, "Team too weak")
    assert "floor 52" in weak.detail and "erased" in weak.detail
    empty = describe_stop("the abyss team is empty: set it in the game, then start again")
    assert empty.title == "Abyss team empty"


def test_a_full_inventory_says_which_one():
    display = describe_stop("cannot start: the artifacts inventory is full")
    assert display.title == "Cannot start"
    assert display.detail.startswith("Artifact inventory full.")
    assert describe_stop("cannot start: the equipment inventory is full").detail.startswith("Equipment inventory full.")
    assert describe_stop("cannot start: the heroes inventory is full").detail.startswith("Hero inventory full.")


def test_the_gear_run_shows_its_own_numbers():
    progress = Progress(gear_judged=12, gear_enhanced=16, gear_removed=5, gear_gold=384_000)
    snapshot = BotStatus().snapshot()
    assert run_metrics("gear_roll", snapshot, progress) == [("12", "pieces judged"), ("16", "tiers enhanced"), ("5", "sold or extracted")]
    line, numbers = gear_summary(progress, 60, Stats())
    assert numbers[2] == ("384k", "gold spent")


def test_a_theater_stop_with_paid_acts_left_says_why():
    from core.panel import describe_stop

    shown = describe_stop("Moonlight Theater: nothing more to watch, 2 acts watched and 0 tickets bought in this run; acts that need a ticket were left (gold under 20,000,000)")
    assert shown.title == "Theater paused" and "under 20M" in shown.detail
    done = describe_stop("Moonlight Theater: nothing more to watch, 2 acts watched and 0 tickets bought in this run")
    assert done.title == "Theater done"


def test_missing_page_files_are_named(tmp_path):
    from core.panel_window import missing_assets

    assert missing_assets(ROOT / "ui") == []
    (tmp_path / "index.html").write_text("", encoding="utf-8")
    assert missing_assets(tmp_path) == ["app.css", "app.js"]


def test_the_page_can_call_every_exposed_method(panel):
    from core.panel_window import API

    assert all(callable(getattr(panel, name)) for name in API)


def test_a_gear_run_is_named_after_its_mode_not_the_abyss_route(panel):
    panel.pick_mode("gear_sort")
    panel.start()
    panel.status.set_state("ROUTE")
    panel.feed.lines.append("20:16:00 screen: gear inventory")
    panel.feed.added += 1
    shown = panel.view()["band"]
    assert (shown["title"], shown["detail"], shown["doing"]) == ("Sell gear", "Gear: going through the inventory", "")
    assert shown["route"] is None
    finish(panel)


def test_stove_without_admin_cannot_start_and_says_why(tmp_path):
    bare = make_panel(tmp_path, Settings(client="stove"))
    bare.is_admin = lambda: False
    bare.pick_mode("story")
    shown = bare.view()
    assert not shown["can_start"] and shown["admin_note"].startswith("STOVE runs the game as administrator")
    assert bare.start() is False and bare.worker is None
    bare.is_admin = lambda: True
    assert bare.view()["admin_note"] == "" and bare.view()["can_start"]


def test_steam_never_needs_admin(tmp_path):
    bare = make_panel(tmp_path, Settings(client="steam"))
    bare.is_admin = lambda: False
    assert bare.view()["admin_note"] == ""


def test_picking_stove_without_admin_restarts_the_bot_as_admin(tmp_path):
    bare = make_panel(tmp_path, Settings(client="steam"))
    bare.is_admin = lambda: False
    asked, quit = [], []
    bare.relaunch = lambda: asked.append(True) or True
    bare.on_quit = lambda: quit.append(True)
    assert bare.choose_client("stove")
    assert asked == [True] and quit == [True]
    assert json.loads(bare.settings_path.read_text(encoding="utf-8"))["client"] == "stove"


def test_a_refused_admin_prompt_keeps_the_window_open(tmp_path):
    bare = make_panel(tmp_path, Settings(client="steam"))
    bare.is_admin = lambda: False
    quit = []
    bare.relaunch = lambda: False
    bare.on_quit = lambda: quit.append(True)
    bare.choose_client("stove")
    assert quit == [] and bare.view()["admin_note"]
    assert bare.restart_as_admin() is False and quit == []


def test_the_admin_relaunch_drops_the_client_argument_and_never_asks_twice():
    from core.window import ELEVATED_FLAG, relaunch_arguments

    assert relaunch_arguments(["--client", "steam", "--start", "continue"]) == ["--start", "continue", ELEVATED_FLAG]
    assert relaunch_arguments(["--client=steam", ELEVATED_FLAG]) == [ELEVATED_FLAG]
    assert relaunch_arguments([]) == [ELEVATED_FLAG]


def test_the_admin_relaunch_keeps_the_client_asked_on_the_command_line():
    from core.window import ELEVATED_FLAG, relaunch_arguments

    argv = ["--client", "stove", "--goal", "arena", "--start", "continue"]
    assert relaunch_arguments(argv, "stove") == ["--goal", "arena", "--start", "continue", "--client", "stove", ELEVATED_FLAG]


def test_each_mode_sums_up_the_rules_it_follows_from_settings():
    from core.panel import mode_summary

    rules = Settings(server="Foundry", stop_heroes=["Zio", "Lisette"], top_rules=[(1, 5), (0, 20)]).model_dump()
    assert mode_summary("abyss", rules) == "Keeps the account with 1 or more 5★ Moonlight, or top-ranked ones, or one of 2 chosen heroes · otherwise erases it on Foundry"
    assert mode_summary("abyss", {**rules, "reset_account": False}).endswith("never erases it")
    assert mode_summary("theater", rules) == "Buys tickets only above 20M gold"
    assert mode_summary("theater", {**rules, "theater_buy_tickets": False}) == "Buys no tickets"
    assert mode_summary("arena", {**rules, "arena_friendship_refill": True}) == "Quick Match when possible · refills with friendship"
    assert mode_summary("gear_sort", {**rules, "gear_max_gold_millions": 2.5}) == "Chases speed and gear score · up to +15 · extracts bad +0 pieces · at most 2.5M gold"
    assert mode_summary("story", rules) == mode_summary("climb", rules) == ""


def test_the_home_summary_follows_the_mode_and_links_to_its_settings_tab(panel):
    shown = panel.view()
    assert shown["summary"].startswith("Keeps the account") and shown["summary_tab"] == "Reroll"
    panel.pick_mode("arena")
    assert panel.view()["summary_tab"] == "Arena"
    panel.back()
    assert panel.view()["summary"] == ""


def test_a_gear_dry_run_is_flagged_while_it_runs(panel):
    panel.set_setting("gear_dry_run", True)
    panel.pick_mode("gear_sort")
    assert panel.view()["dry_run"] is False
    panel.start()
    assert panel.view()["dry_run"] is True
    finish(panel)
    assert panel.view()["dry_run"] is False


def test_the_gear_advisor_shows_what_it_sees(panel):
    panel.pick_mode("gear_advise")
    idle = panel.view()
    assert idle["can_start"] and not idle["can_erase"] and idle["numbers"] == [] and idle["advice"] is None
    assert idle["summary"].startswith("Judges as the gear modes do") and idle["summary_tab"] == "Gear"
    assert panel.start() is True
    panel.worker.join(1)
    assert panel.runs == [(False, "gear_advise")]
    waiting = panel.view()
    assert waiting["band"]["title"] == "Gear overlay" and waiting["advice"]["note"] == "Looking at the game…"
    sell = Advice("Row 1, card 1", (140, 150, 180, 120), "sell", "no potential left at its level", "epic", "boots", 6, "speed", "speed", {"health": 180.0, "crit_chance": 4.0})
    worn = Advice("Row 1, card 2", (310, 150, 180, 120), "enhance", "speed potential 20 (needs 18)", "epic", "weapon", 3, "speed", "attack", {"speed": 6.0}, equipped=True)
    panel.advice_board.publish(ScreenAdvice("inventory", None, (sell, worn)), "")
    shown = panel.view()
    assert shown["band"]["detail"] == "Inventory"
    assert shown["numbers"] == [("1", "to enhance"), ("0", "to keep"), ("1", "to sell or extract")]
    first, second = shown["advice"]["cards"]
    assert first["label"] == "Sell" and first["title"] == "Epic boots +6 · Speed set"
    assert first["subs"] == [["Health", "180", ""], ["Crit chance", "4%", ""]]
    assert second["flags"] == ["Worn"]
    assert shown["activity"] == [] or isinstance(shown["activity"], list)
    finish(panel, "stopped by the user")
    assert panel.view()["advice"] is None


def test_a_click_on_an_advisor_card_shows_it_and_a_second_goes_back(panel):
    panel.pick_piece("Row 1, card 2")
    assert panel.advice_pick == "Row 1, card 2"
    panel.pick_piece("Row 1, card 2")
    assert panel.advice_pick == ""


def test_the_account_page_shows_the_gear_and_applies_the_targets(panel, tmp_path):
    from core.account import AccountResult, ScannedPiece, save_result
    from core.settings import apply_settings, gear_policy
    from core.gear import role_target

    panel.pick_mode("gear_account")
    account = panel.view()["account"]
    assert account["roles"] == [] and not account["can_apply"] and "No evaluation yet" in account["note"]
    assert panel.apply_account_scores() != ""
    # Twelve bruiser helmets from 90 down, as fast as 17 down.
    pieces = tuple(ScannedPiece("helmet", "speed", 85, {"bruiser": 90.0 - index}, 17.0 - index, 60.0 - index) for index in range(12))
    save_result(AccountResult("2026-11-12 20:00", 12, 1, 0, pieces), tmp_path / "gear")
    assert panel.set_role_heroes("bruiser", 10) == ""
    account = panel.view()["account"]
    bruiser = next(row for row in account["roles"] if row["role"] == "bruiser")
    assert account["level"] == "Starting" and "12 +15 pieces read" in account["note"] and "median 54.5" in account["level_note"]
    # Ten heroes, ten seats: the ten best helmets are seated, the last two no hero needs.
    assert (bruiser["heroes"], bruiser["count"], bruiser["best"], bruiser["worst"]) == (10, 10, "90%", "81%")
    assert account["unneeded_count"] == 2 and account["unneeded"][0]["slot"] == "helmet"
    assert [piece["value"] for piece in account["unneeded"][0]["pieces"]] == [79.0, 80.0]
    assert account["unneeded"][0]["pieces"][0]["kept"] == ""
    assert "helmet 81" in bruiser["targets"] and "ring 65" in bruiser["targets"]
    helmet = next(row for row in account["speed"] if row["slot"] == "helmet")
    assert (helmet["fastest"], helmet["values"], helmet["target"]) == ("17 · 16 · 15", [17, 16, 15], "18")
    # The targets' chart: the 10th best helmet, and rings at the user's own target with no piece.
    cells = {cell["slot"]: cell for cell in bruiser["cells"]}
    assert (cells["helmet"]["target"], cells["helmet"]["count"], cells["helmet"]["own"]) == (81, 10, False)
    assert (cells["ring"]["target"], cells["ring"]["count"], cells["ring"]["own"], cells["ring"]["short"]) == (65, 0, True, True)
    assert bruiser["values"][0] == 81 and len(bruiser["values"]) == 10
    # Fewer heroes: the targets follow at once, before any new scan.
    assert panel.set_role_heroes("bruiser", 2) == ""
    assert "helmet 89" in next(row for row in panel.view()["account"]["roles"] if row["role"] == "bruiser")["targets"]
    assert panel.set_role_heroes("bruiser", -1) != "" and panel.set_role_heroes("nobody", 2) == "Unknown role"
    assert panel.apply_account_scores() == "" and panel.view()["account"]["applied"]
    policy = gear_policy(apply_settings(load_config(ROOT / "config.yaml"), panel.read_settings()).gear)
    assert role_target(policy, "bruiser", "helmet") == 89 and dict(policy.speed_slot_targets)["helmet"] == 18
    assert panel.clear_account_targets() == "" and panel.read_settings().gear_slot_targets == {}
    # Other modes have no account page.
    panel.pick_mode("gear_sort")
    assert panel.view()["account"] is None


def test_another_mode_does_not_show_the_last_runs_result(panel):
    # The arena ran and stopped; the theater opened afterwards is ready, with none of the arena's numbers.
    panel.run_goal = "arena"
    panel.status.finished("Arena: no flags left: 2 fights, 2 won")
    panel.pick_mode("arena")
    assert panel.view()["numbers"]
    panel.pick_mode("theater")
    view = panel.view()
    assert (view["band"]["kind"], view["band"]["title"], view["numbers"]) == ("idle", "Theater", [])
    assert "flags" not in view["band"]["detail"]
def test_the_admin_relaunch_opens_no_console(tmp_path):
    from core.window import windowed_python

    (tmp_path / "python.exe").write_text("", encoding="utf-8")
    assert windowed_python(str(tmp_path / "python.exe")) == str(tmp_path / "python.exe")
    (tmp_path / "pythonw.exe").write_text("", encoding="utf-8")
    assert windowed_python(str(tmp_path / "python.exe")) == str(tmp_path / "pythonw.exe")


def test_the_summon_reroll_is_offered_and_erases_accounts(panel):
    panel.select_goal("summons")
    assert panel.view()["goal"] == "reroll_summons" and panel.can_erase()


def test_a_running_account_evaluation_shows_its_step_and_the_tabs_read():
    from core.progress import read_progress

    lines = [
        "12:16:15 account: setting the inventory filters: worn pieces shown, highest level first",
        "12:16:24 account: weapon tab",
        "12:16:40 account: weapon tab done: 24 +15 pieces (under +15)",
        "12:16:40 account: helmet tab",
    ]
    shown = band(snapshot("ROUTE"), "gear_account", read_progress(lines))
    assert shown["doing"] == "Reading the helmet tab (2 of 6)"
    assert shown["route"] == {"text": "1 of 6 slot tabs read", "fraction": 1 / 6}
    early = band(snapshot("ROUTE"), "gear_account", read_progress(["12:16:12 screen: lobby"]))
    assert early["doing"] == "On the way to the inventory" and early["route"]["fraction"] == 0


def test_locked_or_worn_pieces_the_rules_would_sell_are_listed_with_the_better_ones():
    from core.account import evaluate
    from core.gear import Piece
    from core.panel import kept_view

    good = Piece("ring", "attack_percent", 15, {"crit_chance": 16, "crit_damage": 22, "speed": 12, "health_percent": 9}, "epic", "critical", item_level=88)
    junk = Piece("ring", "attack_percent", 15, {"defense": 30, "health": 160, "effect_resistance": 5, "defense_percent": 5}, "epic", "health", item_level=88)
    result = evaluate([good, good, junk, good], kept={2: "worn", 3: "locked"})
    shown = kept_view(result, Settings().model_dump())
    assert shown["kept_note"].startswith("1 locked and 1 worn +15 pieces judged")
    assert [(row["kind"], row["action"]) for row in shown["outdated"]] == [("worn", "sell")], "the good locked ring stays"
    row = shown["outdated"][0]
    assert row["title"] == "Epic ring, health set" and row["better"] == "3 better ring pieces on the account"
    assert row["reason"]


def test_an_evaluation_from_before_the_kept_check_says_to_run_it_again():
    from dataclasses import replace

    from core.account import evaluate
    from core.panel import kept_view

    shown = kept_view(replace(evaluate([]), kept_checked=False), Settings().model_dump())
    assert shown["outdated"] == [] and "run it again" in shown["kept_note"]
    assert kept_view(evaluate([]), Settings().model_dump())["kept_note"].endswith("all still worth keeping.")
