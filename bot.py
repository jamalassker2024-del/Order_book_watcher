import asyncio
import logging
import requests
import feedparser
from decimal import Decimal
import os
import time

# --- PRODUCTION CONFIGURATION ---
CONFIG = {
    "SYMBOL": "PEPEUSDT",         
    "TRADE_SIZE_USD": Decimal("1.00"), 
    "BASE_IMBALANCE_THRESH": Decimal("0.10"), # LOWERED for more action
    "DEPTH": 20,                               
    "POLL_SPEED": 1.0, # Slowed slightly to prevent API rate limits                        
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://cryptopanic.com/news/rss/"
    ],
    "FEE_RATE": Decimal("0.001"), 
    "PROFIT_TARGET": Decimal("0.012") 
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WeightWatcher-PRO")

class WeightWatcherPRO:
    def __init__(self):
        self.shadow_balance = Decimal("3.00")
        self.news_multiplier = Decimal("1.0")
        self.imbalance_history = []
        self.start_time = time.time()

    async def scan_news(self):
        hot_keywords = ["BREAKING", "SEC", "LIQUIDATION", "PUMP", "LISTING"]
        while True:
            try:
                found_heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:5]:
                        if any(word in entry.title.upper() for word in hot_keywords):
                            found_heat = True
                            break
                self.news_multiplier = Decimal("0.7") if found_heat else Decimal("1.0")
            except:
                pass
            await asyncio.sleep(60)

    async def get_market_data(self):
        try:
            depth_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit={CONFIG['DEPTH']}"
            price_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            
            depth_res = requests.get(depth_url, timeout=2).json()
            price_res = requests.get(price_url, timeout=2).json()

            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in depth_res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in depth_res['asks'])
            
            # Prevent division by zero
            total_weight = bid_weight + ask_weight
            imbalance = (bid_weight - ask_weight) / total_weight if total_weight > 0 else Decimal("0")

            return {
                "imbalance": imbalance,
                "best_bid": Decimal(price_res['bidPrice']),
                "best_ask": Decimal(price_res['askPrice'])
            }
        except Exception as e:
            logger.error(f"Data Fetch Error: {e}")
            return None

    async def run(self):
        logger.info(f"🚀 V15.1 LIVE MONITOR | Target: {CONFIG['SYMBOL']}")
        asyncio.create_task(self.scan_news())
        
        while True:
            data = await self.get_market_data()
            if not data:
                await asyncio.sleep(2)
                continue

            current_imb = data["imbalance"]
            self.imbalance_history.append(current_imb)
            if len(self.imbalance_history) > 5: self.imbalance_history.pop(0)
            
            avg_imbalance = sum(self.imbalance_history) / len(self.imbalance_history)
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier

            # --- LIVE HEARTBEAT LOG ---
            # This ensures you always see movement in Railway
            logger.info(f"🔍 AVG Imbalance: {round(avg_imbalance, 4)} | Target: ±{round(dynamic_threshold, 2)}")

            if abs(avg_imbalance) > dynamic_threshold:
                side = "BUY" if avg_imbalance > 0 else "SELL"
                entry_price = data["best_ask"] if side == "BUY" else data["best_bid"]
                
                logger.info(f"🎯 {side} SIGNAL TRIGGERED | Entry: {entry_price}")
                
                # Wait for 10 seconds of price movement
                await asyncio.sleep(10) 
                
                result_data = await self.get_market_data()
                if result_data:
                    exit_price = result_data["best_bid"] if side == "BUY" else result_data["best_ask"]
                    
                    # Calculation
                    raw_move = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                    net_profit_percent = raw_move - (CONFIG["FEE_RATE"] * 2)
                    
                    profit_usd = CONFIG["TRADE_SIZE_USD"] * net_profit_percent
                    self.shadow_balance += profit_usd
                    
                    status = "✅ PROFIT" if profit_usd > 0 else "🔻 LOSS/FEES"
                    logger.info(f"{status}: {round(profit_usd, 4)} | New Balance: ${round(self.shadow_balance, 3)}")
                
                self.imbalance_history = [] 
                await asyncio.sleep(5) 

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherPRO().run())
