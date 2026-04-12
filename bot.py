import asyncio
import json
import sys
import websockets
from decimal import Decimal
from collections import deque
import time

# --- PRO-GRADE CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("100.0"),
    "ENTRY_THRESHOLD": Decimal("0.25"),   # Strict entry
    "VELOCITY_THRESHOLD": Decimal("0.05"), # Must be accelerating
    "FEE": Decimal("0.0004"),
    "TARGET_PROFIT": Decimal("0.0025"),  # 0.25% (Faster turnover)
    "STOP_LOSS": Decimal("-0.0040"),     # 0.40%
    "MAX_SPREAD": Decimal("0.0002"),     # Don't trade if spread > 0.02%
}

class SentinelUltra:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.active_trade = None
        self.imb_history = deque(maxlen=3) # Short window for velocity
        self.last_trade_time = 0

    def get_weighted_imb(self, bids, asks):
        # Weighting the top 5 levels much more aggressively (HFT Style)
        b_w = sum((Decimal(b[0]) * Decimal(b[1])) * (Decimal(1) / (i + 1)) for i, b in enumerate(bids[:10]))
        a_w = sum((Decimal(a[0]) * Decimal(a[1])) * (Decimal(1) / (i + 1)) for i, a in enumerate(asks[:10]))
        return (b_w - a_w) / (b_w + a_w)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        async with websockets.connect(url) as ws:
            print(f"\n--- ⚡ SENTINEL ULTRA: VELOCITY SCALPER [{CONFIG['SYMBOL'].upper()}] ---")
            
            while True:
                try:
                    data = json.loads(await ws.recv())
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    spread = (ask_p - bid_p) / bid_p
                    
                    curr_imb = self.get_weighted_imb(bids, asks)
                    self.imb_history.append(curr_imb)

                    # Calculate Velocity (Acceleration of the book)
                    velocity = curr_imb - self.imb_history[0] if len(self.imb_history) > 1 else 0

                    if self.active_trade:
                        t = self.active_trade
                        price_now = bid_p if t['side'] == "BUY" else ask_p
                        roi = ((price_now - t['entry']) / t['entry']) if t['side'] == "BUY" else ((t['entry'] - price_now) / t['entry'])
                        net_roi = roi - (CONFIG["FEE"] * 2)

                        # EARLY EXIT LOGIC (Micro-scalping)
                        # If we have any profit and imbalance flips hard, take it and run
                        flip_signal = (t['side'] == "BUY" and curr_imb < -0.1) or (t['side'] == "SELL" and curr_imb > 0.1)
                        
                        sys.stdout.write(f"\r 🟢 {t['side']} | PnL: {round(net_roi*100, 3)}% | Imb: {round(curr_imb, 2)}   ")
                        sys.stdout.flush()

                        if net_roi >= CONFIG["TARGET_PROFIT"] or net_roi <= CONFIG["STOP_LOSS"] or (net_roi > 0.0005 and flip_signal):
                            pnl_usd = CONFIG["TRADE_SIZE_USD"] * net_roi
                            self.balance += pnl_usd
                            reason = "TARGET" if net_roi >= CONFIG["TARGET_PROFIT"] else "FLIP" if flip_signal else "STOP"
                            print(f"\n✅ {reason} EXIT | PnL: ${round(pnl_usd, 2)} | Bal: ${round(self.balance, 2)}\n")
                            self.active_trade = None

                    else:
                        # SCANNING FOR ACCELERATION
                        sys.stdout.write(f"\r 📡 SCANNING | Imb: {round(curr_imb, 2)} | Vel: {round(velocity, 2)} | Spread: {round(spread*100, 4)}%   ")
                        sys.stdout.flush()

                        if spread <= CONFIG["MAX_SPREAD"]:
                            # Entry trigger: High Imbalance AND positive velocity
                            if curr_imb > CONFIG["ENTRY_THRESHOLD"] and velocity > CONFIG["VELOCITY_THRESHOLD"]:
                                self.active_trade = {"side": "BUY", "entry": ask_p}
                                print(f"\n🚀 RAPID BUY @ {ask_p} (Vel: {round(velocity, 2)})")
                            
                            elif curr_imb < -CONFIG["ENTRY_THRESHOLD"] and velocity < -CONFIG["VELOCITY_THRESHOLD"]:
                                self.active_trade = {"side": "SELL", "entry": bid_p}
                                print(f"\n🚀 RAPID SELL @ {bid_p} (Vel: {round(velocity, 2)})")

                except Exception as e:
                    await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(SentinelUltra().run())
