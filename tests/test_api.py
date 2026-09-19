from __future__ import annotations

from fastapi.testclient import TestClient

from pa.api.app import create_app
from pa.clock import MarketClock
from pa.execution.paper import PaperBroker
from pa.orchestrator.engine import RuntimeState
from pa.risk.gates import KillSwitch
from pa.storage.journal import EventJournal
from tests.conftest import RTH_NOW, make_settings


def _client(tmp_path, **overrides):
    settings = make_settings(tmp_path)
    for key, value in overrides.items():
        setattr(settings, key, value)
    clock = MarketClock(now_fn=lambda: RTH_NOW)
    broker = PaperBroker(settings.starting_equity)
    kill = KillSwitch(settings.kill_file)
    journal = EventJournal(settings.journal_db)
    state = RuntimeState(started_at=RTH_NOW, using_fixtures=True)
    app = create_app(settings, state, broker, kill, journal, clock)
    return TestClient(app), kill, broker, state


def test_study_mode_is_visible_on_the_dashboard(tmp_path) -> None:
    """The dashboard is where a human checks before acting, so it must say so.

    Telegram lines are banded already. A banner on one surface and silence on
    the other is how a study note gets traded by mistake.
    """
    client, _, _, _ = _client(tmp_path, study_mode=True)
    assert client.get("/health").json()["study_mode"] is True

    off, _, _, _ = _client(tmp_path, study_mode=False)
    assert off.get("/health").json()["study_mode"] is False

    # The page is a static template and the banner is rendered client-side from
    # /health, so the flag is what can be asserted here. Check the template is
    # actually wired to it rather than that the words appear, which they always
    # do -- they are in the script itself.
    assert "h.study_mode" in client.get("/").text


def test_health_and_status_page(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    home = client.get("/")
    assert home.status_code == 200
    assert "PA paper desk" in home.text
    assert "Monday morning" in home.text
    assert "Strong signals" in home.text
    assert "Today's Signals" in home.text
    assert "Previous Signals" in home.text
    assert "What we learned" in home.text
    assert "desk-kpis" in home.text
    assert "Opened" in home.text
    health = client.get("/health")
    assert health.status_code == 200
    body = health.json()
    assert body["ok"] is True
    assert body["trading_mode"] == "paper"
    assert body["killed"] is False
    monday = client.get("/monday")
    assert monday.status_code == 200
    assert monday.json()["ok"] is False
    strongest = client.get("/strongest")
    assert strongest.status_code == 200
    body = strongest.json()
    assert body["ok"] is True
    assert "phase" in body
    assert body["strongest"] is None
    watched = client.post(
        "/watch",
        json={"ticker": "SPY", "right": "call", "strike": 770, "expiry": "2026-08-31", "entry": 1.32},
    )
    assert watched.status_code == 200
    baby = client.get("/babysitter")
    assert baby.status_code == 200
    assert baby.json()["ok"] is True
    assert any(p["ticker"] == "SPY" for p in baby.json()["positions"])


def test_kill_and_resume(tmp_path) -> None:
    client, kill, _, _ = _client(tmp_path)
    posted = client.post("/kill")
    assert posted.status_code == 200
    assert kill.is_killed()
    assert client.get("/health").json()["killed"] is True
    client.post("/resume")
    assert kill.is_killed() is False


def test_positions_empty(tmp_path) -> None:
    client, _, _, _ = _client(tmp_path)
    assert client.get("/positions").json()["positions"] == []
    account = client.get("/account").json()
    assert account["equity"] == 100_000
