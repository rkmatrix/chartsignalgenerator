from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from pa.domain.models import JournalEvent


class EventJournal:
    """SQLite append-only journal for decisions, fills, and health events."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        # The scan loop, the exit watcher and the API all write here. With the
        # default 5s busy timeout a slow writer raised "database is locked" and
        # took the whole desk down at 01:58 on 2026-10-03; it stayed down for
        # three sessions.
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with self._connect() as conn:
            # Readers no longer block the writer, and the writer no longer
            # blocks readers.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bars (
                    ticker TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    open REAL, high REAL, low REAL, close REAL, volume REAL,
                    PRIMARY KEY (ticker, ts, timeframe)
                )
                """
            )
            conn.commit()

    def append(self, topic: str, payload: dict[str, Any], ts: datetime) -> JournalEvent:
        event = JournalEvent(topic=topic, payload=payload, ts=ts)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO events (ts, topic, payload) VALUES (?, ?, ?)",
                (ts.isoformat(), topic, json.dumps(payload, default=str)),
            )
            conn.commit()
        return event

    def recent(self, limit: int = 50, topic: str | None = None) -> list[JournalEvent]:
        sql = "SELECT ts, topic, payload FROM events"
        params: list[Any] = []
        if topic:
            sql += " WHERE topic = ?"
            params.append(topic)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        events: list[JournalEvent] = []
        for row in rows:
            events.append(
                JournalEvent(
                    topic=row["topic"],
                    payload=json.loads(row["payload"]),
                    ts=datetime.fromisoformat(row["ts"]),
                )
            )
        return events

    def store_bars(self, bars: list[Any]) -> None:
        if not bars:
            return
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT OR REPLACE INTO bars
                (ticker, ts, timeframe, open, high, low, close, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        b.ticker,
                        b.ts.isoformat(),
                        b.timeframe.value if hasattr(b.timeframe, "value") else str(b.timeframe),
                        b.open,
                        b.high,
                        b.low,
                        b.close,
                        b.volume,
                    )
                    for b in bars
                ],
            )
            conn.commit()

    def load_bars(self, ticker: str, timeframe: str = "1m", limit: int = 2000) -> list:
        from pa.domain.models import Bar, Timeframe

        sql = """
            SELECT ticker, ts, timeframe, open, high, low, close, volume
            FROM bars WHERE ticker = ? AND timeframe = ?
            ORDER BY ts ASC
        """
        with self._connect() as conn:
            rows = conn.execute(sql, (ticker.upper(), timeframe)).fetchall()
        bars: list[Bar] = []
        for row in rows[-limit:]:
            bars.append(
                Bar(
                    ticker=row["ticker"],
                    ts=datetime.fromisoformat(row["ts"]),
                    open=row["open"],
                    high=row["high"],
                    low=row["low"],
                    close=row["close"],
                    volume=row["volume"],
                    timeframe=Timeframe(row["timeframe"]),
                )
            )
        return bars

    def prune(self, retain_days: int, now: datetime) -> int:
        cutoff = now.timestamp() - retain_days * 86400
        deleted = 0
        with self._connect() as conn:
            rows = conn.execute("SELECT id, ts FROM events").fetchall()
            drop: list[int] = []
            for row in rows:
                try:
                    ts = datetime.fromisoformat(row["ts"])
                    if ts.timestamp() < cutoff:
                        drop.append(row["id"])
                except ValueError:
                    continue
            if drop:
                conn.executemany("DELETE FROM events WHERE id = ?", [(i,) for i in drop])
                conn.commit()
                deleted = len(drop)
        return deleted

    def events_on_day(self, day: str) -> list[JournalEvent]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT ts, topic, payload FROM events WHERE ts LIKE ? ORDER BY id ASC",
                (f"{day}%",),
            ).fetchall()
        return [
            JournalEvent(
                topic=row["topic"],
                payload=json.loads(row["payload"]),
                ts=datetime.fromisoformat(row["ts"]),
            )
            for row in rows
        ]
