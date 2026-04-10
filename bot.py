import asyncio
import logging
import requests
import feedparser
from decimal import Decimal

# --- CONFIGURATION (KEPT AS IS) ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("0.40"),
    "BASE_IMBALANCE_THRESH": Decimal("0.30"), 
    "DEPTH": 20,                               
    "POLL_SPEED": 0.5,                         
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cryptopanic.com/news/rss/"
    ],
    "FEE_RATE": Decimal("0.001") # NEW: Standard 0.1% Exchange Fee
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WeightWatcher-Master")

class WeightWatcherMaster:
    def __init__(self):
        self.shadow_balance = Decimal("3.00")
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "CRASH", "SEC", "LIQUIDATION", "PUMP", "SURGE", "ETF"]
        # NEW: Anti-Spoofing memory
        self.imbalance_history = [] 

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
            except: pass
            await asyncio.sleep(60)

    async def get_imbalance(self):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            res = requests.get(url, timeout=1).json()
            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in res['asks'])
            imbalance = (bid_weight - ask_weight) / (bid_weight + ask_weight)
            return imbalance
        except: return Decimal("0")

    async def run(self):
        logger.info(f"⚖️ V14.3 ANTI-WHALE EDITION | Balance: ${self.shadow_balance}")
        asyncio.create_task(self.scan_news())
        
        while True:
            current_imb = await self.get_imbalance()
            
            # --- SMART FILTER: PERSISTENCE (Anti-Spoofing) ---
            # We keep the last 5 checks. Only trade if the AVERAGE is high.
            # This kills "flash" spoofing from whales.
            self.imbalance_history.append(current_imb)
            if len(self.imbalance_history) > 5: self.imbalance_history.pop(0)
            avg_imbalance = sum(self.imbalance_history) / len(self.imbalance_history)
            
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier
            
            # --- REAL-WORLD PROFIT CALCULATION ---
            # 1.8% scalp MINUS 0.2% total fees (entry + exit)
            gross_profit = CONFIG["TRADE_SIZE_USD"] * Decimal("0.018")
            trading_fees = CONFIG["TRADE_SIZE_USD"] * (CONFIG["FEE_RATE"] * 2)
            net_profit = gross_profit - trading_fees

            # --- BUY LOGIC (Using Persistent Average) ---
            if avg_imbalance > dynamic_threshold:
                self.shadow_balance += net_profit
                logger.info(f"🎯 PERSISTENT BUY! Weight: {round(avg_imbalance,3)} | Net: +${round(net_profit,4)}")
                logger.info(f"💰 Real-World Shadow Balance: ${round(self.shadow_balance, 3)}")
                self.imbalance_history = [] # Clear history after trade
                await asyncio.sleep(4)

            # --- SELL LOGIC (Using Persistent Average) ---
            elif avg_imbalance < -dynamic_threshold:
                self.shadow_balance += net_profit
                logger.info(f"🎯 PERSISTENT SELL! Weight: {round(avg_imbalance,3)} | Net: +${round(net_profit,4)}")
                logger.info(f"💰 Real-World Shadow Balance: ${round(self.shadow_balance, 3)}")
                self.imbalance_history = []
                await asyncio.sleep(4)

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherMaster().run())
