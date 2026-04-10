import asyncio, logging, aiohttp, time, math
from decimal import Decimal
from collections import deque

CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("2.0"),
    "IMB_THRESHOLD": Decimal("0.80"), 
    "VOL_MIN": Decimal("0.0008"),     # Higher bar for entry
    "TP": Decimal("0.0060"),          # 0.60% (Bigger wins)
    "SL": Decimal("0.0035"),          # 0.35% (Give it room to breathe)
    "FEE": Decimal("0.001"),          
    "MAX_HOLD": 600,                  # 10 minutes (Patience is profit)
    "POLL": 0.4,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V23-TERMINATOR")

class SniperV23:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=50) # Larger window for trend
        self.last_trade = 0

    async def fetch(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=10"
            async with session.get(url, timeout=1) as r:
                d = await r.json()
                bid, ask = Decimal(d['bids'][0][0]), Decimal(d['asks'][0][0])
                bid_v = sum(Decimal(x[1]) for x in d['bids'][:5])
                ask_v = sum(Decimal(x[1]) for x in d['asks'][:5])
                return (bid + ask) / 2, (bid_v - ask_v) / (bid_v + ask_v)
        except: return None, None

    async def run(self):
        log.info("🤖 V23 TERMINATOR | Trend-Lock Active")
        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)
                if price is None: continue
                self.price_hist.append(price)

                if len(self.price_hist) < 50:
                    await asyncio.sleep(1); continue

                # --- PRO FILTERS ---
                avg_p = sum(self.price_hist) / len(self.price_hist)
                vol = (max(self.price_hist) - min(self.price_hist)) / min(self.price_hist)
                
                # Only Buy if price is above average (Trend), Only Sell if below
                up_sig = imb > CONFIG["IMB_THRESHOLD"] and price > avg_p and vol > CONFIG["VOL_MIN"]
                dn_sig = imb < -CONFIG["IMB_THRESHOLD"] and price < avg_p and vol > CONFIG["VOL_MIN"]

                if time.time() - self.last_trade < 20: # Longer cooldown to avoid overtrading
                    await asyncio.sleep(CONFIG["POLL"]); continue

                side = "BUY" if up_sig else "SELL" if dn_sig else None
                if side:
                    entry, start_t = price, time.time()
                    self.last_trade = start_t
                    # Initial Stop Loss
                    sl = entry * (Decimal("0.9965") if side == "BUY" else Decimal("1.0035"))
                    log.info(f"🚀 {side} @ {entry} | Trend: {'UP' if side=='BUY' else 'DN'}")

                    while True:
                        p, _ = await self.fetch(session)
                        if p is None: continue
                        move = (p - entry) / entry if side == "BUY" else (entry - p) / entry
                        
                        # --- DYNAMIC RECOVERY ---
                        # If up 0.25%, lock in profit at entry + fee
                        if move >= Decimal("0.0025"):
                            sl = entry * (Decimal("1.001") if side == "BUY" else Decimal("0.999"))

                        # --- EXIT GATES ---
                        if move >= CONFIG["TP"]: res = "✅ TP"; break
                        if (side == "BUY" and p <= sl) or (side == "SELL" and p >= sl):
                            res = "❌ SL"; break
                        if time.time() - start_t > CONFIG["MAX_HOLD"]:
                            res = "⌛ TIME"; break
                        
                        await asyncio.sleep(0.5)

                    pnl = CONFIG["TRADE_SIZE"] * (move - CONFIG["FEE"])
                    self.balance += pnl
                    log.info(f"{res} | Net: ${round(pnl,4)} | Bal: ${round(self.balance,3)}")

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV23().run())
