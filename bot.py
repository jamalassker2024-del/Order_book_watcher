import asyncio
import logging
import aiohttp
import time
from decimal import Decimal
from collections import deque

CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("2.0"),    # Slightly larger to make move-to-fee ratio better

    "IMB_THRESHOLD": Decimal("0.70"), # Higher quality requirement
    "IMB_ACCEL": Decimal("0.20"),     # Stronger momentum required

    "TP": Decimal("0.0035"),          # 0.35% (Now significantly higher than fees)
    "SL": Decimal("0.0015"),          # 0.15% (Tight stop to protect balance)
    
    "FEE": Decimal("0.001"),          # 0.1% Binance Convert Fee
    "MAX_HOLD": 30,                   # Give the market 30s to move
    "POLL": 0.3,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V20-FORTRESS")

class SniperV20:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=15)
        self.imb_hist = deque(maxlen=10)
        self.last_trade = 0

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
        log.info("🛡️ V20 FORTRESS ONLINE | Risk Ratio 2.3:1")
        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)
                if price is None: continue

                self.price_hist.append(price)
                self.imb_hist.append(imb)

                if len(self.price_hist) < 15:
                    await asyncio.sleep(1)
                    continue

                # --- MOMENTUM & PRICE ACTION ---
                imb_change = self.imb_hist[-1] - self.imb_hist[0]
                price_trending_up = price > self.price_hist[0]
                price_trending_down = price < self.price_hist[0]

                # --- SIGNAL (Must have Imbalance + Momentum + Price Confirmation) ---
                up_sig = imb > CONFIG["IMB_THRESHOLD"] and imb_change > CONFIG["IMB_ACCEL"] and price_trending_up
                down_sig = imb < -CONFIG["IMB_THRESHOLD"] and imb_change < -CONFIG["IMB_ACCEL"] and price_trending_down

                if (time.time() - self.last_trade < 10): # Cooldown
                    await asyncio.sleep(CONFIG["POLL"])
                    continue

                direction = "BUY" if up_sig else "SELL" if down_sig else None

                if direction:
                    entry = price
                    self.last_trade = time.time()
                    log.info(f"🚀 {direction} ENTRY @ {entry} (Imb: {round(imb,2)})")

                    start_time = time.time()
                    while True:
                        p, _ = await self.fetch(session)
                        if p is None: continue

                        # Calculate move
                        raw_move = (p - entry) / entry if direction == "BUY" else (entry - p) / entry
                        
                        # Exit Logic
                        if raw_move >= CONFIG["TP"]:
                            res = "✅ TP"
                            break
                        elif raw_move <= -CONFIG["SL"]:
                            res = "❌ SL"
                            break
                        elif time.time() - start_time > CONFIG["MAX_HOLD"]:
                            res = "⌛ TIME"
                            break
                        
                        await asyncio.sleep(0.2)

                    # Final Math: Move - Fee
                    net_pnl = CONFIG["TRADE_SIZE"] * (raw_move - CONFIG["FEE"])
                    self.balance += net_pnl
                    log.info(f"{res} | Move: {round(raw_move*100,3)}% | Net: ${round(net_pnl,4)} | Bal: ${round(self.balance,3)}")

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV20().run())
