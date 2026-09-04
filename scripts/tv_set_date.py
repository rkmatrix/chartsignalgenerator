import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
OUT = Path(r"C:\Projects\trading\PA\data\history")

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
    page.bring_to_front()

    info = page.evaluate(
        """() => {
          const root = document.querySelector('[data-name="go-to-date-dialog"]')
            || document.querySelector('[class*="dialog"]');
          const inputs = [...document.querySelectorAll('input')].map(el => ({
            type: el.type, value: el.value, aria: el.getAttribute('aria-label'),
            name: el.name, placeholder: el.placeholder,
            visible: !!(el.offsetWidth || el.offsetHeight)
          })).filter(x => x.visible).slice(0, 25);
          return {inputs, header: document.body.innerText.slice(0, 500)};
        }"""
    )
    for row in info["inputs"]:
        print(row)

    # Click calendar caption (year/month)
    captions = page.locator("text=August 2026")
    print("august caption", captions.count())

    # Fill first date-like input
    date_input = page.locator('input[value*="2026"]').first
    print("date inputs", page.locator('input[value*="2026"]').count())
    if date_input.count():
        date_input.click(force=True)
        date_input.fill("2025-01-02")
        print("filled via value=2026")
    else:
        # click the visible date field next to calendar
        page.get_by_text("2026-08-29").click(force=True)
        page.keyboard.press("Control+A")
        page.keyboard.type("2025-01-02")
        print("typed over 2026-08-29")

    page.wait_for_timeout(500)
    page.screenshot(path=str(OUT / "tv_date_typed.png"), full_page=False)

    # Confirm
    page.locator('[data-name="go-to-date-dialog"] button').filter(has_text="Go to").click(force=True)
    page.wait_for_timeout(2000)
    page.screenshot(path=str(OUT / "tv_landed.png"), full_page=False)
    print("done")
