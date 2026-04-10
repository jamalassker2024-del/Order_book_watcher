import asyncio
import logging
import aiohttp
import time
from decimal import Decimal
from collections import deque

# --- TUNED FOR PROFITABILITY ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("2.0"),

    "IMB_THRESHOLD": Decimal("0.80"), # 🎯 Slightly adjusted for more activity
    "IMB_ACCEL": Decimal("0.15"),     # 🚀 Momentum requirement
    
    "VOL_MIN": Decimal("0.0005"),     # 📊 Price must move at least 0.05% 
    
    "TP": Decimal("0.0040"),          # ✅ 0.40% Take Profit
    "SL": Decimal("0.0020"),          # ❌ 0.20% Stop Loss
    
    "FEE": Decimal("0.001"),          
    "MAX_HOLD": 180,                  
    "POLL": 0.4,
    "HEARTBEAT_INTERVAL": 60          # 💓 Diagnostic log every 60 seconds
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V21.1-HEARTBEAT")

class SniperV21:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=20) 
        self.imb_hist = deque(maxlen=10)
        self.last_trade = 0
        self.last_heartbeat = time.time()

    async def fetch(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=20"
            async with session.get(url, timeout=1) as r:
                d = await r.json()
                bid, ask = Decimal(d['bids'][0][0]), Decimal(d['asks'][0][0])
                mid = (bid + ask) / 2
                bid_vol = sum(Decimal(x[1]) for x in d['bids'])
                ask_vol = sum(Decimal(x[1]) for x in d['asks'])
                imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                return mid, imb
        except:
            return None, None

    async def run(self):
        log.info("🎯 V21.1 HEARTBEAT ONLINE | Diagnostic Mode Active")
        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)
                if price is None: continue

                self.price_hist.append(price)
                self.imb_hist.append(imb)

                if len(self.price_hist) < 20:
                    await asyncio.sleep(1)
                    continue

                # --- CALCULATIONS ---
                high, low = max(self.price_hist), min(self.price_hist)
                current_vol = (high - low) / low
                imb_change = self.imb_hist[-1] - self.imb_hist[0]
                
                # --- HEARTBEAT DIAGNOSTIC ---
                if time.time() - self.last_heartbeat > CONFIG["HEARTBEAT_INTERVAL"]:
                    reason = "OK"
                    if current_vol < CONFIG["VOL_MIN"]: reason = "Market Too Quiet (Low Vol)"
                    elif abs(imb) < CONFIG["IMB_THRESHOLD"]: reason = "Waiting for Whale Imbalance"
                    log.info(f"💓 HEARTBEAT | Bal: ${round(self.balance,3)} | Imb: {round(imb,2)} | Vol: {round(current_vol*100,3)}% | Status: {reason}")
                    self.last_heartbeat = time.time()

                # --- TRIGGERS ---
                is_volatile = current_vol >= CONFIG["VOL_MIN"]
                price_trend_up = price > self.price_hist[0]
                price_trend_down = price < self.price_hist[0]

                up_sig = imb > CONFIG["IMB_THRESHOLD"] and imb_change > CONFIG["IMB_ACCEL"] and price_trend_up and is_volatile
                down_sig = imb < -CONFIG["IMB_THRESHOLD"] and imb_change < -CONFIG["IMB_ACCEL"] and price_trend_down and is_volatile

                if (time.time() - self.last_trade < 15):
                    await asyncio.sleep(CONFIG["POLL"])
                    continue

                direction = "BUY" if up_sig else "SELL" if down_sig else None

                if direction:
                    entry = price
                    self.last_trade = time.time()
                    log.info(f"🚀 {direction} ENTRY @ {entry} | Vol: {round(current_vol*100,3)}%")

                    start_time = time.time()
                    while True:
                        p, _ = await self.fetch(session)
                        if p is None: continue
                        raw_move = (p - entry) / entry if direction == "BUY" else (entry - p) / entry
                        
                        if raw_move >= CONFIG["TP"]:
                            res = "✅ TP"
                            break
                        elif raw_move <= -CONFIG["SL"]:
                            res = "❌ SL"
                            break
                        elif time.time() - start_time > CONFIG["MAX_HOLD"]:
                            res = "⌛ TIME"
                            break
                        await asyncio.sleep(0.3)

                    net_pnl = CONFIG["TRADE_SIZE"] * (raw_move - CONFIG["FEE"])
                    self.balance += net_pnl
                    log.info(f"{res} | Move: {round(raw_move*100,3)}% | Net: ${round(net_pnl,4)} | Bal: ${round(self.balance,3)}")

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV21().run())
