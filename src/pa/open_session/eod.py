"""End-of-day summary posted to the Chart Signals chat at the close.

Counts are keyed off the signal book, which is the same record the dashboard
grades from, so the message can never disagree with the Previous Signals table.

  generated  signals booked today (opened_at)
  accuracy   PASS / graded, on signals booked today
  today P/L  realized dollars on positions closed today (closed_at)
  overall    realized dollars across every closed row in the book

Run:  python -m pa.open_session.eod [--dry-run] [--day YYYY-MM-DD] [--force]
"""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def _day(row: dict, field: str) -> str:
    return str(row.get(field) or "")[:10]


def _dollars(row: dict) -> float | None:
    val = row.get("pnl_dollars")
    return None if val is None else float(val)


def build_summary(data_dir: Path, day: str | None = None) -> dict:
    """Grade one session from the book. Pure — no I/O beyond reading the book."""
    from pa.open_session.ledger import load_book

    day = day or datetime.now(ET).date().isoformat()
    trades = list(load_book(data_dir).get("trades") or [])

    booked = [r for r in trades if _day(r, "opened_at") == day]
    graded = [r for r in booked if r.get("prediction") in {"PASS", "FAIL"}]
    passes = sum(1 for r in graded if r.get("prediction") == "PASS")

    closed_today = [
        r for r in trades if _day(r, "closed_at") == day and _dollars(r) is not None
    ]
    all_closed = [r for r in trades if r.get("status") == "closed" and _dollars(r) is not None]

    verdicts: dict[str, int] = {}
    for row in booked:
        key = str(row.get("verdict") or "TAKE").upper()
        verdicts[key] = verdicts.get(key, 0) + 1

    best = max(closed_today, key=_dollars, default=None)
    worst = min(closed_today, key=_dollars, default=None)

    # Conversion: of the trades that went green at any point, how many stayed
    # green? This is the number that actually diagnoses the desk. Across the
    # live-feed book 75.3% of trades went green and only 31.2% closed green,
    # which located the problem in the exits rather than the signal engine.
    # Watching it daily is how we tell whether the exit work is doing anything.
    ever_green = went_green = 0
    for row in closed_today:
        entry, peak = row.get("entry"), row.get("peak_mark")
        if entry is None or peak is None:
            continue
        try:
            if float(peak) > float(entry):
                ever_green += 1
                if (_dollars(row) or 0.0) > 0:
                    went_green += 1
        except (TypeError, ValueError):
            continue

    # Green days, counted the same way the P/L is: realised dollars per session.
    by_day: dict[str, float] = {}
    for row in all_closed:
        d = _day(row, "closed_at")
        if d:
            by_day[d] = by_day.get(d, 0.0) + (_dollars(row) or 0.0)
    sessions = sorted(by_day)
    green_days = sum(1 for d in sessions if by_day[d] > 0)

    return {
        "day": day,
        "generated": len(booked),
        "verdicts": verdicts,
        "ever_green": ever_green,
        "held_green": went_green,
        "conversion_pct": round(went_green / ever_green * 100, 1) if ever_green else None,
        "green_days": green_days,
        "total_days": len(sessions),
        "graded": len(graded),
        "passes": passes,
        "accuracy_pct": round(passes / len(graded) * 100, 1) if graded else None,
        "today_pl": round(sum(_dollars(r) or 0.0 for r in closed_today), 2),
        "overall_pl": round(sum(_dollars(r) or 0.0 for r in all_closed), 2),
        "closed_today": len(closed_today),
        "open_now": sum(1 for r in booked if r.get("status") != "closed"),
        "best": None if best is None else {"ticker": best.get("ticker"), "pnl": _dollars(best)},
        "worst": None if worst is None else {"ticker": worst.get("ticker"), "pnl": _dollars(worst)},
    }


def _money(amount: float) -> str:
    return f"{'-' if amount < 0 else '+'}${abs(amount):,.0f}"


def format_summary(data: dict) -> str:
    parsed = datetime.strptime(data["day"], "%Y-%m-%d")
    label = f"{parsed:%a %b} {parsed.day}"
    rows: list[tuple[str, str]] = [("Signals generated", str(data["generated"]))]

    verdicts = data.get("verdicts") or {}
    if verdicts:
        order = [v for v in ("TAKE", "WATCH", "SKIP") if verdicts.get(v)]
        if order:
            rows.append((
                "  " + " / ".join(order),
                " / ".join(str(verdicts[v]) for v in order),
            ))

    if data["graded"]:
        rows.append((
            "Prediction accuracy",
            f"{data['passes']}/{data['graded']}  ({data['accuracy_pct']}%)",
        ))
    else:
        rows.append(("Prediction accuracy", "- (nothing graded yet)"))

    # The exit scoreboard. Accuracy says whether the call was right; this says
    # whether the desk managed to keep it, which is where the money has been
    # going. A conversion well under 100% means winners are round-tripping.
    if data.get("ever_green"):
        rows.append((
            "Went green / kept it",
            f"{data['held_green']}/{data['ever_green']}  ({data['conversion_pct']}%)",
        ))

    rows.append(("Today's P/L", _money(data["today_pl"])))
    rows.append(("Overall P/L", _money(data["overall_pl"])))

    if data.get("total_days"):
        rows.append((
            "Green sessions",
            f"{data['green_days']}/{data['total_days']}",
        ))

    if data.get("best") and (data["best"]["pnl"] or 0) > 0:
        rows.append(("Highest profit", f"{data['best']['ticker']}  {_money(data['best']['pnl'])}"))
    if data.get("worst") and (data["worst"]["pnl"] or 0) < 0:
        rows.append(("Heavy loss", f"{data['worst']['ticker']}  {_money(data['worst']['pnl'])}"))
    if data.get("open_now"):
        rows.append(("Still open", str(data["open_now"])))

    width = max(len(name) for name, _ in rows)
    body = "\n".join(f"{name.ljust(width)}  {value}" for name, value in rows)
    head = f"PA Desk EOD {label}"
    if data.get("study_mode"):
        head += "  [STUDY - NOT TRADED]"
    return f"{head}\n\n{body}"


def send_eod(
    data_dir: Path, *, day: str | None = None, settings=None, force: bool = False
) -> tuple[bool, str]:
    """Build and post. Silent on an empty session (holiday) unless forced."""
    from pa.open_session.telegram import send_signal

    data = build_summary(data_dir, day)
    data["study_mode"] = bool(getattr(settings, "study_mode", False))
    text = format_summary(data)
    if not force and not data["generated"] and not data["closed_today"]:
        return False, text
    return send_signal(text, settings=settings, monospace=True), text


def main(argv: list[str] | None = None) -> int:
    from pa.config import get_settings

    parser = argparse.ArgumentParser(description="Post the EOD summary to Telegram.")
    parser.add_argument("--day", default=None, help="YYYY-MM-DD (default: today ET)")
    parser.add_argument("--dry-run", action="store_true", help="print, do not send")
    parser.add_argument("--force", action="store_true", help="send even on an empty session")
    args = parser.parse_args(argv)

    settings = get_settings()
    if args.dry_run:
        print(format_summary(build_summary(settings.data_dir, args.day)))
        return 0
    ok, text = send_eod(settings.data_dir, day=args.day, settings=settings, force=args.force)
    print(text)
    print(f"\nsent={ok}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
