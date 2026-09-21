"""An online learner that updates its own weights from every closed signal.

The existing learn.py is not learning. Its weights are three hand-picked
constants (1.0 / 0.8 / 0.55) selected by threshold, it counts per-key buckets so
nothing generalises across contexts, and SKIP_STREAK=2 blocks a recipe after two
consecutive losses. In a process that is right 48% of the time, two losses in a
row arrive constantly by chance, so that rule mostly fits noise and then never
retries what it blocked.

This module replaces the mechanism with Bayesian linear regression updated once
per closed trade. Three properties matter:

REWARD IS NET OF COST. The model is trained on option return after the round
trip, not on direction. A call that rose 0.05% was still a losing trade once the
spread is paid, and it is trained on as one. Rewarding direction is how a desk
convinces itself it is right while its balance falls.

IT KNOWS WHAT IT DOES NOT KNOW. Bayesian linear regression gives a posterior
variance as well as a mean, so the agent can distinguish "this loses" from "no
idea yet". Bucket counting cannot, which is why the old rule panicked at n=2.

IT IS ALLOWED TO DECLINE. Capital is committed on the LOWER confidence bound and
exploration happens on the upper one. Optimism is free while a signal is only
being recorded; pessimism is what protects the account. An agent forced to act
on every signal can only rank them. One that may abstain can survive a feature
set with no edge, which is the situation actually measured here: entry edge
-0.0015% against a round trip costing 0.0796% of the underlying.

Survival is therefore not mainly a matter of picking winners. It is mostly a
matter of learning how seldom to act, and concentrating in the few contexts that
clear the cost bar.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

# Option P&L per unit of underlying move, fitted by regression over trades priced
# on the live NBBO (see scripts/exit_lab.py). ROUND_TRIP is what a position gives
# up to the spread on the way in and out, expressed in percent of premium, and is
# the bar any edge has to clear before it is worth anything.
LEVERAGE = 72.68
ROUND_TRIP = 5.79

WINDOWS = ("first_hour", "mid_morning", "after_90", "lunch", "afternoon", "late")
BUCKETS = ("index", "wild", "mega", "slow")
STRATEGIES = (
    "premarket_breakout",
    "orb",
    "gap_and_go",
    "ema_align",
    "ema_cross",
    "vwap_side",
    "momentum_burst",
    "ema50_break",
    "bb_rejection",
    "pullback_to_vwap",
    "level_retest",
)

INDEX = frozenset({"SPY", "QQQ", "DIA", "IWM"})
WILD = frozenset({"TSLA", "NVDA", "AMD", "PLTR"})
SLOW = frozenset({"JPM", "BAC", "XOM"})


def bucket_for(ticker: str) -> str:
    name = (ticker or "").upper()
    if name in INDEX:
        return "index"
    if name in WILD:
        return "wild"
    if name in SLOW:
        return "slow"
    return "mega"


FEATURE_NAMES: tuple[str, ...] = (
    ("bias", "score", "is_call", "n_families", "mins_to_close", "tape_agrees", "macd_agrees")
    + tuple(f"win:{w}" for w in WINDOWS)
    + tuple(f"bkt:{b}" for b in BUCKETS)
    + tuple(f"str:{s}" for s in STRATEGIES)
)
DIM = len(FEATURE_NAMES)


def features(sig: dict) -> np.ndarray:
    """Context vector for one signal, from fields known at print time only.

    Shared by training and live serving on purpose. When the two are built by
    separate code they drift, and the model is then scored on a vector it was
    never fitted on -- a failure that is invisible until the money is gone.
    """
    x = np.zeros(DIM, dtype=float)
    idx = {name: i for i, name in enumerate(FEATURE_NAMES)}

    x[idx["bias"]] = 1.0
    # Centred and scaled so no single feature dominates the ridge penalty.
    score = float(sig.get("score") or 0.0)
    x[idx["score"]] = (score - 70.0) / 30.0
    x[idx["is_call"]] = 1.0 if str(sig.get("dir") or sig.get("direction") or "") == "call" else -1.0

    strategies = [str(s) for s in (sig.get("stack") or sig.get("strategies") or []) if s]
    if isinstance(sig.get("stack"), str):
        strategies = [s for s in str(sig["stack"]).split("+") if s]
    families = sig.get("families")
    n_fam = len(families) if families else len({_family(s) for s in strategies})
    x[idx["n_families"]] = (n_fam - 2.0) / 2.0

    mins = sig.get("mins_to_close")
    x[idx["mins_to_close"]] = (float(mins) - 180.0) / 180.0 if mins is not None else 0.0

    # The only two readings measured to carry consistent information. Absent
    # values stay at 0.0, which is the neutral point of a signed feature.
    for key, feat in (("tape_agrees", "tape_agrees"), ("macd_agrees", "macd_agrees")):
        val = sig.get(key)
        if val is not None:
            x[idx[feat]] = 1.0 if val else -1.0

    window = str(sig.get("window") or "")
    if window in WINDOWS:
        x[idx[f"win:{window}"]] = 1.0

    x[idx[f"bkt:{bucket_for(str(sig.get('tk') or sig.get('ticker') or ''))}"]] = 1.0

    for strat in strategies:
        key = f"str:{strat}"
        if key in idx:
            x[idx[key]] = 1.0
    return x


_FAMILY = {
    "premarket_breakout": "level", "orb": "level", "gap_and_go": "gap",
    "ema_align": "trend", "ema_cross": "trend", "vwap_side": "trend",
    "momentum_burst": "momentum", "ema50_break": "momentum",
    "bb_rejection": "mean_rev", "pullback_to_vwap": "mean_rev", "level_retest": "mean_rev",
}


def _family(strategy: str) -> str:
    return _FAMILY.get(strategy, strategy)


def reward_from_move(move_pct: float) -> float:
    """Option return after the round trip, in percent of premium.

    Trained on this rather than on direction. Being right about direction and
    wrong about whether it paid is the mistake that loses money slowly.
    """
    return LEVERAGE * float(move_pct) - ROUND_TRIP


class OnlineLearner:
    """Bayesian linear regression over signal context, updated per outcome.

    Keeps A = XtX + lambda*I and b = Xty, so a weight update is exact and costs
    one rank-one addition -- no learning rate to tune and no chance of the
    divergence that SGD suffers when rewards are heavy-tailed, which option
    returns very much are.

    Rewards are scaled to fractions of premium internally so the posterior
    variance stays numerically sane.
    """

    def __init__(self, dim: int = DIM, ridge: float = 1.0, half_life_days: float = 45.0) -> None:
        self.dim = dim
        self.ridge = ridge
        # Old regimes have to fade or the agent cannot evolve; it would average
        # today's tape with a market two months gone and call the blend truth.
        self.half_life_days = half_life_days
        self.A = np.eye(dim) * ridge
        self.b = np.zeros(dim)
        self.n = 0
        self.reward_sum = 0.0

    def decay(self, days: float = 1.0) -> None:
        """Fade the evidence by elapsed time, keeping the ridge floor intact."""
        if days <= 0 or self.half_life_days <= 0:
            return
        factor = 0.5 ** (days / self.half_life_days)
        self.A = self.A * factor + np.eye(self.dim) * self.ridge * (1.0 - factor)
        self.b *= factor

    def update(self, x: np.ndarray, reward_pct: float) -> None:
        """One closed outcome. Positive reward pulls weights toward it, negative away."""
        r = float(reward_pct) / 100.0
        self.A += np.outer(x, x)
        self.b += x * r
        self.n += 1
        self.reward_sum += r

    @property
    def theta(self) -> np.ndarray:
        return np.linalg.solve(self.A, self.b)

    def predict(self, x: np.ndarray) -> tuple[float, float]:
        """Posterior mean and standard deviation of reward, in percent of premium."""
        A_inv = np.linalg.inv(self.A)
        mean = float(x @ (A_inv @ self.b))
        var = float(x @ (A_inv @ x))
        return mean * 100.0, math.sqrt(max(var, 0.0)) * 100.0

    def lcb(self, x: np.ndarray, beta: float = 1.0) -> float:
        """Pessimistic estimate. What capital is committed on."""
        mean, sd = self.predict(x)
        return mean - beta * sd

    def ucb(self, x: np.ndarray, beta: float = 1.0) -> float:
        """Optimistic estimate. What exploration is done on, since it is free."""
        mean, sd = self.predict(x)
        return mean + beta * sd

    def decide(self, x: np.ndarray, beta: float = 1.0, edge_floor: float = 0.0) -> str:
        """TAKE / WATCH / SKIP.

        TAKE needs the pessimistic bound to clear the floor, so capital moves
        only where the edge survives its own uncertainty. WATCH is everything
        still plausibly positive: it costs nothing, and it is how the agent keeps
        gathering evidence in contexts it has not yet ruled out instead of
        blocking them forever the way the old streak rule did.
        """
        mean, sd = self.predict(x)
        if mean - beta * sd > edge_floor:
            return "TAKE"
        if mean + beta * sd > edge_floor:
            return "WATCH"
        return "SKIP"

    def to_dict(self) -> dict:
        return {
            "dim": self.dim,
            "ridge": self.ridge,
            "half_life_days": self.half_life_days,
            "A": self.A.tolist(),
            "b": self.b.tolist(),
            "n": self.n,
            "reward_sum": self.reward_sum,
            "feature_names": list(FEATURE_NAMES),
        }

    @classmethod
    def from_dict(cls, data: dict) -> OnlineLearner:
        got = list(data.get("feature_names") or [])
        if got and got != list(FEATURE_NAMES):
            # Feature set changed under a saved model. Silently reusing the old
            # weights would score a new vector against stale coefficients, so
            # start clean instead.
            return cls()
        m = cls(
            dim=int(data.get("dim") or DIM),
            ridge=float(data.get("ridge") or 1.0),
            half_life_days=float(data.get("half_life_days") or 45.0),
        )
        m.A = np.array(data["A"], dtype=float)
        m.b = np.array(data["b"], dtype=float)
        m.n = int(data.get("n") or 0)
        m.reward_sum = float(data.get("reward_sum") or 0.0)
        return m

    def top_weights(self, k: int = 10) -> list[tuple[str, float]]:
        theta = self.theta
        order = np.argsort(-np.abs(theta))[:k]
        return [(FEATURE_NAMES[i], float(theta[i]) * 100.0) for i in order]


def learn_from_book(data_dir: Path, trades: list[dict] | None = None) -> dict:
    """Fold every newly closed trade into the weights. Safe to call repeatedly.

    Realised pnl_pct is used as the reward directly and deliberately: the fill
    already crossed the spread both ways, so the round trip is inside the number
    rather than modelled on top of it. Adding ROUND_TRIP here would charge it
    twice.

    Each trade is learned from exactly once. Replaying the book on every poll
    would let one outcome masquerade as a hundred and collapse the posterior
    variance, which is precisely the confidence the agent uses to decide whether
    to risk money.
    """
    from pa.open_session.ledger import load_book

    model = load_model(data_dir)
    seen = set(_learned_ids(data_dir))
    rows = trades if trades is not None else (load_book(data_dir).get("trades") or [])

    learned = 0
    for row in rows:
        tid = str(row.get("id") or "")
        if not tid or tid in seen:
            continue
        if row.get("status") != "closed" or row.get("pnl_pct") is None:
            continue
        # A fabricated 0% from an expiry that was never quoted is not an outcome.
        if str(row.get("prediction") or "") == "unknown":
            continue
        # Only trades priced on the live NBBO are real outcomes.
        #
        # Rows quoted off the 15-minute-delayed chain average +29.3% at a 63.8%
        # win rate, against -7.8% at 31.6% for the 76 priced live. That gap is a
        # pricing artefact, not skill: the delayed era fits a +28% intercept, so
        # a position whose underlying never moved still booked +28%.
        #
        # Training on the blend put the model at +16.1% a trade and would have
        # taught it that trading is lucrative. It is the single most effective
        # way to build an agent that confidently loses money, so the contaminated
        # era is excluded even though it is the majority of the book.
        if str(row.get("quote_source") or "") != "uw":
            continue
        sig = {
            "tk": row.get("ticker"),
            "dir": row.get("direction") or ("put" if row.get("opt") == "P" else "call"),
            "window": row.get("window") or "",
            "score": row.get("score") or 0,
            "stack": row.get("strategies") or [],
            "families": row.get("families") or [],
        }
        model.update(features(sig), float(row["pnl_pct"]))
        seen.add(tid)
        learned += 1

    if learned:
        save_model(data_dir, model)
        _save_learned_ids(data_dir, seen)
    return {"learned": learned, "n": model.n, "total": len(seen)}


def _ids_path(data_dir: Path) -> Path:
    return data_dir / "history" / "bandit_seen.json"


def _learned_ids(data_dir: Path) -> list[str]:
    path = _ids_path(data_dir)
    if not path.exists():
        return []
    try:
        return list(json.loads(path.read_text(encoding="utf-8")) or [])
    except Exception:
        return []


def _save_learned_ids(data_dir: Path, ids) -> None:
    path = _ids_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(sorted(ids)), encoding="utf-8")


def model_path(data_dir: Path) -> Path:
    return data_dir / "history" / "bandit.json"


def load_model(data_dir: Path) -> OnlineLearner:
    path = model_path(data_dir)
    if not path.exists():
        return OnlineLearner()
    try:
        return OnlineLearner.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return OnlineLearner()


def save_model(data_dir: Path, model: OnlineLearner) -> None:
    path = model_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(model.to_dict(), indent=2), encoding="utf-8")
    tmp.replace(path)
