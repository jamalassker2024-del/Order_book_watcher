import asyncio
import logging
import requests
import feedparser
from decimal import Decimal
import os
import time

# --- PRODUCTION CONFIGURATION ---
CONFIG = {
    "SYMBOL": "PEPEUSDT",         # Better for small balance volatility
    "TRADE_SIZE_USD": Decimal("1.00"), # Minimum Binance Notional
    "BASE_IMBALANCE_THRESH": Decimal("0.35"), 
    "DEPTH": 20,                               
    "POLL_SPEED": 0.5,                         
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://cryptopanic.com/news/rss/"
    ],
    "FEE_RATE": Decimal("0.001"), # 0.1% Standard Fee
    "PROFIT_TARGET": Decimal("0.012") # 1.2% realistic scalp
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WeightWatcher-PRO")

class WeightWatcherPRO:
    def __init__(self):
        # Starts at $3.00 (Simulated until you deposit your Reward Card)
        self.shadow_balance = Decimal("3.00")
        self.news_multiplier = Decimal("1.0")
        self.imbalance_history = []
        self.start_time = time.time()

    async def scan_news(self):
        """Reduces threshold during high-volatility news events"""
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
            except Exception as e:
                logger.warning(f"News Scan Lag: {e}")
            await asyncio.sleep(60)

    async def get_market_data(self):
        """Fetches Order Book and Current Prices simultaneously"""
        try:
            depth_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit={CONFIG['DEPTH']}"
            price_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            
            # Using requests for simplicity; for ultra-HFT use aiohttp
            depth_res = requests.get(depth_url, timeout=1).json()
            price_res = requests.get(price_url, timeout=1).json()

            bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in depth_res['bids'])
            ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in depth_res['asks'])
            imbalance = (bid_weight - ask_weight) / (bid_weight + ask_weight)

            return {
                "imbalance": imbalance,
                "best_bid": Decimal(price_res['bidPrice']),
                "best_ask": Decimal(price_res['askPrice'])
            }
        except:
            return None

    async def run(self):
        logger.info(f"🚀 V15 PRODUCTION READY | Target: {CONFIG['SYMBOL']}")
        asyncio.create_task(self.scan_news())
        
        while True:
            data = await self.get_market_data()
            if not data:
                await asyncio.sleep(1)
                continue

            current_imb = data["imbalance"]
            self.imbalance_history.append(current_imb)
            if len(self.imbalance_history) > 5: self.imbalance_history.pop(0)
            avg_imbalance = sum(self.imbalance_history) / len(self.imbalance_history)
            
            dynamic_threshold = CONFIG["BASE_IMBALANCE_THRESH"] * self.news_multiplier

            # --- EXECUTION LOGIC ---
            if abs(avg_imbalance) > dynamic_threshold:
                side = "BUY" if avg_imbalance > 0 else "SELL"
                entry_price = data["best_ask"] if side == "BUY" else data["best_bid"]
                
                logger.info(f"🎯 {side} SIGNAL | Weight: {round(avg_imbalance,3)} | Entry: {entry_price}")
                
                # Real-world delay: Wait for price action
                await asyncio.sleep(10) 
                
                # Check if trade was profitable
                result_data = await self.get_market_data()
                if result_data:
                    exit_price = result_data["best_bid"] if side == "BUY" else result_data["best_ask"]
                    
                    # Calculate Net PnL minus 0.2% round-trip fees
                    raw_move = (exit_price - entry_price) / entry_price if side == "BUY" else (entry_price - exit_price) / entry_price
                    net_profit_percent = raw_move - (CONFIG["FEE_RATE"] * 2)
                    
                    if net_profit_percent > 0:
                        profit_usd = CONFIG["TRADE_SIZE_USD"] * net_profit_percent
                        self.shadow_balance += profit_usd
                        logger.info(f"💰 PROFIT! Net: +${round(profit_usd, 4)} | Balance: ${round(self.shadow_balance, 3)}")
                    else:
                        loss_usd = CONFIG["TRADE_SIZE_USD"] * abs(net_profit_percent)
                        self.shadow_balance -= loss_usd
                        logger.info(f"🔻 SLIPPAGE/LOSS: -${round(loss_usd, 4)} | Balance: ${round(self.shadow_balance, 3)}")
                
                self.imbalance_history = [] # Reset after execution
                await asyncio.sleep(5) # Cooldown

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    try:
        asyncio.run(WeightWatcherPRO().run())
    except KeyboardInterrupt:
        pass
