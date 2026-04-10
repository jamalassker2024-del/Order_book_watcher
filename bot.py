import asyncio
import logging
import requests
from decimal import Decimal
from collections import deque
import time

# --- CONFIG ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("0.30"),

    "WINNER_THRESHOLD": Decimal("0.75"),   # ✅ FIXED
    "SPREAD_LIMIT": Decimal("0.0005"),     # 0.05%
    "FEE": Decimal("0.001"),               # 0.1%

    "TRADE_DURATION": 20,                  # ✅ faster exits
    "DEPTH": 20,
    "POLL_SPEED": 1,
    "COOLDOWN": 10                        # ✅ prevent spam
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("SNIPER-V17")

class SniperBot:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_history = deque(maxlen=15)   # ✅ faster trend
        self.last_trade_time = 0

    def get_market_data(self):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            data = requests.get(url, timeout=2).json()

            bids = data['bids']
            asks = data['asks']

            bid_vol = sum(Decimal(b[1]) for b in bids)
            ask_vol = sum(Decimal(a[1]) for a in asks)

            imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)

            best_bid = Decimal(bids[0][0])
            best_ask = Decimal(asks[0][0])

            mid_price = (best_bid + best_ask) / 2

            spread = (best_ask - best_bid) / best_bid  # ✅ FIXED

            return imbalance, mid_price, spread

        except Exception as e:
            logger.warning(f"API error: {e}")
            return Decimal("0"), Decimal("0"), Decimal("0")

    async def run(self):
        logger.info("🔥 SNIPER V17 LIVE (ACTIVE MODE)")

        while True:
            imb, price, spread = self.get_market_data()

            if price == 0:
                await asyncio.sleep(1)
                continue

            self.price_history.append(price)

            logger.info(f"Price: {price} | Imb: {round(imb,3)} | Spread: {round(spread,6)}")

            # --- CONDITIONS ---
            if len(self.price_history) < 10:
                await asyncio.sleep(1)
                continue

            old_price = self.price_history[0]

            uptrend = price > old_price
            downtrend = price < old_price

            now = time.time()

            # Cooldown
            if now - self.last_trade_time < CONFIG["COOLDOWN"]:
                continue

            # Spread filter
            if spread > CONFIG["SPREAD_LIMIT"]:
                logger.info("⛔ Skipped: Spread too high")
                continue

            # --- SIGNAL ---
            direction = None

            if imb > CONFIG["WINNER_THRESHOLD"] and uptrend:
                direction = "BUY"

            elif imb < -CONFIG["WINNER_THRESHOLD"] and downtrend:
                direction = "SELL"

            # --- EXECUTION ---
            if direction:
                logger.info(f"🚀 TRADE {direction} | Imb: {round(imb,3)}")

                entry_price = price
                self.last_trade_time = now

                await asyncio.sleep(CONFIG["TRADE_DURATION"])

                _, exit_price, _ = self.get_market_data()

                if direction == "BUY":
                    move = (exit_price - entry_price) / entry_price
                else:
                    move = (entry_price - exit_price) / entry_price

                net_move = move - CONFIG["FEE"]

                pnl = CONFIG["TRADE_SIZE_USD"] * net_move
                self.balance += pnl

                if pnl > 0:
                    logger.info(f"💰 WIN | PnL: {round(pnl,4)}")
                else:
                    logger.info(f"💀 LOSS | PnL: {round(pnl,4)}")

                logger.info(f"🏦 Balance: {round(self.balance,2)}")

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(SniperBot().run())
