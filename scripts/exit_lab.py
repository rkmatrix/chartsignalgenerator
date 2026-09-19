"""Reconstruct the option path the signal book never recorded, then replay exits.

The book keeps entry, exit, peak and trough — endpoints and two extremes. Every
exit question ("would a lower breakeven arm have rescued QQQ or scratched
NVDA?") depends on the path BETWEEN them, so none of them can be answered from
the book. But an option's path is a function of its underlying's path, and we
hold 25 sessions of 1m bars across 17 tickers. So rebuild it.

Stores the raw underlying path and lets the replayer apply the option model, so
a change of model does not mean regenerating for twenty minutes.

Two of the model's three terms are fitted on live-feed trades only (the pre-09-11
delayed-quote era fits a +28% intercept — free money at zero underlying move,
which cannot happen — at R2=0.11, so it would calibrate against stale noise):

    pnl_pct = leverage * move_pct + round_trip

The third term, theta, is deliberately NOT fitted. Adding minutes_held to the
regression returns +0.28% per minute: time makes a long option gain value. That
is backwards, and it is not a small-sample wobble, it is simultaneity. Holding
time is decided by the exit rule, losers are stopped out in minutes and winners
are left to run, so minutes_held measures survivorship and not decay. Regressing
on it while studying exit rules fits the outcome to itself.

So theta comes from theory instead. An ATM option's time value scales with the
square root of time remaining, which is a property of the contract rather than
of our exits, and exit_sweep.py reports how much its conclusions move if that
assumption is wrong.

Writes data/history/exit_paths.json for exit_sweep.py to replay.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, r"C:\Projects\trading\PA\src")
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from pa.domain.models import Bar, Timeframe  # noqa: E402
from pa.open_session.fuse import fuse  # noqa: E402
from pa.open_session.grade import score_for  # noqa: E402
from pa.open_session.playbook import apply_playbook  # noqa: E402
from pa.open_session.setups import SOLO_STRATEGIES, setups_for  # noqa: E402

ET = ZoneInfo("America/New_York")
RTH_OPEN = dtime(9, 30)
HIST = Path(r"C:\Projects\trading\PA\data\history")
COOLDOWN = 15
TAKE_CUT = 75
MAX_FORWARD = 180  # bars of path to keep; a 0DTE is worthless long before this


# ---------------------------------------------------------------- model fit


def solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        if abs(m[c][c]) < 1e-12:
            return [0.0] * n
        for r in range(n):
            if r == c:
                continue
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    return [m[i][n] / m[i][i] for i in range(n)]


def load_tape() -> dict[str, dict[str, float]]:
    tape: dict[str, dict[str, float]] = {}
    for p in HIST.glob("*_1m_hist.json"):
        tk = p.name.split("_")[0].upper()
        rows: dict[str, float] = {}
        for b in json.loads(p.read_text(encoding="utf-8"))["bars"]:
            ts = datetime.fromisoformat(b["ts"]).astimezone(ET)
            rows[ts.strftime("%Y-%m-%d %H:%M")] = float(b["close"])
        tape[tk] = rows
    return tape


def spot_at(tape, tk: str, when) -> float | None:
    if not when or tk not in tape:
        return None
    try:
        ts = datetime.fromisoformat(str(when)).astimezone(ET)
    except Exception:
        return None
    ts = ts.replace(second=0, microsecond=0)
    rows = tape[tk]
    for _ in range(30):
        key = ts.strftime("%Y-%m-%d %H:%M")
        if key in rows:
            return rows[key]
        ts -= timedelta(minutes=1)
    return None


def fit_model(tape) -> tuple[float, float, int, float]:
    book = json.loads((HIST / "signal_book.json").read_text(encoding="utf-8"))["trades"]
    obs: list[tuple[float, float, float]] = []
    for r in book:
        if r.get("status") != "closed" or r.get("pnl_pct") is None or not r.get("entry"):
            continue
        if str(r.get("opened_at", ""))[:10] < "2026-09-11":
            continue  # delayed-quote era; calibrating on it fits noise
        tk = str(r.get("ticker") or "").upper()
        s0 = spot_at(tape, tk, r.get("opened_at"))
        s1 = spot_at(tape, tk, r.get("closed_at"))
        if not s0 or not s1:
            continue
        try:
            t0 = datetime.fromisoformat(str(r["opened_at"]))
            t1 = datetime.fromisoformat(str(r["closed_at"]))
        except Exception:
            continue
        held = max(0.0, (t1 - t0).total_seconds() / 60.0)
        sign = 1.0 if str(r.get("direction", "")).lower() in ("call", "long", "buy") else -1.0
        obs.append((sign * (s1 - s0) / s0 * 100.0, held, float(r["pnl_pct"])))
    if len(obs) < 15:
        raise SystemExit(f"only {len(obs)} live-feed trades joined to tape; cannot fit")

    n = len(obs)
    sx = sum(o[0] for o in obs)
    sxx = sum(o[0] * o[0] for o in obs)
    sy = sum(o[2] for o in obs)
    sxy = sum(o[0] * o[2] for o in obs)
    lev, rt = solve([[sxx, sx], [sx, float(n)]], [sxy, sy])
    my = sy / n
    sst = sum((o[2] - my) ** 2 for o in obs)
    ssr = sum((o[2] - (lev * o[0] + rt)) ** 2 for o in obs)
    return lev, rt, n, (1 - ssr / sst if sst else 0.0)


# ---------------------------------------------------------------- signals


def bars_for(tk: str) -> tuple[list[Bar], float | None]:
    path = HIST / f"{tk}_1m_hist.json"
    if not path.exists():
        return [], None
    payload = json.loads(path.read_text(encoding="utf-8"))
    out = [
        Bar(
            ticker=tk,
            ts=datetime.fromisoformat(b["ts"]),
            open=float(b["open"]),
            high=float(b["high"]),
            low=float(b["low"]),
            close=float(b["close"]),
            volume=float(b["volume"]),
            timeframe=Timeframe.M1,
        )
        for b in payload["bars"]
    ]
    return out, payload.get("prior_close")


def main() -> None:
    tape = load_tape()
    lev, rt, n, r2 = fit_model(tape)
    print("=== fitted on live-feed trades only ===")
    print(f"  leverage    {lev:8.1f}x per 1% of underlying")
    print(f"  round trip  {rt:8.2f}% at zero move")
    print(f"  n={n}  R2={r2:.2f}")
    print("  theta: not fitted (see module docstring), applied by exit_sweep\n")

    tickers = sorted({p.name.split("_")[0].upper() for p in HIST.glob("*_1m_hist.json")})
    out: list[dict] = []
    for tk in tickers:
        bars, first_prior = bars_for(tk)
        if not bars:
            continue
        by_day: dict[str, list[Bar]] = {}
        for bar in bars:
            by_day.setdefault(bar.ts.astimezone(ET).date().isoformat(), []).append(bar)
        days = [
            d
            for d in sorted(by_day)
            if sum(1 for b in by_day[d] if b.ts.astimezone(ET).time() >= RTH_OPEN) >= 300
        ]
        made = 0
        for di, day in enumerate(days):
            db = by_day[day]
            prior = first_prior if di == 0 else by_day[days[di - 1]][-1].close
            rth = [i for i, b in enumerate(db) if b.ts.astimezone(ET).time() >= RTH_OPEN]
            last_sig = -10_000
            for i in rth:
                bar = db[i]
                clock = bar.ts.astimezone(ET)
                elapsed = (clock.hour * 60 + clock.minute) - (9 * 60 + 30)
                if elapsed < 0:
                    continue
                cands = setups_for(tk, db[: i + 1], orb_minutes=15, prior_close=prior)
                if not cands:
                    continue
                cands, pb = apply_playbook(cands, tk, elapsed)
                fused = fuse(cands, spy_bias=None)
                if not fused or fused.vetoed or fused.direction not in {"call", "put"}:
                    continue
                n_fam = len(fused.families or [])
                solo = n_fam < 2 and any(s in SOLO_STRATEGIES for s in (fused.strategies or []))
                score = score_for(fused.conviction, families=n_fam, solo=solo)
                if pb.window == "first_hour":
                    verdict = "WATCH"
                elif score >= TAKE_CUT:
                    verdict = "TAKE"
                elif score >= 50:
                    verdict = "WATCH"
                else:
                    continue
                if i - last_sig < COOLDOWN:
                    continue
                last_sig = i

                entry = bar.close
                sign = 1.0 if fused.direction == "call" else -1.0
                # Signed underlying move per minute held, model-free. The option
                # conversion happens in the replayer.
                path = [
                    round(sign * (db[j].close - entry) / entry * 100.0, 4)
                    for j in range(i + 1, min(i + 1 + MAX_FORWARD, len(db)))
                ]
                if len(path) < 10:
                    continue
                mins_to_close = max(1, int((16 * 60) - (clock.hour * 60 + clock.minute)))
                out.append(
                    {
                        "tk": tk,
                        "day": day,
                        "time": clock.strftime("%H:%M"),
                        "window": pb.window,
                        "dir": fused.direction,
                        "verdict": verdict,
                        "score": score,
                        "stack": "+".join(fused.strategies),
                        "bars_left": len(db) - i,
                        "mins_to_close": mins_to_close,
                        "path": path,
                    }
                )
                made += 1
        print(f"  {tk:<6} {len(days):>3} sessions -> {made:>4} signals")

    dest = HIST / "exit_paths.json"
    dest.write_text(
            json.dumps(
                {"model": {"lev": lev, "round_trip": rt, "r2": r2, "n": n}, "signals": out},
            ),
        encoding="utf-8",
    )
    print(f"\nwrote {len(out)} signals with reconstructed paths -> {dest}")


if __name__ == "__main__":
    main()
