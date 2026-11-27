"""State handlers, one module per state. HANDLERS maps each built state to its handler."""

from core.config import State
from core.state import StateHandler
from states.boot import boot
from states.halt import halt
from states.launch import launch
from states.login import login
from states.popup_dismiss import popup_dismiss
from states.recover import recover
from states.reset_account import reset_account
from states.route import route
from states.story import story
from states.summon import summon

HANDLERS: dict[State, StateHandler] = {
    State.BOOT: boot,
    State.LAUNCH: launch,
    State.LOGIN: login,
    State.POPUP_DISMISS: popup_dismiss,
    State.RESET_ACCOUNT: reset_account,
    State.STORY: story,
    State.SUMMON: summon,
    State.ROUTE: route,
    State.RECOVER: recover,
    State.HALT: halt,
}
