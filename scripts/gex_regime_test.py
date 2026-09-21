"""Does dealer gamma positioning explain why our signals average to nothing?

The Kmer strategy's central claim is that gamma regime decides whether breakouts
work at all: in positive gamma dealers sell rips and buy dips, damping moves and
failing breakouts; in negative gamma they do the reverse and amplify them.

If that is right it would explain the flattest result in this project. Our
setups are overwhelmingly breakout and momentum shaped, and they measure to
almost exactly zero edge. A rule that wins in one regime and loses in the other
averages to zero -- which is what a zero reading looks like from the outside.

This is worth testing precisely because GEX is not another function of price.
Every indicator tried so far -- EMA, VWAP, Bollinger, MACD, the tape reading --
is computed from the same close series, so they mostly re-describe each other.
Gamma comes from options open interest and is genuinely orthogonal.

Same discipline as everything else: split by date into halves, judge against
each name's own round-trip bar, and permute to see what noise produces.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.bandit import LEVERAGE  # noqa: E402

H = ROOT / "data" / "history"
CACHE = H / "gex_cache.json"
BASE = "https://api.unusualwhales.com"

# Median round trip as % of premium, from historical NBBO. Names absent here
# were not measured as cheap enough to matter.
ROUND_TRIP = {
    "SPY": 0.97, "QQQ": 1.70, "IWM": 2.84, "TSLA": 3.02, "PLTR": 3.13,
    "NFLX": 3.56, "NVDA": 4.43, "META": 4.44, "BAC": 6.25, "AAPL": 8.24,
    "DIA": 9.26, "AMZN": 9.32, "JPM": 13.06, "MSFT": 14.85, "XOM": 15.32,
    "AMD": 16.36, "GOOGL": 16.53,
}


def api_key() -> str:
    k = os.environ.get("UNUSUAL_WHALES_API_KEY", "")
    if k:
        return k
    env = ROOT / ".env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("UNUSUAL_WHALES_API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def fetch_gex(tickers: list[str]) -> dict[str, dict[str, float]]:
    """Net gamma exposure per ticker per day. Cached; one call covers ~250 days."""
    if CACHE.exists():
        try:
            return json.loads(CACHE.read_text(encoding="utf-8"))
        except Exception:
            pass
    key = api_key()
    if not key:
        raise SystemExit("no API key")
    out: dict[str, dict[str, float]] = {}
    for tk in tickers:
        req = urllib.request.Request(
            f"{BASE}/api/stock/{tk}/greek-exposure",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                body = json.loads(r.read().decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"  {tk}: {exc}")
            continue
        series = {}
        for row in body.get("data") or []:
            day = row.get("date")
            try:
                # Naive GEX: dealers are assumed short calls and long puts, so
                # call gamma adds and put gamma subtracts. Sign is what matters
                # here, not the absolute dollar figure.
                cg = float(row.get("call_gamma") or 0.0)
                pg = float(row.get("put_gamma") or 0.0)
            except (TypeError, ValueError):
                continue
            if day:
                series[day] = cg - pg
        out[tk] = series
        print(f"  {tk:<6} {len(series)} sessions of GEX")
        time.sleep(0.15)
    CACHE.write_text(json.dumps(out, indent=2), encoding="utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hold", type=int, default=30)
    ap.add_argument("--min-n", type=int, default=40)
    args = ap.parse_args()

    book = json.loads((H / "exit_paths.json").read_text(encoding="utf-8"))
    sigs = [s for s in book["signals"] if len(s["path"]) >= args.hold]
    tickers = sorted({s["tk"] for s in sigs})

    print("fetching GEX history...")
    gex = fetch_gex(tickers)

    # Regime is relative to each name's own history: "positive gamma" means high
    # for this ticker, since the absolute scale differs wildly between SPY and
    # PLTR. Split at the median of the sessions actually traded.
    rows = []
    for s in sigs:
        series = gex.get(s["tk"]) or {}
        g = series.get(s["day"])
        if g is None:
            continue
        rows.append({
            "tk": s["tk"], "day": s["day"], "dir": s["dir"],
            "window": s.get("window") or "", "gex": g,
            "move": s["path"][args.hold - 1],
        })
    if not rows:
        print("no signals joined to GEX")
        return

    by_tk_median = {
        tk: median([r["gex"] for r in rows if r["tk"] == tk])
        for tk in {r["tk"] for r in rows}
    }
    for r in rows:
        r["pos_gamma"] = r["gex"] >= by_tk_median[r["tk"]]

    days = sorted({r["day"] for r in rows})
    cut = days[len(days) // 2]
    print(f"\n{len(rows)} signals joined to GEX over {len(days)} sessions, hold={args.hold}m")
    print(f"split at {cut}\n")

    def report(label: str, sel: list[dict]) -> tuple[float, float, int]:
        early = [r["move"] for r in sel if r["day"] < cut]
        late = [r["move"] for r in sel if r["day"] >= cut]
        if len(early) < args.min_n or len(late) < args.min_n:
            print(f"  {label:<40} n={len(sel):<5} too few")
            return 0.0, 0.0, len(sel)
        me, ml = mean(early), mean(late)
        print(
            f"  {label:<40} n={len(sel):<5} early={me:+.4f}% late={ml:+.4f}%"
        )
        return me, ml, len(sel)

    print("=== does gamma regime separate the signals? ===")
    report("all signals", rows)
    pos = [r for r in rows if r["pos_gamma"]]
    neg = [r for r in rows if not r["pos_gamma"]]
    pe, pl, _ = report("positive gamma (dealers damp moves)", pos)
    ne, nl, _ = report("negative gamma (dealers amplify)", neg)

    print("\n=== the claim: breakouts fail in positive gamma ===")
    print(f"  negative-gamma minus positive-gamma, early half: {ne - pe:+.4f}%")
    print(f"  negative-gamma minus positive-gamma, late half:  {nl - pl:+.4f}%")
    consistent = (ne - pe > 0) == (nl - pl > 0)
    print(f"  same sign in both halves: {'YES' if consistent else 'NO'}")

    print("\n=== does either regime clear its own bar? ===")
    print(f"  {'ticker':<7} {'regime':<9} {'n':>5} {'early':>9} {'late':>9} {'bar':>8}  verdict")
    winners = []
    for tk in sorted({r["tk"] for r in rows}):
        bar = ROUND_TRIP.get(tk, 99.0) / LEVERAGE
        for label, sel in (
            ("neg gex", [r for r in rows if r["tk"] == tk and not r["pos_gamma"]]),
            ("pos gex", [r for r in rows if r["tk"] == tk and r["pos_gamma"]]),
        ):
            e = [r["move"] for r in sel if r["day"] < cut]
            la = [r["move"] for r in sel if r["day"] >= cut]
            if len(e) < args.min_n or len(la) < args.min_n:
                continue
            me, ml = mean(e), mean(la)
            ok = me > bar and ml > bar
            print(
                f"  {tk:<7} {label:<9} {len(sel):>5} {me:>+8.4f}% {ml:>+8.4f}%"
                f" {bar:>7.4f}%  {'CLEARS BOTH' if ok else '-'}"
            )
            if ok:
                winners.append((tk, label, sel, bar))

    print(f"\n=== {len(winners)} cohort(s) clear their own bar in both halves ===")
    if winners:
        rng = random.Random(23)
        for tk, label, sel, bar in winners:
            real = mean(r["move"] for r in sel)
            moves = [r["move"] for r in sel]
            beats = 0
            for _ in range(500):
                flipped = [m if rng.random() < 0.5 else -m for m in moves]
                if mean(flipped) >= real:
                    beats += 1
            p = beats / 500
            print(
                f"  {tk:<7} {label:<9} mean={real:+.4f}% bar={bar:.4f}% p={p:.3f}"
                f"  {'SURVIVES' if p < 0.05 else 'noise'}"
            )


if __name__ == "__main__":
    main()
