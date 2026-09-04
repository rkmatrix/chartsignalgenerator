"""TAKE policy aimed at 95% prediction accuracy.

Sep 3's later-session TAKEs (QQQ/MSFT/AAPL/NVDA) were 4/4 PASS, all held
≥15 minutes. First hour still leaked four TAKEs including JPM −46%. This
module never TAKEs first hour, even if a stale policy JSON dropped the ban.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

TARGET_ACCURACY = 0.95
MIN_HARD_BAN_N = 5
MIN_READY_N = 5
ALWAYS_WATCH_WINDOWS = frozenset({"first_hour"})
PLAN_HOLD_MINUTES = 15


def _verdict(row: dict) -> str:
    v = str(row.get("verdict") or "TAKE").upper()
    return "TAKE" if v in {"TAKE", "EXECUTE"} else v


def _stack(row: dict) -> str:
    return "+".join(sorted({str(s) for s in (row.get("strategies") or []) if s}))


def _side(row: dict) -> str:
    return str(row.get("direction") or ("put" if row.get("opt") == "P" else "call")).lower()


def _window(row: dict) -> str:
    raw = str(row.get("window") or "")
    if raw:
        return raw
    ts = row.get("opened_at")
    if not ts:
        return ""
    try:
        opened = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return ""
    from pa.open_session.clock import minutes_since_open
    from pa.open_session.playbook import session_window

    return session_window(minutes_since_open(opened)) or ""


def _held_minutes(row: dict) -> float | None:
    o, c = row.get("opened_at"), row.get("closed_at")
    if not o or not c:
        return None
    try:
        a = datetime.fromisoformat(str(o).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(c).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (b - a).total_seconds() / 60.0


def closed_takes(trades: list[dict]) -> list[dict]:
    out = [r for r in (trades or []) if r.get("status") == "closed" and r.get("pnl_pct") is not None and _verdict(r) == "TAKE"]
    out.sort(key=lambda r: str(r.get("opened_at") or r.get("closed_at") or ""))
    return out


def _stats(rows: list[dict]) -> dict:
    n = len(rows)
    passes = sum(1 for r in rows if float(r.get("pnl_pct") or 0) >= 0)
    dol = round(sum(float(r.get("pnl_dollars") or 0) for r in rows), 2)
    return {
        "n": n,
        "passes": passes,
        "pct": round(100.0 * passes / n, 1) if n else None,
        "pnl": dol,
        "hit": bool(n >= MIN_READY_N and passes / n >= TARGET_ACCURACY),
    }


def default_policy() -> dict:
    return {
        "target": TARGET_ACCURACY,
        "banned_windows": sorted(ALWAYS_WATCH_WINDOWS),
        "banned_window_sides": [],
        "banned_stacks": [],
        "in_sample": {"n": 0, "passes": 0, "pct": None, "pnl": 0.0, "hit": False},
        "oos": {"n": 0, "passes": 0, "pct": None, "pnl": 0.0, "hit": False},
        "raw": {"n": 0, "passes": 0, "pct": None, "pnl": 0.0, "hit": False},
        "why": [
            "First hour is WATCH — that window is the leak (Sep 2 −$124, Sep 3 JPM −46%).",
            "Plan-stop waits 15 minutes so 0DTE quote chops are not booked as TAKE FAILs.",
            "TAKE target is 95% on the analog slice (not first hour, hold ≥15m).",
        ],
        "ready": False,
    }


def _allowed(row: dict, banned_windows: set[str], banned_ws: set[str], banned_stacks: set[str]) -> bool:
    window = _window(row)
    side = _side(row)
    if window in banned_windows:
        return False
    if window and f"{window}|{side}" in banned_ws:
        return False
    stack = _stack(row)
    if stack and stack in banned_stacks:
        return False
    return True


def _wr(rows: list[dict]) -> float:
    n = len(rows)
    return (sum(1 for r in rows if float(r.get("pnl_pct") or 0) >= 0) / n) if n else 0.0


def _hard_bans(body: list[dict]) -> tuple[set[str], set[str], list[str]]:
    """Ban slices that lose more than half the time with enough n. Never un-ban first hour."""
    banned_ws: set[str] = set()
    banned_stacks: set[str] = set()
    why: list[str] = []
    by_ws: dict[str, list] = defaultdict(list)
    by_st: dict[str, list] = defaultdict(list)
    for row in body:
        w, side, st = _window(row), _side(row), _stack(row)
        if w:
            by_ws[f"{w}|{side}"].append(row)
        if st:
            by_st[st].append(row)
    for key, rows in by_ws.items():
        if len(rows) >= MIN_HARD_BAN_N and _wr(rows) < 0.5:
            banned_ws.add(key)
            why.append(f"WATCH {key.replace('|', ' ')}s — { _stats(rows)['passes']}/{len(rows)} PASS.")
    for key, rows in by_st.items():
        if len(rows) >= MIN_HARD_BAN_N and _wr(rows) < 0.5:
            banned_stacks.add(key)
            why.append(f"WATCH stack {key} — { _stats(rows)['passes']}/{len(rows)} PASS.")
    return banned_ws, banned_stacks, why


def _analog(rows: list[dict]) -> list[dict]:
    """Closes that match the new exit: the print was held at least 15 minutes."""
    return [row for row in rows if (_held_minutes(row) or 0) >= PLAN_HOLD_MINUTES]


def _walk_forward_oos(takes: list[dict], banned_windows: set[str]) -> dict:
    days: dict[str, list[dict]] = defaultdict(list)
    for row in takes:
        day = str(row.get("opened_at") or row.get("closed_at") or "")[:10]
        if day:
            days[day].append(row)
    oos: list[dict] = []
    prior: list[dict] = []
    for day in sorted(days):
        body = [t for t in prior if _window(t) not in banned_windows]
        bws, bst, _ = _hard_bans(body) if prior else (set(), set(), [])
        analog = []
        for row in days[day]:
            if _allowed(row, banned_windows, bws, bst):
                analog.append(row)
        oos.extend(_analog(analog))
        prior.extend(days[day])
    return _stats(oos)


def fit_policy(trades: list[dict], target: float = TARGET_ACCURACY) -> dict:
    takes = closed_takes(trades)
    policy = default_policy()
    policy["target"] = target
    policy["raw"] = _stats(takes)
    banned_windows = set(ALWAYS_WATCH_WINDOWS)
    body = [t for t in takes if _window(t) not in banned_windows]
    banned_ws, banned_stacks, extra_why = _hard_bans(body)
    allowed = [t for t in takes if _allowed(t, banned_windows, banned_ws, banned_stacks)]
    analog = _analog(allowed)
    policy["banned_windows"] = sorted(banned_windows)
    policy["banned_window_sides"] = sorted(banned_ws)
    policy["banned_stacks"] = sorted(banned_stacks)
    policy["in_sample"] = _stats(analog)
    policy["oos"] = _walk_forward_oos(takes, banned_windows)
    policy["why"] = (policy["why"] + extra_why)[:8]
    policy["ready"] = bool(policy["in_sample"]["hit"])
    if analog:
        st = policy["in_sample"]
        policy["why"].insert(
            0,
            f"Analog TAKE slice (not first hour, hold ≥{PLAN_HOLD_MINUTES}m): "
            f"{st['passes']}/{st['n']} PASS ({st['pct']}%).",
        )
    return policy


def _banned_windows(state: dict | None) -> set[str]:
    pol = (state or {}).get("policy") or {}
    return set(pol.get("banned_windows") or []) | set(ALWAYS_WATCH_WINDOWS)


def allows_take(
    *,
    window: str,
    direction: str,
    strategies: list | None,
    state: dict | None,
) -> tuple[bool, str]:
    pol = (state or {}).get("policy") or default_policy()
    window = str(window or "")
    direction = str(direction or "").lower()
    stack = _stack({"strategies": strategies or []})
    if window in _banned_windows(state):
        return False, (
            f"{window or 'this window'} is WATCH until TAKE accuracy on that slice "
            f"is {int(TARGET_ACCURACY * 100)}%"
        )
    if window and f"{window}|{direction}" in set(pol.get("banned_window_sides") or []):
        return False, f"{window} {direction}s are WATCH — losing slice"
    if stack and stack in set(pol.get("banned_stacks") or []):
        return False, f"{stack} is WATCH — that stack loses more than half the time"
    return True, ""
