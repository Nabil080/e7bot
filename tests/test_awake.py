"""keep_awake asks Windows to keep the display on, then lets it go."""

import core.awake as awake


def test_keep_awake_asks_then_releases(monkeypatch):
    calls = []
    monkeypatch.setattr(awake, "_set_state", lambda flags: calls.append(flags) or True)
    with awake.keep_awake() as accepted:
        assert accepted
        assert calls == [awake.ES_CONTINUOUS | awake.ES_SYSTEM_REQUIRED | awake.ES_DISPLAY_REQUIRED]
    assert calls[-1] == awake.ES_CONTINUOUS


def test_keep_awake_releases_after_an_error(monkeypatch):
    calls = []
    monkeypatch.setattr(awake, "_set_state", lambda flags: calls.append(flags) or True)
    try:
        with awake.keep_awake():
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert calls[-1] == awake.ES_CONTINUOUS
