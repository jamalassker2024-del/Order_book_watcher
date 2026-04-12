import asyncio
import json
import sys
import websockets
from decimal import Decimal
from collections import deque
import time

# --- CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("100.0"),
    "BASE_THRESHOLD": Decimal("0.22"),  # Increased slightly to filter noise
    "FEE": Decimal("0.0004"),           # 0.04% (Binance Standard)
    "TARGET_PROFIT": Decimal("0.0035"), # 0.35%
    "STOP_LOSS": Decimal("-0.0050"),    # 0.50%
    "COOLDOWN_SECS": 5,                 # Minimum time between trades
}

class SentinelProV2:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.active_trade = None
        self.imb_stream = deque(maxlen=8) # Slightly longer window for smoother signal
        self.last_trade_time = 0

    def get_weighted_imbalance(self, bids, asks):
        """Applies linear decay weighting (Level 1 has more weight than Level 20)."""
        b_w = sum((Decimal(b[0]) * Decimal(b[1])) * (Decimal(20 - i) / 20) for i, b in enumerate(bids))
        a_w = sum((Decimal(a[0]) * Decimal(a[1])) * (Decimal(20 - i) / 20) for i, a in enumerate(asks))
        return (b_w - a_w) / (b_w + a_w)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        while True: # Outer loop for reconnection
            try:
                async with websockets.connect(url) as ws:
                    print(f"\n--- 🦅 SENTINEL PRO V2 ACTIVE [{CONFIG['SYMBOL'].upper()}] ---")
                    
                    while True:
                        raw_data = await ws.recv()
                        data = json.loads(raw_data)
                        bids, asks = data.get("bids", []), data.get("asks", [])
                        if not bids or not asks: continue

                        bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                        current_imb = self.get_weighted_imbalance(bids, asks)
                        self.imb_stream.append(current_imb)

                        # --- TRADE MONITORING ---
                        if self.active_trade:
                            t = self.active_trade
                            price_now = bid_p if t['side'] == "BUY" else ask_p
                            
                            # Calculate Net PnL (including fees for both entry and exit)
                            raw_move = (price_now - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - price_now) / t['entry']
                            net_roi = raw_move - (CONFIG["FEE"] * 2)
                            live_pnl = CONFIG["TRADE_SIZE_USD"] * net_roi
                            
                            color = "\033[92m" if live_pnl > 0 else "\033[91m"
                            reset = "\033[0m"

                            sys.stdout.write(
                                f"\r 🟢 {t['side']} ACTIVE | Entry: {t['entry']} | PnL: {color}${round(live_pnl, 2)}{reset} ({round(net_roi*100, 3)}%)    "
                            )
                            sys.stdout.flush()

                            if net_roi >= CONFIG["TARGET_PROFIT"] or net_roi <= CONFIG["STOP_LOSS"]:
                                self.balance += live_pnl
                                print(f"\n✅ CLOSED | Net: ${round(live_pnl,2)} | New Bal: ${round(self.balance, 2)}")
                                self.active_trade = None
                                self.last_trade_time = time.time()

                        # --- SCANNING MODE ---
                        else:
                            avg_imb = sum(self.imb_stream) / len(self.imb_stream)
                            cooldown_active = time.time() - self.last_trade_time < CONFIG["COOLDOWN_SECS"]
                            
                            status_msg = "WAITING" if cooldown_active else "SCANNING"
                            sys.stdout.write(f"\r 📡 {status_msg}... Imbalance: {round(avg_imb, 3)} | BTC: ${bid_p}    ")
                            sys.stdout.flush()

                            if not cooldown_active and len(self.imb_stream) == self.imb_stream.maxlen:
                                side = None
                                if avg_imb > CONFIG["BASE_THRESHOLD"]: side = "BUY"
                                elif avg_imb < -CONFIG["BASE_THRESHOLD"]: side = "SELL"

                                if side:
                                    entry_p = ask_p if side == "BUY" else bid_p
                                    self.active_trade = {"side": side, "entry": entry_p}
                                    print(f"\n🚀 {side} ORDER EXECUTED @ {entry_p}")

            except Exception as e:
                print(f"\n⚠️ Connection Lost: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

if __name__ == "__main__":
    try:
        asyncio.run(SentinelProV2().run())
    except KeyboardInterrupt:
        print("\nTerminated.")
