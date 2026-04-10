import asyncio
import logging
import aiohttp
import feedparser
import time
from decimal import Decimal

# --- REAL-WORLD CONFIGURATION ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("1.00"),    # USD size
    "BASE_IMB_THRESH": Decimal("0.35"),# 35% Weight difference
    "DEPTH": 20,
    "POLL_SPEED": 0.3,                 # 300ms for faster reaction
    "TP": Decimal("0.0050"),           # 0.50% Actual Move (Realistic for scalping)
    "SL": Decimal("0.0025"),           # 0.25% Stop Loss
    "FEE": Decimal("0.001"),           # 0.1% Binance Fee
    "NEWS_FEEDS": [
        "https://cointelegraph.com/rss",
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cryptopanic.com/news/rss/"
    ]
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V25-SENTINEL")

class WeightWatcherSentinel:
    def __init__(self):
        self.balance = Decimal("10.00")
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "CRASH", "SEC", "LIQUIDATION", "PUMP", "SURGE", "ETF"]
        self.last_news_check = 0

    # --- NEWS SCANNER (Sensitivity Booster) ---
    async def scan_news(self, session):
        while True:
            try:
                found_heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    async with session.get(url, timeout=5) as resp:
                        content = await resp.text()
                        feed = feedparser.parse(content)
                        for entry in feed.entries[:5]:
                            if any(word in entry.title.upper() for word in self.hot_keywords):
                                found_heat = True
                                break
                
                self.news_multiplier = Decimal("0.6") if found_heat else Decimal("1.0")
                if found_heat:
                    log.info("📡 NEWS ALERT: Market Heat Detected. Lowering Entry Bar.")
            except Exception as e:
                log.debug(f"News fetch error: {e}")
            await asyncio.sleep(60)

    # --- ORDER BOOK & PRICE ENGINE ---
    async def get_market_data(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit={CONFIG['DEPTH']}"
            async with session.get(url, timeout=1) as resp:
                res = await resp.json()
                
                # Best Bid/Ask for Mid Price
                bid_price = Decimal(res['bids'][0][0])
                ask_price = Decimal(res['asks'][0][0])
                mid_price = (bid_price + ask_price) / 2
                
                # Weight calculation
                bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in res['bids'])
                ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in res['asks'])
                
                imbalance = (bid_weight - ask_weight) / (bid_weight + ask_weight)
                return imbalance, mid_price
        except:
            return None, None

    async def run(self):
        log.info(f"⚖️ V25 SENTINEL ONLINE | Live Conditions Enabled | Bal: ${self.balance}")
        
        async with aiohttp.ClientSession() as session:
            # Start News Task
            asyncio.create_task(self.scan_news(session))
            
            while True:
                imb, price = await self.get_market_data(session)
                if imb is None: continue
                
                dynamic_thresh = CONFIG["BASE_IMB_THRESH"] * self.news_multiplier
                
                direction = None
                if imb > dynamic_thresh: direction = "BUY"
                elif imb < -dynamic_thresh: direction = "SELL"
                
                if direction:
                    entry_price = price
                    log.info(f"🚀 ENTRY {direction} @ {entry_price} | Imb: {round(imb,3)}")
                    
                    # --- LIVE POSITION TRACKING ---
                    start_time = time.time()
                    while True:
                        _, current_price = await self.get_market_data(session)
                        if current_price is None: continue
                        
                        # Calculate Profit/Loss based on direction
                        if direction == "BUY":
                            move = (current_price - entry_price) / entry_price
                        else:
                            move = (entry_price - current_price) / entry_price
                            
                        # Exit Checks
                        if move >= CONFIG["TP"]:
                            net_pnl = CONFIG["TRADE_SIZE"] * (move - (CONFIG["FEE"] * 2))
                            self.balance += net_pnl
                            log.info(f"✅ TAKE PROFIT | Move: {round(move*100,2)}% | Net: ${round(net_pnl,4)} | Bal: ${round(self.balance,3)}")
                            break
                        
                        if move <= -CONFIG["SL"]:
                            net_pnl = CONFIG["TRADE_SIZE"] * (move - (CONFIG["FEE"] * 2))
                            self.balance += net_pnl
                            log.info(f"❌ STOP LOSS | Move: {round(move*100,2)}% | Net: ${round(net_pnl,4)} | Bal: ${round(self.balance,3)}")
                            break
                            
                        # Safety Timeout (10 minutes)
                        if time.time() - start_time > 600:
                            log.info("⌛ Trade Timed Out. Exiting at Mid-Market.")
                            break
                            
                        await asyncio.sleep(0.3)

                await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(WeightWatcherSentinel().run())
