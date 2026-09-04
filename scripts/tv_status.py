import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
out = Path(r"C:\Projects\trading\PA\data\history")
out.mkdir(parents=True, exist_ok=True)

with sync_playwright() as p:
    browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
    ctx = browser.contexts[0]
    pages = list(ctx.pages)
    print("pages", len(pages))
    for page in pages:
        print("url", page.url)
        print("title", page.title())
    page = next((x for x in pages if "tradingview.com" in (x.url or "")), pages[0])
    page.bring_to_front()
    page.screenshot(path=str(out / "tv_after_login.png"), full_page=False)
    print("shot", out / "tv_after_login.png")
    html = page.content().lower()
    print("has_signin_heading", ">sign in<" in html or "sign in</h" in html)
    print("has_chart", "/chart" in page.url)
    print("has_user_menu", "header-user-menu" in html or "user-menu" in html)
