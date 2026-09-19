"""Does the desk enter after the move it is trying to catch has already happened?

Direction is right 48% of the time, which is established. This asks why. The
mechanism most consistent with a momentum/breakout engine is chasing: the setups
fire once a move is visible on the chart, which is by definition after it has
run, and a short-horizon reversion then takes the other side.

Measures the signed underlying move BEFORE each signal against the signed move
after it. Signed by the signal's own direction, so positive "before" means the
tape was already going the way the desk then bet.

If chasing is real: large positive before, negative after, and the relationship
should strengthen as the prior run gets bigger.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from statistics import mean
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ET = ZoneInfo("America/New_York")
HIST = Path(r"C:\Projects\trading\PA\data\history")
BEFORE = (5, 15, 30)
AFTER = 15


def main() -> None:
    blob = json.loads((HIST / "exit_paths.json").read_text(encoding="utf-8"))
    lev = blob["model"]["lev"]
    rt = abs(blob["model"]["round_trip"])
    sigs = blob["signals"]

    closes: dict[str, list[tuple[str, float]]] = {}
    for p in HIST.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        rows = []
        for b in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            rows.append((ts.strftime("%Y-%m-%d %H:%M"), float(b["close"])))
        rows.sort()
        closes[tk] = rows

    index: dict[str, dict[str, int]] = {
        tk: {k: i for i, (k, _) in enumerate(rows)} for tk, rows in closes.items()
    }

    joined = []
    for s in sigs:
        tk = s["tk"]
        if tk not in index:
            continue
        key = f"{s['day']} {s['time']}"
        i = index[tk].get(key)
        if i is None or i < max(BEFORE):
            continue
        rows = closes[tk]
        sign = 1.0 if s["dir"] == "call" else -1.0
        here = rows[i][1]
        pre = {}
        for b in BEFORE:
            prior = rows[i - b][1]
            pre[b] = sign * (here - prior) / prior * 100.0
        if len(s["path"]) < AFTER:
            continue
        joined.append({"pre": pre, "post": s["path"][AFTER - 1],
                       "window": s["window"], "stack": s["stack"], "day": s["day"]})

    print(f"joined {len(joined)} of {len(sigs)} signals to their prior bars\n")
    print("Was the tape ALREADY moving our way before we entered?")
    print("%-12s %12s" % ("lookback", "mean move %"))
    for b in BEFORE:
        print("%-12s %12.4f" % (f"prior {b}m", mean(j["pre"][b] for j in joined)))
    print(f"\nmean move in the {AFTER}m AFTER entry: {mean(j['post'] for j in joined):+.4f}%")
    print(f"(needs {rt/lev:+.4f}% to break even on the contract)\n")

    print("=== sort signals by how far the tape had already run (prior 15m) ===")
    print("the chasing hypothesis predicts the top rows do worst\n")
    js = sorted(joined, key=lambda j: j["pre"][15])
    n = len(js)
    print("%-22s %7s %14s %13s %8s" % ("prior 15m run", "n", "mean prior %", "mean after %", "dir ok"))
    for lo, hi, lbl in (
        (0, 10, "most AGAINST us"), (10, 30, "against"), (30, 50, "flat-ish"),
        (50, 70, "with us"), (70, 90, "strongly with us"), (90, 100, "most WITH us"),
    ):
        g = js[int(n * lo / 100):int(n * hi / 100)]
        if not g:
            continue
        ok = sum(1 for j in g if j["post"] > 0) / len(g) * 100.0
        print("%-22s %7d %14.4f %13.4f %7.1f%%"
              % (lbl, len(g), mean(j["pre"][15] for j in g), mean(j["post"] for j in g), ok))

    print("\n=== if the top decile is genuinely exhausted, fading it should pay ===")
    top = js[int(n * 0.9):]
    fade = [-j["post"] for j in top]
    days = sorted({j["day"] for j in top})
    cut = days[len(days) // 2]
    fe = [-j["post"] for j in top if j["day"] < cut]
    fl = [-j["post"] for j in top if j["day"] >= cut]
    be = rt / lev
    print(f"fading the most-extended decile: {mean(fade):+.4f}%  (need {be:+.4f}%)")
    print(f"  early half {mean(fe):+.4f}%   late half {mean(fl):+.4f}%")
    verdict = "CLEARS the bar in both halves" if fe and fl and mean(fe) > be and mean(fl) > be \
        else "does not clear the bar"
    print(f"  -> {verdict}")


if __name__ == "__main__":
    main()
