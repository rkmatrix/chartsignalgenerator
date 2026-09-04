from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pa.bus.events import EventBus
from pa.calendar import MarketCalendar
from pa.clock import MarketClock
from pa.config import Settings
from pa.data.flow import fixture_flow, synthetic_book
from pa.data.polygon import MarketData
from pa.domain.models import (
    AssetClass,
    OrderIntent,
    PatchRequest,
    Quote,
    RiskAction,
    Side,
    Signal,
    TradingMode,
)
from pa.execution.paper import PaperBroker
from pa.news.agent import NewsAgent
from pa.notify import post_webhook
from pa.orchestrator.resolve import SignalInbox, resolve
from pa.quant.features import pearson, returns
from pa.quant.strategy import QuantStrategy
from pa.risk.gates import KillSwitch, RiskAgent
from pa.risk.hedge import beta_hedge_note
from pa.scores import Scorekeeper
from pa.storage.journal import EventJournal

log = logging.getLogger("pa.orchestrator")


@dataclass
class RuntimeState:
    started_at: datetime
    last_error: str | None = None
    last_skip: str | None = None
    last_bar_ts: datetime | None = None
    last_step_at: datetime | None = None
    signals: list[Signal] = field(default_factory=list)
    using_fixtures: bool = True
    live_advisory: bool = False
    running: bool = True
    paused: bool = False
    last_news: list = field(default_factory=list)
    last_correlation: float | None = None
    last_briefing: str = ""
    patch_requests: list[PatchRequest] = field(default_factory=list)
    books: dict = field(default_factory=dict)

    def remember_signal(self, signal: Signal, limit: int = 50) -> None:
        self.signals.insert(0, signal)
        del self.signals[limit:]


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        clock: MarketClock,
        data: MarketData,
        strategy: QuantStrategy,
        risk: RiskAgent,
        kill: KillSwitch,
        broker: PaperBroker,
        journal: EventJournal,
        bus: EventBus,
        state: RuntimeState,
        inbox: SignalInbox | None = None,
        calendar: MarketCalendar | None = None,
        news: NewsAgent | None = None,
        scores: Scorekeeper | None = None,
    ) -> None:
        self.settings = settings
        self.clock = clock
        self.data = data
        self.strategy = strategy
        self.risk = risk
        self.kill = kill
        self.broker = broker
        self.journal = journal
        self.bus = bus
        self.state = state
        self.inbox = inbox or SignalInbox()
        self.calendar = calendar or MarketCalendar(settings)
        self.news = news or NewsAgent(settings.news_api_key)
        self.scores = scores or Scorekeeper()
        self._closes: dict[str, list[float]] = {}

    async def step(self) -> None:
        now = self.clock.now()
        self.state.last_step_at = now
        quotes: dict[str, Quote] = {}
        try:
            quotes = await self._refresh_quotes(now)
            await self._refresh_news(now)
            self._correlation(now)

            if self.kill.is_killed():
                fills = self.broker.flatten_all(quotes, now)
                self.journal.append(
                    "halt",
                    {
                        "reason": "kill_switch",
                        "source": self.kill.last_event.source.value if self.kill.last_event else "file",
                        "fills": [f.model_dump(mode="json") for f in fills],
                    },
                    now,
                )
                self.state.last_skip = "kill_switch"
                return

            managed = self.broker.manage_open(
                quotes,
                now,
                trail_pct=self.settings.trail_pct,
                scale_gain_pct=self.settings.scale_out_gain_pct,
                scale_fraction=self.settings.scale_out_fraction,
            )
            for fill in managed:
                self.journal.append("fill", {**fill.model_dump(mode="json"), "reason": fill.side.value}, now)
                await post_webhook(self.settings.webhook_url, "fill", fill.model_dump(mode="json"), now)

            self._warn_near_expiry(now)
            note = beta_hedge_note(self.broker.snapshot())
            if note:
                self.journal.append("hedge", {"note": note}, now)

            try:
                self._refresh_advisory(now)
            except Exception as exc:
                log.warning("advisory refresh: %s", exc)

            if self.state.paused:
                self.state.last_skip = "paused"
                self.journal.append("skip", {"reason": "paused"}, now)
                return

            skip = self.clock.skip_reason(now)
            macro = self.calendar.macro_blackout(now)
            if macro:
                skip = skip or f"macro:{macro.name}"
            if any(getattr(n, "risk_off", False) for n in self.state.last_news):
                skip = skip or "news_risk_off"
            self.state.last_skip = skip
            if skip:
                self.journal.append("skip", {"reason": skip}, now)
                log.info("skip: %s", skip)
                return

            pending = self.inbox.drain()
            for ticker in self.settings.tickers:
                await self._process_ticker(ticker, quotes, now, pending)

            if now.minute % 30 == 0:
                self.journal.prune(self.settings.journal_retain_days, now)
        except Exception as exc:
            self.state.last_error = str(exc)
            log.exception("step failed")
            patch = PatchRequest(
                ts=now,
                error=str(exc),
                suggestion="Inspect the traceback, add a regression test, and patch by hand. Auto-apply is disabled.",
            )
            self.state.patch_requests.append(patch)
            self.journal.append("error", {"message": str(exc)}, now)
            self.journal.append("patch_request", patch.model_dump(mode="json"), now)

    async def _refresh_news(self, now: datetime) -> None:
        try:
            items = await self.news.headlines(now, self.settings.tickers)
        except Exception:
            items = []
        self.state.last_news = items
        self.state.last_briefing = self._briefing(now, items)

    def _briefing(self, now: datetime, items: list) -> str:
        heads = "; ".join(i.headline for i in items[:3]) or "no headlines"
        return (
            f"Paper desk {now.strftime('%Y-%m-%d %H:%M %Z')}. "
            f"Equity {self.broker.equity():.0f}. Skip {self.state.last_skip or 'none'}. News: {heads}."
        )

    def _refresh_advisory(self, now: datetime) -> None:
        """Open-tape strongest signal + position advice. Never places live orders."""
        if not self.state.live_advisory:
            return
        from pa.babysitter.feed import review_positions
        from pa.open_session.clock import minutes_until_close, session_phase
        from pa.open_session.scan import OFF_TAPE, scan_open

        phase = session_phase(now, self.clock)
        tape = scan_open(self.settings, self.clock, fetch=phase not in OFF_TAPE)
        strongest = tape.get("strongest") or {}
        if strongest.get("text_buy"):
            self.state.last_briefing = strongest["text_buy"]
        elif strongest.get("text"):
            self.state.last_briefing = strongest["text"]
        elif tape.get("note"):
            self.state.last_briefing = tape["note"]
        levels = {row["ticker"]: row for row in tape.get("rows") or [] if row.get("ticker")}
        review_positions(
            self.settings,
            broker=self.broker,
            levels_by_ticker=levels,
            dosv_url=self.settings.signalvalidator_url,
            minutes_to_close=minutes_until_close(now, self.clock),
        )

    def _correlation(self, now: datetime) -> None:
        series = [self._closes.get(t, []) for t in self.settings.tickers[:2]]
        if len(series) == 2:
            r = pearson(returns(series[0]), returns(series[1]))
            self.state.last_correlation = r
            if r is not None:
                self.journal.append("correlation", {"value": r, "pair": self.settings.tickers[:2]}, now)

    async def _refresh_quotes(self, now: datetime) -> dict[str, Quote]:
        quotes: dict[str, Quote] = {}
        names = list(self.settings.tickers) + ["VIX"]
        for ticker in names:
            try:
                quote = await self.data.quote(ticker)
            except Exception as exc:
                log.warning("quote failed %s: %s", ticker, exc)
                continue
            if quote:
                quotes[ticker] = quote
                self.broker.mark(ticker, quote.last)
                self.state.books[ticker] = synthetic_book(quote)
        for pos in self.broker.snapshot():
            if pos.occ_symbol and pos.occ_symbol not in quotes:
                self.broker.mark(pos.occ_symbol, self.broker._marks.get(pos.occ_symbol, pos.avg_price))
        return quotes

    def _warn_near_expiry(self, now: datetime) -> None:
        tomorrow = now.date() + timedelta(days=1)
        for pos in self.broker.snapshot():
            if pos.expiry and pos.expiry.date() <= tomorrow:
                self.journal.append(
                    "expiry_warning",
                    {"ticker": pos.ticker, "occ": pos.occ_symbol, "expiry": pos.expiry.isoformat()},
                    now,
                )

    async def _process_ticker(
        self,
        ticker: str,
        quotes: dict[str, Quote],
        now: datetime,
        pending: list[Signal],
    ) -> None:
        bars = await self.data.bars(ticker, limit=400)
        if not bars:
            self.journal.append("skip", {"reason": "no_bars", "ticker": ticker}, now)
            return
        self.journal.store_bars(bars)
        self.state.last_bar_ts = bars[-1].ts
        self._closes.setdefault(ticker, []).append(bars[-1].close)
        self._closes[ticker] = self._closes[ticker][-400:]

        existing = self.broker.position_for(ticker)
        if existing:
            self.journal.append(
                "skip",
                {"reason": "already_in_position", "ticker": ticker, "qty": existing.qty},
                now,
            )
            return

        preliminary = self.strategy.evaluate(ticker, bars, option=None)
        option = None
        if preliminary.side != Side.FLAT:
            try:
                option = await self.data.option_for_signal(ticker, preliminary.side, now)
            except Exception as exc:
                log.warning("option snapshot failed %s: %s", ticker, exc)
        quant_signal = self.strategy.evaluate(ticker, bars, option=option)
        extra = [s for s in pending if s.ticker.upper() == ticker.upper()]
        chosen = resolve([quant_signal, *extra]) or quant_signal
        self.scores.on_signal(chosen)
        self.state.remember_signal(chosen)
        await self.bus.publish("signal", chosen)
        self.journal.append("signal", chosen.model_dump(mode="json"), now)

        if chosen.side == Side.FLAT:
            return

        if chosen.source != "quant":
            option = None

        quote = quotes.get(ticker)
        if quote is None:
            quote = await self.data.quote(ticker)
        if quote is None:
            self.journal.append("skip", {"reason": "no_quote", "ticker": ticker}, now)
            return

        flow = fixture_flow(ticker, now, chosen.side)
        self.journal.append("flow", flow.model_dump(mode="json"), now)

        if option:
            intent_side = Side.BUY
            asset = AssetClass.OPTION
            occ = option.occ_symbol
            px_quote = option.quote
            stop = round(option.quote.last * 0.5, 4) if option.quote.last > 0 else None
        else:
            intent_side = chosen.side
            asset = AssetClass.ETF
            occ = None
            px_quote = quote
            stop = round(quote.last * 0.98, 4) if chosen.side == Side.BUY else round(quote.last * 1.02, 4)

        raw_qty = self.risk.size_qty(self.broker.equity(), px_quote.mid or px_quote.last, asset)
        if raw_qty <= 0:
            raw_qty = 1
        chunks = self.broker.slice_qtys(raw_qty)
        vix_last = quotes["VIX"].last if "VIX" in quotes else None
        extra_blackout = self.calendar.earnings_tickers(now)

        for chunk in chunks:
            intent = OrderIntent(
                ticker=ticker,
                qty=chunk,
                side=intent_side,
                asset_class=asset,
                trading_mode=TradingMode.PAPER,
                occ_symbol=occ,
                limit_hint=px_quote.mid,
                stop_price=stop,
                ts=now,
                reason=";".join(chosen.reasons),
            )
            decision = self.risk.evaluate(
                intent,
                now=now,
                equity=self.broker.equity(),
                day_pnl_pct=self.broker.day_pnl_pct(),
                open_positions=self.broker.open_position_count(),
                has_position=self.broker.has_position(ticker, occ),
                quote=px_quote,
                vix_last=vix_last,
                is_entry=True,
                extra_blackout=extra_blackout,
                buying_power=self.broker.buying_power(),
                consecutive_losses=self.broker.consecutive_losses,
            )
            self.journal.append("risk", decision.model_dump(mode="json"), now)
            if decision.action == RiskAction.HALT:
                self.broker.flatten_all(quotes, now)
                return
            if decision.action != RiskAction.ALLOW or not decision.intent:
                return

            fill = self.broker.submit(decision.intent, px_quote, now)
            if occ:
                self.broker.mark(occ, fill.price)
                pos = self.broker.position_for(ticker, occ)
                if pos and option:
                    pos.expiry = option.expiry
            await self.bus.publish("fill", fill)
            self.journal.append(
                "fill",
                {
                    **fill.model_dump(mode="json"),
                    "expected_mid": fill.expected_mid,
                    "slippage": fill.slippage,
                },
                now,
            )
            await post_webhook(self.settings.webhook_url, "fill", fill.model_dump(mode="json"), now)
            self.state.last_briefing = (
                f"Filled {fill.side} {fill.qty} {fill.ticker} @ {fill.price:.2f}. "
                f"Reasons: {intent.reason[:180]}"
            )
