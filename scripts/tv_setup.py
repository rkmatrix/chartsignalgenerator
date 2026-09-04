from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
SHOT = Path(r"C:\Projects\trading\PA\data\history")
SHOT.mkdir(parents=True, exist_ok=True)
PINE = Path(r"C:\Projects\trading\PA\data\history\PA_ChartTrader_SPY_2025.pine").read_text(
    encoding="utf-8"
)


def shot(page: Page, name: str) -> None:
    path = SHOT / f"tv_{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print("shot", path)


def click_first(page: Page, locator, timeout: int = 8000) -> bool:
    loc = locator.first
    try:
        loc.wait_for(state="visible", timeout=timeout)
        loc.click(timeout=timeout)
        return True
    except Exception as exc:  # noqa: BLE001
        print("click failed", exc)
        return False


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = next((x for x in ctx.pages if "tradingview.com" in (x.url or "")), ctx.pages[0])
        page.bring_to_front()
        page.set_viewport_size({"width": 1400, "height": 900})
        page.wait_for_timeout(2000)

        # Dismiss leftover dialogs
        for label in ("Accept", "I agree", "Close", "Skip", "Maybe later", "Not now"):
            btn = page.get_by_role("button", name=label)
            if btn.count():
                try:
                    btn.first.click(timeout=1500)
                    print("dismissed", label)
                except Exception:
                    pass

        # Timeframe -> 1 hour
        if click_first(page, page.get_by_role("button", name="1 day")):
            print("opened timeframe")
            page.wait_for_timeout(800)
            if not click_first(page, page.get_by_role("button", name="1 hour")):
                if not click_first(page, page.get_by_text("1 hour", exact=True)):
                    click_first(page, page.get_by_text("60 minutes", exact=False))
            page.wait_for_timeout(1500)
        shot(page, "tf")

        # Indicators dialog
        if click_first(page, page.get_by_role("button", name="Indicators, metrics, and strategies")):
            print("opened indicators")
            page.wait_for_timeout(1200)
            search = page.get_by_placeholder("Search")
            if search.count() == 0:
                search = page.locator('input[type="text"]').first
            try:
                search.first.fill("MACD")
                page.wait_for_timeout(800)
                page.get_by_text("MACD", exact=True).first.click(timeout=4000)
                print("added MACD")
            except Exception as exc:
                print("MACD add failed", exc)
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        shot(page, "macd")

        # Add EMA / VWAP / RSI the same way
        for query, pick in (
            ("Moving Average Exponential", "Moving Average Exponential"),
            ("VWAP", "VWAP"),
            ("Relative Strength Index", "Relative Strength Index"),
        ):
            if click_first(page, page.get_by_role("button", name="Indicators, metrics, and strategies")):
                page.wait_for_timeout(700)
                box = page.get_by_placeholder("Search")
                if box.count() == 0:
                    box = page.locator('input[type="text"]')
                try:
                    box.first.fill(query)
                    page.wait_for_timeout(700)
                    page.get_by_text(pick, exact=True).first.click(timeout=4000)
                    print("added", pick)
                except Exception as exc:
                    print("add failed", pick, exc)
                page.keyboard.press("Escape")
                page.wait_for_timeout(400)
        shot(page, "indicators")

        # Bar replay
        if click_first(page, page.get_by_role("button", name="Bar replay")):
            print("opened replay")
            page.wait_for_timeout(1500)
        shot(page, "replay")

        # Try jumping via Go to / date
        for name in ("Go to", "Select date", "Jump to", "Date"):
            loc = page.get_by_role("button", name=name)
            if loc.count():
                print("found", name, loc.count())
        page.keyboard.press("Control+Alt+G")
        page.wait_for_timeout(800)
        shot(page, "goto")

        print("pine chars", len(PINE))


if __name__ == "__main__":
    main()
