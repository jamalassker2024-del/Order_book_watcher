import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- V16.5 SNIPER-AGGRESSOR CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # Aggression with Confirmation
    "IMB_THRESHOLD": Decimal("0.055"),   # 5.5% Imbalance (Strong conviction)
    "CONFIRM_WINDOW": 3,                 # Must see imbalance for 3 snapshots (300ms)
    "LEVELS": 20,
    
    # Optimized Financials for Win Rate
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0018"),  # $130 move (Higher to cover fees/sl)
    "STOP_LOSS": Decimal("-0.0030"),     # Standard buffer
    "BE_TRIGGER": Decimal("0.0010"),     # Break-even trigger: if up 0.1%, don't lose
    
    # Inventory & Speed
    "MAX_TRADES": 15,                    
    "MIN_PRICE_DIFF": Decimal("8.0"),    # Spread entries out to avoid clusters
    "MAX_TRADE_AGE_SEC": 420             # 7 Minute flush
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
log = logging.getLogger("SNIPER_AGG")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=CONFIG["CONFIRM_WINDOW"])
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
        log.info(f"🎯 SNIPER-AGGRESSOR ONLINE | {CONFIG['SYMBOL'].upper()}")
        
        async with websockets.connect(url) as ws:
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    imb = self.calc_imbalance(bids, asks)
                    self.imb_history.append(imb)
                    now = time.time()

                    # --- HEARTBEAT ---
                    self.msg_count += 1
                    if self.msg_count % 100 == 0:
                        log.info(f"📊 [STATUS] {len(self.trades)} Active | Bal: ${round(self.balance, 2)} | Imb: {round(imb,3)}")
                        sys.stdout.flush()

                    # --- SUSTAINED IMBALANCE ENTRY ---
                    # Only enters if the last 3 snapshots all show the same bias
                    avg_imb = sum(self.imb_history) / len(self.imb_history) if self.imb_history else 0
                    
                    if (abs(avg_imb) > CONFIG["IMB_THRESHOLD"] and 
                        len(self.trades) < CONFIG["MAX_TRADES"] and 
                        abs(ask_p - self.last_entry_price) > CONFIG["MIN_PRICE_DIFF"]):
                        
                        side = "BUY" if avg_imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        self.trades.append({"side": side, "entry": entry, "time": now, "be_active": False})
                        self.last_entry_price = entry
                        log.info(f"🚀 {side} CONFIRMED | Imb: {round(avg_imb,3)} | P: {entry}")
                        sys.stdout.flush()

                    # --- PROFIT-GUARD MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        
                        # 1. Break-Even Protection (Lock in if price moves favorably)
                        if net >= CONFIG["BE_TRIGGER"]:
                            t["be_active"] = True

                        # 2. Exit Logic
                        exit_trigger = False
                        reason = ""

                        if net >= CONFIG["TARGET_PROFIT"]:
                            exit_trigger, reason = True, "💰 WIN"
                        elif t["be_active"] and net < Decimal("0.0001"): # Close if it returns to entry after being up
                            exit_trigger, reason = True, "🛡️ PROTECT"
                        elif net <= CONFIG["STOP_LOSS"]:
                            exit_trigger, reason = True, "❌ SL"
                        elif (now - t['time']) > CONFIG["MAX_TRADE_AGE_SEC"]:
                            exit_trigger, reason = True, "⏰ TIME"

                        if exit_trigger:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{reason} | Net: {round(net*100,4)}% | PnL: ${round(pnl,3)} | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception as e:
                    break

if __name__ == "__main__":
    bot = BTCOrderFlowBot()
    while True:
        try: asyncio.run(bot.run())
        except: time.sleep(1)
