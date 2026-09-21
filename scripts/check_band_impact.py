"""Show which of today's quoted contracts the premium gate now admits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.ledger import (  # noqa: E402
    MAX_RISK_PER_TRADE, MIN_PREMIUM, contracts_for, risk_block,
)

cache = json.loads((ROOT / "data" / "history" / "contracts_cache.json").read_text(encoding="utf-8"))
print(f"floor ${MIN_PREMIUM:.2f}; no ceiling, sized to ${MAX_RISK_PER_TRADE:.0f} risk per trade\n")

ok = blocked = 0
for key, v in sorted(cache.items(), key=lambda kv: float(kv[1].get("entry") or 0)):
    entry = v.get("entry")
    if entry is None:
        continue
    entry = float(entry)
    why = risk_block({"entry": entry, "plan_stop_pct": 25.0})
    if why is None:
        ok += 1
        n = contracts_for(entry, 25.0)
        print(f"  PASS    {key:<28} ${entry:.2f}  x{n:<3} "
              f"cost ${n * entry * 100:>6.0f}  risk ${n * entry * 100 * 0.25:>5.0f}")
    else:
        blocked += 1
        print(f"  refuse  {key:<28} ${entry:.2f}   {why}")

print(f"\n  {ok} tradeable, {blocked} refused   (before this change: 2 tradeable, 7 refused)")
