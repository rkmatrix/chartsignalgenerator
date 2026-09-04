import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
out = Path(r"C:\Projects\trading\PA\data\history")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
    page.bring_to_front()
    page.goto("https://www.tradingview.com/#signin", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)
    print("url", page.url)
    print("title", page.title())
    page.screenshot(path=str(out / "tv_signin.png"), full_page=False)
    print("shot", out / "tv_signin.png")
