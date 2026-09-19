"""How much option P&L does one percent of underlying actually buy us?

The engulfing backtest measures edge in R on the underlying, but we trade
contracts. To judge it we need the conversion, and there are two honest ways to
get it, so do both and see whether they agree:

  modeled    ATM delta of about 0.5, so leverage ~= 0.5 * spot / premium
  realized   join closed trades against the 1m tape and divide the option move
             by the underlying move that produced it

The realized number is net of the bid/ask round trip, because pnl_pct is struck
from entry-at-ask to exit-at-bid. So realized should land BELOW modeled, and the
gap between them is the round-trip cost expressed in the same units.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ET = ZoneInfo("America/New_York")
hist = Path(r"C:\Projects\trading\PA\data\history")

tape: dict[str, dict[str, float]] = {}
for path in hist.glob("*_1m_hist.json"):
    tk = path.name.split("_")[0].upper()
    rows = {}
    for b in json.loads(path.read_text(encoding="utf-8"))["bars"]:
        ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
        rows[ts.strftime("%Y-%m-%d %H:%M")] = float(b["close"])
    tape[tk] = rows

book = json.loads(Path(r"C:\Projects\trading\PA\data\history\signal_book.json")
                  .read_text(encoding="utf-8"))["trades"]


def spot_at(tk: str, when: str | None) -> float | None:
    if not when or tk not in tape:
        return None
    try:
        ts = datetime.fromisoformat(str(when)).astimezone(ET)
    except Exception:
        return None
    rows = tape[tk]
    for back in range(0, 30):  # nearest earlier minute that traded
        key = ts.replace(second=0, microsecond=0).strftime("%Y-%m-%d %H:%M")
        if key in rows:
            return rows[key]
        ts = ts.replace(minute=ts.minute) - __import__("datetime").timedelta(minutes=1)
    return None


modeled: list[float] = []
realized: list[float] = []
matched = 0

for r in book:
    entry = r.get("entry")
    strike = r.get("strike")
    if not entry or not strike or float(entry) <= 0:
        continue
    # Contracts are picked at the money, so the strike stands in for spot.
    modeled.append(0.5 * float(strike) / float(entry))

    if r.get("status") != "closed" or r.get("pnl_pct") is None:
        continue
    tk = str(r.get("ticker") or "").upper()
    s0, s1 = spot_at(tk, r.get("opened_at")), spot_at(tk, r.get("closed_at"))
    if not s0 or not s1:
        continue
    d = 1.0 if str(r.get("direction", "")).lower() in ("call", "long", "buy") else -1.0
    move = d * (s1 - s0) / s0 * 100.0
    matched += 1
    realized.append((move, float(r["pnl_pct"])))

print(f"contracts priced: {len(modeled)}      closed trades joined to tape: {matched}\n")
if modeled:
    m = median(modeled)
    print(f"modeled leverage   median {m:>6.1f}x   "
          f"(1% of underlying -> {m:.0f}% of premium)")
if len(realized) < 10:
    print("not enough joined trades to regress")
    raise SystemExit

# pnl_pct = slope * move + intercept. Slope is the leverage we actually get,
# intercept is what the round trip costs us at zero move. Dividing instead of
# fitting would fold the second into the first and explode on small moves.
n = len(realized)
mx = sum(m for m, _ in realized) / n
my = sum(p for _, p in realized) / n
sxx = sum((m - mx) ** 2 for m, _ in realized)
sxy = sum((m - mx) * (p - my) for m, p in realized)
slope = sxy / sxx if sxx else 0.0
intercept = my - slope * mx
ss_tot = sum((p - my) ** 2 for _, p in realized)
ss_res = sum((p - (slope * m + intercept)) ** 2 for m, p in realized)
r2 = 1 - ss_res / ss_tot if ss_tot else 0.0

print(f"realized leverage  {slope:>6.1f}x   fitted on {n} trades, R2={r2:.2f}")
print(f"round-trip cost    {intercept:>6.1f}%  of premium at zero underlying move")

print("\n=== what this means for the engulfing edge ===")
print("edge in R -> percent of underlying -> percent of premium, then pay the round trip\n")
for rr, edge, risk in ((1.3, 0.109, 0.156), (1.6, 0.112, 0.156), (2.0, 0.164, 0.156)):
    under = edge * risk
    gross = under * slope
    net = gross + intercept
    print(f"  RR {rr}: +{edge:.3f}R = {under:+.4f}% underlying "
          f"= {gross:+6.2f}% gross {net:+7.2f}% net")
