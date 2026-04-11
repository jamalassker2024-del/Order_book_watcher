import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- ULTIMATE PRODUCTION CONFIGURATION ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # Sensitivity
    "IMB_THRESHOLD": Decimal("0.02"),    # 2% imbalance triggers entry
    "LEVELS": 20,
    
    # Financials (Tuned for High-Frequency)
    "FEE": Decimal("0.0006"),            # 0.06% Taker Fee
    "TARGET_PROFIT": Decimal("0.0007"),  # Grab 0.07% (High-speed exit)
    "STOP_LOSS": Decimal("-0.0015"),     # Protect against sudden dumps
    
    "MAX_TRADES": 20,                    # Increased slots for high activity
    "COOLDOWN": 0.3                      # 300ms between entries
}

# Railway-optimized logging
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("ULTIMATE_PRO")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=3)
        self.last_entry = 0
        self.msg_count = 0
        self.start_time = time.time()

    def calc_imbalance(self, bids, asks):
        # Professional Volume-Weighting
        b_vol = sum(Decimal(b[1]) for b in bids)
        a_vol = sum(Decimal(a[1]) for a in asks)
        if b_vol + a_vol == 0: return Decimal("0")
        return (b_vol - a_vol) / (b_vol + a_vol)

    async def run(self):
        # 2026 Stability Endpoint
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        log.info(f"🛰️ INITIALIZING ULTIMATE ENGINE | PAIR: {CONFIG['SYMBOL'].upper()}")
        sys.stdout.flush()

        async with websockets.connect(url) as ws:
            log.info(f"✅ ENGINE ONLINE | TARGET PROFIT: {CONFIG['TARGET_PROFIT']*100}%")
            sys.stdout.flush()
            
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    
                    # 2026 Data Unpacking Fix
                    bids = data.get("bids", data.get("b", []))
                    asks = data.get("asks", data.get("a", []))
                    
                    if not bids or not asks:
                        continue

                    bid_p = Decimal(bids[0][0])
                    ask_p = Decimal(asks[0][0])
                    
                    imb = self.calc_imbalance(bids, asks)
                    self.imb_history.append(imb)
                    
                    now = time.time()

                    # --- 1. HEARTBEAT SUMMARY (Every 10 seconds) ---
                    self.msg_count += 1
                    if self.msg_count % 100 == 0:
                        uptime = round((time.time() - self.start_time) / 60, 1)
                        log.info(f"📊 [STATUS] {len(self.trades)} Open Trades | Bal: ${round(self.balance, 2)} | Uptime: {uptime}m")
                        sys.stdout.flush()

                    # --- 2. AGGRESSIVE ENTRY ---
                    if abs(imb) > CONFIG["IMB_THRESHOLD"] and (now - self.last_entry > CONFIG["COOLDOWN"]):
                        if len(self.trades) < CONFIG["MAX_TRADES"]:
                            side = "BUY" if imb > 0 else "SELL"
                            entry = ask_p if side == "BUY" else bid_p
                            self.trades.append({"side": side, "entry": entry, "time": now})
                            self.last_entry = now
                            log.info(f"🔥 {side} EXEC | Imb: {round(imb,4)} | Price: {entry}")
                            sys.stdout.flush()

                    # --- 3. TRADE MANAGEMENT (SCALPING) ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        
                        # Calculate move
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        
                        # Net profit after entry + exit fees
                        net = move - (CONFIG["FEE"] * 2)

                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"]:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            status = "💰 WIN" if net > 0 else "❌ LOSS"
                            log.info(f"{status} | PnL: {round(net*100,4)}% | Result: ${round(pnl,4)} | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception as e:
                    log.error(f"⚠️ Connection interrupted: {e}")
                    sys.stdout.flush()
                    break

if __name__ == "__main__":
    bot = BTCOrderFlowBot()
    while True:
        try:
            asyncio.run(bot.run())
        except Exception as e:
            log.info(f"🔄 Auto-Restarting Engine... {e}")
            time.sleep(2)
