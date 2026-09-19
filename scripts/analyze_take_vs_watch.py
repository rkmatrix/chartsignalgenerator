"""Why do TAKE signals lose money while WATCH signals make it?

TAKE is the desk's high-conviction call: score >= 75 and past the first hour.
WATCH is everything else. Over 175 closed rows TAKE runs 46% for -$929 while
WATCH runs 66% for +$1,669, which is conviction pointing the wrong way.

Before believing that, control for the confounds. first_hour is FORCED to WATCH,
so the two groups do not see the same tape, and TAKE may simply be riding bigger
premiums where the same percentage loss costs more dollars.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path
from statistics import mean

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

INDEX = {"SPY", "SPX", "QQQ", "DIA", "IWM"}
book = json.loads(
    Path(r"C:\Projects\trading\PA\data\history\signal_book.json").read_text(encoding="utf-8")
)
rows = [
    r for r in book.get("trades", [])
    if r.get("status") == "closed" and r.get("pnl_pct") is not None
]
for r in rows:
    r["v"] = str(r.get("verdict") or "?").upper()
    r["d"] = float(r.get("pnl_dollars") or 0.0)
    r["p"] = float(r["pnl_pct"])
    r["e"] = float(r.get("entry") or 0.0)


def line(label: str, grp: list[dict], width: int = 24) -> None:
    if not grp:
        print(f"  {label:<{width}} n=0")
        return
    w = [x["d"] for x in grp if x["d"] > 0]
    l = [x["d"] for x in grp if x["d"] <= 0]
    print(
        f"  {label:<{width}} n={len(grp):<4} win={len(w) / len(grp) * 100:>3.0f}%  "
        f"avgW={mean(w) if w else 0:>+6.0f}  avgL={mean(l) if l else 0:>+6.0f}  "
        f"pct={mean([x['p'] for x in grp]):>+6.1f}%  total={sum(x['d'] for x in grp):>+7.0f}"
    )


print("=== headline ===")
for v in ("TAKE", "WATCH"):
    line(v, [r for r in rows if r["v"] == v])

print("\n=== controlling for window (first_hour is forced to WATCH) ===")
windows = sorted({str(r.get("window") or "?") for r in rows})
for wnd in windows:
    grp = [r for r in rows if str(r.get("window") or "?") == wnd]
    print(f"  -- {wnd} (n={len(grp)})")
    for v in ("TAKE", "WATCH"):
        line(f"     {v}", [r for r in grp if r["v"] == v], width=21)

print("\n=== is it just premium size? avg loss is in DOLLARS ===")
for v in ("TAKE", "WATCH"):
    grp = [r for r in rows if r["v"] == v and r["e"] > 0]
    ents = sorted(x["e"] for x in grp)
    print(f"  {v:<6} entry premium: median ${ents[len(ents) // 2]:.2f}  "
          f"mean ${mean(ents):.2f}  max ${max(ents):.2f}")

print("\n=== same thing measured in PERCENT, which is size-blind ===")
for v in ("TAKE", "WATCH"):
    grp = [r for r in rows if r["v"] == v]
    w = [x["p"] for x in grp if x["p"] > 0]
    l = [x["p"] for x in grp if x["p"] <= 0]
    print(f"  {v:<6} avg win {mean(w) if w else 0:>+6.1f}%   avg loss {mean(l) if l else 0:>+6.1f}%   "
          f"expectancy {mean([x['p'] for x in grp]):>+6.2f}%")

print("\n=== which exits does each verdict end up in? ===")
for v in ("TAKE", "WATCH"):
    grp = [r for r in rows if r["v"] == v]
    by = defaultdict(list)
    for r in grp:
        by[str(r.get("reason"))].append(r)
    print(f"  -- {v}")
    for reason, g in sorted(by.items(), key=lambda kv: sum(x["d"] for x in kv[1])):
        print(f"     {reason:<18} n={len(g):<4} {sum(x['d'] for x in g):>+8.0f}  "
              f"({len(g) / len(grp) * 100:>3.0f}% of {v})")

print("\n=== index vs single name ===")
for v in ("TAKE", "WATCH"):
    grp = [r for r in rows if r["v"] == v]
    line(f"{v} index", [r for r in grp if str(r.get("ticker") or "").upper() in INDEX])
    line(f"{v} single", [r for r in grp if str(r.get("ticker") or "").upper() not in INDEX])

print("\n=== excluding the two AVGO blowups that the risk cap now refuses ===")
clean = [r for r in rows if not (r["e"] >= 15.0)]
print(f"  dropped {len(rows) - len(clean)} rows with entry >= $15")
for v in ("TAKE", "WATCH"):
    line(v, [r for r in clean if r["v"] == v])
