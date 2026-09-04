import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
    page.bring_to_front()

    for _ in range(5):
        page.keyboard.press("Escape")
        page.wait_for_timeout(120)
    got = page.get_by_role("button", name="Got it!")
    if got.count():
        got.first.click(force=True, timeout=2000)
        print("got it")

    names = page.evaluate(
        """() => [...document.querySelectorAll('button,[role="button"]')]
          .map(el => (el.getAttribute('aria-label') || el.innerText || '').trim().replace(/\\s+/g,' '))
          .filter(t => t && /go to|replay|date|calendar|select bar|interval/i.test(t))
          .slice(0, 40)"""
    )
    print("buttons", names)

    attrs = page.evaluate(
        """() => [...document.querySelectorAll('[data-name]')]
          .map(el => el.getAttribute('data-name'))
          .filter(t => t && /replay|goto|go-to|date|interval/i.test(t))
          .slice(0, 40)"""
    )
    print("data-name", attrs)

    page.keyboard.press("Alt+g")
    page.wait_for_timeout(800)
    page.screenshot(path=str(OUT / "tv_altg.png"), full_page=False)
    print("altg shot")
