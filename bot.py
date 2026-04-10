import asyncio
import logging
import requests
import feedparser
from decimal import Decimal

# --- CONFIGURATION ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal(".30"),
    "BASE_IMBALANCE_THRESH": Decimal("0.30"), 
    "CONVERT_SPREAD": Decimal("0.001"),        # 0.1% Hidden Spread fee
    "DEPTH": 20,                               
    "POLL_SPEED": 0.5,                         
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
        self.shadow_balance = Decimal("77.70") # Adjusted to your stated balance
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "CRASH", "SEC", "LIQUIDATION", "PUMP", "SURGE", "ETF"]

    async def scan_news(self):
        while True:
            try:
                found_heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:3]:
                        if any(word in entry.title.upper() for word in self.hot_keywords):
                            found_heat = True
                            break
                self.news_multiplier = Decimal("0.5") if found_heat else Decimal("1.0")
            except:
                pass
            await asyncio.sleep(60)

    async def get_market_data(self):
        """Fetches both imbalance and the current mid-price"""
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            res = requests.get(url, timeout=1).json()
            
            # Calculate Weight
            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in res['asks'])
            imbalance = (bid_weight - ask_weight) / (bid_weight + ask_weight)
            
            # Get Mid-Price
            mid_price = (Decimal(res['bids'][0][0]) + Decimal(res['asks'][0][0])) / 2
            return imbalance, mid_price
        except:
            return Decimal("0"), Decimal("0")

    async def run(self):
        logger.info(f"⚖️ V14.3 TRUTH EDITION ONLINE | Balance: ${self.shadow_balance}")
        asyncio.create_task(self.scan_news())
        
        while True:
            imbalance, entry_price = await self.get_market_data()
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier
            
            direction = None
            if imbalance > dynamic_threshold:
                direction = "BUY"
            elif imbalance < -dynamic_threshold:
                direction = "SELL"

            if direction and entry_price > 0:
                logger.info(f"🎯 {direction} TRIGGERED! Weight: {round(imbalance,3)} | Price: ${entry_price}")
                
                # Wait 5 seconds for the market to move (Trade Lock Time)
                await asyncio.sleep(5) 
                
                # Check price again after the wait
                _, exit_price = await self.get_market_data()
                
                # Calculate real PnL (including the hidden 0.1% convert spread)
                if direction == "BUY":
                    price_change = (exit_price - entry_price) / entry_price
                else:
                    price_change = (entry_price - exit_price) / entry_price
                
                # Net Profit = (Price Move %) - (Convert Fee %)
                net_profit_pct = price_change - CONFIG["CONVERT_SPREAD"]
                real_pnl = CONFIG["TRADE_SIZE_USD"] * net_profit_pct
                
                self.shadow_balance += real_pnl
                
                outcome = "✅ WIN" if real_pnl > 0 else "❌ LOSS"
                logger.info(f"{outcome} | Move: {round(price_change*100, 4)}% | Net PnL: ${round(real_pnl, 4)}")
                logger.info(f"💰 Real Shadow Balance: ${round(self.shadow_balance, 3)}")

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherMaster().run())
