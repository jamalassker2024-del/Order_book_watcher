import asyncio
import json
import logging
import websockets
from decimal import Decimal
from collections import deque
import time

# --- FULL AGGRESSIVE CONFIGURATION ---
CONFIG = {
    "SYMBOL": "solusdt",
    "TRADE_SIZE_USD": Decimal("25.0"), 

    "LEVELS": 5,                   # Scanning top 5 levels for maximum speed
    "IMB_THRESHOLD": Decimal("0.05"),  # 5% difference is now enough to trigger
    "DELTA_THRESHOLD": Decimal("0.02"), # Detects even tiny price accelerations

    "MAX_TRADES": 10,              # Increased capacity
    "FEE": Decimal("0.00075"),      # Adjusted for BNB-discount fees
    "COOLDOWN": 0.5,                # Almost no waiting between trades
    "MIN_PROFIT_PERCENT": Decimal("0.0025") # 0.25% mini-scalps
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("AGGRESSIVE_SOL")

class Trade:
    def __init__(self, side, entry):
        self.side = side
        self.entry = entry
        self.time = time.time()

class AggressiveBot:
    def __init__(self):
        self.balance = Decimal("100.00") 
        self.trades = []
        self.imb_history = deque(maxlen=3) # Ultra-fast 300ms reaction
        self.last_entry = 0

    def calc_imbalance(self, bids, asks):
        # High-sensitivity weight calculation
        b_vol = sum(Decimal(b[1]) for b in bids)
        a_vol = sum(Decimal(a[1]) for a in asks)
        if b_vol + a_vol == 0: return Decimal("0")
        return (b_vol - a_vol) / (b_vol + a_vol)

    async def run(self):
        # Using depth5 for the fastest possible data updates
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth5@100ms"
        log.info(f"🔥 AGGRESSIVE MODE ACTIVE | Monitoring {CONFIG['SYMBOL'].upper()}")

        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("b", []), data.get("a", [])
                if not bids or not asks: continue

                # Current Prices
                bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                
                imb = self.calc_imbalance(bids, asks)
                self.imb_history.append(imb)
                delta = imb - self.imb_history[0] if len(self.imb_history) > 1 else 0
                
                now = time.time()

                # --- AGGRESSIVE ENTRY ---
                # Triggered if imbalance exists even slightly
                if (abs(imb) > CONFIG["IMB_THRESHOLD"] and 
                    len(self.trades) < CONFIG["MAX_TRADES"] and 
                    now - self.last_entry > CONFIG["COOLDOWN"]):
                    
                    side = "BUY" if imb > 0 else "SELL"
                    entry = ask_p if side == "BUY" else bid_p

                    self.trades.append(Trade(side, entry))
                    self.last_entry = now
                    log.info(f"⚡ {side} EXEC | Imb: {round(imb,2)} | P: {entry}")

                # --- EXIT LOGIC (Mini-Scalps) ---
                still_open = []
                for t in self.trades:
                    current = bid_p if t.side == "BUY" else ask_p
                    
                    # Net move after fees
                    move = (current - t.entry) / t.entry if t.side == "BUY" else (t.entry - current) / t.entry
                    net = move - (CONFIG["FEE"] * 2)

                    # Quick Exit: Win at 0.25% or cut loss at 0.4%
                    if net >= CONFIG["MIN_PROFIT_PERCENT"] or net <= -0.004:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl
                        icon = "💰" if net > 0 else "💀"
                        log.info(f"{icon} {t.side} CLOSED | Net: {round(net*100,3)}% | Bal: ${round(self.balance, 2)}")
                    else:
                        still_open.append(t)
                
                self.trades = still_open

if __name__ == "__main__":
    asyncio.run(AggressiveBot().run())
