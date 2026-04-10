import asyncio
import logging
import requests
import feedparser
from decimal import Decimal
import os
import time

# --- SNIPER CONFIGURATION ---
CONFIG = {
    "SYMBOL": "PEPEUSDT",
    "TRADE_SIZE_USD": Decimal("1.00"),
    "BASE_IMBALANCE_THRESH": Decimal("0.45"), # High threshold = High quality
    "DEPTH": 20,
    "POLL_SPEED": 0.5,
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://cryptopanic.com/news/rss/"
    ],
    "FEE_RATE": Decimal("0.001"), # 0.1% per trade
    "MIN_MOVE_REQUIRED": Decimal("0.003") # 0.3% move needed to clear fees + profit
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WeightWatcher-Sniper")

class WeightWatcherSniper:
    def __init__(self):
        self.shadow_balance = Decimal("30.00") # Reset for fresh testing
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
                self.news_multiplier = Decimal("0.8") if found_heat else Decimal("1.0")
            except: pass
            await asyncio.sleep(60)

    async def get_market_data(self):
        try:
            depth_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit={CONFIG['DEPTH']}"
            price_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            
            # Using a session would be faster, but requests is fine for this polling speed
            depth_res = requests.get(depth_url, timeout=2).json()
            price_res = requests.get(price_url, timeout=2).json()

            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in depth_res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in depth_res['asks'])
            
            total_weight = bid_weight + ask_weight
            imbalance = (bid_weight - ask_weight) / total_weight if total_weight > 0 else Decimal("0")

            return {
                "imbalance": imbalance,
                "best_bid": Decimal(price_res['bidPrice']),
                "best_ask": Decimal(price_res['askPrice'])
            }
        except: return None

    async def run(self):
        logger.info(f"🎯 SNIPER MODE ACTIVE | Threshold: {CONFIG['BASE_IMBALANCE_THRESH']}")
        asyncio.create_task(self.scan_news())
        
        while True:
            data = await self.get_market_data()
            if not data:
                await asyncio.sleep(1)
                continue

            current_imb = data["imbalance"]
            self.imbalance_history.append(current_imb)
            if len(self.imbalance_history) > 8: self.imbalance_history.pop(0) # Longer memory
            
            avg_imbalance = sum(self.imbalance_history) / len(self.imbalance_history)
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier

            # Only log if it's getting close to interesting (saves Railway log space)
            if abs(avg_imbalance) > (dynamic_threshold * Decimal("0.7")):
                logger.info(f"👀 Watching Volatility... Imbalance: {round(avg_imbalance, 3)}")

            if abs(avg_imbalance) > dynamic_threshold:
                side = "BUY" if avg_imbalance > 0 else "SELL"
                entry_price = data["best_ask"] if side == "BUY" else data["best_bid"]
                
                logger.info(f"🔥 SNIPER TRIGGERED: {side} at {entry_price}")
                
                # Wait longer for the 'Whale' move to actually happen
                await asyncio.sleep(30) 
                
                result_data = await self.get_market_data()
                if result_data:
                    exit_price = result_data["best_bid"] if side == "BUY" else result_data["best_ask"]
                    
                    # Math: (Difference / Entry) - (2 * Fees)
                    price_move = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                    total_fees = CONFIG["FEE_RATE"] * 2
                    net_return = price_move - total_fees
                    
                    profit_usd = CONFIG["TRADE_SIZE_USD"] * net_return
                    self.shadow_balance += profit_usd
                    
                    if net_return > 0:
                        logger.info(f"💰 SNIPER WIN! Net: +${round(profit_usd, 4)} | Balance: ${round(self.shadow_balance, 3)}")
                    else:
                        logger.info(f"💀 SNIPER MISS (Fees/Slippage): -${round(abs(profit_usd), 4)} | Balance: ${round(self.shadow_balance, 3)}")
                
                self.imbalance_history = [] 
                await asyncio.sleep(10) # Longer cooldown to avoid trading in the same 'spike'

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherSniper().run())
