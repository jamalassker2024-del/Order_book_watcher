import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- V18.0 IRON GRID CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # HEDGING STRATEGY
    "IMB_THRESHOLD": Decimal("0.055"),   # Lowered slightly to allow hedge entries
    "CONFIRM_SNAPSHOTS": 4,              # 400ms confirmation
    "LEVELS": 20,
    
    # DIRECTIONAL LIMITS (The Hedge)
    "MAX_TOTAL_TRADES": 16,              
    "MAX_SIDE_EXPOSURE": 10,             # Never more than 10 of ONE side (Buy or Sell)
    
    # FINANCIALS
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0020"),  
    "STOP_LOSS": Decimal("-0.0045"),     # Wider SL because the Hedge absorbs the hit
    "BE_ACTIVATION": Decimal("0.0012"),  
    
    "MIN_PRICE_DIFF": Decimal("12.0"),   
    "MAX_TRADE_AGE_SEC": 600             
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
log = logging.getLogger("IRON_GRID")

class BTCHedgedBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_stream = deque(maxlen=CONFIG["CONFIRM_SNAPSHOTS"])
        self.last_buy_price = Decimal("0")
        self.last_sell_price = Decimal("0")
        self.msg_count = 0

    def calc_imbalance(self, bids, asks):
        b_vol = sum(Decimal(b[1]) for b in bids)
        a_vol = sum(Decimal(a[1]) for a in asks)
        if b_vol + a_vol == 0: return Decimal("0")
        return (b_vol - a_vol) / (b_vol + a_vol)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        log.info(f"⚔️ IRON GRID HEDGE ACTIVE | {CONFIG['SYMBOL'].upper()}")
        
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

                    # --- STATUS UPDATE ---
                    self.msg_count += 1
                    if self.msg_count % 100 == 0:
                        buys = len([t for t in self.trades if t['side'] == "BUY"])
                        sells = len([t for t in self.trades if t['side'] == "SELL"])
                        log.info(f"📊 [HEDGE] B:{buys} | S:{sells} | Bal: ${round(self.balance, 2)} | P: {bid_p}")
                        sys.stdout.flush()

                    # --- HEDGED ENTRY LOGIC ---
                    if len(self.imb_stream) == CONFIG["CONFIRM_SNAPSHOTS"]:
                        avg_imb = sum(self.imb_stream) / len(self.imb_stream)
                        side = "BUY" if avg_imb > CONFIG["IMB_THRESHOLD"] else "SELL" if avg_imb < -CONFIG["IMB_THRESHOLD"] else None
                        
                        if side:
                            current_side_count = len([t for t in self.trades if t['side'] == side])
                            last_p = self.last_buy_price if side == "BUY" else self.last_sell_price
                            entry_p = ask_p if side == "BUY" else bid_p

                            # Check exposure and price distance for this specific side
                            if (current_side_count < CONFIG["MAX_SIDE_EXPOSURE"] and 
                                len(self.trades) < CONFIG["MAX_TOTAL_TRADES"] and
                                abs(entry_p - last_p) > CONFIG["MIN_PRICE_DIFF"]):
                                
                                self.trades.append({
                                    "side": side, "entry": entry_p, "time": now, "protected": False
                                })
                                if side == "BUY": self.last_buy_price = entry_p
                                else: self.last_sell_price = entry_p
                                
                                log.info(f"🚀 OPEN {side} | P: {entry_p} | Imb: {round(avg_imb,3)}")
                                sys.stdout.flush()

                    # --- MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        
                        if net >= CONFIG["BE_ACTIVATION"]: t["protected"] = True

                        exit_now = False
                        reason = ""

                        if net >= CONFIG["TARGET_PROFIT"]: exit_now, reason = True, "💰 WIN"
                        elif t["protected"] and net < Decimal("0.0001"): exit_now, reason = True, "🛡️ HEDGE_GUARD"
                        elif net <= CONFIG["STOP_LOSS"]: exit_now, reason = True, "❌ SL"
                        elif (now - t['time']) > CONFIG["MAX_TRADE_AGE_SEC"]: exit_now, reason = True, "⏰ TIME"

                        if exit_now:
                            pnl = CONFIG["TRADE_SIZE_USD"] * net
                            self.balance += pnl
                            log.info(f"{reason} {t['side']} | Net: {round(net*100,4)}% | Bal: ${round(self.balance, 2)}")
                            sys.stdout.flush()
                        else:
                            open_trades.append(t)
                    self.trades = open_trades

                except Exception:
                    break

if __name__ == "__main__":
    bot = BTCHedgedBot()
    while True:
        try: asyncio.run(bot.run())
        except: time.sleep(1)
