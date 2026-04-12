import asyncio
import json
import logging
import websockets
import sys
from decimal import Decimal
from collections import deque
import time

# --- CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("100.0"), # Larger size to see the PnL "wiggle"
    "BASE_THRESHOLD": Decimal("0.18"),
    "FEE": Decimal("0.0004"),
    "TARGET_PROFIT": Decimal("0.0040"), # 0.4%
    "STOP_LOSS": Decimal("-0.0060"),    # 0.6%
}

class SentinelPro:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.active_trade = None
        self.imb_stream = deque(maxlen=5)

    def get_imbalance(self, bids, asks):
        b_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        a_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        return (b_w - a_w) / (b_w + a_w)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        async with websockets.connect(url) as ws:
            print(f"\n--- ⚔️ SENTINEL LIVE TERMINAL ACTIVE [{CONFIG['SYMBOL'].upper()}] ---")
            
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("bids", []), data.get("asks", [])
                if not bids: continue

                # Real-time prices
                bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                current_imb = self.get_imbalance(bids, asks)
                self.imb_stream.append(current_imb)

                # --- LIVE MONITORING (THE WIGGLE) ---
                if self.active_trade:
                    t = self.active_trade
                    # If BUY, profit is when bid goes up. If SELL, profit is when ask goes down.
                    price_now = bid_p if t['side'] == "BUY" else ask_p
                    
                    raw_move = (price_now - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - price_now) / t['entry']
                    net_roi = raw_move - (CONFIG["FEE"] * 2)
                    live_pnl = CONFIG["TRADE_SIZE_USD"] * net_roi
                    
                    # Color coding for the terminal
                    color = "\033[92m" if live_pnl > 0 else "\033[91m" # Green/Red
                    reset = "\033[0m"

                    # THE "MT4" LIVE ROW
                    sys.stdout.write(
                        f"\r 🟢 ACTIVE {t['side']} | Entry: {t['entry']} | "
                        f"Price: {price_now} | "
                        f"PnL: {color}${round(live_pnl, 2)}{reset} ({round(net_roi*100, 3)}%)   "
                    )
                    sys.stdout.flush()

                    # Exit Logic
                    if net_roi >= CONFIG["TARGET_PROFIT"] or net_roi <= CONFIG["STOP_LOSS"]:
                        self.balance += live_pnl
                        print(f"\n✅ CLOSED | Net: ${round(live_pnl,2)} | New Bal: ${round(self.balance, 2)}\n")
                        self.active_trade = None

                else:
                    # --- SCANNING MODE ---
                    avg_imb = sum(self.imb_stream) / len(self.imb_stream)
                    sys.stdout.write(f"\r 📡 SCANNING... Imbalance: {round(avg_imb, 3)} | BTC: ${bid_p}   ")
                    sys.stdout.flush()

                    if len(self.imb_stream) == 5:
                        side = "BUY" if avg_imb > CONFIG["BASE_THRESHOLD"] else "SELL" if avg_imb < -CONFIG["BASE_THRESHOLD"] else None
                        if side:
                            entry_p = ask_p if side == "BUY" else bid_p
                            self.active_trade = {"side": side, "entry": entry_p}
                            print(f"\n🚀 {side} ORDER EXECUTED @ {entry_p}")

if __name__ == "__main__":
    try:
        asyncio.run(SentinelPro().run())
    except KeyboardInterrupt:
        print("\nTerminated.")
