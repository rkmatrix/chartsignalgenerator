"""Read-only: does the paper host return option chains and option quotes?"""

from __future__ import annotations

import json
from datetime import date, timedelta

from pa.config import Settings
from pa.execution.webull_paper import PAPER_HOST


def _show(label: str, response) -> object:
    status = getattr(response, "status_code", "?")
    try:
        body = response.json()
    except Exception:
        body = getattr(response, "text", str(response))
    print(f"{label}: status={status} body={json.dumps(body, default=str)[:700]}")
    return body


def main() -> None:
    from webull.core.client import ApiClient
    from webull.data.data_client import DataClient

    s = Settings()
    client = ApiClient(str(s.webull_app_key).strip(), str(s.webull_app_secret).strip(), "us")
    client._stream_logger_set = True
    client._file_logger_set = True
    client.add_endpoint("us", PAPER_HOST)
    data = DataClient(client)

    friday = date.today() + timedelta(days=(4 - date.today().weekday()) % 7 or 7)
    try:
        _show(
            "SPY contracts",
            data.instrument.list_option_contracts(
                underlying_symbols="SPY",
                start_date=friday.isoformat(),
                end_date=friday.isoformat(),
                strike_price_gte="764",
                strike_price_lte="766",
            ),
        )
    except Exception as exc:
        print("contracts error:", str(exc)[:500])

    occ = f"SPY{friday.strftime('%y%m%d')}C00765000"
    for label, call in (
        (f"option snapshot {occ}", lambda: data.option_market_data.get_option_snapshot(occ, "US_OPTION")),
        (f"option tick {occ}", lambda: data.option_market_data.get_option_tick(occ, "US_OPTION", count="3")),
    ):
        try:
            _show(label, call())
        except Exception as exc:
            print(f"{label} error:", str(exc)[:500])


if __name__ == "__main__":
    main()
