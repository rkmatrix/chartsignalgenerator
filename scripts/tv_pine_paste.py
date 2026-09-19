"""Paste a .pine file into the TradingView Pine editor and add it to the chart.

Needs Chrome running with --remote-debugging-port=9223 and a signed-in session.
Usage: python scripts/tv_pine_paste.py [path/to/script.pine]
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import Page, sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PINE = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Projects\trading\PA\pine\PA_HuntDesk.pine")
SHOT = Path(r"C:\Projects\trading\PA\data\history")


def shot(page: Page, name: str) -> Path:
    path = SHOT / f"tv_{name}.png"
    page.screenshot(path=str(path))
    print("shot", path)
    return path


def click_any(page: Page, selectors: list[str], timeout: int = 4000) -> bool:
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if loc.count() and loc.is_visible():
                loc.click(timeout=timeout)
                print("clicked", sel)
                return True
        except Exception as exc:  # noqa: BLE001
            print("miss", sel, type(exc).__name__)
    return False


def main() -> None:
    source = PINE.read_text(encoding="utf-8")
    print("pine chars", len(source))
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = next(x for x in ctx.pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        page.wait_for_timeout(1200)

        # TradingView throws modals (ad blocker, upsell) that swallow clicks.
        for _ in range(3):
            closed = click_any(
                page,
                [
                    'button[data-name="close"]',
                    'button[aria-label="Close"]',
                    'span[data-name="close"]',
                    'div[data-dialog-name] button[data-name="close"]',
                ],
                timeout=2000,
            )
            if not closed:
                break
            page.wait_for_timeout(800)
        page.keyboard.press("Escape")
        page.wait_for_timeout(600)
        shot(page, "pine_predismiss")

        opened = click_any(
            page,
            [
                'button[data-name="pine-dialog-button"]',
                'button[aria-label="Pine"]',
                'button[data-name="scripteditor"]',
                'button:has-text("Pine Editor")',
                'text="Pine Editor"',
            ],
        )
        print("editor opened", opened)
        page.wait_for_timeout(2500)
        shot(page, "pine_open")

        editor = page.locator(".monaco-editor textarea, textarea.inputarea").first
        try:
            editor.wait_for(state="attached", timeout=15000)
            editor.click(force=True, timeout=8000)
        except Exception as exc:  # noqa: BLE001
            print("editor focus failed", exc)
            shot(page, "pine_nofocus")
            return

        page.keyboard.press("Control+A")
        page.wait_for_timeout(250)
        page.keyboard.press("Delete")
        page.wait_for_timeout(250)
        page.keyboard.insert_text(source)
        page.wait_for_timeout(2500)
        shot(page, "pine_pasted")

        added = click_any(
            page,
            [
                'button:has-text("Add to chart")',
                'button[data-name="add-script-to-chart"]',
                'div[data-name="add-script-to-chart"]',
            ],
            timeout=8000,
        )
        print("add to chart", added)
        page.wait_for_timeout(6000)
        shot(page, "pine_result")

        for sel in ('[class*="errorText"]', '[class*="error"]', '[data-name="console"]'):
            loc = page.locator(sel)
            n = loc.count()
            if n:
                for i in range(min(n, 6)):
                    try:
                        txt = (loc.nth(i).inner_text() or "").strip()
                    except Exception:
                        continue
                    if txt:
                        print("CONSOLE", sel, "|", txt[:400])


if __name__ == "__main__":
    main()
