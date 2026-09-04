"""Verdict (confidence) and Prediction (was that call right?) — same policy as SignalValidator.

SignalValidator uses EXECUTE / WATCH / SKIP from a 0–100 structure score
(defaults TAKE≥75, WATCH≥50). PA uses TAKE for EXECUTE so the desk matches
the operator's language.

Prediction is decision quality, not 'did we take profit':
  TAKE or WATCH + profit or flat → PASS
  TAKE or WATCH + loss           → FAIL
  SKIP + loss or flat            → PASS (correctly avoided)
  SKIP + profit                  → FAIL (missed a winner)

Two-family prints score ~82 so they still TAKE after the bar rises to 75–80.
Three-family prints score 92. Solo gap/momentum scores ~70 (WATCH if the bar is high).
"""
from __future__ import annotations

TAKE_CUT_MIN = 65
TAKE_CUT_MAX = 90
WATCH_CUT_DEFAULT = 50
TUNE_N = 5

# Two-family conviction ~2.2 must still TAKE after the bar rises to 75–80.
# Old scale (×30) scored 2.2 as 66, so every two-family print became WATCH.
SCORE_SCALE = 30


def score_for(conviction: float | None, families: int | None = None, solo: bool = False) -> int:
    try:
        conv = float(conviction or 0)
    except (TypeError, ValueError):
        conv = 0.0
    if families is None:
        if conv >= 2.0:
            families = 2
        elif conv >= 1.0:
            families = 1
            solo = True
        else:
            families = 1
    n = int(families)
    if n >= 3:
        raw = 92.0
    elif n >= 2:
        raw = 82.0 + max(0.0, conv - 2.0) * 15.0
    elif solo:
        raw = 70.0 + max(0.0, conv - 1.0) * 10.0
    else:
        raw = conv * SCORE_SCALE
    return int(min(100, max(0, round(raw))))


def cutoffs_from(state: dict | None = None) -> tuple[int, int]:
    rec = (state or {}).get("cutoffs") or {}
    try:
        take = int(rec.get("take") or TAKE_CUT_MIN)
    except (TypeError, ValueError):
        take = TAKE_CUT_MIN
    try:
        watch = int(rec.get("watch") or WATCH_CUT_DEFAULT)
    except (TypeError, ValueError):
        watch = WATCH_CUT_DEFAULT
    take = min(TAKE_CUT_MAX, max(TAKE_CUT_MIN, take))
    watch = min(take - 1, max(30, watch))
    return take, watch


def tune_take_cut(passes: int, n: int) -> int:
    """Raise the TAKE bar when recent TAKE calls are not passing. Never lower it."""
    if n < TUNE_N:
        return TAKE_CUT_MIN
    wr = passes / n if n else 0.0
    if wr >= 0.9:
        bump = 0
    elif wr >= 0.75:
        bump = 5
    elif wr >= 0.6:
        bump = 10
    else:
        bump = 15
    return min(TAKE_CUT_MAX, TAKE_CUT_MIN + bump)


def verdict_for(score: int, state: dict | None = None) -> str:
    take, watch = cutoffs_from(state)
    if score >= take:
        return "TAKE"
    if score >= watch:
        return "WATCH"
    return "SKIP"


def normalize_verdict(verdict: str | None) -> str:
    v = str(verdict or "TAKE").upper()
    if v == "EXECUTE":
        return "TAKE"
    if v in {"TAKE", "WATCH", "SKIP"}:
        return v
    return "TAKE"


def prediction_for(verdict: str | None, pnl_pct: float | None) -> str:
    """PASS/FAIL from whether the verdict was right given realized option P&L."""
    if pnl_pct is None:
        return "pending"
    v = normalize_verdict(verdict)
    profit = float(pnl_pct) > 0
    loss = float(pnl_pct) < 0
    if v == "SKIP":
        correct = not profit
    else:
        correct = not loss
    return "PASS" if correct else "FAIL"


def metric_class(verdict: str | None) -> str:
    v = normalize_verdict(verdict)
    if v == "TAKE":
        return "executed"
    if v == "WATCH":
        return "advisory"
    return "counterfactual"
