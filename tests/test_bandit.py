from __future__ import annotations

import numpy as np

from pa.open_session.bandit import (
    DIM,
    LEVERAGE,
    ROUND_TRIP,
    OnlineLearner,
    features,
    reward_from_move,
)


def _sig(**kw) -> dict:
    base = {
        "tk": "SPY",
        "dir": "call",
        "window": "after_90",
        "score": 80,
        "stack": ["ema_align", "momentum_burst"],
        "mins_to_close": 120,
    }
    base.update(kw)
    return base


def test_reward_is_net_of_the_round_trip() -> None:
    """A move that is directionally right but too small is still a loss.

    This is the whole point of training on reward rather than on direction.
    """
    break_even = ROUND_TRIP / LEVERAGE
    assert reward_from_move(break_even) == 0.0
    assert reward_from_move(break_even / 2) < 0  # right way, still loses
    assert reward_from_move(break_even * 3) > 0


def test_features_are_stable_and_bounded() -> None:
    x = features(_sig())
    assert x.shape == (DIM,)
    assert np.isfinite(x).all()
    assert abs(x).max() <= 5.0
    # Same input, same vector: train/serve skew starts here.
    assert np.array_equal(x, features(_sig()))


def test_direction_flips_its_feature() -> None:
    call = features(_sig(dir="call"))
    put = features(_sig(dir="put"))
    assert not np.array_equal(call, put)


def test_learner_moves_toward_rewarded_contexts() -> None:
    """Positive outcomes raise the estimate, negative ones lower it."""
    m = OnlineLearner()
    x = features(_sig())
    start, _ = m.predict(x)
    for _ in range(30):
        m.update(x, +20.0)
    up, _ = m.predict(x)
    assert up > start

    m2 = OnlineLearner()
    for _ in range(30):
        m2.update(x, -20.0)
    down, _ = m2.predict(x)
    assert down < start


def test_uncertainty_shrinks_as_evidence_arrives() -> None:
    """Knowing what it does not know is what lets it abstain instead of panic."""
    m = OnlineLearner()
    x = features(_sig())
    _, sd0 = m.predict(x)
    for _ in range(50):
        m.update(x, 5.0)
    _, sd1 = m.predict(x)
    assert sd1 < sd0


def test_it_abstains_while_it_is_still_unsure() -> None:
    """A brand new context must not get capital on one good outcome."""
    m = OnlineLearner()
    x = features(_sig())
    m.update(x, +40.0)
    assert m.decide(x, beta=1.0) != "TAKE"


def test_it_commits_once_the_evidence_is_consistent() -> None:
    m = OnlineLearner()
    x = features(_sig())
    for _ in range(200):
        m.update(x, +30.0)
    assert m.decide(x, beta=1.0) == "TAKE"


def test_a_losing_context_is_skipped_not_merely_downweighted() -> None:
    m = OnlineLearner()
    x = features(_sig())
    for _ in range(200):
        m.update(x, -30.0)
    assert m.decide(x, beta=1.0) == "SKIP"


def test_capital_is_more_cautious_than_exploration() -> None:
    """LCB gates money, UCB gates study. The gap is the margin of safety."""
    m = OnlineLearner()
    x = features(_sig())
    for _ in range(10):
        m.update(x, +10.0)
    assert m.lcb(x) < m.ucb(x)


def test_decay_lets_an_old_regime_fade() -> None:
    """Without this the agent averages today's tape with a market long gone."""
    m = OnlineLearner(half_life_days=10.0)
    x = features(_sig())
    for _ in range(100):
        m.update(x, +30.0)
    before, _ = m.predict(x)
    m.decay(days=60.0)
    after, _ = m.predict(x)
    assert abs(after) < abs(before)


def test_round_trip_through_disk_preserves_the_model() -> None:
    m = OnlineLearner()
    x = features(_sig())
    for _ in range(20):
        m.update(x, 7.5)
    back = OnlineLearner.from_dict(m.to_dict())
    assert np.allclose(back.predict(x), m.predict(x))
    assert back.n == m.n


def test_delayed_chain_outcomes_are_never_learned_from(tmp_path) -> None:
    """The most dangerous training data available, excluded on purpose.

    Rows priced off the 15-minute-delayed chain average +29.3% at a 63.8% win
    rate against -7.8% at 31.6% for rows priced live. That is a pricing
    artefact, and training on the blend put the model at +16.1% a trade -- an
    agent confidently certain that trading pays.
    """
    from pa.open_session.bandit import learn_from_book, load_model

    common = {
        "status": "closed",
        "ticker": "SPY",
        "direction": "call",
        "window": "after_90",
        "score": 88,
        "strategies": ["ema_align"],
    }
    trades = [
        {"id": "delayed-1", "pnl_pct": 40.0, "quote_source": "yahoo", **common},
        {"id": "delayed-2", "pnl_pct": 35.0, **common},  # no source at all
        {"id": "live-1", "pnl_pct": -10.0, "quote_source": "uw", **common},
    ]
    (tmp_path / "history").mkdir(parents=True, exist_ok=True)
    out = learn_from_book(tmp_path, trades=trades)

    assert out["learned"] == 1, "only the live-feed row is a real outcome"
    model = load_model(tmp_path)
    assert model.n == 1
    assert model.reward_sum < 0, "it must learn the loss, not the fiction"


def test_an_outcome_is_learned_from_exactly_once(tmp_path) -> None:
    """Replaying the book each poll would collapse the variance it relies on."""
    from pa.open_session.bandit import learn_from_book, load_model

    trades = [
        {
            "id": "live-1", "status": "closed", "pnl_pct": -10.0, "quote_source": "uw",
            "ticker": "SPY", "direction": "call", "window": "after_90",
            "score": 88, "strategies": ["ema_align"],
        }
    ]
    (tmp_path / "history").mkdir(parents=True, exist_ok=True)
    assert learn_from_book(tmp_path, trades=trades)["learned"] == 1
    assert learn_from_book(tmp_path, trades=trades)["learned"] == 0
    assert load_model(tmp_path).n == 1


def test_gate_demotes_to_watch_rather_than_dropping_the_signal(tmp_path, monkeypatch) -> None:
    """A vetoed signal must still print, so it can still teach the model."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from pa.clock import MarketClock
    from pa.open_session.scan import scan_open
    from tests.conftest import make_settings
    from tests.test_open_session import dump_after_spike

    et = ZoneInfo("America/New_York")
    hunt = datetime(2026, 8, 31, 10, 35, tzinfo=et)
    monkeypatch.setattr("pa.open_session.contract.synthetic_premium", lambda *a, **k: 2.00)

    off = scan_open(
        make_settings(tmp_path / "off", bandit_gate=False),
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    assert off["strongest"] is not None, "fixture should produce a TAKE without the gate"

    on = scan_open(
        make_settings(tmp_path / "on", bandit_gate=True),
        MarketClock(now_fn=lambda: hunt),
        tickers=["SPY"],
        bars_by_ticker={"SPY": dump_after_spike("SPY")},
    )
    # With no evidence the lower bound cannot clear the spread, so capital is
    # refused -- but the idea is still on the board as a WATCH.
    assert on["strongest"] is None
    assert not on["signals"], "no capital is committed"
    assert any(str(s.get("verdict")) == "WATCH" for s in on.get("queue") or []), (
        "the idea must still be on the board, or it can never teach the model"
    )


def test_a_changed_feature_set_discards_stale_weights() -> None:
    """Scoring a new vector against old coefficients is silent nonsense."""
    m = OnlineLearner()
    x = features(_sig())
    for _ in range(20):
        m.update(x, 7.5)
    data = m.to_dict()
    data["feature_names"] = ["something", "else"]
    fresh = OnlineLearner.from_dict(data)
    assert fresh.n == 0
