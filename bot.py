#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
FINAL HYBRID SCALPER – MARKET ENTRY FOR EXTREME SIGNALS, LIMIT FOR MODERATE
- Market entry when OFI > 0.80 (instant, 0.1% fee)
- Limit entry when 0.55 < OFI ≤ 0.80 (0% fee, 0.5s timeout, fallback to market)
- Trade tape confirmation (OFI + taker imbalance)
- Take-profit limit exit (0% fee)
- Trailing stop (locks profit, no timeout)
"""

import asyncio
import json
import websockets
import aiohttp
from decimal import Decimal, getcontext
import time

getcontext().prec = 12

CONFIG = {
    "SYMBOLS": ["DOGSUSDT", "NEIROUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT"],
    "ORDER_SIZE_USDT": Decimal("5.00"),
    "INITIAL_BALANCE": Decimal("100.00"),
    "DEPTH_LEVELS": 5,
    "OFI_LIMIT_THRESHOLD": Decimal("0.55"),
    "OFI_MARKET_THRESHOLD": Decimal("0.80"),
    "TRADE_IMBALANCE_THRESHOLD": Decimal("0.3"),
    "TAKE_PROFIT_BPS": Decimal("2"),           # 0.02% pure profit (limit exit)
    "STOP_LOSS_BPS": Decimal("4"),             # 0.04% initial stop (market exit)
    "TRAIL_ACTIVATE_BPS": Decimal("1"),        # start trailing after 0.01% profit
    "TRAIL_DISTANCE_BPS": Decimal("1"),        # trail 0.01% behind
    "LIMIT_TIMEOUT_SEC": 0.5,                  # cancel limit order after 0.5s
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,
    "SCAN_INTERVAL_MS": 20,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # market entry & stop loss
MAKER_FEE = Decimal("0")       # limit entry & TP

class HybridScalper:
    def __init__(self):
        self.order_books = {}
        self.trade_flows = {}
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

    # ---------- Order Book ----------
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

    # ---------- Trade Flow ----------
    class TradeFlow:
        def __init__(self, symbol):
            self.symbol = symbol
            self.buy_volume = Decimal('0')
            self.sell_volume = Decimal('0')
            self.last_reset = time.time()
            self.window_sec = 1.0

        def add_trade(self, is_buyer_maker, qty, price):
            volume = qty * price
            if is_buyer_maker:
                self.sell_volume += volume
            else:
                self.buy_volume += volume
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
                            is_buyer_maker = data.get('m', False)
                            self.trade_flows[symbol].add_trade(is_buyer_maker, qty, price)
            except Exception:
                await asyncio.sleep(3)

    # ---------- Entry Methods ----------
    def open_market_position(self, symbol, side):
        """Market entry – instant fill, pays 0.1% fee"""
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
        trail_bps = CONFIG["TRAIL_ACTIVATE_BPS"]
        trail_dist = CONFIG["TRAIL_DISTANCE_BPS"]

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
            'entry_time': time.time()
        }

        net_profit = order_size * tp_bps/10000 - order_size * TAKER_FEE
        print(f"⚡ MARKET {side.upper()} {symbol} @ {price:.8f} | ${order_size:.2f} | Target net: +${net_profit:.4f}")
        return True

    def place_entry_limit(self, symbol, side):
        """Limit entry – 0% fee, with timeout"""
        book = self.order_books[symbol]
        if side == 'buy':
            price = book.best_bid() + book.tick_size()
            if price >= book.best_ask():
                price = book.best_ask() - book.tick_size()
        else:
            price = book.best_ask() - book.tick_size()
            if price <= book.best_bid():
                price = book.best_bid() + book.tick_size()
        if price <= 0:
            return False

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"):
                return False

        qty = order_size / price
        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': qty,
            'order_size': order_size,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} {symbol} @ {price:.8f} (0% fee)")
        return True

    def check_entries_and_positions(self):
        now = time.time()

        # 1. Check pending limit entries
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]
            filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                     (order['side'] == 'sell' and book.best_bid() >= order['price'])
            if filled:
                # Convert to position
                tp = order['price'] * (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
                sl = order['price'] * (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else order['price'] * (1 + CONFIG["STOP_LOSS_BPS"]/10000)
                self.balance -= order['order_size']
                self.positions[sym] = {
                    'side': order['side'],
                    'entry': order['price'],
                    'qty': order['qty'],
                    'order_size': order['order_size'],
                    'tp': tp,
                    'sl': sl,
                    'best_price': order['price'],
                    'trailing': False,
                    'entry_time': now
                }
                del self.pending_entries[sym]
                print(f"✅ FILLED LIMIT {sym} @ {order['price']:.8f} | TP @ {tp:.8f}")
            elif now - order['timestamp'] > CONFIG["LIMIT_TIMEOUT_SEC"]:
                # Timeout – check if signal still strong; if yes, upgrade to market
                action, ofi, imb = self.entry_signal(sym)
                if action == order['side'] and ofi > CONFIG["OFI_MARKET_THRESHOLD"]:
                    print(f"⏰ LIMIT TIMEOUT {sym} → upgrading to MARKET {order['side'].upper()}")
                    self.open_market_position(sym, order['side'])
                else:
                    print(f"⌛ LIMIT TIMEOUT {sym} cancelled (signal weak)")
                del self.pending_entries[sym]

        # 2. Manage open positions (trailing stop, TP, SL)
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            mid = book.mid_price()
            if mid <= 0:
                continue

            # Trailing stop
            if pos['side'] == 'buy':
                if mid > pos['best_price']:
                    pos['best_price'] = mid
                    gain_bps = (mid - pos['entry']) / pos['entry'] * 10000
                    if gain_bps >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 - CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl > pos['sl']:
                            pos['sl'] = new_sl
                            print(f"  🔼 Trail {sym}: SL moved to {new_sl:.8f}")
            else:
                if mid < pos['best_price']:
                    pos['best_price'] = mid
                    gain_bps = (pos['entry'] - mid) / pos['entry'] * 10000
                    if gain_bps >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                        pos['trailing'] = True
                    if pos['trailing']:
                        new_sl = mid * (1 + CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                        if new_sl < pos['sl']:
                            pos['sl'] = new_sl
                            print(f"  🔽 Trail {sym}: SL moved to {new_sl:.8f}")

            # Take profit (limit exit, 0% fee)
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or \
                     (pos['side'] == 'sell' and mid <= pos['tp'])
            # Stop loss (market exit, 0.1% fee)
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
        profit = gross - pos['order_size'] - fee
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
        print(f"{'✅' if profit>0 else '❌'} {reason} {sym} | Profit: ${profit:.4f} ({profit_pct:.2f}%) | Balance: ${self.balance:.2f} | WR: {win_rate:.1f}%")
        self.last_trade_time[sym] = time.time()

    # ---------- Combined Signal ----------
    def entry_signal(self, sym):
        book = self.order_books[sym]
        ofi = book.get_ofi(CONFIG["DEPTH_LEVELS"])
        imb = self.trade_flows[sym].get_imbalance()
        if ofi > CONFIG["OFI_LIMIT_THRESHOLD"] and imb > CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'buy', ofi, imb
        if ofi < -CONFIG["OFI_LIMIT_THRESHOLD"] and imb < -CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'sell', ofi, imb
        return None, ofi, imb

    # ---------- Main Loop ----------
    async def run(self):
        async with aiohttp.ClientSession() as session:
            for sym in CONFIG["SYMBOLS"]:
                self.order_books[sym] = self.OrderBook(sym)
                await self.order_books[sym].refresh_snapshot(session)
                self.trade_flows[sym] = self.TradeFlow(sym)
                print(f"✅ {sym} ready")
                asyncio.create_task(self.subscribe_depth(sym))
                asyncio.create_task(self.subscribe_trade(sym))

        print("\n🚀 HYBRID SCALPER – MARKET (extreme) + LIMIT (moderate)")
        print(f"   Limit threshold: {CONFIG['OFI_LIMIT_THRESHOLD']} | Market threshold: {CONFIG['OFI_MARKET_THRESHOLD']}")
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

                self.check_entries_and_positions()

                for sym in CONFIG["SYMBOLS"]:
                    if sym in self.positions or sym in self.pending_entries:
                        continue
                    cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                    if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown:
                        continue
                    action, ofi, imb = self.entry_signal(sym)
                    if action:
                        if ofi > CONFIG["OFI_MARKET_THRESHOLD"]:
                            print(f"⚡ {sym} OFI={ofi:.2f} IMB={imb:.2f} → MARKET {action.upper()} (extreme)")
                            self.open_market_position(sym, action)
                        else:
                            print(f"📝 {sym} OFI={ofi:.2f} IMB={imb:.2f} → LIMIT {action.upper()}")
                            self.place_entry_limit(sym, action)

                if now - self.daily_start >= 86400:
                    print(f"\n💰 DAILY PROFIT: +${self.daily_profit:.4f} | Balance: ${self.balance:.2f}\n")
                    self.daily_profit = Decimal('0')
                    self.daily_start = now

                await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(HybridScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")
