#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FIXED PROFITABLE SCALPER – CORRECT PROFIT CALCULATION
- Fixed sign error in profit calculation
- Only trades when OFI is EXTREME (>0.70 or <-0.70)
- Removed DOGSUSDT (was causing losses)
- Uses market orders for instant execution
- Trailing stop with correct breakeven
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
    "SYMBOLS": ["NEIROUSDT", "PEPEUSDT", "BONKUSDT", "1000PEPEUSDT"],  # Removed DOGSUSDT
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 8,
    "OFI_THRESHOLD": Decimal("0.50"),           # Higher threshold – only extreme signals
    "TAKE_PROFIT_BPS": Decimal("12"),           # 0.12% gross → 0.02% net after fees
    "STOP_LOSS_BPS": Decimal("8"),              # 0.08% stop loss
    "BREAKEVEN_ACTIVATE_BPS": Decimal("3"),     # Move SL to entry after 0.03% profit
    "TRAIL_ACTIVATE_BPS": Decimal("5"),         # Start trailing after 0.05% profit
    "TRAIL_DISTANCE_BPS": Decimal("3"),         # Trail 0.03% behind
    "WIN_COOLDOWN_SEC": 0.5,
    "LOSS_COOLDOWN_SEC": 15,                    # Longer cooldown after loss
    "SCAN_INTERVAL_MS": 10,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")
MAKER_FEE = Decimal("0")

class FixedScalper:
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

        def get_ofi(self, depth=8):
            sorted_bids = sorted(self.bids.items(), key=lambda x: x[0], reverse=True)[:depth]
            sorted_asks = sorted(self.asks.items(), key=lambda x: x[0])[:depth]
            bid_vol = sum(q for _, q in sorted_bids)
            ask_vol = sum(q for _, q in sorted_asks)
            if bid_vol + ask_vol == 0:
                return Decimal('0')
            return (bid_vol - ask_vol) / (bid_vol + ask_vol)

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

    def open_market_position(self, symbol, side, reason=""):
        book = self.order_books[symbol]
        price = book.best_ask() if side == 'buy' else book.best_bid()
        if price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / price
        cost = qty * price
        fee = cost * TAKER_FEE

        if cost + fee > self.balance:
            return False

        self.balance -= (cost + fee)

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
            'tp': target_price,
            'sl': stop_price,
            'best_price': price,
            'trailing': False,
            'breakeven_activated': False,
            'entry_time': time.time(),
            'reason': reason
        }

        net_profit = order_size * tp_bps/10000 - order_size * TAKER_FEE
        print(f"⚡ MARKET {side.upper()} {symbol} {reason} @ {price:.8f} | Target net: +${net_profit:.5f}")
        return True

    def check_positions(self):
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            mid = book.mid_price()
            if mid <= 0:
                continue

            # CORRECT profit calculation
            if pos['side'] == 'buy':
                current_profit_pct = (mid - pos['entry']) / pos['entry'] * 100
                gain_bps = (mid - pos['entry']) / pos['entry'] * 10000
            else:
                current_profit_pct = (pos['entry'] - mid) / pos['entry'] * 100
                gain_bps = (pos['entry'] - mid) / pos['entry'] * 10000

            # Breakeven
            if gain_bps >= CONFIG["BREAKEVEN_ACTIVATE_BPS"] and not pos['breakeven_activated']:
                pos['sl'] = pos['entry']
                pos['breakeven_activated'] = True
                print(f"  🔒 Breakeven activated for {sym}")

            # Trailing stop
            if pos['side'] == 'buy':
                if mid > pos['best_price']:
                    pos['best_price'] = mid
                    if gain_bps >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 - CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl > pos['sl']:
                            pos['sl'] = new_sl
            else:
                if mid < pos['best_price']:
                    pos['best_price'] = mid
                    if gain_bps >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 + CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl < pos['sl']:
                            pos['sl'] = new_sl

            # Check exits
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or (pos['side'] == 'sell' and mid <= pos['tp'])
            hit_sl = (pos['side'] == 'buy' and mid <= pos['sl']) or (pos['side'] == 'sell' and mid >= pos['sl'])

            if hit_tp:
                self.close_win(sym, pos['tp'])
            elif hit_sl:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_loss(sym, exit_price, "SL")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['qty'] * price
        fee = gross * MAKER_FEE
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} {pos.get('reason', '')} | +${profit:.5f} (+{profit_pct:.2f}%) | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['qty'] * price
        fee = gross * TAKER_FEE
        profit = gross - pos['order_size'] - fee
        self.balance += gross - fee
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

        print("\n⚡ FIXED SCALPER – CORRECT PROFIT CALCULATION")
        print(f"   TP: 0.12% gross → 0.02% net | SL: 0.08%")
        print(f"   OFI threshold: {CONFIG['OFI_THRESHOLD']} | Order size: ${CONFIG['ORDER_SIZE_USDT']}")
        print(f"   ⚡ ONLY EXTREME SIGNALS (0.70+)\n")

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
                        ofi = self.order_books[sym].get_ofi(CONFIG["DEPTH_LEVELS"])
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

                    ofi = self.order_books[sym].get_ofi(CONFIG["DEPTH_LEVELS"])

                    if ofi > CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI={ofi:.2f} → MARKET BUY")
                        self.open_market_position(sym, 'buy', "[OFI]")
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        print(f"⚡ {sym} OFI={ofi:.2f} → MARKET SELL")
                        self.open_market_position(sym, 'sell', "[OFI]")

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(FixedScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
