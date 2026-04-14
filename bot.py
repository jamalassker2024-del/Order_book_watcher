#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ULTIMATE LIMIT SCALPER – ORDER FLOW + TRADE TAPE
- Binance depth (OFI) + trade stream (taker flow)
- Limit entry (aggressive maker) + limit TP (0% fees)
- Trailing stop locks in profit, no timeout
- $5 position size, $100 balance
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
    "SYMBOLS": ["DOGSUSDT", "NEIROUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 5,
    "OFI_THRESHOLD": Decimal("0.55"),
    "TRADE_IMBALANCE_THRESHOLD": Decimal("0.3"),   # taker buy/sell ratio > 1.3 or < 0.7
    "TAKE_PROFIT_BPS": Decimal("2"),               # 0.02% pure profit (0% fee)
    "STOP_LOSS_BPS": Decimal("4"),                 # 0.04% initial stop loss (market exit)
    "TRAIL_ACTIVATE_BPS": Decimal("1"),            # start trailing after 0.01% profit
    "TRAIL_DISTANCE_BPS": Decimal("1"),            # trail 0.01% behind
    "ENTRY_TIMEOUT_SEC": 2,                        # cancel limit entry if not filled
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,
    "SCAN_INTERVAL_MS": 20,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # market exit only (stop loss)
MAKER_FEE = Decimal("0")       # limit entry & TP

class UltimateScalper:
    def __init__(self):
        self.order_books = {}     # symbol -> OrderBook
        self.trade_flows = {}     # symbol -> TradeFlow
        self.positions = {}
        self.pending_entries = {}
        self.balance = CONFIG["INITIAL_BALANCE"]
        self.total_trades = 0
        self.winning_trades = 0
        self.daily_profit = Decimal('0')
        self.daily_start = time.time()
        self.last_trade_time = {}
        self.last_trade_result = {}
        self.running = True

    # ---------- Order Book with OFI ----------
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

        def tick_size(self):
            if self.bids:
                prices = sorted(self.bids.keys())
                if len(prices) > 1:
                    return abs(prices[1] - prices[0])
            return Decimal('0.00000001')

        def get_ofi(self, depth=5):
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

    # ---------- Trade Flow (taker buy/sell volume) ----------
    class TradeFlow:
        def __init__(self, symbol):
            self.symbol = symbol
            self.buy_volume = Decimal('0')
            self.sell_volume = Decimal('0')
            self.last_reset = time.time()
            self.window_sec = 1.0    # 1 second window

        def add_trade(self, is_buyer_maker, qty, price):
            """is_buyer_maker: True if the buyer is the maker (i.e., seller aggressive) -> sell trade"""
            volume = qty * price
            if is_buyer_maker:
                self.sell_volume += volume   # aggressive seller
            else:
                self.buy_volume += volume    # aggressive buyer
            # reset window every second
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

    # ---------- WebSocket Subscriptions ----------
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
                            is_buyer_maker = data.get('m', False)   # True if buyer is maker (aggressive seller)
                            self.trade_flows[symbol].add_trade(is_buyer_maker, qty, price)
            except Exception:
                await asyncio.sleep(3)

    # ---------- Limit Order Entry ----------
    def place_entry_limit(self, symbol, side):
        book = self.order_books[symbol]
        if side == 'buy':
            base = book.best_bid()
            if base <= 0:
                return
            price = base + book.tick_size()
            # ensure we don't cross the spread (still maker)
            if price >= book.best_ask():
                price = book.best_ask() - book.tick_size()
        else:
            base = book.best_ask()
            if base <= 0:
                return
            price = base - book.tick_size()
            if price <= book.best_bid():
                price = book.best_bid() + book.tick_size()
        if price <= 0:
            return

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return

        qty = order_size / price
        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': qty,
            'order_size': order_size,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} Posted: {symbol} @ {price:.8f}")

    # ---------- Position Management with Trailing Stop ----------
    def check_fills_and_positions(self):
        now = time.time()

        # 1. Check pending entry fills
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]
            filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                     (order['side'] == 'sell' and book.best_bid() >= order['price'])
            if filled:
                self.balance -= order['order_size']
                tp_price = order['price'] * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
                sl_price = order['price'] * (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 + CONFIG["STOP_LOSS_BPS"]/10000)
                self.positions[sym] = {
                    'side': order['side'],
                    'entry': order['price'],
                    'qty': order['qty'],
                    'order_size': order['order_size'],
                    'tp': tp_price,
                    'sl': sl_price,
                    'best_price': order['price'],
                    'trailing': False,          # trailing not yet active
                    'time': now
                }
                del self.pending_entries[sym]
                print(f"✅ FILLED {sym} @ {order['price']:.8f} | TP @ {tp_price:.8f} (0% fee)")
            elif now - order['timestamp'] > CONFIG["ENTRY_TIMEOUT_SEC"]:
                del self.pending_entries[sym]
                print(f"⌛ ENTRY TIMEOUT: {sym} cancelled")

        # 2. Manage open positions with trailing stop
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            mid = book.mid_price()
            if mid <= 0:
                continue

            # Update trailing stop if price moved in our favour
            if pos['side'] == 'buy':
                if mid > pos['best_price']:
                    pos['best_price'] = mid
                    gain = (mid - pos['entry']) / pos['entry'] * 10000  # in bps
                    if gain >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 - CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl > pos['sl']:
                            pos['sl'] = new_sl
                            print(f"  🔼 Trail {sym}: SL moved to {new_sl:.8f}")
            else:  # sell
                if mid < pos['best_price']:
                    pos['best_price'] = mid
                    gain = (pos['entry'] - mid) / pos['entry'] * 10000
                    if gain >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 + CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl < pos['sl']:
                            pos['sl'] = new_sl
                            print(f"  🔽 Trail {sym}: SL moved to {new_sl:.8f}")

            # Check take-profit (limit exit, 0% fee)
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or \
                     (pos['side'] == 'sell' and mid <= pos['tp'])
            # Check stop-loss (market exit, 0.1% fee)
            hit_sl = (pos['side'] == 'buy' and mid <= pos['sl']) or \
                     (pos['side'] == 'sell' and mid >= pos['sl'])

            if hit_tp:
                self.close_win(sym, pos['tp'])
            elif hit_sl:
                exit_price = book.best_bid() if pos['side'] == 'buy' else book.best_ask()
                self.close_loss(sym, exit_price, "STOP LOSS")

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        gross = pos['qty'] * price
        fee = gross * MAKER_FEE   # 0%
        cost = pos['qty'] * pos['entry']
        profit = (gross - cost) - fee
        self.balance += gross - fee
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit
        win_rate = (self.winning_trades / self.total_trades * 100) if self.total_trades else 0
        profit_pct = (profit / pos['order_size'] * 100) if pos['order_size'] > 0 else 0
        print(f"✅ WIN {sym} (TP LIMIT) | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['qty'] * price
        fee = gross * TAKER_FEE
        cost = pos['qty'] * pos['entry']
        profit = (gross - cost) - fee
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
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    # ---------- Signal: OFI + Trade Tape ----------
    def entry_signal(self, sym):
        book = self.order_books[sym]
        ofi = book.get_ofi(CONFIG["DEPTH_LEVELS"])
        trade_imb = self.trade_flows[sym].get_imbalance()
        # Buy signal: OFI > threshold AND trade_imb > 0.3 (more buy volume)
        if ofi > CONFIG["OFI_THRESHOLD"] and trade_imb > CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'buy', ofi, trade_imb
        # Sell signal: OFI < -threshold AND trade_imb < -0.3
        if ofi < -CONFIG["OFI_THRESHOLD"] and trade_imb < -CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'sell', ofi, trade_imb
        return None, ofi, trade_imb

    # ---------- Main Loop ----------
    async def run(self):
        # Initialise order books and trade flows
        async with aiohttp.ClientSession() as session:
            for sym in CONFIG["SYMBOLS"]:
                self.order_books[sym] = self.OrderBook(sym)
                await self.order_books[sym].refresh_snapshot(session)
                self.trade_flows[sym] = self.TradeFlow(sym)
                print(f"✅ {sym} ready")
                asyncio.create_task(self.subscribe_depth(sym))
                asyncio.create_task(self.subscribe_trade(sym))

        print("\n🚀 ULTIMATE LIMIT SCALPER – OFI + TRADE TAPE")
        print(f"   TP: 0.02% pure profit | SL: 0.04% | Trail: 0.01% after 0.01% gain")
        print(f"   Position: ${CONFIG['ORDER_SIZE_USDT']} | Balance: ${self.balance}\n")

        last_ofi_print = 0
        last_refresh = time.time()
        async with aiohttp.ClientSession() as session:
            while self.running:
                now = time.time()

                if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                    for sym in CONFIG["SYMBOLS"]:
                        await self.order_books[sym].refresh_snapshot(session)
                    last_refresh = now

                if now - last_ofi_print > 3:
                    ofi_str = []
                    for sym in CONFIG["SYMBOLS"]:
                        ofi = self.order_books[sym].get_ofi(CONFIG["DEPTH_LEVELS"])
                        imb = self.trade_flows[sym].get_imbalance()
                        ofi_str.append(f"{sym}:{ofi:.2f}/{imb:.2f}")
                    print(f"🔍 OFI/IMB: {' | '.join(ofi_str)}")
                    last_ofi_print = now

                self.check_fills_and_positions()

                # Open new positions
                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions or sym in self.pending_entries:
                        continue
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue
                    action, ofi, imb = self.entry_signal(sym)
                    if action == 'buy':
                        print(f"⚡ {sym} OFI={ofi:.2f} IMB={imb:.2f} → LIMIT BUY")
                        self.place_entry_limit(sym, 'buy')
                    elif action == 'sell':
                        print(f"⚡ {sym} OFI={ofi:.2f} IMB={imb:.2f} → LIMIT SELL")
                        self.place_entry_limit(sym, 'sell')

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.4f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(UltimateScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
