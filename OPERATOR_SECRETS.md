# Secrets the operator must supply

The system runs **without any of these**. Empty keys → fixtures / local heuristics / internal paper broker.

Fill `.env` when you want live market data or optional vendors. Never commit `.env`.

| Variable | Needed for | If missing |
|----------|------------|------------|
| `POLYGON_API_KEY` | Real bars, quotes, options snapshots | Deterministic fixture tape |
| `FINNHUB_API_KEY` | Live earnings dates | Built-in 2026 fixture calendar |
| `NEWS_API_KEY` | Headlines from NewsAPI | Fixture headlines + lexicon sentiment |
| `OPENAI_API_KEY` | Consultative chat via OpenAI | Local heuristic replies |
| `WEBHOOK_URL` | Fill alerts / “mobile push” stand-in | Journal only |
| `ALPACA_API_KEY` + `ALPACA_API_SECRET` | Future Alpaca **paper** adapter | Internal `PaperBroker` (default) |

`TRADING_MODE` must stay `paper`. Live broker URLs are rejected.

Recommended until you are awake: leave every key empty and run `.\scripts\run_paper.ps1`.
