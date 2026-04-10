import asyncio
import aiohttp
import logging
import time
from decimal import Decimal
from collections import deque

CONFIG = {
    "SYMBOL": "PEPEUSDT",
    "TRADE_SIZE_USD": Decimal("1.0"),

    "IMB_THRESHOLD": Decimal("0.12"),   # lower = more trades
    "MAX_TRADES": 5,                    # allow multiple trades
    "COOLDOWN": 1.5,                    # seconds between entries

    "FEE": Decimal("0.001"),
    "BASE_TP": Decimal("0.004"),        # 0.4%
    "BASE_SL": Decimal("-0.004"),       # -0.4%

    "POLL": 0.3
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("SCALPER")

class Trade:
    def __init__(self, side, entry):
        self.side = side
        self.entry = entry
        self.open_time = time.time()

class ScalperBot:
    def __init__(self):
        self.balance = Decimal("30")
        self.trades = []
        self.last_entry_time = 0

        self.imb_history = deque(maxlen=10)

    async def fetch(self, session):
        try:
            url1 = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            url2 = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=5"

            async with session.get(url1) as r1, session.get(url2) as r2:
                p = await r1.json()
                d = await r2.json()

            bid = Decimal(p['bidPrice'])
            ask = Decimal(p['askPrice'])

            bid_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in d['bids'])
            ask_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in d['asks'])

            imb = (bid_w - ask_w) / (bid_w + ask_w)

            return bid, ask, imb
        except:
            return None

    def smooth_imbalance(self, imb):
        self.imb_history.append(imb)
        return sum(self.imb_history) / len(self.imb_history)

    async def run(self):
        log.info("🚀 SCALPER STARTED")

        async with aiohttp.ClientSession() as session:
            while True:
                data = await self.fetch(session)
                if not data:
                    await asyncio.sleep(CONFIG["POLL"])
                    continue

                bid, ask, imb = data
                imb = self.smooth_imbalance(imb)

                # 🔍 ENTRY LOGIC
                now = time.time()

                if (
                    abs(imb) > CONFIG["IMB_THRESHOLD"]
                    and len(self.trades) < CONFIG["MAX_TRADES"]
                    and now - self.last_entry_time > CONFIG["COOLDOWN"]
                ):
                    side = "BUY" if imb > 0 else "SELL"
                    entry = ask if side == "BUY" else bid

                    self.trades.append(Trade(side, entry))
                    self.last_entry_time = now

                    log.info(f"🚀 OPEN {side} @ {entry} | Active: {len(self.trades)}")

                # 🔄 MANAGE TRADES
                still_open = []

                for t in self.trades:
                    current = bid if t.side == "BUY" else ask

                    move = (
                        (current - t.entry) / t.entry
                        if t.side == "BUY"
                        else (t.entry - current) / t.entry
                    )

                    net = move - (CONFIG["FEE"] * 2)

                    # dynamic TP/SL
                    tp = CONFIG["BASE_TP"]
                    sl = CONFIG["BASE_SL"]

                    if net >= tp or net <= sl:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl

                        log.info(
                            f"{'💰 WIN' if net>0 else '❌ LOSS'} "
                            f"{t.side} | {round(net*100,3)}% | Bal: {round(self.balance,2)}"
                        )
                    else:
                        still_open.append(t)

                self.trades = still_open

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(ScalperBot().run())
