import asyncio
import logging
import aiohttp
from decimal import Decimal
from collections import deque
import time

# --- CONFIG ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("0.30"),

    "WINNER_THRESHOLD": Decimal("0.90"),

    "TP": Decimal("0.003"),   # 0.3%
    "SL": Decimal("0.003"),   # 0.3%

    "SPREAD_MAX": Decimal("0.0004"),  # 0.04%

    "FEE": Decimal("0.001"),  # 0.1% per side

    "MOMENTUM_MIN": Decimal("0.0015"),  # 0.15%

    "DEPTH": 20,
    "POLL_SPEED": 1.0,

    "COOLDOWN": 10
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Sniper-V16")


class SniperV16:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_history = deque(maxlen=30)
        self.position = None
        self.last_trade_time = 0

    async def fetch_data(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit={CONFIG['DEPTH']}"
            async with session.get(url, timeout=1) as resp:
                data = await resp.json()

            bids = data["bids"]
            asks = data["asks"]

            bid_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
            ask_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)

            best_bid = Decimal(bids[0][0])
            best_ask = Decimal(asks[0][0])

            mid = (best_bid + best_ask) / 2
            spread = (best_ask - best_bid) / best_bid
            imbalance = (bid_w - ask_w) / (bid_w + ask_w)

            return imbalance, mid, spread

        except:
            return None, None, None

    def momentum(self):
        if len(self.price_history) < 30:
            return None
        old = self.price_history[0]
        new = self.price_history[-1]
        return (new - old) / old

    async def check_entry(self, imbalance, price, spread):
        now = time.time()

        if self.position is not None:
            return

        if now - self.last_trade_time < CONFIG["COOLDOWN"]:
            return

        if spread > CONFIG["SPREAD_MAX"]:
            return

        mom = self.momentum()
        if mom is None:
            return

        direction = None

        if imbalance > CONFIG["WINNER_THRESHOLD"] and mom > CONFIG["MOMENTUM_MIN"]:
            direction = "BUY"

        elif imbalance < -CONFIG["WINNER_THRESHOLD"] and mom < -CONFIG["MOMENTUM_MIN"]:
            direction = "SELL"

        if direction:
            self.position = {
                "side": direction,
                "entry": price,
                "open_time": now
            }

            logger.info(f"🚀 ENTRY {direction} | Price: {price} | Imb: {round(imbalance,3)} | Mom: {round(mom,4)}")

    async def manage_trade(self, price):
        if self.position is None:
            return

        entry = self.position["entry"]
        side = self.position["side"]

        move = (price - entry) / entry
        if side == "SELL":
            move = -move

        # TP / SL check
        if move >= CONFIG["TP"] or move <= -CONFIG["SL"]:
            await self.close_trade(price, move)

    async def close_trade(self, price, move):
        size = CONFIG["TRADE_SIZE_USD"]

        gross = size * Decimal(str(move))
        fees = size * CONFIG["FEE"] * 2
        pnl = gross - fees

        self.balance += pnl
        self.last_trade_time = time.time()

        status = "💰 WIN" if pnl > 0 else "💀 LOSS"

        logger.info(f"{status} EXIT | Move: {round(move*100,3)}% | PnL: {round(pnl,5)}")
        logger.info(f"🏦 Balance: {round(self.balance, 3)}")

        self.position = None

    async def run(self):
        logger.info("🔥 SNIPER V16 LIVE (REALISTIC MODE)")

        async with aiohttp.ClientSession() as session:
            while True:
                imbalance, price, spread = await self.fetch_data(session)

                if price is None:
                    await asyncio.sleep(1)
                    continue

                self.price_history.append(price)

                logger.info(f"Price: {price} | Imb: {round(imbalance,3)} | Spread: {round(spread,5)}")

                await self.check_entry(imbalance, price, spread)
                await self.manage_trade(price)

                await asyncio.sleep(CONFIG["POLL_SPEED"])


if __name__ == "__main__":
    asyncio.run(SniperV16().run())
