import asyncio, logging, aiohttp, time
from decimal import Decimal
from collections import deque

CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("2.0"),
    "IMB_THRESHOLD": Decimal("0.75"), # 🎯 Lowered slightly for more opportunities
    "VOL_MIN": Decimal("0.0004"),     # 📊 More sensitive to small breakouts
    "TP": Decimal("0.0050"),          # ✅ 0.50% Target (Shoot for higher R:R)
    "SL": Decimal("0.0025"),          # ❌ 0.25% Initial Stop Loss
    "FEE": Decimal("0.001"),          
    "MAX_HOLD": 300,                  # ⌛ Increased to 5 mins (let trades breathe)
    "POLL": 0.3,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V22-PREDATOR")

class SniperV22:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=30) 
        self.last_trade = 0

    async def fetch(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=10"
            async with session.get(url, timeout=1) as r:
                d = await r.json()
                bid, ask = Decimal(d['bids'][0][0]), Decimal(d['asks'][0][0])
                bid_vol = sum(Decimal(x[1]) for x in d['bids'][:5]) # Top 5 levels only
                ask_vol = sum(Decimal(x[1]) for x in d['asks'][:5])
                imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                return (bid + ask) / 2, imb
        except: return None, None

    async def run(self):
        log.info("🐅 V22 PREDATOR LIVE | Trailing Stops Active")
        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)
                if price is None: continue
                self.price_hist.append(price)

                if len(self.price_hist) < 30:
                    await asyncio.sleep(1); continue

                # Logic Gates
                vol = (max(self.price_hist) - min(self.price_hist)) / min(self.price_hist)
                up_sig = imb > CONFIG["IMB_THRESHOLD"] and price > self.price_hist[-15] and vol > CONFIG["VOL_MIN"]
                dn_sig = imb < -CONFIG["IMB_THRESHOLD"] and price < self.price_hist[-15] and vol > CONFIG["VOL_MIN"]

                if time.time() - self.last_trade < 10:
                    await asyncio.sleep(CONFIG["POLL"]); continue

                side = "BUY" if up_sig else "SELL" if dn_sig else None
                if side:
                    entry, start_t = price, time.time()
                    self.last_trade = start_t
                    sl_price = entry * (Decimal("0.9975") if side == "BUY" else Decimal("1.0025"))
                    log.info(f"🚀 {side} @ {entry} | Vol: {round(vol*100,3)}%")

                    while True:
                        p, _ = await self.fetch(session)
                        if p is None: continue
                        
                        move = (p - entry) / entry if side == "BUY" else (entry - p) / entry
                        
                        # 1. Trailing Stop (Move SL to Breakeven after 0.2% gain)
                        if move >= Decimal("0.0020"):
                            sl_price = entry # Can't lose money now!

                        # 2. Exit Conditions
                        if move >= CONFIG["TP"]: res = "✅ TP"; break
                        if (side == "BUY" and p <= sl_price) or (side == "SELL" and p >= sl_price):
                            res = "❌ SL"; break
                        
                        # 3. Dynamic Time Exit (Only kill if losing after 5 mins)
                        if time.time() - start_t > CONFIG["MAX_HOLD"] and move < 0:
                            res = "⌛ TIME"; break
                        elif time.time() - start_t > CONFIG["MAX_HOLD"] * 2: # Hard cap 10 mins
                            res = "⌛ MAX_TIME"; break

                        await asyncio.sleep(0.3)

                    pnl = CONFIG["TRADE_SIZE"] * (move - CONFIG["FEE"])
                    self.balance += pnl
                    log.info(f"{res} | Move: {round(move*100,3)}% | Net: ${round(pnl,4)} | Bal: ${round(self.balance,3)}")

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV22().run())
