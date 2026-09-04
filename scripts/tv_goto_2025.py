from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")


def shot(page: Page, name: str) -> None:
    path = OUT / f"tv_{name}.png"
    page.screenshot(path=str(path), full_page=False)
    print("shot", path)


def force_click(page: Page, locator, label: str) -> bool:
    try:
        locator.first.click(timeout=4000, force=True)
        print("clicked", label)
        return True
    except Exception as exc:
        print("miss", label, exc)
        return False


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        page.set_viewport_size({"width": 1440, "height": 900})

        # Close sale / overlays
        for _ in range(4):
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
        for name in ("Got it!", "Close", "Dismiss"):
            loc = page.get_by_role("button", name=name)
            if loc.count():
                force_click(page, loc, name)

        sale = page.get_by_text("End of Summer sale")
        if sale.count():
            page.keyboard.press("Escape")

        # Ensure replay
        replay = page.get_by_role("button", name="Bar replay")
        if replay.count():
            force_click(page, replay, "Bar replay")
            page.wait_for_timeout(800)

        if not page.get_by_role("heading", name="Go to").count():
            force_click(page, page.get_by_role("button", name="Go to"), "Go to")
            page.wait_for_timeout(600)

        shot(page, "goto_open")

        # Date tab then fill YYYY-MM-DD
        date_tab = page.get_by_role("tab", name="Date")
        if date_tab.count():
            force_click(page, date_tab, "Date tab")

        # Prefer the visible date input inside the dialog
        dialog = page.get_by_role("dialog")
        box = dialog.locator("input").first
        try:
            box.click(timeout=3000, force=True)
            box.fill("")
            box.fill("2025-01-02")
            print("filled 2025-01-02")
        except Exception as exc:
            print("fill fail", exc)
            page.keyboard.press("Control+A")
            page.keyboard.type("2025-01-02")
        page.wait_for_timeout(400)
        shot(page, "goto_filled")

        go = dialog.get_by_role("button", name="Go to")
        if not go.count():
            go = page.get_by_role("button", name="Go to").nth(1)
        force_click(page, go, "Go to confirm")
        page.wait_for_timeout(2500)
        shot(page, "at_2025")

        # If still looking at 2026, click calendar year
        print("url", page.url)
        print("title", page.title())


if __name__ == "__main__":
    main()
