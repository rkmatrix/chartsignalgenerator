from __future__ import annotations

import asyncio
import logging
import sys
import time

import uvicorn

from pa.api.app import create_app
from pa.bus.events import EventBus
from pa.calendar import MarketCalendar
from pa.clock import MarketClock
from pa.config import get_settings
from pa.data.polygon import build_market_data
from pa.execution.factory import build_broker
from pa.llm.client import LLMClient
from pa.news.agent import NewsAgent
from pa.orchestrator.engine import Orchestrator, RuntimeState
from pa.orchestrator.resolve import SignalInbox
from pa.quant.strategy import QuantStrategy
from pa.risk.gates import KillSwitch, RiskAgent
from pa.scores import Scorekeeper
from pa.storage.journal import EventJournal
from pa.voice.chat import ChatSession

log = logging.getLogger("pa")


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            '{"ts":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}'
        )
    )
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)


def build_runtime(now_fn=None):
    settings = get_settings()
    settings.llm_prompt_file.parent.mkdir(parents=True, exist_ok=True)
    if not settings.llm_prompt_file.exists():
        settings.llm_prompt_file.write_text(
            "You are a paper-trading desk assistant. Never recommend live orders.\n",
            encoding="utf-8",
        )
    clock = MarketClock(
        now_fn=now_fn,
        open_buffer_minutes=settings.open_buffer_minutes,
        lunch_start=settings.lunch_start,
        lunch_end=settings.lunch_end,
        close_buffer_minutes=settings.close_buffer_minutes,
    )
    journal = EventJournal(settings.journal_db)
    kill = KillSwitch(settings.kill_file)
    broker = build_broker(settings)
    risk = RiskAgent(settings, kill, clock)
    strategy = QuantStrategy(settings)
    data = build_market_data(settings.polygon_api_key, clock.now, settings.tickers)
    bus = EventBus()
    state = RuntimeState(
        started_at=clock.now(),
        using_fixtures=not settings.has_polygon,
        live_advisory=True,
    )
    calendar = MarketCalendar(settings)
    news = NewsAgent(settings.news_api_key)
    inbox = SignalInbox()
    scores = Scorekeeper()
    orch = Orchestrator(
        settings=settings,
        clock=clock,
        data=data,
        strategy=strategy,
        risk=risk,
        kill=kill,
        broker=broker,
        journal=journal,
        bus=bus,
        state=state,
        inbox=inbox,
        calendar=calendar,
        news=news,
        scores=scores,
    )
    llm = LLMClient(settings.openai_api_key, settings.openai_model, settings.llm_prompt_file)
    chat = ChatSession()
    app = create_app(
        settings,
        state,
        broker,
        kill,
        journal,
        clock,
        orch=orch,
        calendar=calendar,
        llm=llm,
        chat=chat,
    )
    return settings, orch, app


async def _amain() -> None:
    settings, orch, app = build_runtime()
    config = uvicorn.Config(
        app,
        host=settings.api_host,
        port=settings.api_port,
        log_level="info",
        loop="asyncio",
    )
    server = uvicorn.Server(config)

    async def loop() -> None:
        """Run on a fixed cadence, not cadence-plus-however-long-the-work-took.

        Sleeping the full interval *after* the step made the real spacing the sum
        of the two. A scan of eighteen names takes 13-20s, so a 15s setting was
        observed running at 28s median and 49s at p90, and exits can only act as
        often as the desk looks. On 2026-09-21 MSFT sat at +33% on one poll and
        sold at -2.6% on the next, jumping clean over a +18% floor; hard stops
        across the book average -35.8% against a -25% plan. Subtracting the work
        from the sleep roughly halves that blind window for free.
        """
        log.info("paper loop started watchlist=%s fixtures=%s", settings.tickers, orch.state.using_fixtures)
        slow_steps = 0
        while orch.state.running and not server.should_exit:
            started = time.monotonic()
            await orch.step()
            elapsed = time.monotonic() - started
            # A step that outruns the interval means the desk is already looking
            # as often as it can, and the sleep is no longer what limits it.
            if elapsed > settings.poll_seconds:
                slow_steps += 1
                if slow_steps % 20 == 1:
                    log.warning(
                        "step took %.1fs, longer than the %.1fs poll — exits are "
                        "limited by scan time, not by the interval",
                        elapsed, settings.poll_seconds,
                    )
            await asyncio.sleep(max(0.0, settings.poll_seconds - elapsed))

    async def exit_loop() -> None:
        """Watch open positions on their own clock, independent of the scan.

        A position needs watching every few seconds; a new signal does not. Tying
        the two together meant exits could only act once the desk had finished
        pricing eighteen names, which is 13-20s of work. That is how MSFT sat at
        +33% on one observation and sold at -2.6% on the next.

        This deliberately passes no levels. Underlying-based exits (the ORB stop,
        the trigger, structure) need fresh bars, and bars are the expensive part;
        stale ones would fire those stops against a price that has moved. What is
        left are the premium-based exits -- the breakeven floor, the plan stop,
        take-profit, the 0DTE flatten -- and those are precisely the ones that
        were overshooting, because they are levels on a number that gaps. The
        chart-aware exits stay on the scan loop where their inputs are fresh.
        """
        from pa.open_session.clock import minutes_until_close
        from pa.open_session.ledger import watch_exits

        interval = max(1.0, float(settings.exit_poll_seconds))
        log.info("exit watcher started, every %.1fs", interval)
        while orch.state.running and not server.should_exit:
            started = time.monotonic()
            try:
                if orch.state.live_advisory and not orch.state.paused:
                    now = orch.clock.now()
                    if not orch.clock.skip_reason(now):
                        await asyncio.to_thread(
                            watch_exits,
                            settings.data_dir,
                            now,
                            minutes_to_close=minutes_until_close(now, orch.clock),
                            settings=settings,
                        )
            except Exception as exc:
                # Never let a bad quote kill the watcher: a dead exit loop is a
                # position nobody is holding the stop for.
                log.warning("exit watcher: %s", exc)
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - started)))

    await asyncio.gather(server.serve(), loop(), exit_loop())


def main() -> None:
    setup_logging()
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        log.info("shutdown")


if __name__ == "__main__":
    main()
