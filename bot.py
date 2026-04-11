import asyncio
import json
import logging
import websockets
from decimal import Decimal
from collections import deque
import time

# --- BTC OPTIMIZED CONFIGURATION ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"), # BTC usually requires higher notional

    "LEVELS": 20,                  # Scanning deeper for heavy BTC orders
    "IMB_THRESHOLD": Decimal("0.04"),  # 4% difference on BTC is huge!
    "DELTA_THRESHOLD": Decimal("0.01"), # Minimal acceleration for faster entry

    "MAX_TRADES": 8,
    "FEE": Decimal("0.0006"),       # Assuming Tier 1 / BNB Fee levels
    "COOLDOWN": 0.8,                
    "TARGET_PROFIT": Decimal("0.0015"), # 0.15% - Aiming for high volume of tiny wins
    "STOP_LOSS": Decimal("-0.003")      # Tighter protection for BTC
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("BTC_SCALPER")

class Trade:
    def __init__(self, side, entry):
        self.side = side
        self.entry = entry
        self.time = time.time()

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00") # Starting Demo Balance $1k
        self.trades = []
        self.imb_history = deque(maxlen=5) # 500ms memory
        self.last_entry = 0

    def calc_imbalance(self, bids, asks):
        # Calculate Notional Value (Price * Quantity) for all 20 levels
        bid_value = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        ask_value = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        if bid_value + ask_value == 0: return Decimal("0")
        return (bid_value - ask_value) / (bid_value + ask_value)

    async def run(self):
        # Using depth20 for BTC to see the real "Walls"
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        log.info(f"⚡ BTC LIQUIDITY ENGINE ONLINE | Monitoring {CONFIG['SYMBOL'].upper()}")

        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("b", []), data.get("a", [])
                if not bids or not asks: continue

                # Price Data
                bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                
                imb = self.calc_imbalance(bids, asks)
                self.imb_history.append(imb)
                delta = imb - self.imb_history[0] if len(self.imb_history) > 1 else 0
                
                now = time.time()

                # --- ENTRY LOGIC (BTC PRESSURE) ---
                # A 4% imbalance on BTC is a massive signal of a price move
                if (abs(imb) > CONFIG["IMB_THRESHOLD"] and 
                    len(self.trades) < CONFIG["MAX_TRADES"] and 
                    now - self.last_entry > CONFIG["COOLDOWN"]):
                    
                    side = "BUY" if imb > 0 else "SELL"
                    entry = ask_p if side == "BUY" else bid_p

                    self.trades.append(Trade(side, entry))
                    self.last_entry = now
                    log.info(f"🔥 BTC {side} | P: {entry} | Imb: {round(imb,3)}")

                # --- EXIT LOGIC ---
                still_open = []
                for t in self.trades:
                    current = bid_p if t.side == "BUY" else ask_p
                    
                    # Percent change minus fees
                    move = (current - t.entry) / t.entry if t.side == "BUY" else (t.entry - current) / t.entry
                    net = move - (CONFIG["FEE"] * 2)

                    if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"]:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl
                        icon = "💰" if net > 0 else "❌"
                        log.info(f"{icon} {t.side} CLOSED | Net: {round(net*100,3)}% | Bal: ${round(self.balance, 2)}")
                    else:
                        still_open.append(t)
                
                self.trades = still_open

if __name__ == "__main__":
    asyncio.run(BTCOrderFlowBot().run())
