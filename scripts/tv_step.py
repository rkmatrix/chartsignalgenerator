"""Advance TradingView bar replay a few steps if the debug Chrome is up."""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        for _ in range(3):
            page.keyboard.press("Escape")
            page.wait_for_timeout(120)
        replay = page.locator('[data-name="replay"]')
        if replay.count():
            try:
                replay.first.click(timeout=4000, force=True)
                print("replay")
            except Exception as exc:
                print("replay skip", exc)
        page.wait_for_timeout(800)
        play = page.get_by_role("button", name="Play")
        if play.count():
            play.first.click(timeout=4000, force=True)
            print("play")
            page.wait_for_timeout(6000)
            pause = page.get_by_role("button", name="Pause")
            if pause.count():
                pause.first.click(timeout=2000, force=True)
                print("pause")
        else:
            fwd = page.get_by_role("button", name="Forward")
            if fwd.count():
                for _ in range(3):
                    fwd.first.click(force=True)
                    page.wait_for_timeout(400)
                print("forward x3")
            else:
                print("no play/forward")
        page.screenshot(path=str(OUT / "tv_step.png"), full_page=False)
        print("title", page.title())
        print("url", page.url)


if __name__ == "__main__":
    main()
