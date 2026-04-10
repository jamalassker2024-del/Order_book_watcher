import asyncio
import logging
import aiohttp
import time
from decimal import Decimal
from collections import deque

CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("1.5"),

    "IMB_THRESHOLD": Decimal("0.55"),
    "IMB_ACCEL": Decimal("0.15"),   # NEW (momentum)

    "TP": Decimal("0.0015"),        # 0.15%
    "SL": Decimal("0.0010"),        # 0.10%

    "MAX_HOLD": 15,
    "POLL": 0.3,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V19-SNIPER")

class SniperV19:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=10)
        self.imb_hist = deque(maxlen=5)
        self.last_trade = 0

    async def fetch(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=20"
            async with session.get(url, timeout=1) as r:
                d = await r.json()

                bid = Decimal(d['bids'][0][0])
                ask = Decimal(d['asks'][0][0])
                mid = (bid + ask) / 2

                bid_vol = sum(Decimal(x[1]) for x in d['bids'])
                ask_vol = sum(Decimal(x[1]) for x in d['asks'])

                imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)

                return mid, imb
        except:
            return None, None

    async def run(self):
        log.info("🚀 V19 SNIPER LIVE")

        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)

                if price is None:
                    continue

                self.price_hist.append(price)
                self.imb_hist.append(imb)

                log.info(f"P:{price} | Imb:{round(imb,3)}")

                if len(self.imb_hist) < 5:
                    await asyncio.sleep(CONFIG["POLL"])
                    continue

                # --- MOMENTUM ---
                imb_change = self.imb_hist[-1] - self.imb_hist[0]

                # --- EARLY ENTRY ---
                up_signal = imb > CONFIG["IMB_THRESHOLD"] and imb_change > CONFIG["IMB_ACCEL"]
                down_signal = imb < -CONFIG["IMB_THRESHOLD"] and imb_change < -CONFIG["IMB_ACCEL"]

                if time.time() - self.last_trade < 5:
                    continue

                direction = None
                if up_signal:
                    direction = "BUY"
                elif down_signal:
                    direction = "SELL"

                if direction:
                    entry = price
                    self.last_trade = time.time()

                    log.info(f"🎯 ENTRY {direction} @ {entry}")

                    start = time.time()

                    while True:
                        p, _ = await self.fetch(session)
                        if p is None:
                            continue

                        move = (p - entry) / entry if direction == "BUY" else (entry - p) / entry

                        if move >= CONFIG["TP"]:
                            pnl = CONFIG["TRADE_SIZE"] * (move - Decimal("0.001"))
                            self.balance += pnl
                            log.info(f"💰 TP HIT: {round(pnl,4)} | Bal:{round(self.balance,2)}")
                            break

                        if move <= -CONFIG["SL"]:
                            pnl = CONFIG["TRADE_SIZE"] * (move - Decimal("0.001"))
                            self.balance += pnl
                            log.info(f"💀 SL HIT: {round(pnl,4)} | Bal:{round(self.balance,2)}")
                            break

                        if time.time() - start > CONFIG["MAX_HOLD"]:
                            pnl = CONFIG["TRADE_SIZE"] * (move - Decimal("0.001"))
                            self.balance += pnl
                            log.info(f"⌛ EXIT: {round(pnl,4)} | Bal:{round(self.balance,2)}")
                            break

                        await asyncio.sleep(0.2)

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV19().run())
