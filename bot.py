import asyncio
import logging
import requests
import feedparser
from decimal import Decimal

# --- CONFIGURATION ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("1.00"),
    "BASE_IMBALANCE_THRESH": Decimal("0.30"), # 30% weight difference
    "DEPTH": 20,                               # Depth of order book to scan
    "POLL_SPEED": 0.5,                         # Speed of price/book checks
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cryptopanic.com/news/rss/"
    ]
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WeightWatcher-Master")

class WeightWatcherMaster:
    def __init__(self):
        self.shadow_balance = Decimal("3.00")
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "CRASH", "SEC", "LIQUIDATION", "PUMP", "SURGE", "ETF"]

    # --- TASK 1: NEWS SCANNER (Sensitivity Booster) ---
    async def scan_news(self):
        """Scans RSS feeds and lowers the threshold if news is hot"""
        while True:
            try:
                found_heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:3]:
                        if any(word in entry.title.upper() for word in self.hot_keywords):
                            found_heat = True
                            break
                
                # If news is hot, we trade on much smaller imbalances (Multiplier 0.5x reduces threshold)
                self.news_multiplier = Decimal("0.5") if found_heat else Decimal("1.0")
                if found_heat:
                    logger.info("📡 NEWS ALERT: High Volatility Expected. Increasing Sensitivity.")
            except:
                pass
            await asyncio.sleep(60)

    # --- TASK 2: ORDER BOOK ANALYSIS ---
    async def get_imbalance(self):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            res = requests.get(url, timeout=1).json()
            
            # Calculate Weight (Price * Quantity)
            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in res['asks'])
            
            imbalance = (bid_weight - ask_weight) / (bid_weight + ask_weight)
            return imbalance
        except:
            return Decimal("0")

    async def run(self):
        logger.info(f"⚖️ V14.2 MASTER DEMO ONLINE | Balance: ${self.shadow_balance}")
        asyncio.create_task(self.scan_news())
        
        while True:
            imbalance = await self.get_imbalance()
            
            # Apply News Multiplier to the Threshold
            # Example: 0.30 threshold * 0.5 (Hot News) = 0.15 threshold
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier
            
            # --- BUY LOGIC ---
            if imbalance > dynamic_threshold:
                profit = CONFIG["TRADE_SIZE_USD"] * Decimal("0.018") # 1.8% simulated scalp
                self.shadow_balance += profit
                logger.info(f"🎯 IMBALANCE BUY! Weight: {round(imbalance,3)} | News-Boost: {self.news_multiplier != 1.0}")
                logger.info(f"💰 Shadow Balance: ${round(self.shadow_balance, 3)}")
                await asyncio.sleep(4) # Simulate trade lock time

            # --- SELL LOGIC ---
            elif imbalance < -dynamic_threshold:
                profit = CONFIG["TRADE_SIZE_USD"] * Decimal("0.018")
                self.shadow_balance += profit
                logger.info(f"🎯 IMBALANCE SELL! Weight: {round(imbalance,3)} | News-Boost: {self.news_multiplier != 1.0}")
                logger.info(f"💰 Shadow Balance: ${round(self.shadow_balance, 3)}")
                await asyncio.sleep(4)

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherMaster().run())
