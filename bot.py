Here is the complete, integrated script. I have removed the rigid ENTRY_TIMEOUT_SEC and replaced it with a **Dynamic Order Chasing** engine.
This version actively manages your limit orders. If the price moves away but the signal (OFI + Trade Flow) is still strong, the bot will update its price to stay at the front of the book rather than just canceling. It only cancels if the signal disappears, preventing "bad" entries.
```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ULTIMATE DYNAMIC SCALPER – OFI + TRADE TAPE + ORDER CHASER
- Replaces rigid timeouts with a dynamic 'chasing' mechanism.
- Stays at the front of the order book (Best Bid + 1 / Best Ask - 1).
- Automatically pulls orders if the alpha signal decays.
- $5 position size, $100 balance.
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
    "OFI_THRESHOLD": Decimal("0.55"),
    "TRADE_IMBALANCE_THRESHOLD": Decimal("0.3"),
    "TAKE_PROFIT_BPS": Decimal("2"),               # 0.02% pure profit
    "STOP_LOSS_BPS": Decimal("4"),                 # 0.04% initial stop
    "TRAIL_ACTIVATE_BPS": Decimal("1"),            # start trailing after 0.01% profit
    "TRAIL_DISTANCE_BPS": Decimal("1"),            # trail 0.01% behind
    "WIN_COOLDOWN_SEC": 1,
    "LOSS_COOLDOWN_SEC": 15,
    "SCAN_INTERVAL_MS": 20,
    "REFRESH_BOOK_SEC": 20,
    "BINANCE_WS_DEPTH": "wss://stream.binance.com:9443/ws",
    "BINANCE_WS_TRADE": "wss://stream.binance.com:9443/ws",
}

TAKER_FEE = Decimal("0.001")   # Market exit
MAKER_FEE = Decimal("0")       # Limit entry/TP

class UltimateScalper:
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

    # ---------- Order Book Internal Class ----------
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
            except: return False

    class TradeFlow:
        def __init__(self, symbol):
            self.symbol = symbol
            self.buy_volume = Decimal('0')
            self.sell_volume = Decimal('0')
            self.last_reset = time.time()
            self.window_sec = 1.0

        def add_trade(self, is_buyer_maker, qty, price):
            volume = qty * price
            if is_buyer_maker: self.sell_volume += volume
            else: self.buy_volume += volume
            now = time.time()
            if now - self.last_reset > self.window_sec:
                self.buy_volume = self.sell_volume = Decimal('0')
                self.last_reset = now

        def get_imbalance(self):
            total = self.buy_volume + self.sell_volume
            return (self.buy_volume - self.sell_volume) / total if total > 0 else Decimal('0')

    # ---------- Logic Core ----------

    def place_entry_limit(self, symbol, side):
        book = self.order_books[symbol]
        price = self._calculate_chase_price(symbol, side)
        
        if price <= 0: return

        order_size = CONFIG["ORDER_SIZE_USDT"]
        if order_size > self.balance:
            order_size = self.balance * Decimal("0.95")
            if order_size < Decimal("1.00"): return

        self.pending_entries[symbol] = {
            'side': side,
            'price': price,
            'qty': order_size / price,
            'order_size': order_size,
            'timestamp': time.time()
        }
        print(f"📝 LIMIT {side.upper()} Posted: {symbol} @ {price:.8f}")

    def _calculate_chase_price(self, symbol, side):
        book = self.order_books[symbol]
        tick = book.tick_size()
        if side == 'buy':
            price = book.best_bid() + tick
            return min(price, book.best_ask() - tick)
        else:
            price = book.best_ask() - tick
            return max(price, book.best_bid() + tick)

    def check_fills_and_positions(self):
        now = time.time()

        # 1. SMART ORDER CHASING
        for sym in list(self.pending_entries.keys()):
            order = self.pending_entries[sym]
            book = self.order_books[sym]
            
            filled = (order['side'] == 'buy' and book.best_ask() <= order['price']) or \
                     (order['side'] == 'sell' and book.best_bid() >= order['price'])
            
            if filled:
                self._create_position(sym, order)
                continue

            # Check if signal is still valid (Alpha Decay)
            action, ofi, imb = self.entry_signal(sym)
            if action != order['side']:
                del self.pending_entries[sym]
                print(f"📉 SIGNAL DECAY: {sym} cancelled (Alpha vanished)")
                continue

            # Update price if market moved (Chasing)
            new_price = self._calculate_chase_price(sym, order['side'])
            if new_price != order['price']:
                self.pending_entries[sym]['price'] = new_price
                print(f"🔄 CHASING {sym}: Moving limit to {new_price:.8f}")

        # 2. POSITION MANAGEMENT
        for sym, pos in list(self.positions.items()):
            book = self.order_books[sym]
            best_bid, best_ask = book.best_bid(), book.best_ask()
            if best_bid <= 0 or best_ask <= 0: continue
            mid = (best_bid + best_ask) / 2

            # Trailing Stop
            self._update_trailing_stop(sym, pos, mid)

            # Exits
            hit_tp = (pos['side'] == 'buy' and mid >= pos['tp']) or \
                     (pos['side'] == 'sell' and mid <= pos['tp'])
            hit_sl = (pos['side'] == 'buy' and mid <= pos['sl']) or \
                     (pos['side'] == 'sell' and mid >= pos['sl'])

            if hit_tp:
                self.close_win(sym, pos['tp'])
            elif hit_sl:
                exit_price = best_bid if pos['side'] == 'buy' else best_ask
                self.close_loss(sym, exit_price, "STOP LOSS")

    def _create_position(self, sym, order):
        tp_mult = (1 + CONFIG["TAKE_PROFIT_BPS"]/10000) if order['side'] == 'buy' else (1 - CONFIG["TAKE_PROFIT_BPS"]/10000)
        sl_mult = (1 - CONFIG["STOP_LOSS_BPS"]/10000) if order['side'] == 'buy' else (1 + CONFIG["STOP_LOSS_BPS"]/10000)
        
        self.balance -= order['order_size']
        self.positions[sym] = {
            'side': order['side'], 'entry': order['price'], 'qty': order['qty'],
            'order_size': order['order_size'], 'tp': order['price'] * tp_mult,
            'sl': order['price'] * sl_mult, 'best_price': order['price'],
            'trailing': False, 'time': time.time()
        }
        del self.pending_entries[sym]
        print(f"✅ FILLED {sym} @ {order['price']:.8f} | TP @ {self.positions[sym]['tp']:.8f}")

    def _update_trailing_stop(self, sym, pos, mid):
        if pos['side'] == 'buy':
            if mid > pos['best_price']:
                pos['best_price'] = mid
                if (mid - pos['entry']) / pos['entry'] * 10000 >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                    pos['trailing'] = True
                if pos['trailing']:
                    new_sl = mid * (1 - CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                    pos['sl'] = max(pos['sl'], new_sl)
        else:
            if mid < pos['best_price']:
                pos['best_price'] = mid
                if (pos['entry'] - mid) / pos['entry'] * 10000 >= CONFIG["TRAIL_ACTIVATE_BPS"]:
                    pos['trailing'] = True
                if pos['trailing']:
                    new_sl = mid * (1 + CONFIG["TRAIL_DISTANCE_BPS"]/10000)
                    pos['sl'] = min(pos['sl'], new_sl)

    # ---------- Common Methods ----------

    def close_win(self, sym, price):
        pos = self.positions.pop(sym)
        profit = (pos['qty'] * price) - pos['order_size']
        self.balance += (pos['qty'] * price)
        self.total_trades += 1
        self.winning_trades += 1
        self.last_trade_result[sym] = 'win'
        self.daily_profit += profit
        print(f"💰 WIN {sym}: +${profit:.4f} | Balance: ${self.balance:.2f}")
        self.last_trade_time[sym] = time.time()

    def close_loss(self, sym, price, reason):
        pos = self.positions.pop(sym)
        gross = pos['qty'] * price
        fee = gross * TAKER_FEE
        profit = (gross - pos['order_size']) - fee
        self.balance += (gross - fee)
        self.total_trades += 1
        self.last_trade_result[sym] = 'loss' if profit < 0 else 'win'
        if profit > 0: self.winning_trades += 1
        self.daily_profit += profit
        print(f"🛑 {reason} {sym}: ${profit:.4f} | Balance: ${self.balance:.2f}")
        self.last_trade_time[sym] = time.time()

    def entry_signal(self, sym):
        book = self.order_books[sym]
        ofi = book.get_ofi(CONFIG["DEPTH_LEVELS"])
        trade_imb = self.trade_flows[sym].get_imbalance()
        if ofi > CONFIG["OFI_THRESHOLD"] and trade_imb > CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'buy', ofi, trade_imb
        if ofi < -CONFIG["OFI_THRESHOLD"] and trade_imb < -CONFIG["TRADE_IMBALANCE_THRESHOLD"]:
            return 'sell', ofi, trade_imb
        return None, ofi, trade_imb

    # ---------- Infrastructure ----------

    async def subscribe_depth(self, symbol):
        url = f"{CONFIG['BINANCE_WS_DEPTH']}/{symbol.lower()}@depth20@100ms"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        if 'b' in data: self.order_books[symbol].apply_depth(data)
            except: await asyncio.sleep(2)

    async def subscribe_trade(self, symbol):
        url = f"{CONFIG['BINANCE_WS_TRADE']}/{symbol.lower()}@trade"
        while self.running:
            try:
                async with websockets.connect(url) as ws:
                    async for msg in ws:
                        data = json.loads(msg)
                        self.trade_flows[symbol].add_trade(data.get('m', False), Decimal(data['q']), Decimal(data['p']))
            except: await asyncio.sleep(2)

    async def run(self):
        async with aiohttp.ClientSession() as session:
            for sym in CONFIG["SYMBOLS"]:
                self.order_books[sym] = self.OrderBook(sym)
                await self.order_books[sym].refresh_snapshot(session)
                self.trade_flows[sym] = self.TradeFlow(sym)
                asyncio.create_task(self.subscribe_depth(sym))
                asyncio.create_task(self.subscribe_trade(sym))

        last_refresh = time.time()
        while self.running:
            now = time.time()
            if now - last_refresh > CONFIG["REFRESH_BOOK_SEC"]:
                async with aiohttp.ClientSession() as session:
                    for sym in CONFIG["SYMBOLS"]: await self.order_books[sym].refresh_snapshot(session)
                last_refresh = now

            self.check_fills_and_positions()

            for sym in CONFIG["SYMBOLS"]:
                if sym in self.positions or sym in self.pending_entries: continue
                
                cooldown = CONFIG["LOSS_COOLDOWN_SEC"] if self.last_trade_result.get(sym) == 'loss' else CONFIG["WIN_COOLDOWN_SEC"]
                if sym in self.last_trade_time and now - self.last_trade_time[sym] < cooldown: continue
                
                action, ofi, imb = self.entry_signal(sym)
                if action: self.place_entry_limit(sym, action)

            await asyncio.sleep(CONFIG["SCAN_INTERVAL_MS"] / 1000.0)

if __name__ == "__main__":
    try:
        asyncio.run(UltimateScalper().run())
    except KeyboardInterrupt:
        print("\nShutdown complete")

```
