import asyncio
import json
import logging
import websockets
import time
from decimal import Decimal
from collections import deque

# --- CONFIGURATION (Fixed for 2026 API) ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    "IMB_THRESHOLD": Decimal("0.02"),  # Low barrier to guarantee activity
    "LEVELS": 20,
    "FEE": Decimal("0.0006"),
    "TARGET_PROFIT": Decimal("0.0012"),
    "STOP_LOSS": Decimal("-0.0025")
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("BTC_GHOST_FIX")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=5)
        self.last_entry = 0
        self.msg_count = 0 

    def calc_imbalance(self, bids, asks):
        b_val = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        a_val = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        if b_val + a_val == 0: return Decimal("0")
        return (b_val - a_val) / (b_val + a_val)

    async def run(self):
        # 2026 Fix: Use the new combined stream URL format which is more stable
        url = f"wss://stream.binance.com:9443/stream?streams={CONFIG['SYMBOL']}@depth20@100ms"
        
        log.info(f"🛰️ CONNECTING TO BINANCE 2026 NODES...")

        async with websockets.connect(url) as ws:
            log.info(f"✅ CONNECTION ESTABLISHED | Monitoring {CONFIG['SYMBOL'].upper()}")
            
            while True:
                try:
                    # Set a timeout so the bot doesn't hang forever if Binance goes quiet
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=10)
                    data_json = json.loads(raw_data)
                    
                    # Combined streams wrap data in a "data" key
                    data = data_json.get("data", data_json)
                    bids, asks = data.get("b", []), data.get("a", [])
                    
                    if not bids: continue
                    self.msg_count += 1

                    # Log every 50th message just to prove the bot is "alive" and breathing
                    if self.msg_count % 50 == 0:
                        log.info(f"📡 Heartbeat: Data Streaming OK (Msg #{self.msg_count})")

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    imb = self.calc_imbalance(bids, asks)
                    self.imb_history.append(imb)
                    
                    now = time.time()

                    # --- ENTRY ---
                    if abs(imb) > CONFIG["IMB_THRESHOLD"] and (now - self.last_entry > 1.0):
                        side = "BUY" if imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        self.trades.append({"side": side, "entry": entry})
                        self.last_entry = now
                        log.info(f"🔥 {side} EXEC | Imb: {round(imb,3)} | Price: {entry}")

                    # --- MANAGEMENT ---
                    still_open = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)

                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"]:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{'💰' if net > 0 else '❌'} {t['side']} CLOSED | Bal: ${round(self.balance, 2)}")
                        else:
                            still_open.append(t)
                    self.trades = still_open

                except asyncio.TimeoutError:
                    log.warning("⚠️ Stream Timeout - Reconnecting...")
                    break 
                except Exception as e:
                    log.error(f"⚠️ Error: {e}")
                    break

if __name__ == "__main__":
    while True: # Auto-restart loop
        try:
            asyncio.run(BTCOrderFlowBot().run())
        except:
            time.sleep(2)
