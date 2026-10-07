from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class LiveTradingBlocked(RuntimeError):
    """Raised when TRADING_MODE is anything other than paper."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    trading_mode: str = Field(default="paper", alias="TRADING_MODE")
    polygon_api_key: str = Field(default="", alias="POLYGON_API_KEY")
    watchlist: str = Field(default="SPY,QQQ", alias="WATCHLIST")
    starting_equity: float = Field(default=100_000.0, alias="STARTING_EQUITY")
    daily_drawdown_pct: float = Field(default=2.0, alias="DAILY_DRAWDOWN_PCT")
    max_open_positions: int = Field(default=3, alias="MAX_OPEN_POSITIONS")
    max_risk_per_trade_pct: float = Field(default=1.0, alias="MAX_RISK_PER_TRADE_PCT")
    slippage_bps: float = Field(default=5.0, alias="SLIPPAGE_BPS")
    open_buffer_minutes: int = Field(default=15, alias="OPEN_BUFFER_MINUTES")
    lunch_start: str = Field(default="11:45", alias="LUNCH_START")
    lunch_end: str = Field(default="13:15", alias="LUNCH_END")
    close_buffer_minutes: int = Field(default=15, alias="CLOSE_BUFFER_MINUTES")
    earnings_blackout: str = Field(default="", alias="EARNINGS_BLACKOUT")
    vix_halt_level: float = Field(default=40.0, alias="VIX_HALT_LEVEL")
    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    kill_file: Path = Field(default=Path("data/KILL"), alias="KILL_FILE")
    journal_db: Path = Field(default=Path("data/pa.db"), alias="JOURNAL_DB")
    bar_timeframe_minutes: int = Field(default=1, alias="BAR_TIMEFRAME_MINUTES")
    # How often exits get a chance to act. A 0DTE option can spike and retrace
    # inside one interval, and a peak the desk never observes is a peak the
    # breakeven floor cannot defend: on 2026-09-14 AMZN was recorded at +12%
    # while the screen showed roughly +34%.
    poll_seconds: float = Field(default=15.0, alias="POLL_SECONDS")
    # Open positions run on their own clock, because they need watching far more
    # often than the watchlist needs scanning. The scan prices eighteen names and
    # takes 13-20s; a stop does not care. Quoting only the handful of open rows
    # is cheap enough to do every few seconds, which is what stops a floor from
    # being jumped between two observations.
    exit_poll_seconds: float = Field(default=4.0, alias="EXIT_POLL_SECONDS")
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8776, alias="API_PORT")
    ema_fast: int = Field(default=9, alias="EMA_FAST")
    ema_slow: int = Field(default=21, alias="EMA_SLOW")
    rsi_period: int = Field(default=14, alias="RSI_PERIOD")
    rsi_low: float = Field(default=30.0, alias="RSI_LOW")
    rsi_high: float = Field(default=70.0, alias="RSI_HIGH")
    vix_size_down_level: float = Field(default=25.0, alias="VIX_SIZE_DOWN_LEVEL")
    vix_size_down_mult: float = Field(default=0.5, alias="VIX_SIZE_DOWN_MULT")
    trail_pct: float = Field(default=1.5, alias="TRAIL_PCT")
    scale_out_gain_pct: float = Field(default=20.0, alias="SCALE_OUT_GAIN_PCT")
    scale_out_fraction: float = Field(default=0.5, alias="SCALE_OUT_FRACTION")
    streak_reduce_after: int = Field(default=3, alias="STREAK_REDUCE_AFTER")
    streak_size_mult: float = Field(default=0.5, alias="STREAK_SIZE_MULT")
    earnings_blackout_days: int = Field(default=1, alias="EARNINGS_BLACKOUT_DAYS")
    macro_blackout_minutes: int = Field(default=30, alias="MACRO_BLACKOUT_MINUTES")
    journal_retain_days: int = Field(default=30, alias="JOURNAL_RETAIN_DAYS")
    webhook_url: str = Field(default="", alias="WEBHOOK_URL")
    paper_broker: str = Field(default="internal", alias="PAPER_BROKER")
    finnhub_api_key: str = Field(default="", alias="FINNHUB_API_KEY")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    news_api_key: str = Field(default="", alias="NEWS_API_KEY")
    alpaca_api_key: str = Field(default="", alias="ALPACA_API_KEY")
    alpaca_api_secret: str = Field(default="", alias="ALPACA_API_SECRET")
    alpaca_base_url: str = Field(
        default="https://paper-api.alpaca.markets", alias="ALPACA_BASE_URL"
    )
    # Webull OpenAPI, paper only. Orders are sent only when PAPER_BROKER=webull
    # and both secrets are set, and only to the sandbox host. The live host is
    # rejected the same way TRADING_MODE=live is.
    webull_app_key: str = Field(default="", alias="WEBULL_APP_KEY")
    webull_app_secret: str = Field(default="", alias="WEBULL_APP_SECRET")
    webull_account_id: str = Field(default="", alias="WEBULL_ACCOUNT_ID")
    webull_host: str = Field(default="api.sandbox.webull.com", alias="WEBULL_HOST")
    high_risk_notional: float = Field(default=5000.0, alias="HIGH_RISK_NOTIONAL")
    llm_prompt_file: Path = Field(default=Path("data/prompts/operator.md"), alias="LLM_PROMPT_FILE")
    signalvalidator_url: str = Field(default="http://127.0.0.1:8799", alias="SIGNALVALIDATOR_URL")
    telegram_bot_token: str = Field(default="", alias="PA_TELEGRAM_BOT_TOKEN")
    telegram_chat_id: str = Field(default="", alias="PA_TELEGRAM_CHAT_ID")
    telegram_watch: bool = Field(default=True, alias="PA_TELEGRAM_WATCH")
    unusual_whales_api_key: str = Field(default="", alias="UNUSUAL_WHALES_API_KEY")
    # Keep generating, recording and grading signals, but band every alert so
    # none of them is mistaken for something to act on. Set after measuring the
    # entry edge across 4,162 signals and 25 sessions: the mean signed move 15
    # minutes after a signal is -0.0015% of the underlying and the direction is
    # right 48.0% of the time, while the option round trip costs 0.0796% of the
    # underlying (0.0262% on the most generous spread and leverage assumptions
    # that can be defended). No ticker, window or setup stack clears that bar in
    # both halves of a date split except PLTR and BAC/afternoon. Until something
    # does, every alert is a study note. Distinct from trading_mode, which
    # governs broker execution; this governs whether a human should act.
    study_mode: bool = Field(default=False, alias="PA_STUDY_MODE")
    # Let the online learner veto TAKE when its pessimistic estimate does not
    # clear the spread. On by default: it is the only gate trained on realised
    # P&L rather than on chart shape, and a refusal only demotes to WATCH, so a
    # vetoed signal is still printed, graded and learned from.
    bandit_gate: bool = Field(default=True, alias="PA_BANDIT_GATE")
    # Entries and take-profits come from the AlphaWave chart indicator
    # (ChartSignalGenerator / MultiConfluence_Signal_Indicator.pine) instead of
    # the desk's own setup stack. The indicator confirms on a closed 5-minute
    # bar: CALL or PUT to open, TP when the 9 EMA or MACD rolls over.
    use_alphawave: bool = Field(default=True, alias="PA_ALPHAWAVE")

    @field_validator("webull_host")
    @classmethod
    def _webull_paper_host(cls, value: str) -> str:
        host = (value or "api.sandbox.webull.com").strip().lower()
        host = host.removeprefix("https://").removeprefix("http://").split("/")[0]
        if host != "api.sandbox.webull.com":
            raise LiveTradingBlocked(
                f"WEBULL_HOST={host!r} is blocked. Paper orders go only to api.sandbox.webull.com."
            )
        return host

    @field_validator("trading_mode")
    @classmethod
    def _paper_only(cls, value: str) -> str:
        mode = (value or "").strip().lower()
        if mode != "paper":
            raise LiveTradingBlocked(
                f"TRADING_MODE={value!r} is blocked. This phase only allows paper."
            )
        return mode

    @property
    def tickers(self) -> list[str]:
        return [t.strip().upper() for t in self.watchlist.split(",") if t.strip()]

    @property
    def blackout_tickers(self) -> set[str]:
        return {t.strip().upper() for t in self.earnings_blackout.split(",") if t.strip()}

    @property
    def has_polygon(self) -> bool:
        return bool(self.polygon_api_key.strip())

    def ensure_data_dir(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.kill_file.parent.mkdir(parents=True, exist_ok=True)
        self.journal_db.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    # Import-time / first-load guard: even a stray env var cannot enable live.
    raw = os.environ.get("TRADING_MODE", "paper")
    if raw.strip().lower() != "paper":
        raise LiveTradingBlocked(
            f"TRADING_MODE={raw!r} is blocked. This phase only allows paper."
        )
    settings = Settings()
    settings.ensure_data_dir()
    return settings


def reset_settings_cache() -> None:
    get_settings.cache_clear()
