from __future__ import annotations

from datetime import datetime, timedelta

from pa.domain.models import (
    AssetClass,
    Fill,
    OrderIntent,
    Position,
    Quote,
    Side,
    WashSaleFlag,
)


class DuplicateOrderError(ValueError):
    pass


class PaperBroker:
    """Cash paper account. Options are long-only (multiplier 100)."""

    def __init__(self, starting_equity: float, slippage_bps: float = 5.0) -> None:
        self.starting_equity = starting_equity
        self.cash = starting_equity
        self.slippage_bps = slippage_bps
        self.positions: dict[str, Position] = {}
        self.fills: list[Fill] = []
        self._seen_ids: set[str] = set()
        self._marks: dict[str, float] = {}
        self._loss_lots: list[tuple[str, datetime, float]] = []
        self.consecutive_losses = 0
        self.consecutive_wins = 0
        self.wash_flags: list[WashSaleFlag] = []

    def _key(self, ticker: str, occ_symbol: str | None) -> str:
        return occ_symbol or ticker.upper()

    def open_position_count(self) -> int:
        return sum(1 for p in self.positions.values() if p.qty != 0)

    def has_position(self, ticker: str, occ_symbol: str | None = None) -> bool:
        return self.position_for(ticker, occ_symbol) is not None

    def position_for(self, ticker: str, occ_symbol: str | None = None) -> Position | None:
        if occ_symbol:
            pos = self.positions.get(self._key(ticker, occ_symbol))
            if pos and pos.qty != 0:
                return pos
        pos = self.positions.get(self._key(ticker, occ_symbol))
        if pos and pos.qty != 0:
            return pos
        for candidate in self.positions.values():
            if candidate.ticker == ticker.upper() and candidate.qty != 0:
                return candidate
        return None

    def mark(self, key: str, price: float) -> None:
        self._marks[key] = price
        pos = self.positions.get(key)
        if pos and pos.qty != 0:
            pos.unrealized_pnl = (price - pos.avg_price) * pos.qty * pos.multiplier
            if pos.qty > 0:
                pos.high_water = max(pos.high_water or price, price)
            else:
                pos.high_water = min(pos.high_water or price, price) if pos.high_water else price

    def equity(self) -> float:
        mtm = 0.0
        for key, pos in self.positions.items():
            if pos.qty == 0:
                continue
            px = self._marks.get(key, pos.avg_price)
            mtm += pos.qty * px * pos.multiplier
        return self.cash + mtm

    def buying_power(self) -> float:
        return max(0.0, self.cash)

    def realized_pnl(self) -> float:
        return sum(p.realized_pnl for p in self.positions.values())

    def unrealized_pnl(self) -> float:
        total = 0.0
        for key, pos in self.positions.items():
            if pos.qty == 0:
                continue
            px = self._marks.get(key, pos.avg_price)
            total += (px - pos.avg_price) * pos.qty * pos.multiplier
        return total

    def day_pnl(self) -> float:
        return self.equity() - self.starting_equity

    def day_pnl_pct(self) -> float:
        if self.starting_equity == 0:
            return 0.0
        return (self.day_pnl() / self.starting_equity) * 100.0

    def short_term_tax_estimate(self, rate: float = 0.37) -> float:
        gains = max(0.0, self.realized_pnl())
        return gains * rate

    def _fill_price(self, intent: OrderIntent, quote: Quote) -> tuple[float, float]:
        mid = quote.mid if quote.mid > 0 else quote.last
        slip = mid * (self.slippage_bps / 10_000.0)
        if intent.side == Side.BUY:
            price = (quote.ask if quote.ask > 0 else mid) + slip
        else:
            price = (quote.bid if quote.bid > 0 else mid) - slip
        return max(price, 0.01), mid

    def submit(self, intent: OrderIntent, quote: Quote, now: datetime) -> Fill:
        if intent.trading_mode.value != "paper":
            raise PermissionError("live orders are blocked")
        if intent.client_order_id in self._seen_ids:
            raise DuplicateOrderError(intent.client_order_id)
        self._seen_ids.add(intent.client_order_id)

        price, mid = self._fill_price(intent, quote)
        multiplier = 100 if intent.asset_class == AssetClass.OPTION else 1
        key = self._key(intent.ticker, intent.occ_symbol)
        signed = intent.qty if intent.side == Side.BUY else -intent.qty
        cash_delta = -signed * price * multiplier
        if self.cash + cash_delta < 0 and signed > 0:
            raise ValueError("insufficient cash")

        if signed > 0:
            self._flag_wash(intent.ticker, now)

        self.cash += cash_delta
        pos = self.positions.get(key)
        realized_this = 0.0
        if pos is None or pos.qty == 0:
            self.positions[key] = Position(
                ticker=intent.ticker.upper(),
                qty=signed,
                avg_price=price,
                asset_class=intent.asset_class,
                occ_symbol=intent.occ_symbol,
                stop_price=intent.stop_price,
                high_water=price,
                opened_at=now,
                multiplier=multiplier,
            )
        else:
            new_qty = pos.qty + signed
            if pos.qty != 0 and (pos.qty * signed < 0):
                closed = min(abs(signed), abs(pos.qty))
                realized_this = (price - pos.avg_price) * closed * (1 if pos.qty > 0 else -1) * multiplier
                pos.realized_pnl += realized_this
                if realized_this < 0:
                    self._loss_lots.append((pos.ticker, now, realized_this))
                    self.consecutive_losses += 1
                    self.consecutive_wins = 0
                elif realized_this > 0:
                    self.consecutive_wins += 1
                    self.consecutive_losses = 0
            if new_qty == 0:
                pos.qty = 0
                pos.unrealized_pnl = 0.0
            elif (pos.qty > 0 and signed > 0) or (pos.qty < 0 and signed < 0):
                total = pos.avg_price * abs(pos.qty) + price * abs(signed)
                pos.avg_price = total / abs(new_qty)
                pos.qty = new_qty
            else:
                pos.qty = new_qty
                pos.avg_price = price
                pos.high_water = price
            if intent.stop_price is not None:
                pos.stop_price = intent.stop_price

        self.mark(key, price)
        fill = Fill(
            client_order_id=intent.client_order_id,
            ticker=intent.ticker.upper(),
            qty=intent.qty,
            price=price,
            side=intent.side,
            expected_mid=mid,
            slippage=price - mid,
            ts=now,
            occ_symbol=intent.occ_symbol,
            asset_class=intent.asset_class,
        )
        self.fills.append(fill)
        return fill

    def _flag_wash(self, ticker: str, now: datetime) -> None:
        cutoff = now - timedelta(days=30)
        for lot_ticker, ts, pnl in self._loss_lots:
            if lot_ticker == ticker.upper() and ts >= cutoff and pnl < 0:
                self.wash_flags.append(
                    WashSaleFlag(
                        ticker=ticker.upper(),
                        message="possible wash sale: repurchase within 30 days of a realized loss",
                        ts=now,
                    )
                )
                break

    def flatten_all(self, quotes: dict[str, Quote], now: datetime) -> list[Fill]:
        fills: list[Fill] = []
        for key, pos in list(self.positions.items()):
            if pos.qty == 0:
                continue
            quote = quotes.get(pos.occ_symbol or "") or quotes.get(pos.ticker)
            if quote is None:
                last = self._marks.get(key, pos.avg_price)
                quote = Quote(ticker=pos.ticker, ts=now, bid=last, ask=last, last=last)
            side = Side.SELL if pos.qty > 0 else Side.BUY
            intent = OrderIntent(
                ticker=pos.ticker,
                qty=abs(pos.qty),
                side=side,
                asset_class=pos.asset_class,
                occ_symbol=pos.occ_symbol,
                ts=now,
                reason="flatten",
            )
            fills.append(self.submit(intent, quote, now))
        return fills

    def _close_qty(self, pos: Position, qty: int, quote: Quote, now: datetime, reason: str) -> Fill:
        side = Side.SELL if pos.qty > 0 else Side.BUY
        intent = OrderIntent(
            ticker=pos.ticker,
            qty=qty,
            side=side,
            asset_class=pos.asset_class,
            occ_symbol=pos.occ_symbol,
            ts=now,
            reason=reason,
        )
        return self.submit(intent, quote, now)

    def check_stops(self, quotes: dict[str, Quote], now: datetime) -> list[Fill]:
        fills: list[Fill] = []
        for pos in list(self.positions.values()):
            if pos.qty == 0 or pos.stop_price is None:
                continue
            quote = quotes.get(pos.occ_symbol or "") or quotes.get(pos.ticker)
            if quote is None:
                continue
            last = quote.last
            hit = (pos.qty > 0 and last <= pos.stop_price) or (
                pos.qty < 0 and last >= pos.stop_price
            )
            if not hit:
                continue
            fills.append(self._close_qty(pos, abs(pos.qty), quote, now, "stop"))
        return fills

    def check_scale_out(
        self,
        quotes: dict[str, Quote],
        now: datetime,
        gain_pct: float,
        fraction: float,
    ) -> list[Fill]:
        fills: list[Fill] = []
        for pos in list(self.positions.values()):
            if pos.qty == 0 or pos.scale_out_done or abs(pos.qty) < 2:
                continue
            quote = quotes.get(pos.occ_symbol or "") or quotes.get(pos.ticker)
            if quote is None:
                continue
            thresh = pos.avg_price * (1 + gain_pct / 100.0 if pos.qty > 0 else 1 - gain_pct / 100.0)
            hit = quote.last >= thresh if pos.qty > 0 else quote.last <= thresh
            if not hit:
                continue
            qty = max(1, int(abs(pos.qty) * fraction))
            qty = min(qty, abs(pos.qty) - 1)
            if qty <= 0:
                continue
            fills.append(self._close_qty(pos, qty, quote, now, "scale_out"))
            leftover = self.position_for(pos.ticker, pos.occ_symbol)
            if leftover:
                leftover.scale_out_done = True
        return fills

    def check_trails(
        self,
        quotes: dict[str, Quote],
        now: datetime,
        trail_pct: float,
    ) -> list[Fill]:
        fills: list[Fill] = []
        for key, pos in list(self.positions.items()):
            if pos.qty == 0 or trail_pct <= 0:
                continue
            quote = quotes.get(pos.occ_symbol or "") or quotes.get(pos.ticker)
            if quote is None:
                continue
            self.mark(key, quote.last)
            hw = pos.high_water or quote.last
            if pos.qty > 0:
                pos.trail_stop = hw * (1 - trail_pct / 100.0)
                if quote.last <= pos.trail_stop:
                    fills.append(self._close_qty(pos, abs(pos.qty), quote, now, "trail"))
            else:
                pos.trail_stop = hw * (1 + trail_pct / 100.0)
                if quote.last >= pos.trail_stop:
                    fills.append(self._close_qty(pos, abs(pos.qty), quote, now, "trail"))
        return fills

    def manage_open(
        self,
        quotes: dict[str, Quote],
        now: datetime,
        trail_pct: float,
        scale_gain_pct: float,
        scale_fraction: float,
    ) -> list[Fill]:
        fills: list[Fill] = []
        fills.extend(self.check_stops(quotes, now))
        fills.extend(self.check_scale_out(quotes, now, scale_gain_pct, scale_fraction))
        fills.extend(self.check_trails(quotes, now, trail_pct))
        return fills

    def slice_qtys(self, qty: int, slice_size: int = 100) -> list[int]:
        if qty <= slice_size:
            return [qty]
        chunks: list[int] = []
        left = qty
        while left > 0:
            take = min(slice_size, left)
            chunks.append(take)
            left -= take
        return chunks

    def snapshot(self) -> list[Position]:
        return [p.model_copy() for p in self.positions.values() if p.qty != 0]
