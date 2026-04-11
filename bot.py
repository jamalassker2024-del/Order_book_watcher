import asyncio
import json
import logging
import websockets
import time
import sys
from decimal import Decimal
from collections import deque

# --- V16.0 PROFIT SHIELD CONFIG ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("50.0"),
    
    # Strictly follow high-conviction "Whale" walls
    "IMB_THRESHOLD": Decimal("0.06"),    # Increased to 6% (Wait for real pressure)
    "LEVELS": 20,
    
    # Financials - Giving the "Wiggle" Room
    "FEE": Decimal("0.0006"),            
    "TARGET_PROFIT": Decimal("0.0015"),  # Aim for 0.15% (Approx $110 move)
    "STOP_LOSS": Decimal("-0.0035"),     # Wider SL (0.35%) to survive BTC noise
    
    # Inventory Management
    "MAX_TRADES": 10,                    # Focus on fewer, higher-quality trades
    "MIN_PRICE_DIFF": Decimal("10.0"),   # Spread entries by at least $10
    "MAX_TRADE_AGE_SEC": 600             # 10 min max hold
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s", stream=sys.stdout)
log = logging.getLogger("PROFIT_SHIELD")

class BTCOrderFlowBot:
    def __init__(self):
        self.balance = Decimal("1000.00")
        self.trades = []
        self.imb_history = deque(maxlen=10)
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
        log.info(f"🛰️ PROFIT SHIELD ACTIVE | {CONFIG['SYMBOL'].upper()}")
        
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
                    if self.msg_count % 150 == 0:
                        log.info(f"📊 [STATUS] {len(self.trades)} Active | Bal: ${round(self.balance, 2)} | Price: {bid_p}")
                        sys.stdout.flush()

                    # --- SMART ENTRY (High Conviction Only) ---
                    price_dist = abs(ask_p - self.last_entry_price)
                    if (abs(imb) > CONFIG["IMB_THRESHOLD"] and 
                        len(self.trades) < CONFIG["MAX_TRADES"] and 
                        price_dist > CONFIG["MIN_PRICE_DIFF"]):
                        
                        side = "BUY" if imb > 0 else "SELL"
                        entry = ask_p if side == "BUY" else bid_p
                        self.trades.append({"side": side, "entry": entry, "time": now})
                        self.last_entry_price = entry
                        log.info(f"🚀 {side} EXEC | Imb: {round(imb,3)} | P: {entry}")
                        sys.stdout.flush()

                    # --- MANAGEMENT ---
                    open_trades = []
                    for t in self.trades:
                        current = bid_p if t['side'] == "BUY" else ask_p
                        move = (current - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current) / t['entry']
                        net = move - (CONFIG["FEE"] * 2)
                        
                        if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"] or (now - t['time']) > CONFIG["MAX_TRADE_AGE_SEC"]:
                            reason = "💰 WIN" if net >= CONFIG["TARGET_PROFIT"] else "❌ SL" if net <= CONFIG["STOP_LOSS"] else "⏰ TIME"
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
        except: time.sleep(2)
