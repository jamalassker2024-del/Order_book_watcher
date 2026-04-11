import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- V16.1 THE AGGRESSOR CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # Aggression Settings
    "IMB_THRESHOLD": Decimal("0.025"),   # Very sensitive (2.5% imbalance)
    "LEVELS": 20,
    
    # Hyper-Scalping Financials
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0008"),  # Quick 0.08% grabs
    "STOP_LOSS": Decimal("-0.0025"),     # Tighter than before for faster rotation
    
    # Inventory Speed
    "MAX_TRADES": 20,                    # Back up to 20 slots
    "COOLDOWN": 0.2,                     # 200ms (Hyper-fast entry)
    "MIN_PRICE_DIFF": Decimal("2.0"),    # Only needs $2 move to re-entry
    "MAX_TRADE_AGE_SEC": 180             # 3 Minute "Quick Flush" 
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
log = logging.getLogger("AGGRESSOR")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.last_entry_time = 0
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
        log.info(f"💣 AGGRESSOR ENGINE START | {CONFIG['SYMBOL'].upper()}")
        sys.stdout.flush()

        async with websockets.connect(url) as ws:
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    bids, asks = data.get("bids", data.get("b", [])), data.get("asks", data.get("a", []))
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    imb = self.calc_imbalance(bids, asks)
                    now = time.time()

                    # --- HEARTBEAT ---
                    self.msg_count += 1
                    if self.msg_count % 50 == 0: # Faster heartbeat for higher speed
                        log.info(f"📊 [LIVE] {len(self.trades)} Trades | Bal: ${round(self.balance, 2)}")
                        sys.stdout.flush()

                    # --- HYPER-ENTRY LOGIC ---
                    price_dist = abs(ask_p - self.last_entry_price)
                    time_dist = now - self.last_entry_time

                    if (abs(imb) > CONFIG["IMB_THRESHOLD"] and 
                        len(self.trades) < CONFIG["MAX_TRADES"] and 
                        price_dist > CONFIG["MIN_PRICE_DIFF"] and
                        time_dist > CONFIG["COOLDOWN"]):
                        
                        side = "BUY" if imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        self.trades.append({"side": side, "entry": entry, "time": now})
                        self.last_entry_price = entry
                        self.last_entry_time = now
                        log.info(f"🚀 {side} | Imb: {round(imb,3)} | P: {entry}")
                        sys.stdout.flush()

                    # --- FAST EXIT MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        age = now - t['time']

                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"] or age > CONFIG["MAX_TRADE_AGE_SEC"]:
                            if net >= CONFIG["TARGET_PROFIT"]: res = "💰 WIN"
                            elif net <= CONFIG["STOP_LOSS"]: res = "❌ SL"
                            else: res = "⏰ FLUSH" # Cleaned out because it's stale
                            
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{res} | Net: {round(net*100,4)}% | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception as e:
                    log.error(f"⚠️ Error: {e}")
                    break

if __name__ == "__main__":
    bot = BTCOrderFlowBot()
    while True:
        try:
            asyncio.run(bot.run())
        except:
            time.sleep(1)
