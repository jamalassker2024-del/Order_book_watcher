import asyncio
import json
import logging
import websockets
from decimal import Decimal
from collections import deque
import time

# --- CONFIGURATION (Optimized for SOLUSDT 2026) ---
CONFIG = {
    "SYMBOL": "solusdt",
    "TRADE_SIZE_USD": Decimal("20.0"), 

    "LEVELS": 10,                 
    "IMB_THRESHOLD": Decimal("0.12"),  # Lowered from 0.20 to be more active
    "DELTA_THRESHOLD": Decimal("0.04"), # Lowered from 0.08 to catch moves faster

    "MAX_TRADES": 5,
    "FEE": Decimal("0.001"),          
    "COOLDOWN": 1.5,                  
    "MIN_PROFIT_MULTIPLIER": 1.2      # Catch smaller, more frequent wins
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("SOL_ACTIVE")

class Trade:
    def __init__(self, side, entry_vamp, spread):
        self.side = side
        self.entry = entry_vamp
        self.spread = spread
        self.time = time.time()

class ActiveOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("100.00") 
        self.trades = []
        self.imb_history = deque(maxlen=8) # Faster 0.8s memory
        self.last_entry = 0

    def get_vamp(self, levels):
        total_bid_vol = sum(Decimal(b[1]) for b in levels['bids'])
        total_ask_vol = sum(Decimal(a[1]) for a in levels['asks'])
        avg_bid = sum(Decimal(b[0]) * Decimal(b[1]) for b in levels['bids']) / total_bid_vol
        avg_ask = sum(Decimal(a[0]) * Decimal(a[1]) for a in levels['asks']) / total_ask_vol
        return avg_bid, avg_ask, (avg_ask - avg_bid)

    def calc_imbalance(self, bids, asks):
        # Weighting by Notional (Price * Quantity)
        bid_notional = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        ask_notional = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        return (bid_notional - ask_notional) / (bid_notional + ask_notional)

    async def run(self):
        # Using @depth20 for better data on high-volume SOL
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        log.info(f"⚡ SOLANA ACTIVE SCALPER STARTING | Target: {CONFIG['SYMBOL'].upper()}")

        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("b", []), data.get("a", [])
                if not bids or not asks: continue

                v_bid, v_ask, spread = self.get_vamp({'bids': bids, 'asks': asks})
                
                imb = self.calc_imbalance(bids, asks)
                self.imb_history.append(imb)
                if len(self.imb_history) < 3: continue
                
                delta = imb - self.imb_history[0]
                now = time.time()

                # Entry Logic
                avg_imb = sum(self.imb_history) / len(self.imb_history)

                if (abs(avg_imb) > CONFIG["IMB_THRESHOLD"] and 
                    abs(delta) > CONFIG["DELTA_THRESHOLD"] and 
                    len(self.trades) < CONFIG["MAX_TRADES"] and 
                    now - self.last_entry > CONFIG["COOLDOWN"]):
                    
                    side = "BUY" if avg_imb > 0 else "SELL"
                    entry = v_ask if side == "BUY" else v_bid 

                    self.trades.append(Trade(side, entry, spread))
                    self.last_entry = now
                    log.info(f"🚀 {side} | VAMP: {round(entry,3)} | Imb: {round(avg_imb,3)} | Δ:{round(delta,3)}")

                # Management Logic
                still_open = []
                for t in self.trades:
                    current_exit = v_bid if t.side == "BUY" else v_ask
                    move = (current_exit - t.entry) / t.entry if t.side == "BUY" else (t.entry - current_exit) / t.entry
                    net = move - (CONFIG["FEE"] * 2)

                    # Dynamic TP/SL
                    tp_target = (t.spread / t.entry) * CONFIG["MIN_PROFIT_MULTIPLIER"]
                    sl_target = -(tp_target * Decimal("1.5")) # Slightly wider stop to avoid "whipsaws"

                    if net >= tp_target or net <= sl_target:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl
                        log.info(f"{'💰' if net > 0 else '❌'} {t.side} Done | PnL: {round(net*100,3)}% | Bal: ${round(self.balance, 2)}")
                    else:
                        still_open.append(t)
                
                self.trades = still_open

if __name__ == "__main__":
    asyncio.run(ActiveOrderFlowBot().run())
