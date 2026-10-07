"""Read-only Webull check: options eligibility (preview, no order) and quotes.

Prints status codes and response bodies, never the keys.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from pa.config import Settings
from pa.execution.webull_paper import PAPER_HOST, WebullPaper, option_order


def _show(label: str, response) -> None:
    status = getattr(response, "status_code", "?")
    try:
        body = response.json()
    except Exception:
        body = getattr(response, "text", str(response))
    print(f"{label}: status={status} body={json.dumps(body, default=str)[:600]}")


def _client(settings, host: str):
    from webull.core.client import ApiClient

    client = ApiClient(str(settings.webull_app_key).strip(), str(settings.webull_app_secret).strip(), "us")
    client._stream_logger_set = True
    client._file_logger_set = True
    client.add_endpoint("us", host)
    return client


def main() -> None:
    settings = Settings()
    if not settings.webull_app_key or not settings.webull_app_secret:
        print("no Webull keys in .env")
        return

    wb = WebullPaper(settings)
    try:
        acct = wb.account_id()
        print("paper account found:", bool(acct))
    except Exception as exc:
        print("account lookup failed:", exc)
        return

    # Next Friday SPY call near the money, previewed only.
    friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
    row = {"ticker": "SPY", "direction": "call", "strike": 765.0, "expiry": friday.isoformat(), "contracts": 1}
    order = option_order(row, side="BUY", limit=1.00, client_order_id="probe0000000000000000000000000001"[:32])
    try:
        _show("options preview", wb._trade_client().order_v3.preview_order(acct, [order]))
    except Exception as exc:
        print("options preview error:", str(exc)[:600])

    from webull.data.data_client import DataClient

    for host in (PAPER_HOST, "api.webull.com"):
        data = DataClient(_client(settings, host))
        for label, call in (
            ("stock snapshot SPY", lambda: data.market_data.get_snapshot("SPY", "US_STOCK")),
            ("stock quotes SPY", lambda: data.market_data.get_quotes("SPY", "US_STOCK")),
        ):
            try:
                _show(f"[{host}] {label}", call())
            except Exception as exc:
                print(f"[{host}] {label} error:", str(exc)[:400])


if __name__ == "__main__":
    main()
