import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- CONFIGURATION (2026 PRODUCTION STANDARDS) ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    "IMB_THRESHOLD": Decimal("0.015"), # 1.5% - Very sensitive for guaranteed trades
    "LEVELS": 20,
    "FEE": Decimal("0.0006"),
    "TARGET_PROFIT": Decimal("0.001"), # Quick 0.1% scalps
    "STOP_LOSS": Decimal("-0.002")
}

# Ensure logs appear instantly on Railway
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("PRO_FIX")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=3)
        self.last_entry = 0

    def calc_imbalance(self, bids, asks):
        # Weighting by volume for maximum accuracy
        b_vol = sum(Decimal(b[1]) for b in bids)
        a_vol = sum(Decimal(a[1]) for a in asks)
        if b_vol + a_vol == 0: return Decimal("0")
        return (b_vol - a_vol) / (b_vol + a_vol)

    async def run(self):
        # 2026 NEW ENDPOINT: Use the dedicated 'public' market data node
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        log.info(f"🛰️ ATTEMPTING CONNECTION TO BINANCE PUBLIC NODE...")
        sys.stdout.flush()

        async with websockets.connect(url) as ws:
            log.info(f"✅ ACTIVE | FEED: {CONFIG['SYMBOL'].upper()} | Waiting for signal...")
            sys.stdout.flush()
            
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    
                    # 2026 DATA FIX: Binance data now arrives with 'b' and 'a' directly in the root
                    bids = data.get("bids", data.get("b", []))
                    asks = data.get("asks", data.get("a", []))
                    
                    if not bids or not asks:
                        continue

                    bid_p = Decimal(bids[0][0])
                    ask_p = Decimal(asks[0][0])
                    
                    imb = self.calc_imbalance(bids, asks)
                    self.imb_history.append(imb)
                    
                    now = time.time()

                    # --- AGGRESSIVE ENTRY ---
                    if abs(imb) > CONFIG["IMB_THRESHOLD"] and (now - self.last_entry > 0.5):
                        side = "BUY" if imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        self.trades.append({"side": side, "entry": entry})
                        self.last_entry = now
                        log.info(f"🔥 {side} EXEC | Imbalance: {round(imb,4)} | Price: {entry}")
                        sys.stdout.flush()

                    # --- TRADE MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)

                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"]:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{'💰 WIN' if net > 0 else '❌ LOSS'} | Net: {round(net*100,3)}% | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception as e:
                    log.error(f"⚠️ Error in loop: {e}")
                    sys.stdout.flush()
                    break

if __name__ == "__main__":
    bot = BTCOrderFlowBot()
    while True:
        try:
            asyncio.run(bot.run())
        except Exception as e:
            log.info(f"🔄 Restarting... {e}")
            time.sleep(1)
