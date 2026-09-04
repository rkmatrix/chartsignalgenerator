# PA — Paper-First Trading Agent

Single-process paper trading loop: data → quant → risk → paper broker → SQLite journal. **Live orders are structurally blocked.**

Architecture, phases, and the “won’t build as written” list: [Master_Trading_Agent_Architecture.md](Master_Trading_Agent_Architecture.md).

Keys you may add later (none required): [OPERATOR_SECRETS.md](OPERATOR_SECRETS.md).

## Setup

Python 3.11+. From this directory:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
Copy-Item .env.example .env
```

## Run

```powershell
.\scripts\run_paper.ps1
```

Desk: [http://127.0.0.1:8776/](http://127.0.0.1:8776/). On Wi-Fi/LAN use `http://<this-PC-IP>:8776/` (port 8776 is PA; 8765 is Precision, 8766 is ChopTrading).

Replay a stored session (after bars have been journaled) or fixtures via:

```powershell
.\scripts\replay.ps1
```

**2025 chart trader** (look at hourly SPY like a day trader, guess, forward-test, retune — no look-ahead):

```powershell
python -m pa.chart_trader
```

Opens results under `data/history/`. TradingView chart: https://www.tradingview.com/chart/?symbol=AMEX:SPY

```powershell
.\scripts\chart_trader.ps1
```

**Monday morning board** (high-volume tickers, honest OOS accuracy, option-style cards — paper only):

```powershell
python -m pa.chart_trader.scan
```

or `.\scripts\autopilot.ps1`. Desk section **Monday morning** reads `data/history/monday_signals.json`.

Docker: `docker compose up --build` (still `TRADING_MODE=paper`).

## Tests

```powershell
pytest
```

## What is implemented

P0 loop plus P1/P2 modules: replay, walk-forward (report only), VIX size-down, RSI divergence, S/R, Fibonacci, trailing stops, scale-out, observational scores, economic/earnings calendar, TradingView webhook (risk still mandatory), richer desk, news fixtures, silent chat, optional LLM, synthetic L2/flow, wash-sale flag, tax estimate, hedge note, Docker, GitHub Actions.

**Not built as originally specified:** Robinhood UI automation, sub-ms routing, live orders, wake-word microphone, sentiment TTS, Twitter firehose, millisecond EDGAR, auto-applying code patches, multi-account mirroring.
