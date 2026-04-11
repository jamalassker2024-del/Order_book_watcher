import asyncio
import json
import logging
import websockets
from decimal import Decimal
from collections import deque
import time

# --- CONFIGURATION (Full Professional Settings) ---
CONFIG = {
    "SYMBOL": "pepeusdt",
    "TRADE_SIZE_USD": Decimal("10.0"), # $10 demo trades

    "LEVELS": 10,                 
    "IMB_THRESHOLD": Decimal("0.20"),  # Slightly tighter for quality
    "DELTA_THRESHOLD": Decimal("0.08"), 

    "MAX_TRADES": 5,
    "FEE": Decimal("0.001"),          # 0.1% (Standard Taker Fee)
    "COOLDOWN": 2.0,                  
    "MIN_PROFIT_MULTIPLIER": 1.5      # TP must be 1.5x the Spread
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("PRO_DEMO")

class Trade:
    def __init__(self, side, entry_vamp, spread):
        self.side = side
        self.entry = entry_vamp
        self.spread = spread
        self.time = time.time()

class ProOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("100.00") # Start with $100 Demo
        self.trades = []
        self.imb_history = deque(maxlen=10) # 1-second memory (100ms * 10)
        self.last_entry = 0

    def get_vamp(self, levels):
        """Calculates Volume Adjusted Mid Price (Simulates Real Execution)"""
        total_bid_vol = sum(Decimal(b[1]) for b in levels['bids'])
        total_ask_vol = sum(Decimal(a[1]) for a in levels['asks'])
        
        # Effective Price = (Price * Vol) / Total Vol
        avg_bid = sum(Decimal(b[0]) * Decimal(b[1]) for b in levels['bids']) / total_bid_vol
        avg_ask = sum(Decimal(a[0]) * Decimal(a[1]) for a in levels['asks']) / total_ask_vol
        
        return avg_bid, avg_ask, (avg_ask - avg_bid)

    def calc_imbalance(self, bids, asks):
        bid_notional = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        ask_notional = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        return (bid_notional - ask_notional) / (bid_notional + ask_notional)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth{CONFIG['LEVELS']}@100ms"
        log.info(f"💎 V15.0 PRO DEMO STARTED | Pair: {CONFIG['SYMBOL'].upper()}")

        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("b", []), data.get("a", [])
                if not bids or not asks: continue

                # 1. Get Realistic Prices (VAMP)
                v_bid, v_ask, spread = self.get_vamp({'bids': bids, 'asks': asks})
                
                # 2. Calculate Imbalance & Acceleration
                imb = self.calc_imbalance(bids, asks)
                self.imb_history.append(imb)
                if len(self.imb_history) < 5: continue
                
                # Delta is the change over the last 0.5s
                delta = imb - self.imb_history[0]
                now = time.time()

                # 3. Entry Logic (Filtered for Spoofing)
                # We check if imbalance is SUSTAINED (avg of history)
                avg_imb = sum(self.imb_history) / len(self.imb_history)

                if (abs(avg_imb) > CONFIG["IMB_THRESHOLD"] and 
                    abs(delta) > CONFIG["DELTA_THRESHOLD"] and 
                    len(self.trades) < CONFIG["MAX_TRADES"] and 
                    now - self.last_entry > CONFIG["COOLDOWN"]):
                    
                    side = "BUY" if avg_imb > 0 else "SELL"
                    entry = v_ask if side == "BUY" else v_bid # We pay the VAMP price

                    self.trades.append(Trade(side, entry, spread))
                    self.last_entry = now
                    log.info(f"🚀 {side} Order | VAMP: {entry} | Imb: {round(avg_imb,3)} (Δ:{round(delta,3)})")

                # 4. Realistic Trade Management
                still_open = []
                for t in self.trades:
                    # Current exit price is the other side's VAMP
                    current_exit = v_bid if t.side == "BUY" else v_ask
                    
                    # Move calculation
                    move = (current_exit - t.entry) / t.entry if t.side == "BUY" else (t.entry - current_exit) / t.entry
                    
                    # Net Profit minus Fees (0.1% each side)
                    net = move - (CONFIG["FEE"] * 2)

                    # Dynamic TP/SL: Requires more move if spread is wider
                    tp_target = (t.spread / t.entry) * CONFIG["MIN_PROFIT_MULTIPLIER"]
                    sl_target = -(tp_target) 

                    if net >= tp_target or net <= sl_target:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl
                        status = "💰 WIN" if net > 0 else "❌ LOSS"
                        log.info(f"{status} {t.side} | Net: {round(net*100,3)}% | Bal: ${round(self.balance, 2)}")
                    else:
                        still_open.append(t)
                
                self.trades = still_open

if __name__ == "__main__":
    asyncio.run(ProOrderFlowBot().run())
