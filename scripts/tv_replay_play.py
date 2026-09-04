import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
    page.bring_to_front()
    for _ in range(3):
        page.keyboard.press("Escape")
        page.wait_for_timeout(100)

    # Close sale
    page.evaluate(
        """() => {
          for (const el of document.querySelectorAll('button,[aria-label]')) {
            const t = (el.getAttribute('aria-label') || el.textContent || '').toLowerCase();
            if (t.includes('close') && el.closest('[class*="toast"], [class*="promo"], [class*="offer"]')) {
              el.click();
            }
          }
        }"""
    )

    # Enter replay if needed, then play a few bars
    page.locator('[data-name="replay"]').first.click(force=True)
    page.wait_for_timeout(1000)
    play = page.get_by_role("button", name="Play")
    if play.count():
        play.first.click(force=True)
        print("play")
        page.wait_for_timeout(4000)
        pause = page.get_by_role("button", name="Pause")
        if pause.count():
            pause.first.click(force=True)
            print("pause")
    else:
        print("no play button", page.evaluate("""() => [...document.querySelectorAll('button')]
          .map(b => (b.getAttribute('aria-label')||b.innerText||'').trim())
          .filter(t => /play|pause|select bar|speed/i.test(t)).slice(0,20)"""))

    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "tv_jan2_read.png"), full_page=False)
    print("shot", OUT / "tv_jan2_read.png")
    print("title", page.title())
