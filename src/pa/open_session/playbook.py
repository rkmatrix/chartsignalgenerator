"""Ticker × time-of-day playbook.

Instagram clips are noisy. The part that is real is session structure: the
same indicator is not the same trade at 9:50 and at 11:10, and TSLA's open is
not SPY's open.

Windows (minutes after 09:30 ET)
  first_hour   0–60     opening drive / ORB / gap
  mid_morning  60–90    first pullback after the open
  after_90     90–lunch continuation, VWAP, failed-break mean-rev
  afternoon    post-lunch same idea as after_90

Buckets
  index   SPY SPX QQQ IWM DIA  — trade the open
  wild    TSLA NVDA AMD PLTR   — let the open shake out; hunt after ~90m
  mega    AAPL MSFT …          — cleaner trend, still not a first-hour mean-rev
  slow    JPM BAC XOM          — levels + trend, skip 1m bursts

This is microstructure, not a backtested win-rate. We drop the known-bad
window/setup pairs; we do not invent a 70% claim.
"""
from __future__ import annotations

from dataclasses import dataclass, replace

from pa.open_session.setups import FAMILY, Candidate

INDEX = frozenset({"SPY", "SPX", "QQQ", "IWM", "DIA"})
WILD = frozenset({"TSLA", "NVDA", "AMD", "PLTR"})
SLOW = frozenset({"JPM", "BAC", "XOM"})
# Everything else in the hunt list is a large single-name: AAPL, MSFT, AMZN…

OPEN_DRIVE = frozenset(
    {"gap_and_go", "orb", "premarket_breakout", "momentum_burst", "ema_align", "ema_cross"}
)
MEAN_REV = frozenset({"bb_rejection", "pullback_to_vwap", "level_retest"})
CONTINUATION = frozenset(
    {
        "ema_align",
        "ema_cross",
        "ema50_break",
        "pullback_to_vwap",
        "level_retest",
        "bb_rejection",
        "momentum_burst",
    }
)
LEVEL_TREND = frozenset(
    {"premarket_breakout", "orb", "ema_align", "ema_cross", "level_retest", "pullback_to_vwap"}
)
GAP_ONLY = frozenset({"gap_and_go"})
AFTER_OPEN = CONTINUATION | frozenset({"premarket_breakout"})


@dataclass(frozen=True)
class Playbook:
    window: str
    bucket: str
    allow: frozenset[str]
    boost: dict[str, float]
    why: str

    def as_dict(self) -> dict:
        return {
            "window": self.window,
            "bucket": self.bucket,
            "why": self.why,
            "allow": sorted(self.allow),
        }


def session_window(elapsed: float | None) -> str:
    if elapsed is None or elapsed < 0:
        return "closed"
    if elapsed < 60:
        return "first_hour"
    if elapsed < 90:
        return "mid_morning"
    if elapsed < 135:
        return "after_90"
    if elapsed < 225:
        return "lunch"
    if elapsed < 330:
        return "afternoon"
    return "late"


def bucket_for(ticker: str) -> str:
    name = ticker.upper()
    if name in INDEX:
        return "index"
    if name in WILD:
        return "wild"
    if name in SLOW:
        return "slow"
    return "mega"


def playbook_for(ticker: str, elapsed: float | None) -> Playbook:
    window = session_window(elapsed)
    bucket = bucket_for(ticker)
    name = ticker.upper()

    if window in {"closed", "lunch", "late"}:
        return Playbook(window, bucket, frozenset(), {}, f"{name}: no new entries this window")

    if bucket == "index":
        if window == "first_hour":
            return Playbook(
                window,
                bucket,
                OPEN_DRIVE,
                {"orb": 1.25, "gap_and_go": 1.25, "momentum_burst": 1.2, "premarket_breakout": 1.15},
                f"{name}: first hour — ORB / gap / momentum (index opening drive)",
            )
        if window == "mid_morning":
            return Playbook(
                window,
                bucket,
                OPEN_DRIVE | MEAN_REV | frozenset({"ema50_break"}),
                {"pullback_to_vwap": 1.15, "level_retest": 1.15, "orb": 1.05},
                f"{name}: 60–90m — first pullback still OK, opening range not stale yet",
            )
        return Playbook(
            window,
            bucket,
            CONTINUATION,
            {"pullback_to_vwap": 1.25, "level_retest": 1.2, "bb_rejection": 1.2, "ema_align": 1.1},
            f"{name}: after 90m — VWAP/level retest and failed rips, not a fresh ORB chase",
        )

    if bucket == "wild":
        if window == "first_hour":
            return Playbook(
                window,
                bucket,
                GAP_ONLY,
                {"gap_and_go": 1.2},
                f"{name}: first hour is open chop — only a real gap-and-go; wait ~90m for the rest",
            )
        if window == "mid_morning":
            return Playbook(
                window,
                bucket,
                MEAN_REV | frozenset({"ema_align", "ema_cross", "ema50_break"}),
                {"pullback_to_vwap": 1.2, "level_retest": 1.15},
                f"{name}: 60–90m — shakeout fading; VWAP/level only, still no ORB chase",
            )
        return Playbook(
            window,
            bucket,
            AFTER_OPEN,
            {
                "pullback_to_vwap": 1.25,
                "level_retest": 1.25,
                "bb_rejection": 1.2,
                "ema50_break": 1.15,
                "momentum_burst": 1.1,
            },
            f"{name}: after 90m — this is the window (pullback, retest, band rejection, EMA50)",
        )

    if bucket == "slow":
        if window == "first_hour":
            allow = LEVEL_TREND
        else:
            allow = (LEVEL_TREND | frozenset({"bb_rejection", "ema50_break"})) - frozenset({"gap_and_go"})
        return Playbook(
            window,
            bucket,
            allow,
            {"orb": 1.15, "premarket_breakout": 1.15, "level_retest": 1.2, "ema_align": 1.1},
            f"{name}: levels + trend — skip 1m bursts on a slow name",
        )

    # mega-cap single names
    if window == "first_hour":
        return Playbook(
            window,
            bucket,
            OPEN_DRIVE,
            {"orb": 1.2, "premarket_breakout": 1.15, "ema_align": 1.1},
            f"{name}: first hour — ORB/PM + trend; skip band fades into the opening drive",
        )
    if window == "mid_morning":
        return Playbook(
            window,
            bucket,
            OPEN_DRIVE | MEAN_REV | frozenset({"ema50_break"}),
            {"pullback_to_vwap": 1.2, "level_retest": 1.15, "ema_align": 1.1},
            f"{name}: 60–90m — trend pullbacks after the open",
        )
    return Playbook(
        window,
        bucket,
        CONTINUATION,
        {"pullback_to_vwap": 1.25, "level_retest": 1.2, "bb_rejection": 1.15, "ema_align": 1.1},
        f"{name}: after 90m — VWAP/retest/bands, not a late ORB chase",
    )


def apply_playbook(
    candidates: list[Candidate], ticker: str, elapsed: float | None
) -> tuple[list[Candidate], Playbook]:
    sl = playbook_for(ticker, elapsed)
    if not candidates:
        return [], sl
    out: list[Candidate] = []
    for cand in candidates:
        if cand.strategy not in sl.allow:
            continue
        if cand.strategy not in FAMILY:
            continue
        m = float(sl.boost.get(cand.strategy, 1.0))
        if abs(m - 1.0) < 1e-9:
            out.append(cand)
        else:
            out.append(replace(cand, conviction=round(cand.conviction * m, 3)))
    return out, sl
