from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, TimeoutError as PwTimeout, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")
PINE = Path(r"C:\Projects\trading\PA\data\history\PA_ChartTrader_SPY_2025.pine").read_text(
    encoding="utf-8"
)
CHART = "https://www.tradingview.com/chart/?symbol=AMEX:SPY&interval=60"


def shot(page: Page, name: str) -> None:
    path = OUT / f"tv_{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print("shot", path)


def dismiss(page: Page) -> None:
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
    for name in ("Got it!", "Got it", "Accept", "I agree", "Skip", "Maybe later", "Not now"):
        loc = page.get_by_role("button", name=name)
        if loc.count():
            try:
                loc.first.click(timeout=1200)
                print("dismissed", name)
            except Exception:
                pass


def click_named(page: Page, name: str, timeout: int = 6000) -> bool:
    loc = page.get_by_role("button", name=name)
    try:
        loc.first.click(timeout=timeout)
        print("clicked", name)
        return True
    except Exception as exc:
        print("miss", name, exc)
        return False


def add_indicator(page: Page, query: str, pick: str) -> None:
    if not click_named(page, "Indicators, metrics, and strategies"):
        return
    page.wait_for_timeout(600)
    box = page.get_by_placeholder("Search")
    if box.count() == 0:
        box = page.locator('input[type="text"]')
    try:
        box.first.fill(query)
        page.wait_for_timeout(700)
        page.get_by_text(pick, exact=True).first.click(timeout=4000)
        print("added", pick)
    except Exception as exc:
        print("indicator fail", pick, exc)
    page.keyboard.press("Escape")
    page.wait_for_timeout(400)


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        page.set_viewport_size({"width": 1440, "height": 900})
        page.goto(CHART, wait_until="domcontentloaded", timeout=90000)
        page.wait_for_timeout(5000)
        dismiss(page)
        shot(page, "chart")

        add_indicator(page, "Moving Average Exponential", "Moving Average Exponential")
        add_indicator(page, "Moving Average Exponential", "Moving Average Exponential")
        add_indicator(page, "Volume Weighted Average Price", "VWAP")
        add_indicator(page, "MACD", "MACD")
        add_indicator(page, "Relative Strength Index", "Relative Strength Index")
        dismiss(page)
        shot(page, "inds")

        # Pine editor via bottom dock
        pine_btn = page.get_by_role("button", name="Pine Editor")
        if pine_btn.count() == 0:
            pine_btn = page.get_by_text("Pine Editor", exact=True)
        try:
            pine_btn.first.click(timeout=4000)
            print("opened pine editor")
            page.wait_for_timeout(1500)
            page.keyboard.press("Control+A")
            page.wait_for_timeout(200)
            page.keyboard.insert_text(PINE)
            page.wait_for_timeout(400)
            for name in ("Add to chart", "Save", "Save script"):
                loc = page.get_by_role("button", name=name)
                if loc.count():
                    try:
                        loc.first.click(timeout=2500)
                        print("pine", name)
                    except Exception:
                        pass
            page.wait_for_timeout(800)
        except Exception as exc:
            print("pine skip", exc)
        shot(page, "pine")

        if click_named(page, "Bar replay"):
            page.wait_for_timeout(1500)
        shot(page, "replay")

        # Go to date
        if click_named(page, "Go to"):
            page.wait_for_timeout(600)
            for sel in ('input[type="text"]', "input"):
                loc = page.locator(sel)
                if loc.count():
                    try:
                        loc.last.fill("2025-01-02")
                        page.keyboard.press("Enter")
                        print("typed date")
                        break
                    except Exception:
                        continue
        page.wait_for_timeout(1500)
        shot(page, "jan2025")


if __name__ == "__main__":
    main()
