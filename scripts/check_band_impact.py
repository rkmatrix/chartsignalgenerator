"""Show which of today's quoted contracts the premium gate now admits."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pa.open_session.ledger import MAX_PREMIUM, MIN_PREMIUM, risk_block  # noqa: E402

cache = json.loads((ROOT / "data" / "history" / "contracts_cache.json").read_text(encoding="utf-8"))
print(f"premium gate now ${MIN_PREMIUM:.2f} to ${MAX_PREMIUM:.2f}\n")

ok = blocked = 0
for key, v in sorted(cache.items(), key=lambda kv: float(kv[1].get("entry") or 0)):
    entry = v.get("entry")
    if entry is None:
        continue
    why = risk_block({"entry": float(entry), "plan_stop_pct": 25.0})
    if why is None:
        ok += 1
        print(f"  PASS    {key:<28} ${float(entry):.2f}")
    else:
        blocked += 1
        print(f"  refuse  {key:<28} ${float(entry):.2f}   {why}")

print(f"\n  {ok} tradeable, {blocked} refused   (before this change: 2 tradeable, 7 refused)")
