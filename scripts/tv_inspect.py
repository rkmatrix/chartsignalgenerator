import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

out = Path(r"C:\Projects\trading\PA\data\history")
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    page = next((x for x in ctx.pages if "tradingview.com" in (x.url or "")), ctx.pages[0])
    page.bring_to_front()
    page.wait_for_timeout(8000)
    print("url", page.url)
    print("title", page.title())
    selectors = [
        'button:has-text("Sign in")',
        'button:has-text("Get started")',
        '[data-name="header-user-menu-button"]',
        '[aria-label*="Open user menu"]',
        'a[href*="signin"]',
        'button[aria-label="Bar replay"]',
        'button[aria-label="Indicators, metrics, and strategies"]',
        'button:has-text("Pine")',
    ]
    for sel in selectors:
        try:
            n = page.locator(sel).count()
        except Exception as exc:  # noqa: BLE001
            n = f"err {exc}"
        print(sel, n)
    shot = out / "tv_session.png"
    page.screenshot(path=str(shot), full_page=False)
    print("shot", shot)
