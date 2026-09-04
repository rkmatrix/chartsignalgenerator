from __future__ import annotations

import re


def parse_indicator_spec(text: str) -> dict:
    """Turn a short English spec into indicator settings. No code execution."""
    raw = text.strip().lower()
    out: dict = {"raw": text.strip()}
    ema = re.findall(r"ema\s+(\d+)\s+(\d+)", raw)
    if ema:
        out["ema_fast"] = int(ema[0][0])
        out["ema_slow"] = int(ema[0][1])
    rsi = re.search(r"rsi\s+(\d+)", raw)
    if rsi:
        out["rsi_period"] = int(rsi.group(1))
    if "vwap" in raw:
        out["vwap"] = True
    if "macd" in raw:
        out["macd"] = True
    if "fib" in raw or "fibonacci" in raw:
        out["fibonacci"] = True
    return out
