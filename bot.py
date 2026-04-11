import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- V17.0 CITADEL CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # ANTI-NOISE TACTICS
    "IMB_THRESHOLD": Decimal("0.075"),   # 7.5% - Only whales allowed
    "CONFIRM_SNAPSHOTS": 5,              # Must stay imbalanced for 500ms (5 snapshots)
    "LEVELS": 20,
    
    # PROFIT OPTIMIZATION
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0022"),  # Higher target for higher win-rate quality
    "STOP_LOSS": Decimal("-0.0030"),     
    "BE_ACTIVATION": Decimal("0.0011"),  # If price moves +0.11%, move SL to entry
    
    # AGGRESSIVE INVENTORY
    "MAX_TRADES": 12,                    
    "MIN_PRICE_DIFF": Decimal("15.0"),   # Force trades to be spread out ($15 gap)
    "MAX_TRADE_AGE_SEC": 480             # 8 min flush
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
log = logging.getLogger("CITADEL")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        # Store recent imbalances to filter noise
        self.imb_stream = deque(maxlen=CONFIG["CONFIRM_SNAPSHOTS"])
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
        log.info(f"🛡️ CITADEL ENGINE ACTIVE | {CONFIG['SYMBOL'].upper()}")
        
        async with websockets.connect(url) as ws:
            while True:
                try:
                    raw_data = await asyncio.wait_for(ws.recv(), timeout=15)
                    data = json.loads(raw_data)
                    bids, asks = data.get("bids", []), data.get("asks", [])
                    if not bids or not asks: continue

                    bid_p, ask_p = Decimal(bids[0][0]), Decimal(asks[0][0])
                    imb = self.calc_imbalance(bids, asks)
                    self.imb_stream.append(imb)
                    now = time.time()

                    # --- HEARTBEAT ---
                    self.msg_count += 1
                    if self.msg_count % 100 == 0:
                        avg_imb = sum(self.imb_stream)/len(self.imb_stream) if self.imb_stream else 0
                        log.info(f"📊 [LIVE] {len(self.trades)} Active | Bal: ${round(self.balance, 2)} | AvgImb: {round(avg_imb,3)}")
                        sys.stdout.flush()

                    # --- SMART TACTIC: SUSTAINED PRESSURE ENTRY ---
                    if len(self.imb_stream) == CONFIG["CONFIRM_SNAPSHOTS"]:
                        avg_imb = sum(self.imb_stream) / len(self.imb_stream)
                        
                        # Only trade if the AVERAGE imbalance is strong (filters one-off noise)
                        if (abs(avg_imb) > CONFIG["IMB_THRESHOLD"] and 
                            len(self.trades) < CONFIG["MAX_TRADES"] and 
                            abs(ask_p - self.last_entry_price) > CONFIG["MIN_PRICE_DIFF"]):
                            
                            side = "BUY" if avg_imb > 0 else "SELL"
                            entry = ask_p if side == "BUY" else bid_p
                            self.trades.append({
                                "side": side, 
                                "entry": entry, 
                                "time": now, 
                                "sl": CONFIG["STOP_LOSS"],
                                "protected": False 
                            })
                            self.last_entry_price = entry
                            log.info(f"🚀 {side} ENTER | Confirmed Imb: {round(avg_imb,3)} | P: {entry}")
                            sys.stdout.flush()

                    # --- SMART TACTIC: DYNAMIC PROTECTION ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        
                        # Break-even switch: If we are deep in profit, lock the floor at 0.01% profit
                        if net >= CONFIG["BE_ACTIVATION"]:
                            t["protected"] = True

                        # Exit conditions
                        exit_now = False
                        reason = ""

                        if net >= CONFIG["TARGET_PROFIT"]:
                            exit_now, reason = True, "💰 WIN"
                        elif t["protected"] and net < Decimal("0.0001"):
                            exit_now, reason = True, "🛡️ GUARD" # Saved a winner from becoming a loser
                        elif net <= t["sl"]:
                            exit_now, reason = True, "❌ SL"
                        elif (now - t['time']) > CONFIG["MAX_TRADE_AGE_SEC"]:
                            exit_now, reason = True, "⏰ TIME"

                        if exit_now:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{reason} | Net: {round(net*100,4)}% | Bal: ${round(self.balance, 2)}")
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
