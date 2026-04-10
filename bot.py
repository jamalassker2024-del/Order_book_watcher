import asyncio
import logging
import requests
import feedparser
from decimal import Decimal
from collections import deque

# --- CONFIGURATION ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal(".30"),
    "WINNER_THRESHOLD": Decimal("0.90"),     # Only 90%+ imbalance triggers
    "CONVERT_SPREAD": Decimal("0.001"),       # 0.1% real-world fee/spread
    "TRADE_DURATION": 60,                     # Give it 60s to beat the fee
    "DEPTH": 20,
    "POLL_SPEED": 1.0,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("Sniper-Bot")

class SniperBot:
    def __init__(self):
        self.shadow_balance = Decimal("77.69") # Starting from your last log
        self.price_history = deque(maxlen=30)  # Stores 30 seconds of price data

    async def get_market_data(self):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            res = requests.get(url, timeout=1).json()
            bid_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in res['bids'])
            ask_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in res['asks'])
            imbalance = (bid_w - ask_w) / (bid_w + ask_w)
            mid_price = (Decimal(res['bids'][0][0]) + Decimal(res['asks'][0][0])) / 2
            return imbalance, mid_price
        except:
            return Decimal("0"), Decimal("0")

    async def run(self):
        logger.info(f"🚀 SNIPER V15 ONLINE | Target: {CONFIG['WINNER_THRESHOLD']} Imbalance")
        
        while True:
            imb, current_price = await self.get_market_data()
            if current_price == 0: continue
            
            self.price_history.append(current_price)
            
            # --- TREND FILTER ---
            # Only trade if we have 30s of data and price is moving our way
            if len(self.price_history) < 30:
                await asyncio.sleep(1)
                continue
                
            old_price = self.price_history[0]
            is_uptrend = current_price > old_price
            is_downtrend = current_price < old_price

            # --- SNIPER LOGIC ---
            direction = None
            if imb > CONFIG["WINNER_THRESHOLD"] and is_uptrend:
                direction = "BUY"
            elif imb < -CONFIG["WINNER_THRESHOLD"] and is_downtrend:
                direction = "SELL"

            if direction:
                logger.info(f"🎯 SNIPER {direction} | Imbalance: {round(imb,3)} | Price: ${current_price}")
                
                # The Patient Wait (60 seconds)
                await asyncio.sleep(CONFIG["TRADE_DURATION"])
                
                _, exit_price = await self.get_market_data()
                
                # Math: (Price Move) - (Entry Fee + Exit Fee)
                if direction == "BUY":
                    move = (exit_price - current_price) / current_price
                else:
                    move = (current_price - exit_price) / current_price
                
                # Subtract spread (0.1%)
                net_move = move - CONFIG["CONVERT_SPREAD"]
                pnl = CONFIG["TRADE_SIZE_USD"] * net_move
                self.shadow_balance += pnl
                
                status = "💰 WIN" if pnl > 0 else "💀 LOSS"
                logger.info(f"{status} | Move: {round(move*100,3)}% | Net PnL: ${round(pnl,4)}")
                logger.info(f"🏦 New Balance: ${round(self.shadow_balance, 3)}")
            
            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(SniperBot().run())
