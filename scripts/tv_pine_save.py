"""Replace the open Pine script with a file and save it (updates the chart instance).

Usage: python scripts/tv_pine_save.py <path/to/script.pine>
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(sys.argv[1] if len(sys.argv) > 1 else r"C:\Projects\trading\PA\pine\PA_HuntDesk.pine")
SHOT = Path(r"C:\Projects\trading\PA\data\history")


def main() -> None:
    text = SRC.read_text(encoding="utf-8")
    print("loading", SRC.name, len(text), "chars")
    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp("http://127.0.0.1:9223")
        ctx = browser.contexts[0]
        page = next(x for x in ctx.pages if "tradingview.com" in (x.url or ""))
        page.bring_to_front()
        page.wait_for_timeout(600)

        if not page.locator(".monaco-editor textarea, textarea.inputarea").count():
            page.evaluate(
                """() => {
                    const b = document.querySelector('button[data-name="pine-dialog-button"]');
                    if (b) b.click();
                }"""
            )
            page.wait_for_timeout(3000)

        try:
            ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin="https://www.tradingview.com")
        except Exception:
            pass

        editor = page.locator(".monaco-editor textarea, textarea.inputarea").first
        editor.click(force=True, timeout=15000)
        page.keyboard.press("Control+A")
        page.wait_for_timeout(200)
        page.keyboard.press("Delete")
        page.wait_for_timeout(400)
        page.evaluate("async (t) => { await navigator.clipboard.writeText(t); }", text)
        page.keyboard.press("Control+V")
        page.wait_for_timeout(3500)
        page.keyboard.press("Control+S")
        page.wait_for_timeout(6000)

        body = page.evaluate("() => document.body.innerText") or ""
        problems = [
            line.strip()
            for line in body.splitlines()
            if any(k in line.lower() for k in ("syntax error", "error at", "undeclared", "mismatched", "cannot call"))
        ]
        print("PROBLEMS:", problems[:8] if problems else "none")
        page.screenshot(path=str(SHOT / "tv_pine_save.png"), animations="disabled", timeout=60000)
        print("shot", SHOT / "tv_pine_save.png")


if __name__ == "__main__":
    main()
