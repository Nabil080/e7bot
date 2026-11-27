// The bot window's page: draws what core/panel.py says and sends it the clicks. No logic of its own beyond drawing.
"use strict";

const POLL_MS = 250;
const LOG_KEEP = 5000;

// The settings you change from one run to the next, in each mode on Home. Names are the fields of core/settings.py.
const DRY_RUN = { name: "gear_dry_run", label: "Dry run: judge and log every piece, touch nothing", type: "switch" };
const RUN = {
  story: [
    { name: "story_max_revives", label: "Skystone revives per fight (max)", icon: "skystone.png", type: "number", min: 0, max: 10, step: 1 },
    { note: "A lost fight is retried by paying the revive in skystones, up to this many times, then it counts as a loss (3 in a row stop the bot). With 0, it never pays." },
  ],
  theater: [
    { name: "theater_target_acts", label: "Acts to watch (0 = all)", icon: "film.png", type: "number", min: 0, max: 1000, step: 1 },
    { name: "theater_buy_tickets", label: "Buy tickets with gold (500,000 each)", icon: "ticket.png", type: "switch" },
    { note: "It stops at this many acts, or when nothing is left to watch. Without buying, it watches the free acts and the ones your tickets pay for." },
  ],
  arena: [
    { name: "arena_target_fights", label: "Fights to do (0 = until out of flags)", icon: "arena_flag.png", type: "number", min: 0, max: 1000, step: 1 },
    { name: "arena_attack_players", label: "Attack players (instead of NPCs)", type: "switch" },
    { note: "Fights against players change your ranking." },
  ],
  shop: [
    { name: "shop_refreshes", label: "Refreshes", type: "number", min: 0, max: 5000, step: 1 },
    { type: "shop_cost" },
    { name: "shop_stop_bookmarks", label: "Stop after this many bookmarks (0 = no limit)", icon: "covenant_bookmark.png", type: "number", min: 0, max: 100000, step: 5 },
    { name: "shop_stop_medals", label: "Stop after this many medals (0 = no limit)", icon: "mystic_medal.png", type: "number", min: 0, max: 1000000, step: 50 },
    { note: "Each refresh shows a new list: it buys every Covenant Bookmarks (5 for 184,000 gold) and Mystic Medals (50 for 280,000 gold) in it, then refreshes. It stops after the refreshes, at the first target reached, or when gold or skystones run out." },
  ],
  penguins: [
    { name: "penguins_kind", label: "Stop after", type: "choice", choices: [["purchases", "Purchases"], ["stigma", "Stigma spent"]] },
    { name: "penguins_amount", label: "How many", icon: "penguin.png", type: "number", min: 1, max: 100000000, step: 1 },
    { type: "penguin_cost" },
    { note: "One purchase: 50 penguins for 5,100 stigma. With a stigma amount, it stops before a purchase would go over it. It also stops when the stigma runs out." },
  ],
  gear_craft: [
    { name: "gear_craft_hunt", label: "Hunt", type: "select", choices: "hunts" },
    { name: "gear_craft_slot", label: "Piece", type: "select", choices: "slots" },
    { name: "gear_craft_pieces", label: "Pieces to craft", type: "number", min: 1, max: 500, step: 10 },
    { name: "gear_craft_then", label: "Then", type: "select", choices: "craft_then" },
    DRY_RUN,
    { note: "It crafts 10 at a time, then one by one, and stops early when the hunt's materials or the gold limit run out." },
  ],
  gear_sort: [
    DRY_RUN,
    { name: "gear_review", label: "Sort review: tick what it would sell or extract, then stop in Arrange", type: "switch" },
  ],
  gear_roll: [DRY_RUN],
};

// The rules, set once and rarely changed, tab by tab in Settings.
const SETTINGS = {
  Reroll: [
    { heading: "Account" },
    { name: "server", label: "Server", type: "select", choices: "servers" },
    { name: "reset_account", label: "Reset the account at the end of a reroll", type: "switch" },
    { name: "event_summons", label: "Abyss: pull the free event summons", type: "switch" },
    { note: "The reset erases the account on the chosen server, for good." },
    { heading: "Keep the account when…" },
    { name: "min_moonlight_5star", label: "At least this many 5★ Moonlight", icon: "moonlight.png", type: "number", min: 1, max: 5, step: 1 },
    { type: "top_rule", index: 0 },
    { type: "top_rule", index: 1 },
    { type: "heroes" },
    { note: "One rule is enough. With 1 in the first setting, any 5★ Moonlight keeps the account: the others only matter from 2. Ranking: data/ml5_ranking.json, 0 turns a top rule off." },
  ],
  Theater: [
    { heading: "Tickets" },
    { name: "theater_min_gold_millions", label: "Don't buy below (million gold)", icon: "gold.png", type: "number", min: 0, max: 1000, step: 1 },
    { note: "Buying tickets is turned on or off in the Moonlight Theater mode, on Home." },
  ],
  Arena: [
    { heading: "Auto arena" },
    { name: "arena_quick_match", label: "Quick Match when possible", type: "switch" },
    { name: "arena_friendship_refill", label: "Refill flags with friendship points", icon: "friendship.png", type: "switch" },
    { name: "arena_skystone_refill", label: "Refill with skystones (30 for 5 flags)", icon: "skystone.png", type: "switch" },
    { note: "The game gives 10 quick matches a day, then the bot fights on auto. Friendship refills 5 flags once a day. Skystones are only used to reach the chosen fight count, never with 0." },
  ],
  Gear: [
    { type: "gear_pages" },
    { page: "Rules", heading: "What to chase" },
    { page: "Rules", name: "gear_chase", label: "Chase", type: "choice", choices: [["speed", "Speed"], ["score", "Gear score"], ["both", "Both"]] },
    { page: "Rules", name: "gear_min_speed_speed_set", label: "Target speed after reforge, Speed set", type: "number", min: 0, max: 60, step: 1 },
    { page: "Rules", name: "gear_min_speed_other_sets", label: "Target speed after reforge, other sets", type: "number", min: 0, max: 60, step: 1 },
    { page: "Rules", name: "gear_efficiency", label: "Efficiency to keep a piece and to go on enhancing it", type: "efficiency" },
    { page: "Rules", heading: "What happens to a piece" },
    { page: "Rules", name: "gear_max_level", label: "Enhance up to", type: "choice", choices: [[12, "+12"], [15, "+15"]] },
    { page: "Rules", name: "gear_new_bad_action", label: "+0 piece to discard", type: "choice", choices: [["extract", "Extract"], ["sell", "Sell"]] },
    { page: "Rules", name: "gear_sell_failed", label: "Sell pieces that failed after enhancing", type: "switch" },
    { page: "Rules", name: "gear_lock_finished", label: "Lock finished pieces", type: "switch" },
    { page: "Rules", name: "gear_flat_main_needs_speed", label: "Flat main stat: kept only with speed", type: "switch" },
    { page: "Rules", name: "gear_stone", label: "Count on a modification stone", type: "choice", choices: [["always", "Always"], ["great", "Great pieces"], ["never", "Never"]] },
    { page: "Rules", name: "gear_stone_min_efficiency", label: "Great weapon, helmet, armor: efficiency with the stone at least (%)", type: "number", min: 0, max: 100, step: 1 },
    { page: "Rules", name: "gear_stone_min_efficiency_right", label: "Great necklace, ring, boots: at least (%)", type: "number", min: 0, max: 100, step: 1 },
    { page: "Rules", note: "Target speed: the speed after reforge a piece must still be able to reach at +15, or speed alone stops being a reason to enhance it. A red piece may try one point lower. Efficiency: a piece's potential (its stats weighed for a role, plus the rolls left) in % of a perfect piece of the same slot and main stat for that role. A piece is enhanced while one role can reach the efficiency asked, and a finished piece is kept from it (or at the target speed). The set moves it: −5 on the best sets, +10 or +20 on weak ones. Each role can ask its own, in Roles." },
    { page: "Roles", heading: "Main roles" },
    { page: "Roles", type: "roles" },
    { page: "Roles", note: "A piece is judged for every role ticked that its set and main stat allow, and kept if one of them reaches its efficiency. Empty: the role follows the general efficiency, in Rules. The score is what that efficiency means on a slot that can have every stat, against the role's perfect piece. A role is only tried on a slot that can have at least 3 of its stats. The % is a helmet's: a slot where a good piece for the role comes more easily asks up to 10 more (a weapon, a bulky armor), a rare one up to 10 less (a necklace, ring or boots with a fitting main stat). Untick the niche roles your heroes don't use: their weaker perfect piece keeps more pieces." },
    { page: "Run", heading: "Every gear mode" },
    { page: "Run", name: "gear_only_new", label: "Only +0 pieces (the ones it enhances stay in)", type: "switch" },
    { page: "Run", name: "gear_max_pieces", label: "Most pieces changed (0 = no limit)", type: "number", min: 0, max: 5000, step: 1 },
    { page: "Run", name: "gear_max_gold_millions", label: "Most gold spent, millions (0 = no limit)", icon: "gold.png", type: "number", min: 0, max: 1000, step: 0.5 },
    { page: "Run", name: "gear_screenshots", label: "Save a screenshot of every decision", type: "switch" },
    { page: "Run", type: "action", label: "Open the gear screenshots", icon: "i-folder", call: "open_gear_folder" },
    { page: "Run", note: "Roll enhances the pieces worth it 16 at a time in Bulk Enhancement, one tier at a time, and judges them again after each tier. Dry run and Sort review are in each gear mode, on Home. Screenshots and decisions.csv go to logs/gear, one folder a day." },
  ],
  Advanced: [
    { heading: "Game client" },
    { type: "client" },
    { heading: "Input" },
    { name: "pre_action_delay_min_ms", label: "Min. delay before an action (ms)", type: "number", min: 0, max: 5000, step: 50 },
    { name: "pre_action_delay_max_ms", label: "Max. delay before an action (ms)", type: "number", min: 0, max: 5000, step: 50 },
    { name: "skip_hold_seconds", label: "Max. hold to skip a video (s)", type: "number", min: 0.5, max: 10, step: 0.5 },
    { name: "click_marker", label: "Show where the bot clicks", type: "switch" },
    { type: "reset_stats" },
  ],
};

// Which modes erase the account, as the roster says it.
const ERASES = { reroll_summons: true, reroll_abyss: true };

let api = null;
let options = null;
let last = null;
let logSeen = 0;
let logLines = [];
let currentTab = "Home";
let heroFilter = "";
// Every input of a setting, by its name: one setting can show in several places (dry run, in each gear mode).
const inputs = {};
let uid = 0;
// The reset button of each settings page.
const resetButtons = [];

function register(key, node) {
  (inputs[key] = inputs[key] || []).push(node);
}

const $ = (id) => document.getElementById(id);

function el(tag, attributes = {}, children = []) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) {
    if (key === "text") node.textContent = value;
    else if (key === "on") for (const [event, handler] of Object.entries(value)) node.addEventListener(event, handler);
    else if (value !== false && value !== null && value !== undefined) node.setAttribute(key, value === true ? "" : value);
  }
  for (const child of [].concat(children)) if (child) node.append(child);
  return node;
}

function icon(id) {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
  use.setAttribute("href", `#${id}`);
  svg.append(use);
  return svg;
}

// Change a node only when its text differs, so the page does not repaint at every poll.
function setText(node, text) {
  if (node.textContent !== text) node.textContent = text;
}

function show(node, visible) {
  node.hidden = !visible;
}

function call(name, ...args) {
  return api[name](...args).catch((error) => console.error(name, error));
}

// Building the page once

// The rail: the modes, grouped, then the pages. Home has no rail item of its own: a mode opens it, the brand goes
// back to all the modes.
function buildTabs() {
  const bar = $("tabs");
  for (const name of options.tabs) {
    if (name === "Home") continue;
    bar.append(el("button", { class: "rail-item", role: "tab", id: `tab-${name}`, "aria-controls": `page-${name}`, on: { click: () => selectTab(name) } }, [
      options.tab_icons[name] ? glyph(options.tab_icons[name]) : null, el("span", { class: "rail-label", text: name }),
    ]));
  }
  selectTab("Home");
}

function selectTab(name) {
  currentTab = name;
  for (const tab of options.tabs) {
    const selected = tab === name;
    const button = $(`tab-${tab}`);
    if (button) {
      button.setAttribute("aria-selected", selected);
      button.tabIndex = selected ? 0 : -1;
    }
    show($(`page-${tab}`), selected);
  }
  markRail();
}

// Modes that share a menu: one item of the rail, named and drawn as below, picked between on their page.
const MENUS = { gear_work: { label: "Craft, enhance, sell", glyph: "workshop.png", picks: { gear_craft: "Craft", gear_roll: "Enhance", gear_sort: "Sell" } } };

// The keys a rail item stands for: its mode, or every mode of its menu.
function itemModes(item) {
  return item.dataset.menu ? options.goals.filter((goal) => goal.menu === item.dataset.menu).map((goal) => goal.key) : [item.dataset.mode];
}

function markRail() {
  for (const item of document.querySelectorAll("#rail-modes .rail-item")) {
    const modes = itemModes(item);
    const current = currentTab === "Home" && last !== null && modes.includes(last.goal);
    if (item.getAttribute("aria-current") !== String(current)) item.setAttribute("aria-current", current);
    const running = last !== null && last.running && modes.includes(last.run_key);
    item.classList.toggle("running", running);
  }
}

function openMode(key) {
  selectTab("Home");
  call("pick_mode", key).then(refresh);
}

function buildModes() {
  let category = "";
  let roster = null;
  for (const goal of options.goals) {
    if (goal.category !== category) {
      category = goal.category;
      $("rail-modes").append(el("h2", { class: "rail-group", text: category }));
      // On Home, each category has its heading and its own grid of modes.
      roster = el("div", { class: "roster" });
      $("main-modes").append(el("h2", { class: "section", text: category }), roster);
    }
    const menu = MENUS[goal.menu];
    // A menu's first mode stands for the whole menu in the rail; the others are picked on its page.
    if (!menu || options.goals.find((other) => other.menu === goal.menu) === goal) {
      $("rail-modes").append(el("button", {
        class: "rail-item", "data-mode": goal.key, "data-menu": goal.menu || null, disabled: !goal.available, on: { click: () => openMode(goal.key) },
      }, [
        glyph(menu ? menu.glyph : goal.icon), el("span", { class: "rail-label", text: menu ? menu.label : goal.label }), el("span", { class: "rail-live", text: "running" }),
      ]));
    }
    // The same modes in the main area when none is open, each with what it does.
    const tag = ERASES[goal.key] ? el("span", { class: "tag erases", text: "erases accounts" }) : null;
    roster.append(el("button", { class: "mode-row", "data-mode": goal.key, disabled: !goal.available, on: { click: () => openMode(goal.key) } }, [
      goal.icon ? glyph(goal.icon, "mode-glyph") : null, el("span", { class: "mode-name", text: goal.label }), tag, el("span", { class: "mode-what", text: goal.description }),
    ]));
  }
  // The picker of a menu's modes, on their page.
  for (const [key, menu] of Object.entries(MENUS)) {
    buildSegmented($("menu-picker"), Object.values(menu.picks), (label) => {
      const [mode] = Object.entries(menu.picks).find(([, name]) => name === label);
      openMode(mode);
    });
    $("menu-picker").dataset.menu = key;
  }
  $("brand").addEventListener("click", () => { selectTab("Home"); call("back").then(refresh); });
  $("back-to-run").addEventListener("click", () => call("show_run").then(refresh));
  $("start").addEventListener("click", () => call("start", false).then(refresh));
  $("stop").addEventListener("click", () => call("stop").then(refresh));
  $("erase").addEventListener("click", () => call("erase").then(refresh));
  $("show-pulls").addEventListener("click", () => call("open_image"));
  $("restart-admin").addEventListener("click", () => call("restart_as_admin").then(refresh));
  $("account-general-heroes").addEventListener("change", () => send("gear_general_heroes", readNumber($("account-general-heroes"))));
  $("account-openers").addEventListener("change", () => send("gear_openers", readNumber($("account-openers"))));
  $("account-clear").addEventListener("click", () => call("clear_account_targets").then(refresh));
  $("account-apply").addEventListener("click", async () => {
    const problem = await call("apply_account_scores");
    setText($("account-message"), problem || "Applied: the gear modes and the Gear overlay now ask each slot its target, per role, and the speed set its speed per slot.");
    $("account-message").classList.toggle("error", Boolean(problem));
    show($("account-message"), true);
    refresh();
  });
  $("export").addEventListener("click", () => call("export_journal").then(refresh));
}

function buildSegmented(container, names, onPick) {
  for (const name of names) {
    container.append(el("button", { role: "tab", "data-name": name, text: name, on: { click: () => onPick(name) } }));
  }
}

function markSegmented(container, chosen) {
  for (const button of container.children) {
    const selected = button.dataset.name === String(chosen);
    if (button.getAttribute("aria-selected") !== String(selected)) button.setAttribute("aria-selected", selected);
  }
}

function buildClients() {
  for (const client of options.clients) {
    $("client-choices").append(el("button", { class: "button primary big", text: client.label, on: { click: () => call("choose_client", client.key).then(refresh) } }));
  }
}

function buildRun() {
  const form = $("run-settings");
  for (const [goal, items] of Object.entries(RUN)) {
    const panel = el("div", { class: "panel", "data-goal": goal, hidden: true });
    for (const item of items) panel.append(buildItem(item));
    form.append(panel);
  }
  $("summary-link").addEventListener("click", async () => {
    await call("show_settings", last.summary_tab);
    selectTab("Settings");
    refresh();
  });
}

function buildSettings() {
  buildSegmented($("stats-tabs"), options.stats_tabs, (name) => call("show_stats", name).then(refresh));
  buildSegmented($("help-tabs"), options.help.map(([name]) => name), showHelp);
  showHelp(options.help[0][0]);
  buildSegmented($("settings-tabs"), options.settings_tabs, (name) => call("show_settings", name).then(refresh));
  const form = $("settings-form");
  for (const [tab, items] of Object.entries(SETTINGS)) {
    const panel = el("div", { class: "panel", "data-tab": tab, hidden: true });
    for (const item of items) panel.append(buildItem(item));
    // Each page resets on its own: Gear's three pages one by one, every other tab whole.
    const pages = [...new Set(items.map((item) => item.page).filter(Boolean))];
    if (pages.length) for (const page of pages) panel.append(resetPage(`${tab} › ${page}`, items.filter((item) => item.page === page), page));
    else panel.append(resetPage(tab, items));
    form.append(panel);
  }
}

// The settings a page's items hold, by their names in core/settings.py.
function settingNames(items) {
  const special = { top_rule: ["top_rules"], heroes: ["stop_heroes"], roles: ["gear_roles", "gear_role_efficiency"] };
  return [...new Set(items.flatMap((item) => (item.name ? [item.name] : special[item.type] || [])))];
}

// A button that puts one page's settings back to their defaults, on a second click.
function resetPage(name, items, page) {
  const button = el("button", { class: "button quiet small", type: "button", text: `Reset ${name} to defaults`, on: { click: () => call("reset_settings", name, settingNames(items)).then(refresh) } });
  const note = el("p", { class: "note" });
  resetButtons.push({ name, button, note });
  const node = el("div", { class: "danger-zone" }, [button, note]);
  if (page) node.dataset.page = page;
  return node;
}

function buildItem(item) {
  const wrap = (node) => {
    if (item.page) node.dataset.page = item.page;
    return node;
  };
  if (item.heading) return wrap(el("h3", { text: item.heading }));
  if (item.note) return wrap(el("p", { class: "note", text: item.note }));
  switch (item.type) {
    case "switch": return wrap(switchRow(item));
    case "number": return wrap(numberRow(item));
    case "select": return wrap(selectRow(item));
    case "choice": return wrap(choiceRow(item));
    case "top_rule": return topRuleRow(item.index);
    case "efficiency": return wrap(efficiencyRow(item));
    case "roles": return wrap(rolesTable());
    case "heroes": return heroPicker();
    case "gear_pages": {
      const bar = el("div", { class: "segmented", id: "gear-pages", role: "tablist", "aria-label": "Gear settings" });
      buildSegmented(bar, options.gear_pages, (name) => call("show_gear_page", name).then(refresh));
      return bar;
    }
    case "shop_cost": return shopCost();
    case "penguin_cost": return penguinCost();
    case "action": return wrap(el("div", { class: "row" }, el("button", { class: "button quiet small", type: "button", on: { click: () => call(item.call) } }, [icon(item.icon), item.label])));
    case "client": {
      const bar = el("div", { class: "segmented wide", id: "client-pick", role: "group", "aria-label": "Game client" });
      for (const client of options.clients) {
        bar.append(el("button", { type: "button", "data-name": client.key, text: client.label, on: { click: () => call("choose_client", client.key).then(refresh) } }));
      }
      return bar;
    }
    case "reset_stats": {
      const button = el("button", { class: "button", id: "reset-stats", type: "button", text: "Reset the stats", on: { click: () => call("reset_stats").then(refresh) } });
      return el("div", { class: "danger-zone" }, [button, el("p", { class: "note", id: "reset-stats-note" })]);
    }
  }
  return document.createTextNode("");
}

function fieldError(name) {
  const node = el("p", { class: "field-error", hidden: true });
  register(`error:${name}`, node);
  return node;
}

function switchRow(item) {
  const id = `set-${item.name}-${++uid}`;
  const input = el("input", { type: "checkbox", role: "switch", id, on: { change: () => send(item.name, input.checked) } });
  register(item.name, input);
  const label = el("label", { for: id }, [item.icon ? gameIcon(item.icon) : null, item.label]);
  return el("div", { class: "row" }, [label, el("span", { class: "switch" }, input)]);
}

function numberRow(item) {
  const id = `set-${item.name}-${++uid}`;
  const input = el("input", { type: "number", id, min: item.min, max: item.max, step: item.step, inputmode: "decimal", on: { change: () => send(item.name, readNumber(input)) } });
  register(item.name, input);
  const label = el("label", { for: id }, [item.icon ? gameIcon(item.icon) : null, item.label]);
  return el("div", {}, [el("div", { class: "row" }, [label, input]), fieldError(item.name)]);
}

// One of the game's white menu glyphs, in ui/glyphs, beside a mode or a page.
function glyph(file, kind = "glyph") {
  return el("img", { class: kind, src: `glyphs/${file}`, alt: "" });
}

// One of the game's own icons, in ui/icons, beside a number or a label.
function gameIcon(file) {
  return el("img", { class: "game-icon", src: `icons/${file}`, alt: "" });
}

// A caption, with the game's icon of what it counts when it has one.
function captioned(caption) {
  const file = options.caption_icons[caption];
  return file ? [gameIcon(file), caption] : [caption];
}

// What the refreshes cost, kept in step with the number as it is typed: 300 skystones for 100.
function shopCost() {
  const said = el("span", { class: "cost-value" });
  const line = el("p", { class: "cost", id: "shop-cost" }, [el("span", { text: "Costs" }), said, gameIcon("skystone.png"), el("span", { text: "skystones" })]);
  line.tell = (value) => {
    const text = String(value).trim();
    const count = Number(text);
    setText(said, text !== "" && Number.isInteger(count) && count >= 0 ? (count * options.shop_refresh_price).toLocaleString("en-US") : "—");
  };
  return line;
}

// What the penguin limit means, kept in step with the number as it is typed: purchases, penguins and stigma.
const PENGUIN_PRICE = 5100;
function penguinCost() {
  const purchasesSaid = el("span", { class: "cost-value" });
  const penguinsSaid = el("span", { class: "cost-value" });
  const stigmaSaid = el("span", { class: "cost-value" });
  const line = el("p", { class: "cost", id: "penguin-cost" }, [
    purchasesSaid, el("span", { text: "·" }), penguinsSaid, gameIcon("penguin.png"), el("span", { text: "·" }), stigmaSaid, gameIcon("stigma.png"),
  ]);
  line.tell = (kind, value) => {
    const amount = Number(String(value).trim());
    const valid = Number.isInteger(amount) && amount >= 1;
    const purchases = !valid ? 0 : kind === "stigma" ? Math.floor(amount / PENGUIN_PRICE) : amount;
    setText(purchasesSaid, valid ? `${purchases.toLocaleString("en-US")} purchase${purchases === 1 ? "" : "s"}` : "—");
    setText(penguinsSaid, valid ? (purchases * 50).toLocaleString("en-US") : "—");
    setText(stigmaSaid, valid ? (purchases * PENGUIN_PRICE).toLocaleString("en-US") : "—");
  };
  return line;
}

function selectRow(item) {
  const id = `set-${item.name}-${++uid}`;
  const choices = options[item.choices].map((choice) => (typeof choice === "string" ? { key: choice, label: choice } : choice));
  const input = el("select", { id, on: { change: () => send(item.name, input.value) } }, choices.map((choice) => el("option", { value: choice.key, text: choice.label })));
  register(item.name, input);
  return el("div", { class: "row" }, [el("label", { for: id, text: item.label }), input]);
}

function choiceRow(item) {
  const bar = el("div", { class: "segmented", role: "radiogroup", "aria-label": item.label });
  for (const [value, label] of item.choices) {
    bar.append(el("button", { type: "button", role: "radio", "data-name": String(value), text: label, on: { click: () => send(item.name, value) } }));
  }
  register(item.name, bar);
  // Three choices do not fit beside a label in this narrow window: they go under it.
  return el("div", { class: item.choices.length > 2 ? "row stacked" : "row" }, [el("span", { class: "label", text: item.label }), bar]);
}

// The efficiency asked: a slider, with what it means in gear score on a perfect piece of 72 (four useful stats).
const FULL_PERFECT = 72;

function efficiencyRow(item) {
  const id = `set-${item.name}-${++uid}`;
  const input = el("input", { type: "range", id, min: 30, max: 100, step: 1, class: "slider" });
  const said = el("span", { class: "slider-value", id: "efficiency-said" });
  const tell = () => setText(said, `${input.value}% · gear score ${Math.round((input.value * FULL_PERFECT) / 100)} of ${FULL_PERFECT}`);
  input.addEventListener("input", tell);
  input.addEventListener("change", () => send(item.name, Number(input.value)));
  register(item.name, input);
  return el("div", { class: "row stacked" }, [el("div", { class: "slider-head" }, [el("label", { for: id, text: item.label }), said]), input]);
}

// Each role: judged or not, and its own efficiency, empty to follow the general one.
const roleInputs = {};

function rolesTable() {
  const rows = options.roles.map((role) => {
    const on = el("input", { type: "checkbox", "aria-label": `Judge pieces for ${role.label}` });
    const own = el("input", { type: "number", class: "narrow", min: 0, max: 100, step: 1, inputmode: "decimal", "aria-label": `${role.label}: own efficiency %` });
    const score = el("span", { class: "role-score" });
    const reset = el("button", { type: "button", class: "button quiet small", text: "Auto", title: "Follow the general efficiency" });
    const sendRole = (value) => call("set_role", role.key, on.checked, value).then(refresh);
    on.addEventListener("change", () => sendRole(readNumber(own)));
    own.addEventListener("change", () => sendRole(readNumber(own)));
    reset.addEventListener("click", () => { own.value = ""; sendRole(""); });
    roleInputs[role.key] = { on, own, score, reset, perfect: role.perfect, extra: role.extra };
    const name = el("label", { class: role.main ? "role-name" : "role-name niche" }, [on, el("span", { text: role.label })]);
    return { main: role.main, node: el("div", {}, [el("div", { class: "row role-row" }, [name, score, own, el("span", { text: "%" }), reset]), fieldError(`role.${role.key}`)]) };
  });
  // The main roles, then the niche ones under their own heading.
  return el("div", { class: "roles-table" }, [
    ...rows.filter((row) => row.main).map((row) => row.node),
    el("h3", { text: "Niche roles" }),
    el("p", { class: "note", text: "Fewer heroes wear their pieces: on Auto they ask 15 points more than the main roles." }),
    ...rows.filter((row) => !row.main).map((row) => row.node),
  ]);
}

function renderRoles(settings, invalid) {
  for (const [key, row] of Object.entries(roleInputs)) {
    row.on.checked = settings.gear_roles.includes(key);
    const own = settings.gear_role_efficiency[key];
    const auto = Math.min(100, settings.gear_efficiency + row.extra);
    const value = own ?? auto;
    if (document.activeElement !== row.own && !(`role.${key}` in invalid)) row.own.value = own ?? "";
    row.own.placeholder = String(auto);
    row.reset.disabled = own === undefined;
    // Targets per slot applied from the account evaluation win over the role's own %.
    const measured = settings.gear_slot_targets[key];
    setText(row.score, measured ? "account targets per slot" : `≈ ${Math.round((value * row.perfect) / 100)} of ${row.perfect}`);
    setError(`role.${key}`, invalid[`role.${key}`]);
  }
}

function topRuleRow(index) {
  const count = el("input", { type: "number", class: "narrow", min: 0, max: 5, step: 1, "aria-label": "How many" });
  const rank = el("input", { type: "number", class: "narrow", min: 1, max: 69, step: 1, "aria-label": "Top of the ranking" });
  const sendRule = () => call("set_top_rule", index, readNumber(count), readNumber(rank)).then(refresh);
  count.addEventListener("change", sendRule);
  rank.addEventListener("change", sendRule);
  inputs[`top_rules.${index}`] = [count, rank];
  const line = el("div", { class: "row" }, el("div", { class: "rule-line" }, [el("span", { text: "Or at least" }), count, el("span", { text: "ML5 in the top" }), rank]));
  return el("div", {}, [line, fieldError(`top_rules.${index}`)]);
}

function heroPicker() {
  const search = el("input", { type: "search", placeholder: "Search a hero", "aria-label": "Search a hero", on: { input: () => { heroFilter = search.value; drawHeroes(); } } });
  const list = el("div", { class: "hero-list", id: "hero-list", role: "group", "aria-label": "5★ Moonlight heroes that keep the account" });
  return el("div", {}, [
    el("div", { class: "row" }, el("span", { class: "label", text: "Or one of these 5★ Moonlight" })),
    el("div", { class: "hero-search" }, [icon("i-search"), search]),
    list,
    el("p", { class: "chosen", id: "hero-chosen" }),
  ]);
}

function drawHeroes() {
  const list = $("hero-list");
  const chosen = new Set(last ? last.settings.stop_heroes : []);
  const wanted = heroFilter.trim().toLowerCase();
  const shown = options.heroes.filter((name) => name.toLowerCase().includes(wanted));
  list.replaceChildren(...shown.map((name) => {
    const box = el("input", { type: "checkbox", on: { change: () => call("set_hero", name, box.checked).then(refresh) } });
    box.checked = chosen.has(name);
    return el("label", {}, [box, el("span", { text: name })]);
  }));
  if (!shown.length) list.append(el("p", { class: "empty", text: "No hero matches this search." }));
}

function readNumber(input) {
  const value = input.value.trim();
  if (value === "") return value;
  const number = Number(value);
  return Number.isNaN(number) ? value : number;
}

function send(name, value) {
  call("set_setting", name, value).then(refresh);
}

// Drawing the view at each poll

function render(view) {
  const previous = last;
  last = view;
  if (document.title !== view.title) document.title = view.title;
  show($("client-dialog"), view.needs_client);
  renderBand(view.band);
  renderHome(view);
  renderStats(view);
  renderSettings(view, previous);
  setText($("export-note"), view.export_note);
  $("export-note").classList.toggle("error", view.export_failed);
}

// The word in the status card's header, by state.
const CERTS = { running: "LIVE", idle: "READY", kept: "KEPT", attention: "CHECK" };

function renderBand(band) {
  for (const node of [$("band"), $("status-slab")]) {
    if (node.dataset.kind !== band.kind) node.dataset.kind = band.kind;
  }
  // Off Home, the header names the page; the state and the run's buttons stay.
  setText($("headline"), currentTab === "Home" ? band.title : currentTab);
  setText($("kind-label"), band.kind_label);
  setText($("timer"), band.timer);
  setText($("slab-cert"), CERTS[band.kind] || "");
  setText($("slab-title"), band.title);
  setText($("detail"), band.detail);
  setText($("doing"), band.doing);
  show($("doing"), Boolean(band.doing));
  show($("route"), Boolean(band.route));
  if (band.route) {
    setText($("route-text"), band.route.text);
    $("route-fill").style.width = `${Math.round(band.route.fraction * 100)}%`;
  }
  show($("show-pulls"), band.image);
}

function renderHome(view) {
  const running = view.running;
  // During a run the user may browse the menu and other modes; the run's own view shows only on its mode.
  const live = view.on_run;
  const browsing = running && !live;
  $("page-Home").classList.toggle("running", live);
  show($("back-to-run"), browsing);
  setText($("back-to-run-text"), `Back to the run: ${view.run_name}`);
  show($("menu"), !view.goal);
  show($("mode"), Boolean(view.goal));
  markRail();
  setText($("client-name"), view.client ? (view.client === "steam" ? "Steam" : "STOVE") : "");
  show($("description"), !live);
  setText($("description"), view.description);
  // A mode of a menu shows the picker of its siblings above what it does.
  const goal = options.goals.find((one) => one.key === view.goal);
  const menu = goal && MENUS[goal.menu];
  show($("menu-picker"), Boolean(menu));
  if (menu) markSegmented($("menu-picker"), menu.picks[goal.key]);
  const runPanel = live || !RUN[view.goal] ? null : view.goal;
  show($("run"), Boolean(runPanel));
  $("run-settings").disabled = running;
  for (const panel of $("run-settings").children) show(panel, panel.dataset.goal === runPanel);
  show($("summary-line"), !live && Boolean(view.summary));
  setText($("summary"), view.summary);
  show($("dry-run-badge"), view.dry_run);
  show($("session"), !running && Boolean(view.session));
  setText($("session"), view.session);

  // The mode's first number goes big in the card's header, the others under it.
  const numbers = view.goal && !browsing ? view.numbers : [];
  const [grade, ...others] = numbers;
  // At rest the header says it all: the card shows only a run, a kept account, something to look at, or numbers.
  show($("status-slab"), view.band.kind !== "idle" || numbers.length > 0);
  show($("slab-grade-box"), Boolean(grade));
  setText($("slab-grade"), grade ? grade[0] : "");
  const gradeCaption = $("slab-grade-caption");
  const caption = grade ? grade[1] : "";
  if (gradeCaption.dataset.text !== caption) {
    gradeCaption.dataset.text = caption;
    gradeCaption.replaceChildren(...(caption ? captioned(caption) : []));
  }
  const figures = $("figures");
  show(figures, others.length > 0);
  const text = others.map(([value, caption]) => `${value}|${caption}`).join("~");
  if (figures.dataset.text !== text) {
    figures.dataset.text = text;
    figures.replaceChildren(...others.map(([value, caption]) => el("div", { class: "figure" }, [el("dd", { text: value }), el("dt", {}, captioned(caption))])));
  }

  renderAdvice(live ? view.advice : null);
  renderAccount(live ? null : view.account, running);
  show($("watch"), live && !view.advice);
  const events = view.activity.map(([when, what]) => `${when}|${what}`).join("~");
  const list = $("activity");
  if (list.dataset.text !== events) {
    list.dataset.text = events;
    list.replaceChildren(...view.activity.map(([when, what]) => el("li", {}, [el("time", { text: when }), el("span", { text: what })])));
  }
  show($("activity-empty"), live && !view.activity.length);

  show($("start"), !running);
  $("start").disabled = !view.can_start;
  show($("stop"), running);
  show($("erase"), !running && view.can_erase);
  setText($("erase"), view.erase_label);
  $("erase").classList.toggle("armed", view.erase_armed);
  show($("admin-note"), !running && Boolean(view.admin_note));
  setText($("admin-note"), view.admin_note);
  show($("restart-admin"), !running && Boolean(view.admin_note));
  show($("kept-note"), Boolean(view.kept_note));
  setText($("kept-note"), view.kept_note);
  setText($("place"), view.place);
}

// The gear advisor: the cards on screen in a grid like the game's, then one piece in detail. A click on a card shows
// it; a second click goes back to the piece selected in the game.
function verdict(piece) {
  return el("span", { class: "verdict", "data-action": piece.action, text: piece.label || "Unread" });
}

function statIcon(file) {
  return el("img", { class: "stat-icon", src: `icons/${file}`, alt: "" });
}

function tile(card) {
  // A small card in the verdict's colour, its efficiency in the corner; the verdict and role in its tooltip.
  const known = card.efficiency !== null;
  const picture = el("span", { class: "tile-pic" }, [
    card.thumb ? el("img", { src: card.thumb, alt: "" }) : null,
    card.level ? el("span", { class: "tile-level", text: `+${card.level}` }) : null,
    el("span", { class: "tile-eff", text: known ? `${card.efficiency}%` : card.word }),
  ]);
  const node = el("button", {
    class: "tile", "data-action": card.action, "aria-pressed": String(card.selected),
    title: known ? `${card.where} · ${card.word} · ${card.efficiency}% as ${card.role}` : `${card.where} · ${card.word}`,
    on: { click: () => call("pick_piece", card.where).then(refresh) },
  }, picture);
  if (card.row !== null) {
    node.style.gridRow = String(card.row + 1);
    node.style.gridColumn = String(card.column + 1);
  }
  return node;
}

function statRow(stat) {
  const name = el("span", { class: "stat-name" }, [statIcon(stat.icon), el("span", { text: stat.name }), stat.state === "dead" ? el("em", { text: "useless" }) : null]);
  const value = el("span", { class: "stat-value", text: stat.rolls ? `${stat.value} ${stat.rolls}` : stat.value });
  const rows = [el("li", { class: `stat ${stat.state}` }, [name, value])];
  if (stat.state === "swapped") {
    rows.push(el("li", { class: "stat stone" }, [el("span", { class: "stat-name", text: `↳ stone: ${stat.into}` }), el("span", { class: "stat-value", text: stat.into_value })]));
  }
  return rows;
}

// A sentence with its percentages in bold, the efficiency numbers the details are about.
function emphasized(text) {
  return text.split(/(\d+(?:\.\d+)?%)/).map((part, index) => (index % 2 ? el("strong", { class: "num", text: part }) : document.createTextNode(part)));
}

function pieceDetail(piece) {
  const head = el("div", { class: "piece-head" }, [verdict(piece), el("span", { class: "piece-title", text: piece.title || piece.where })]);
  const parts = [head];
  const flags = [piece.where, ...piece.flags].join(" · ");
  if (!piece.title) {
    parts.push(el("p", { class: "piece-why", text: piece.reason }));
    return parts;
  }
  parts.push(el("p", { class: "piece-summary", text: piece.summary }));
  parts.push(el("p", { class: "piece-sub" }, emphasized(`${flags} · judged as ${piece.role} · ${piece.efficiency}% efficient · gear score ${piece.score}`)));
  const main = el("li", { class: "stat main" }, [
    el("span", { class: "stat-name" }, [statIcon(piece.main_icon), el("span", { text: piece.main })]),
    el("span", { class: piece.main_good ? "stat-value good-main" : "stat-value", text: piece.main_good ? "main · counts as good" : "main" }),
  ]);
  parts.push(el("ul", { class: "stats" }, [main, ...piece.stats.flatMap(statRow)]));
  if (piece.roles.length) {
    parts.push(el("ul", { class: "roles" }, piece.roles.map((line) => el("li", { class: line.passes ? "role pass" : "role fail" }, [
      el("span", { class: "role-mark" }, icon(line.passes ? "i-check" : "i-cross")),
      el("span", {}, [el("strong", { text: line.role }), document.createTextNode(" · "), ...emphasized(line.text)]),
    ]))));
  }
  parts.push(el("p", { class: "piece-why" }, emphasized(piece.reason)));
  return parts;
}

function renderAdvice(advice) {
  show($("advice"), Boolean(advice));
  if (!advice) return;
  const text = JSON.stringify(advice);
  if ($("advice").dataset.text === text) return;
  $("advice").dataset.text = text;
  setText($("advice-note"), advice.note);
  show($("advice-note"), Boolean(advice.note));
  // Fixed-width columns, as many as the game's grid: no window width squeezes one.
  const columns = Math.max(4, ...advice.grid.map((card) => (card.column ?? 0) + 1));
  $("advice-grid").style.setProperty("--columns", columns);
  $("advice-grid").replaceChildren(...advice.grid.map(tile));
  show($("advice-grid"), advice.grid.length > 0);
  setText($("advice-hint"), advice.hint);
  show($("advice-hint"), Boolean(advice.hint));
  $("advice-detail").replaceChildren(...(advice.detail ? pieceDetail(advice.detail) : []));
}

// The account evaluation: charts of what the gear is worth now, then the targets per slot from the heroes geared.
const SVG_NS = "http://www.w3.org/2000/svg";
const SLOT_SHORT = { weapon: "Wpn", helmet: "Helm", armor: "Arm", necklace: "Neck", ring: "Ring", boots: "Boots" };

function svg(tag, attributes = {}, text = "") {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text) node.textContent = text;
  return node;
}

// The level: the median gear score on a scale from 50 to 76, each level's name in the middle of its stretch.
function drawScale(node, score) {
  const low = 50, high = 76, left = 8, right = 352;
  const x = (value) => left + ((Math.min(high, Math.max(low, value)) - low) / (high - low)) * (right - left);
  const parts = [svg("line", { x1: left, x2: right, y1: 12, y2: 12, class: "scale-track" })];
  score.levels.forEach(([lowest, label], index) => {
    const start = x(Math.max(low, lowest));
    const end = index + 1 < score.levels.length ? x(score.levels[index + 1][0]) : right;
    if (lowest > low) parts.push(svg("line", { x1: start, x2: start, y1: 6, y2: 18, class: "scale-tick" }));
    parts.push(svg("text", { x: (start + end) / 2, y: 34, class: "scale-label", "text-anchor": "middle" }, label));
  });
  if (score.best != null) parts.push(svg("line", { x1: x(score.best), x2: x(score.best), y1: 5, y2: 19, class: "scale-best" }));
  if (score.median != null) parts.push(svg("circle", { cx: x(score.median), cy: 12, r: 5, class: "scale-median" }));
  node.replaceChildren(...parts);
}

// A role's pieces: one tick each on an axis shared by every role, the median taller and white.
const STRIP_LOW = 20, STRIP_HIGH = 115, STRIP_WIDTH = 200;
const stripX = (value) => ((Math.min(STRIP_HIGH, Math.max(STRIP_LOW, value)) - STRIP_LOW) / (STRIP_HIGH - STRIP_LOW)) * STRIP_WIDTH;

// The strips' scale under them, in the same columns.
function stripAxis() {
  const scale = el("div", { class: "strip-scale" });
  for (const value of [40, 60, 80, 100]) {
    scale.append(el("span", { text: `${value}%`, style: `left: ${(stripX(value) / STRIP_WIDTH) * 100}%` }));
  }
  return el("div", { class: "strip-row strip-axis", "aria-hidden": "true" }, [el("span"), scale, el("span")]);
}

function strip(values, median) {
  const width = STRIP_WIDTH, x = stripX;
  const node = svg("svg", { viewBox: `0 0 ${width} 16`, class: "strip", preserveAspectRatio: "none" });
  node.append(svg("line", { x1: 0, x2: width, y1: 8, y2: 8, class: "strip-track" }));
  for (const value of values) node.append(svg("line", { x1: x(value), x2: x(value), y1: 3, y2: 13, class: "strip-tick" }));
  if (median != null) node.append(svg("line", { x1: x(median), x2: x(median), y1: 0, y2: 16, class: "strip-median" }));
  return node;
}

// A target: one hue, stronger the higher it asks; dim when it is the user's own target.
function heatCell(cell, role) {
  // A role's cell says how many of its seats a piece fills; the General row counts the slot's pieces.
  const seats = cell.heroes != null && role !== "General" ? ` · ${cell.count}/${cell.heroes} seats` : ` (${cell.count} piece${cell.count === 1 ? "" : "s"})`;
  const node = el("div", {
    class: cell.own ? "heat own" : "heat", role: "cell",
    title: `${role} · ${cell.slot}: ${cell.target}%${seats}${cell.own ? ", your own target" : ""}`,
  }, [
    el("span", { text: String(Math.round(cell.target)) }),
    ...(cell.heroes != null && role !== "General" ? [el("small", { class: "heat-seats", text: `${cell.count}/${cell.heroes}` })] : []),
  ]);
  if (!cell.own) {
    const strength = Math.min(90, Math.max(20, Math.round(20 + (Math.min(100, cell.target) - 60) * 1.75)));
    node.style.background = `color-mix(in oklab, var(--label) ${strength}%, var(--g2))`;
    if (strength >= 55) node.style.color = "#fff";
  }
  return node;
}

function renderAccount(account, running) {
  show($("account"), Boolean(account));
  if (!account) return;
  $("account-apply").disabled = running || !account.can_apply || account.applied;
  $("account-clear").disabled = running || !account.applied_any;
  setText($("account-apply"), account.applied ? "Targets applied" : "Apply these targets");
  for (const [id, value] of [["account-general-heroes", account.general_heroes], ["account-openers", account.openers]]) {
    if (document.activeElement !== $(id)) $(id).value = value;
  }
  const text = JSON.stringify(account);
  if ($("account").dataset.text === text) return;
  $("account").dataset.text = text;
  // Before any scan: one line saying how to get one, and nothing else.
  show($("account-empty"), account.empty);
  setText($("account-empty"), account.empty ? account.note : "");
  show($("account-body"), !account.empty);
  if (account.empty) return;
  setText($("account-level"), account.level || "");
  show($("account-scale"), Boolean(account.score && account.score.median != null));
  if (account.score) drawScale($("account-scale"), account.score);
  $("account-figures").replaceChildren(...account.figures.map(([value, caption]) => el("div", { class: "figure" }, [el("dd", { text: value }), el("dt", { text: caption })])));
  setText($("account-note"), account.note);
  $("account-strips").replaceChildren(...account.roles.map((role) => el("div", {
    class: role.niche ? "strip-row niche" : "strip-row", role: "listitem",
    title: `${role.name}: ${role.count} pieces, median ${role.median}, best ${role.best}, worst ${role.worst}`,
  }, [
    el("span", { class: "strip-name", text: role.short }),
    strip(role.values, role.median_value),
    el("span", { class: "strip-count", text: role.count ? role.median : "—" }),
  ])), stripAxis());
  const head = el("div", { class: "grid-row grid-head", role: "row" }, [
    el("span", { text: "" }), el("span", { text: "heroes" }), ...account.slots.map((slot) => el("span", { text: SLOT_SHORT[slot] || slot })),
  ]);
  const rows = account.roles.map((role) => {
    const heroes = el("input", { type: "number", class: "narrow grid-input", min: 0, max: 1000, step: 1, value: role.heroes, disabled: running, "aria-label": `${role.name}: heroes geared, 0 follows the General row` });
    heroes.addEventListener("change", () => call("set_role_heroes", role.role, readNumber(heroes) || 0).then(refresh));
    return el("div", { class: role.niche ? "grid-row niche" : "grid-row", role: "row" }, [
      el("span", { class: "grid-name", text: role.short, title: role.name }), heroes, ...role.cells.map((cell) => heatCell(cell, role.name)),
    ]);
  });
  const general = el("div", { class: "grid-row grid-general", role: "row" }, [
    el("span", { class: "grid-name", text: "General" }), el("span", { class: "grid-heroes", text: String(account.general_heroes) }),
    ...(account.general_cells || []).map((cell) => heatCell(cell, "General")),
  ]);
  $("account-grid").replaceChildren(head, ...rows, general);
  // Pieces no seat takes: every hero geared in their roles has a better one.
  setText($("account-unneeded-note"), account.unneeded_count
    ? `${account.unneeded_count} +15 piece${account.unneeded_count === 1 ? "" : "s"} no hero needs: each hero geared in their roles has a better one. The first to sell, weakest first. Worn and locked ones say so: the gear modes never touch them.`
    : "Every +15 piece has a hero's seat.");
  $("account-unneeded").replaceChildren(...(account.unneeded || []).map((row) => el("div", { class: "kept-row", role: "listitem" }, [
    el("span", { class: "kept-chip", text: `${row.slot}: ${row.count}` }),
    el("div", { class: "kept-text" }, row.pieces.map((piece) => el("small", {
      text: `${piece.kept ? `${piece.kept} · ` : ""}${piece.set} set · best as ${piece.role} ${Math.round(piece.value)}% · gear score ${Math.round(piece.score)}`,
    }))),
  ])));
  // Locked or worn pieces the rules of now would sell or extract: the gear modes never touch them, the player decides.
  setText($("account-kept-note"), account.kept_note || "");
  $("account-outdated").replaceChildren(...(account.outdated || []).map((row) => el("div", { class: "kept-row", role: "listitem" }, [
    el("span", { class: "kept-chip", text: `${row.kind}: ${row.action}` }),
    el("div", { class: "kept-text" }, [
      el("strong", { text: row.title }),
      el("small", { text: row.stats }),
      el("p", { text: row.better ? `${row.reason} ${row.better}.` : row.reason }),
    ]),
  ])));
  // One scale for every slot's speed bar, so the bars compare.
  const top = Math.max(20, ...account.speed.flatMap((row) => [...row.values, row.target_value || 0]));
  $("account-speed").replaceChildren(...account.speed.map((row) => {
    const width = 200;
    const x = (value) => (value / top) * width;
    const bar = svg("svg", { viewBox: `0 0 ${width} 16`, class: "strip", preserveAspectRatio: "none" });
    if (row.values.length) bar.append(svg("rect", { x: 0, y: 4, width: Math.max(2, x(row.values[0])), height: 8, rx: 2, class: "speed-bar" }));
    for (const value of row.values.slice(1)) bar.append(svg("line", { x1: x(value), x2: x(value), y1: 2, y2: 14, class: "speed-tick" }));
    if (row.target_value != null) bar.append(svg("line", { x1: x(row.target_value), x2: x(row.target_value), y1: 0, y2: 16, class: "speed-target" }));
    return el("div", { class: "strip-row", role: "listitem", title: `${row.slot}: fastest ${row.fastest}, target ${row.target}` }, [
      el("span", { class: "strip-name", text: row.slot.charAt(0).toUpperCase() + row.slot.slice(1) }), bar, el("span", { class: "strip-count beacon", text: row.target }),
    ]);
  }));
}

// The Help page: a few plain steps for the mode picked. The text is fixed: it comes with the options, once.
function showHelp(name) {
  markSegmented($("help-tabs"), name);
  const [, steps] = options.help.find(([mode]) => mode === name);
  $("help-body").replaceChildren(...steps.map((step) => el("li", { text: step })));
}

function renderStats(view) {
  markSegmented($("stats-tabs"), view.stats_tab);
  const tab = view.stats.find(([name]) => name === view.stats_tab);
  const text = JSON.stringify(tab);
  const body = $("stats-body");
  if (body.dataset.text === text) return;
  body.dataset.text = text;
  body.replaceChildren(...tab[1].flatMap(([title, tiles]) => [
    el("h2", { class: "section", text: title }),
    el("dl", { class: "stat-table" }, tiles.flatMap(([value, caption]) => [el("dt", {}, captioned(caption)), el("dd", { text: value })])),
  ]));
}

function renderSettings(view, previous) {
  markSegmented($("settings-tabs"), view.settings_tab);
  for (const panel of document.querySelectorAll(".settings .panel")) show(panel, panel.dataset.tab === view.settings_tab);
  const gearPages = $("gear-pages");
  markSegmented(gearPages, view.gear_page);
  for (const node of document.querySelectorAll("[data-page]")) show(node, node.dataset.page === view.gear_page);
  markSegmented($("client-pick"), view.client || "");
  $("settings-form").disabled = view.running;
  show($("settings-locked"), view.running);
  $("client-pick").querySelectorAll("button").forEach((button) => { button.disabled = view.running; });

  for (const [name, value] of Object.entries(view.settings)) {
    for (const input of inputs[name] || []) {
      if (input.classList.contains("segmented")) {
        markSegmented(input, value);
      } else if (input.type === "checkbox") {
        if (input.checked !== value) input.checked = value;
      } else if (document.activeElement !== input && input.value !== String(value) && !(name in view.invalid)) {
        input.value = value;
      }
    }
    setError(name, view.invalid[name]);
  }
  view.settings.top_rules.slice(0, 2).forEach(([count, rank], index) => {
    const [countInput, rankInput] = inputs[`top_rules.${index}`];
    const key = `top_rules.${index}`;
    if (!(key in view.invalid)) {
      if (document.activeElement !== countInput) countInput.value = count;
      if (document.activeElement !== rankInput) rankInput.value = rank;
    }
    setError(key, view.invalid[key]);
  });
  renderRoles(view.settings, view.invalid);
  const penguinLine = $("penguin-cost");
  const penguinTyped = (inputs.penguins_amount || [])[0];
  if (penguinLine && penguinTyped) {
    penguinLine.tell(view.settings.penguins_kind, document.activeElement === penguinTyped ? penguinTyped.value : view.settings.penguins_amount);
  }
  const cost = $("shop-cost");
  const typed = (inputs.shop_refreshes || [])[0];
  if (cost && typed) cost.tell(document.activeElement === typed ? typed.value : view.settings.shop_refreshes);
  const said = $("efficiency-said");
  if (said) setText(said, `${view.settings.gear_efficiency}% · gear score ${Math.round((view.settings.gear_efficiency * FULL_PERFECT) / 100)} of ${FULL_PERFECT}`);
  const heroes = view.settings.stop_heroes;
  if (!previous || previous.settings.stop_heroes.join("|") !== heroes.join("|")) drawHeroes();
  setText($("hero-chosen"), heroes.length ? `Chosen: ${heroes.join(", ")}` : "No hero chosen.");

  const reset = $("reset-stats");
  setText(reset, view.reset_stats_armed ? "Confirm: erase everything" : "Reset the stats");
  reset.classList.toggle("danger-armed", view.reset_stats_armed);
  setText($("reset-stats-note"), view.reset_stats_note);
  const [notePage, noteText] = view.reset_settings_note;
  for (const { name, button, note } of resetButtons) {
    const armed = view.reset_settings_armed === name;
    setText(button, armed ? "Confirm: back to the defaults" : `Reset ${name} to defaults`);
    button.classList.toggle("danger-armed", armed);
    button.disabled = view.running;
    setText(note, notePage === name ? noteText : "");
  }
}

function setError(name, message) {
  for (const input of inputs[name] || []) input.setAttribute("aria-invalid", Boolean(message));
  for (const node of inputs[`error:${name}`] || []) {
    show(node, Boolean(message));
    setText(node, message ? `Not valid: ${message}` : "");
  }
}

// The log: newest first, staying at the top unless the user scrolled down to read.

async function refreshLog(added) {
  if (added === logSeen) return;
  const update = await api.log_lines(logSeen);
  if (update.reset) logLines = [];
  logLines = logLines.concat(update.lines).slice(-LOG_KEEP);
  logSeen = update.added;
  const box = $("log");
  const following = box.scrollTop <= 2;
  box.textContent = logLines.slice().reverse().join("\n");
  if (following) box.scrollTop = 0;
}

// Polling

async function refresh() {
  try {
    const view = await api.view();
    render(view);
    await refreshLog(view.log_added);
  } catch (error) {
    console.error(error);
  }
}

function fatal(text) {
  setText($("fatal-text"), text);
  show($("fatal"), true);
}

let booted = false;

async function boot() {
  if (booted) return;
  booted = true;
  if (!window.pywebview || !window.pywebview.api || !window.pywebview.api.view) {
    fatal("This page is the bot's window: open it with E7Bot.exe or main.py, not in a browser.");
    return;
  }
  api = window.pywebview.api;
  try {
    options = await api.options();
  } catch (error) {
    fatal(`The bot did not answer: ${error}. Close the window and start the bot again; the details are in logs/bot.log.`);
    return;
  }
  buildTabs();
  buildModes();
  buildClients();
  buildSettings();
  buildRun();
  await refresh();
  const tick = async () => {
    await refresh();
    setTimeout(tick, POLL_MS);
  };
  setTimeout(tick, POLL_MS);
}

if (window.pywebview && window.pywebview.api && window.pywebview.api.view) boot();
else window.addEventListener("pywebviewready", boot);
// Opened outside the bot, pywebview never comes: say so instead of staying blank.
setTimeout(() => { if (!api) boot(); }, 4000);
