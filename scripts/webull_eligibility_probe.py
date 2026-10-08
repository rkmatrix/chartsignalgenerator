"""Preview only, never places: which paper account and which expiry Webull accepts.

Account ids are masked. Secrets are never printed.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from pa.config import Settings
from pa.execution.webull_paper import WebullPaper, option_order


def _mask(value: str) -> str:
    return value[:3] + "…" + value[-2:] if len(value) > 6 else "…"


def _body(response) -> str:
    try:
        return json.dumps(response.json(), default=str)[:260]
    except Exception:
        return str(getattr(response, "text", response))[:260]


def _accounts(payload) -> list[dict]:
    if isinstance(payload, dict):
        if payload.get("account_id") or payload.get("accountId"):
            return [payload]
        found: list[dict] = []
        for value in payload.values():
            found += _accounts(value)
        return found
    if isinstance(payload, list):
        found = []
        for item in payload:
            found += _accounts(item)
        return found
    return []


def main() -> None:
    s = Settings()
    paper = WebullPaper(s)
    trade = paper._trade_client()
    listing = trade.account_v2.get_account_list()
    accounts = _accounts(listing.json())
    print(f"accounts: {len(accounts)}")
    for acct in accounts:
        shown = {k: v for k, v in acct.items() if "id" not in k.lower() and "number" not in k.lower()}
        print("  ", _mask(str(acct.get("account_id") or acct.get("accountId"))), shown)

    today = date.today()
    friday = today + timedelta(days=(4 - today.weekday()) % 7 or 7)
    for acct in accounts:
        acct_id = str(acct.get("account_id") or acct.get("accountId"))
        for label, expiry in (("0DTE", today), ("friday", friday)):
            row = {"ticker": "SPY", "strike": 670, "expiry": expiry.isoformat(), "direction": "call", "contracts": 1}
            order = option_order(row, side="BUY", limit=0.05, client_order_id="probe" + label)
            try:
                response = trade.order_v3.preview_order(acct_id, [order])
                print(f"{_mask(acct_id)} {label} {expiry}: status={response.status_code} {_body(response)}")
            except Exception as exc:
                print(f"{_mask(acct_id)} {label} {expiry}: {str(exc)[:260]}")


if __name__ == "__main__":
    main()
