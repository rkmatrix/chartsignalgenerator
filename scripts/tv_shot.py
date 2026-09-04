import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
    page.bring_to_front()
    for _ in range(2):
        page.keyboard.press("Escape")
        page.wait_for_timeout(150)
    print("url", page.url)
    print("title", page.title())
    page.screenshot(path=str(OUT / "tv_jan2_read.png"), full_page=False)
    print("ok")
