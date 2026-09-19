"""Replay the booked trades under candidate risk rules before adopting them.

Equal-risk sizing is not automatically an improvement: it shrinks the big
premium names but scales the cheap ones UP, so on a strategy with no edge it can
enlarge the loss. A correlation cap only helps if the dropped trades were on
average worse than the kept ones. Both need measuring, and on more than one day.

Rules layered in order:
  stop      honour the plan stop instead of exiting far below it
  floor     a trade that ran >=25% may not close red
  corr      cap concurrent same-direction exposure (indices count as one trade)
  size      size every trade to the same dollar risk, skip what cannot be sized

Usage: python scripts/simulate_risk_rules.py [risk_per_trade]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

RISK_PER_TRADE = float(sys.argv[1]) if len(sys.argv) > 1 else 150.0
MAX_RISK_MULT = 1.5          # 1 contract may not risk more than this x the budget
MAX_PER_DIRECTION = 3        # concurrent open positions per side
MAX_INDEX_PER_DIRECTION = 1  # SPY/SPX/QQQ/DIA/IWM are effectively the same trade
INDEX = {"SPY", "SPX", "QQQ", "DIA", "IWM"}

book = json.loads(Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8"))
rows = [r for r in book["trades"] if r.get("opened_at") and r.get("entry") and r.get("pnl_pct") is not None]


def adjusted_pct(r: dict, use_stop: bool, use_floor: bool) -> float:
    pct = float(r["pnl_pct"])
    entry = float(r["entry"])
    stop = float(r.get("plan_stop_pct") or 25.0)
    peak = r.get("peak_mark")
    if use_stop and pct < -stop:
        pct = -stop
    if use_floor and peak is not None and entry > 0:
        run = (float(peak) - entry) / entry * 100.0
        if run >= 25.0 and pct <= 0:
            pct = 0.0
    return pct


MIN_PREMIUM = 0.20


def contracts_for(r: dict) -> int | None:
    """Stay at one contract and REFUSE what is too big or too cheap.

    Scaling cheap contracts up to a fixed dollar risk looked spectacular in the
    first version of this sweep (+$26k), but 73% of that came from ten rows such
    as BAC at $0.03 sized to 200 contracts on a "+400%" move. Those quotes all
    predate the Unusual Whales feed, so the percentages are artifacts of the
    15-minute-delayed chain, and 200 contracts of a 3c option is not fillable
    anyway. Sizing here only ever removes risk, it never adds leverage.
    """
    entry = float(r["entry"])
    stop = float(r.get("plan_stop_pct") or 25.0)
    if entry < MIN_PREMIUM:
        return None
    risk_per_contract = entry * 100.0 * stop / 100.0
    if risk_per_contract <= 0 or risk_per_contract > RISK_PER_TRADE * MAX_RISK_MULT:
        return None
    return 1


def passes_correlation(r: dict, open_rows: list[dict]) -> bool:
    side = str(r.get("direction"))
    same = [o for o in open_rows if str(o.get("direction")) == side]
    if len(same) >= MAX_PER_DIRECTION:
        return False
    if str(r.get("ticker")).upper() in INDEX:
        if sum(1 for o in same if str(o.get("ticker")).upper() in INDEX) >= MAX_INDEX_PER_DIRECTION:
            return False
    return True


def run(use_stop: bool, use_floor: bool, use_corr: bool, use_size: bool) -> tuple[float, int, int]:
    by_day: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[str(r["opened_at"])[:10]].append(r)

    total = 0.0
    taken = 0
    skipped = 0
    for day in sorted(by_day):
        day_rows = sorted(by_day[day], key=lambda x: str(x["opened_at"]))
        live: list[dict] = []
        for r in day_rows:
            opened = datetime.fromisoformat(r["opened_at"])
            live = [o for o in live if o["_closed"] > opened]
            if use_corr and not passes_correlation(r, live):
                skipped += 1
                continue
            n = contracts_for(r) if use_size else 1
            if n is None:
                skipped += 1
                continue
            pct = adjusted_pct(r, use_stop, use_floor)
            total += pct / 100.0 * float(r["entry"]) * 100.0 * n
            taken += 1
            closed = r.get("closed_at")
            r["_closed"] = datetime.fromisoformat(closed) if closed else opened
            live.append(r)
    return total, taken, skipped


print(f"book: {len(rows)} closed trades, risk budget ${RISK_PER_TRADE:.0f}/trade\n")
print(f"{'rules':<34} {'total $':>12} {'trades':>8} {'skipped':>9}")
print("-" * 66)
for label, flags in [
    ("as traded (baseline)", (False, False, False, False)),
    ("+ stop honoured", (True, False, False, False)),
    ("+ stop + breakeven floor", (True, True, False, False)),
    ("+ stop + floor + corr cap", (True, True, True, False)),
    ("+ stop + floor + risk cap", (True, True, False, True)),
    ("all four rules", (True, True, True, True)),
]:
    tot, n, sk = run(*flags)
    print(f"{label:<34} {tot:>+12.2f} {n:>8} {sk:>9}")

# Only 2026-09-10 onwards was priced on the live NBBO feed; everything earlier
# came off the 15-minute-delayed chain, so treat it as untrustworthy.
clean = [r for r in rows if str(r["opened_at"])[:10] >= "2026-09-10"]
if clean:
    keep = rows[:]
    globals()["rows"] = clean
    print(f"\n=== only trades priced on the live feed ({len(clean)} trades) ===")
    for label, flags in [
        ("as traded (baseline)", (False, False, False, False)),
        ("+ stop honoured", (True, False, False, False)),
        ("+ stop + breakeven floor", (True, True, False, False)),
        ("all four rules", (True, True, True, True)),
    ]:
        tot, n, sk = run(*flags)
        print(f"{label:<34} {tot:>+12.2f} {n:>8} {sk:>9}")
    globals()["rows"] = keep

print("\n=== all four rules, per day ===")
by_day_rows: dict[str, list[dict]] = defaultdict(list)
for r in rows:
    by_day_rows[str(r["opened_at"])[:10]].append(r)
print(f"{'day':<12} {'baseline $':>12} {'all rules $':>13} {'delta':>11}")
print("-" * 50)
for day in sorted(by_day_rows):
    keep = rows[:]
    globals()["rows"] = by_day_rows[day]
    base, _, _ = run(False, False, False, False)
    fixed, _, _ = run(True, True, True, True)
    globals()["rows"] = keep
    print(f"{day:<12} {base:>+12.2f} {fixed:>+13.2f} {fixed - base:>+11.2f}")
