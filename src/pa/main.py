from __future__ import annotations

import asyncio
import logging
import sys

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
        log.info("paper loop started watchlist=%s fixtures=%s", settings.tickers, orch.state.using_fixtures)
        while orch.state.running and not server.should_exit:
            await orch.step()
            await asyncio.sleep(settings.poll_seconds)

    await asyncio.gather(server.serve(), loop())


def main() -> None:
    setup_logging()
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        log.info("shutdown")


if __name__ == "__main__":
    main()
