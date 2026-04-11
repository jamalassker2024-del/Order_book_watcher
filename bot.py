import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- CONFIGURATION (The "Dilemma" Fix Tuning) ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # Sensitivity
    "IMB_THRESHOLD": Decimal("0.035"),   # 3.5% difference for entry
    "LEVELS": 20,
    
    # Financials 
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0006"),  # Grab 0.06% moves
    "STOP_LOSS": Decimal("-0.0012"),     
    
    # Inventory Management (Crucial for the "Lock" issue)
    "MAX_TRADES": 15,                    
    "COOLDOWN": 1.0,                     # 1s between entries to prevent spam
    "MIN_PRICE_DIFF": Decimal("3.0"),    # Must move $3 from last entry to open again
    "MAX_TRADE_AGE_SEC": 300             # 5 min max hold to keep slots free
}

# Railway log flushing
logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(message)s",
    stream=sys.stdout
)
log = logging.getLogger("DILEMMA_FIX")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=5)
        self.last_entry_price = Decimal("0")
        self.msg_count = 0
        self.start_time = time.time()

    def calc_imbalance(self, bids, asks):
        b_vol = sum(Decimal(b[1]) for b in bids)
        a_vol = sum(Decimal(a[1]) for a in asks)
        if b_vol + a_vol == 0: return Decimal("0")
        return (b_vol - a_vol) / (b_vol + a_vol)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        
        log.info(f"🛰️ INITIALIZING DILEMMA BUSTER | {CONFIG['SYMBOL'].upper()}")
        sys.stdout.flush()

        async with websockets.connect(url) as ws:
            log.info(f"✅ CONNECTION STABLE | Monitoring Order Book...")
            sys.stdout.flush()
            
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    
                    bids = data.get("bids", data.get("b", []))
                    asks = data.get("asks", data.get("a", []))
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    imb = self.calc_imbalance(bids, asks)
                    now = time.time()

                    # --- 1. PERIODIC STATUS ---
                    self.msg_count += 1
                    if self.msg_count % 100 == 0:
                        uptime = round((now - self.start_time) / 60, 1)
                        log.info(f"📊 [STATUS] {len(self.trades)} Trades | Bal: ${round(self.balance, 2)} | Price: {bid_p}")
                        sys.stdout.flush()

                    # --- 2. SMART ENTRY (Prevents Price Lock) ---
                    # Checks: Imbalance trigger AND Slot availability AND Price distance from last trade
                    price_dist = abs(ask_p - self.last_entry_price)
                    
                    if (abs(imb) > CONFIG["IMB_THRESHOLD"] and 
                        len(self.trades) < CONFIG["MAX_TRADES"] and 
                        price_dist > CONFIG["MIN_PRICE_DIFF"]):
                        
                        side = "BUY" if imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        
                        self.trades.append({"side": side, "entry": entry, "time": now})
                        self.last_entry_price = entry
                        log.info(f"🚀 {side} EXEC | Imb: {round(imb,3)} | P: {entry} | Dist: ${round(price_dist,2)}")
                        sys.stdout.flush()

                    # --- 3. DYNAMIC EXIT MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        age = now - t['time']

                        # EXIT CONDITIONS: Profit Target OR Stop Loss OR Age Limit (to unfreeze slots)
                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"] or age > CONFIG["MAX_TRADE_AGE_SEC"]:
                            reason = "💰 WIN" if net >= CONFIG["TARGET_PROFIT"] else "⏰ TIME" if age > CONFIG["MAX_TRADE_AGE_SEC"] else "❌ SL"
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{reason} | Net: {round(net*100,4)}% | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception as e:
                    log.error(f"🔄 Connection Issue: {e}")
                    sys.stdout.flush()
                    break

if __name__ == "__main__":
    bot = BTCOrderFlowBot()
    while True:
        try:
            asyncio.run(bot.run())
        except Exception as e:
            time.sleep(2)
