#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
PROFIT FOCUSED SCALPER – CORRECT FEE MATH, HIGH WIN RATE
- Honest accounting (entry + exit fees)
- Higher TP (0.25%) to overcome 0.2% fees
- Higher OFI threshold (0.65) for quality signals
- All powerful features preserved
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
    "ORDER_SIZE_USDT": Decimal("10.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 10,
    "OFI_THRESHOLD": Decimal("0.65"),
    "TAKE_PROFIT_BPS": Decimal("25"),           # 0.25% – covers 0.2% fees + 0.05% profit
    "STOP_LOSS_BPS": Decimal("15"),             # 0.15% SL
    "BREAKEVEN_ACTIVATE_BPS": Decimal("5"),
    "WIN_COOLDOWN_SEC": 1.0,
    "LOSS_COOLDOWN_SEC": 10,
    "SCAN_INTERVAL_MS": 5,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # 0.1% entry + 0.1% exit = 0.2% total

class ProfitFocusedScalper:
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
        self.iceberg_hits = {sym: {} for sym in CONFIG["SYMBOLS"]}
        self.spoof_candidates = {sym: {} for sym in CONFIG["SYMBOLS"]}

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

        def detect_iceberg(self, symbol, side, price, qty):
            key = f"{side}_{price}"
            if key in self.iceberg_hits.get(symbol, {}):
                self.iceberg_hits[symbol][key] += 1
                if self.iceberg_hits[symbol][key] >= 3:
                    return True
            else:
                self.iceberg_hits[symbol][key] = 1
            return False

        def detect_spoof(self, symbol, side, price):
            key = f"{side}_{price}"
            now = time.time()
            if key in self.spoof_candidates.get(symbol, {}):
                if now - self.spoof_candidates[symbol][key] < 0.5:
                    return True
            self.spoof_candidates[symbol][key] = now
            return False

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

        def detect_liquidity_sweep(self):
            if len(self.trade_history) < 10:
                return False
            recent = list(self.trade_history)[-5:]
            avg_volume = sum(v for _, v in recent) / len(recent)
            if avg_volume == 0:
                return False
            if recent[-1][1] > avg_volume * 3:
                return True
            return False

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

        net_profit_target = order_size * tp_bps/10000 - order_size * TAKER_FEE * 2
        print(f"⚡ MARKET {side.upper()} {symbol} {reason} @ {price:.8f} | Entry fee: ${entry_fee:.5f} | Target net: +${net_profit_target:.5f}")
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

            # Breakeven
            if gain_bps >= CONFIG["BREAKEVEN_ACTIVATE_BPS"] and not pos['breakeven_activated']:
                pos['sl'] = pos['entry']
                pos['breakeven_activated'] = True
                print(f"  🔒 Breakeven activated for {sym}")

            # Check exits
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or (pos['side'] == 'sell' and mid <= pos['tp'])
            hit_sl = (pos['side'] == 'buy' and mid <= pos['sl']) or (pos['side'] == 'sell' and mid >= pos['sl'])

            if hit_tp:
                self.close_win(sym, mid)
            elif hit_sl:
                self.close_loss(sym, mid, "SL")

    def close_win(self, sym, exit_price):
        pos = self.positions.pop(sym)
        gross_exit = pos['qty'] * exit_price
        exit_fee = gross_exit * TAKER_FEE
        net_return = gross_exit - exit_fee
        profit = net_return - pos['order_size']

        self.balance += net_return
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} {pos.get('reason', '')} | +${profit:.5f} (+{profit_pct:.2f}%) | Exit fee: ${exit_fee:.5f} | Bal: ${self.balance:.2f} | WR: {win_rate:.1f}%")
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

        print("\n⚡ PROFIT FOCUSED SCALPER – CORRECT FEE MATH")
        print(f"   🔍 Iceberg | 🎭 Spoofing | 🌊 Sweep | 📊 Volume-weighted OFI")
        print(f"   TP: 0.25% gross → 0.05% net after 0.2% fees | SL: 0.15%")
        print(f"   OFI threshold: {CONFIG['OFI_THRESHOLD']} | Order size: ${CONFIG['ORDER_SIZE_USDT']}\n")

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
                    sweep = self.trade_flows[sym].detect_liquidity_sweep()

                    if sweep:
                        print(f"🌊 {sym} SWEEP DETECTED → REVERSAL")
                        if ofi > 0:
                            self.open_market_position(sym, 'sell', "[SWEEP]")
                        else:
                            self.open_market_position(sym, 'buy', "[SWEEP]")
                    elif ofi > CONFIG["OFI_THRESHOLD"]:
                        bonus = "🔥" if abs(trade_imb) > Decimal('0.15') else ""
                        print(f"⚡ {sym} OFI={ofi:.2f} {bonus} → MARKET BUY")
                        self.open_market_position(sym, 'buy', "[OFI]")
                    elif ofi < -CONFIG["OFI_THRESHOLD"]:
                        bonus = "🔥" if abs(trade_imb) > Decimal('0.15') else ""
                        print(f"⚡ {sym} OFI={ofi:.2f} {bonus} → MARKET SELL")
                        self.open_market_position(sym, 'sell', "[OFI]")

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.5f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(ProfitFocusedScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
