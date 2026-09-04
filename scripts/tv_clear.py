import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
out = Path(r"C:\Projects\trading\PA\data\history")


def main() -> None:
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        page = next(x for x in browser.contexts[0].pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        for _ in range(3):
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)
        for name in ("Got it!", "Got it", "Close"):
            loc = page.get_by_role("button", name=name)
            if loc.count():
                try:
                    loc.first.click(timeout=2000)
                    print("clicked", name)
                except Exception as exc:
                    print("skip", name, exc)
        page.wait_for_timeout(800)
        # header auth clues
        for sel in (
            'button:has-text("Sign in")',
            'span:has-text("Sign in")',
            'a:has-text("Sign in")',
            'button:has-text("Join for free")',
            '[aria-label*="user" i]',
            '[data-name="header-user-menu-button"]',
        ):
            print(sel, page.locator(sel).count())
        texts = page.locator("header, [class*='header']").all_inner_texts()[:2]
        print("header_bits", [t[:200].replace("\n", " | ") for t in texts[:1]])
        page.screenshot(path=str(out / "tv_ready.png"), full_page=False)
        print("shot", out / "tv_ready.png")


if __name__ == "__main__":
    main()
