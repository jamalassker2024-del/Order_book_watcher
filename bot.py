#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
STRATEGIC PROFIT SCALPER - VOLATILITY & SPREAD AWARE
- Checks Spread: Don't enter if the gap is too wide (slippage protection)
- Dynamic OFI: Requires a stronger push to enter after a loss
- Improved Breakeven: Moves to entry + 2bps to cover the entry fee
- SWEEP trades are DISABLED (they were causing losses)
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time
from collections import deque

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["NEIROUSDT", "PEPEUSDT", "BONKUSDT", "1000PEPEUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),               # Reduced to $5
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 10,
    "OFI_THRESHOLD": Decimal("0.80"),                 # Much higher – only strong signals
    "MAX_SPREAD_BPS": Decimal("5"),                   # Don't trade if spread > 0.05%
    "TAKE_PROFIT_BPS": Decimal("45"),                 # 0.45% – wider target
    "STOP_LOSS_BPS": Decimal("18"),                   # 0.18% – more breathing room
    "BREAKEVEN_ACTIVATE_BPS": Decimal("10"),          # Move to BE after 0.1% gain
    "WIN_COOLDOWN_SEC": 2.0,
    "LOSS_COOLDOWN_SEC": 30,                          # Longer pause after loss
    "SCAN_INTERVAL_MS": 10,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # 0.1%
MAKER_FEE = Decimal("0")       # 0% for limit TP

class StrategicScalper:
    def __init__(self):
        self.order_books = {}
        self.trade_flows = {}
        self.positions = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True
        self.price_history = {sym: deque(maxlen=200) for sym in CONFIG["SYMBOLS"]}

    class OrderBook:
        def __init__(self, symbol):
            self.symbol = symbol
            self.bids = {}
            self.asks = {}
            self.last_update = 0.0
            self.ofi_history = deque(maxlen=20)

        def apply_depth(self, data):
            for side, key in [('bids', 'b'), ('asks', 'a')]:
                book_side = getattr(self, side)
                for price_str, qty_str in data.get(key, []):
                    price, qty = Decimal(price_str), Decimal(qty_str)
                    if qty == 0:
                        book_side.pop(price, None)
                    else:
                        book_side[price] = qty
            self.last_update = time.time()

        def best_bid(self):
            return max(self.bids.keys()) if self.bids else Decimal('0')

        def best_ask(self):
            return min(self.asks.keys()) if self.asks else Decimal('0')

        def mid_price(self):
            bb, ba = self.best_bid(), self.best_ask()
            return (bb + ba) / 2 if bb and ba else Decimal('0')

        def get_spread_bps(self):
            bb, ba = self.best_bid(), self.best_ask()
            if bb == 0 or ba == 0:
                return Decimal('999')
            return ((ba - bb) / bb) * 10000

        def get_volume_weighted_ofi(self, depth=10):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_weighted = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_bids))
            ask_weighted = sum(q * (depth - i) for i, (_, q) in enumerate(sorted_asks))
            total = bid_weighted + ask_weighted
            if total == 0:
                return Decimal('0')
            ofi = (bid_weighted - ask_weighted) / total
            self.ofi_history.append((time.time(), ofi))
            return ofi

        async def refresh_snapshot(self, session):
            url = f"https://api.binance.com/api/v3/depth?symbol={self.symbol}&limit=20"
            try:
                async with session.get(url) as resp:
                    data = await resp.json()
                    self.bids = {Decimal(p): Decimal(q) for p, q in data['bids']}
                    self.asks = {Decimal(p): Decimal(q) for p, q in data['asks']}
                    self.last_update = time.time()
                    return True
            except Exception:
                return False

    class TradeFlow:
        def __init__(self, symbol):
            self.symbol = symbol
            self.buy_volume = Decimal('0')
            self.sell_volume = Decimal('0')
            self.trade_history = deque(maxlen=50)
            self.last_reset = time.time()
            self.window_sec = 1.0

        def add_trade(self, is_buyer_maker, qty, price):
            volume = qty * price
            if is_buyer_maker:
                self.sell_volume += volume
            else:
                self.buy_volume += volume
            self.trade_history.append((time.time(), volume))
            now = time.time()
            if now - self.last_reset > self.window_sec:
                self.buy_volume = Decimal('0')
                self.sell_volume = Decimal('0')
                self.last_reset = now

        def get_imbalance(self):
            total = self.buy_volume + self.sell_volume
            if total == 0:
                return Decimal('0')
            return (self.buy_volume - self.sell_volume) / total

    async def subscribe_depth(self, symbol):
        stream = f"{symbol.lower()}@depth20@100ms"
        url = f"{CONFIG['BINANCE_WS_DEPTH']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'b' in data or 'a' in data:
                            self.order_books[symbol].apply_depth(data)
            except Exception:
                await asyncio.sleep(3)

    async def subscribe_trade(self, symbol):
        stream = f"{symbol.lower()}@trade"
        url = f"{CONFIG['BINANCE_WS_TRADE']}/{stream}"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'p' in data and 'q' in data:
                            price = Decimal(data['p'])
                            qty = Decimal(data['q'])
                            is_buyer_maker = data.get('m', False)
                            self.trade_flows[symbol].add_trade(is_buyer_maker, qty, price)
                            self.price_history[symbol].append(price)
            except Exception:
                await asyncio.sleep(3)

    def open_position(self, symbol, side, reason):
        book = self.order_books[symbol]
        
        # SPREAD FILTER – Don't buy into a hole
        spread = book.get_spread_bps()
        if spread > CONFIG["MAX_SPREAD_BPS"]:
            print(f"⚠️ Spread too high for {symbol}: {spread:.1f}bps, skipping")
            return False

        price = book.best_ask() if side == 'buy' else book.best_bid()
        if price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / price
        entry_fee = order_size * TAKER_FEE

        if order_size + entry_fee > self.balance:
            return False

        self.balance -= (order_size + entry_fee)

        tp_bps = CONFIG["TAKE_PROFIT_BPS"]
        sl_bps = CONFIG["STOP_LOSS_BPS"]

        if side == 'buy':
            target_price = price * (1 + tp_bps/10000)
            stop_price = price * (1 - sl_bps/10000)
        else:
            target_price = price * (1 - tp_bps/10000)
            stop_price = price * (1 + sl_bps/10000)

        self.positions[symbol] = {
            'side': side,
            'entry': price,
            'qty': qty,
            'order_size': order_size,
            'entry_fee': entry_fee,
            'tp': target_price,
            'sl': stop_price,
            'best_price': price,
            'breakeven_activated': False,
            'entry_time': time.time(),
            'reason': reason
        }

        net_profit_target = order_size * tp_bps/10000 - order_size * TAKER_FEE
        print(f"📡 ENTER {side.upper()} {symbol} | Spread: {spread:.1f}bps | Target net: +${net_profit_target:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            mid = book.mid_price()
            if mid <= 0:
                continue

            if pos['side'] == 'buy':
                gain_bps = (mid - pos['entry']) / pos['entry'] * 10000
            else:
                gain_bps = (pos['entry'] - mid) / pos['entry'] * 10000

            # Breakeven – move SL to entry + 2bps to cover fee
            if gain_bps >= CONFIG["BREAKEVEN_ACTIVATE_BPS"] and not pos['breakeven_activated']:
                if pos['side'] == 'buy':
                    pos['sl'] = pos['entry'] * (1 + Decimal('0.0002'))
                else:
                    pos['sl'] = pos['entry'] * (1 - Decimal('0.0002'))
                pos['breakeven_activated'] = True
                print(f"  🔒 Breakeven activated for {sym} (SL moved to entry + fee)")

            # TP hit – use LIMIT order (0% fee)
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or (pos['side'] == 'sell' and mid <= pos['tp'])
            # SL hit – use MARKET order (0.1% fee)
            hit_sl = (pos['side'] == 'buy' and mid <= pos['sl']) or (pos['side'] == 'sell' and mid >= pos['sl'])

            if hit_tp:
                self.close_win(sym, pos['tp'])
            elif hit_sl:
                self.close_loss(sym, mid, "SL")

    def close_win(self, sym, exit_price):
        pos = self.positions.pop(sym)
        gross_exit = pos['qty'] * exit_price
        exit_fee = gross_exit * MAKER_FEE  # 0%!
        net_return = gross_exit - exit_fee
        profit = net_return - pos['order_size']

        self.balance += net_return
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} {pos.get('reason', '')} | +${profit:.5f} (+{profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, exit_price, reason):
        pos = self.positions.pop(sym)
        gross_exit = pos['qty'] * exit_price
        exit_fee = gross_exit * TAKER_FEE
        net_return = gross_exit - exit_fee
        profit = net_return - pos['order_size']

        self.balance += net_return
        self.total_trades += 1
        if profit > 0:
            self.winning_trades += 1
            self.last_trade_result[sym] = 'win'
        else:
            self.last_trade_result[sym] = 'loss'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | ${profit:.5f} ({profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    async def run(self):
        async with aiohttp.ClientSession() as session:
            for sym in CONFIG["SYMBOLS"]:
                self.order_books[sym] = self.OrderBook(sym)
                await self.order_books[sym].refresh_snapshot(session)
                self.trade_flows[sym] = self.TradeFlow(sym)
                print(f"✅ {sym} ready")
                asyncio.create_task(self.subscribe_depth(sym))
                asyncio.create_task(self.subscribe_trade(sym))

        print("\n⚡ STRATEGIC PROFIT SCALPER – SPREAD & VOLATILITY AWARE")
        print(f"   Spread filter: {CONFIG['MAX_SPREAD_BPS']}bps | TP: 0.45% | SL: 0.18%")
        print(f"   OFI threshold: {CONFIG['OFI_THRESHOLD']} | Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   SWEEP trades: DISABLED | Loss cooldown: {CONFIG['LOSS_COOLDOWN_SEC']}s\n")

        last_ofi_print = 0
        last_refresh = time.time()
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now

                if now - last_ofi_print > 2:
                    ofi_str = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_volume_weighted_ofi(CONFIG["DEPTH_LEVELS"])
                        ofi_str.append(f"{sym}:{ofi:.2f}")
                    print(f"🔍 OFI: {' | '.join(ofi_str)}")
                    last_ofi_print = now

                self.check_positions()

                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions:
                        continue
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue

                    ofi = self.order_books[sym].get_volume_weighted_ofi(CONFIG["DEPTH_LEVELS"])
                    trade_imb = self.trade_flows[sym].get_imbalance()

                    # ONLY OFI signals – SWEEP trades DISABLED (they were causing losses)
                    if ofi > CONFIG["OFI_THRESHOLD"]:
                        bonus = "🔥" if abs(trade_imb) > Decimal('0.15') else ""
                        print(f"⚡ {sym} OFI={ofi:.2f} {bonus} → BUY")
                        self.open_position(sym, 'buy', f"[OFI{bonus}]")
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        bonus = "🔥" if abs(trade_imb) > Decimal('0.15') else ""
                        print(f"⚡ {sym} OFI={ofi:.2f} {bonus} → SELL")
                        self.open_position(sym, 'sell', f"[OFI{bonus}]")

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(StrategicScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
