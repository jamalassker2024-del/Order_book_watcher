import asyncio
import json
import logging
import websockets
import feedparser
import requests
from decimal import Decimal
from collections import deque
import time

# --- CONFIGURATION ---
CONFIG = {
    "SYMBOL": "btcusdt",
    "TRADE_SIZE_USD": Decimal("20.0"),
    "BASE_THRESHOLD": Decimal("0.15"),  # 15% imbalance
    "CONFIRM_SNAPSHOTS": 5,             # 500ms of sustained pressure
    "NEWS_FEEDS": ["https://cryptopanic.com/news/rss/"],
    "FEE": Decimal("0.0004"),           # Standard VIP0 Maker/Taker avg
    "TARGET_PROFIT": Decimal("0.0035"), # 0.35% (Realistic scalp)
    "STOP_LOSS": Decimal("-0.0060"),    # 0.6%
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("Sentinel")

class HybridSentinel:
    def __init__(self):
        self.balance = Decimal("100.00")
        self.news_multiplier = Decimal("1.0")
        self.imb_stream = deque(maxlen=CONFIG["CONFIRM_SNAPSHOTS"])
        self.active_trade = None
        self.hot_keywords = ["BREAKING", "SEC", "ETF", "PUMP", "DUMP", "LIQUIDATION"]

    # --- NEWS ENGINE (From Strategy 1) ---
    async def scan_news(self):
        """Reduces threshold (increases sensitivity) during breaking news"""
        while True:
            try:
                heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:5]:
                        if any(w in entry.title.upper() for w in self.hot_keywords):
                            heat = True; break
                self.news_multiplier = Decimal("0.6") if heat else Decimal("1.0")
                if heat: log.info("📡 VOLATILITY ALERT: News is hot. Tightening sensors.")
            except Exception as e: log.error(f"News Error: {e}")
            await asyncio.sleep(60)

    def get_imbalance(self, bids, asks):
        """Calculates USD weight imbalance (Price * Quantity)"""
        bid_weight = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
        ask_weight = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
        return (bid_weight - ask_weight) / (bid_weight + ask_weight)

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth20@100ms"
        asyncio.create_task(self.scan_news())
        
        async with websockets.connect(url) as ws:
            log.info("⚔️ SENTINEL HYBRID ONLINE")
            while True:
                data = json.loads(await ws.recv())
                bids, asks = data.get("bids", []), data.get("asks", [])
                if not bids: continue

                price = Decimal(bids[0][0])
                current_imb = self.get_imbalance(bids, asks)
                self.imb_stream.append(current_imb)

                # --- 1. TRADE MANAGEMENT ---
                if self.active_trade:
                    t = self.active_trade
                    # Calculate Net ROI
                    move = (price - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - price) / t['entry']
                    net = move - (CONFIG["FEE"] * 2)

                    if net >= CONFIG["TARGET_PROFIT"] or net <= CONFIG["STOP_LOSS"]:
                        pnl = CONFIG["TRADE_SIZE_USD"] * net
                        self.balance += pnl
                        status = "💰 WIN" if net > 0 else "❌ LOSS"
                        log.info(f"{status} | Net: {round(net*100,3)}% | New Bal: ${round(self.balance,2)}")
                        self.active_trade = None
                        await asyncio.sleep(2) # Cooldown

                # --- 2. ENTRY LOGIC (Weighted & Sustained) ---
                elif len(self.imb_stream) == CONFIG["CONFIRM_SNAPSHOTS"]:
                    avg_imb = sum(self.imb_stream) / len(self.imb_stream)
                    dynamic_thresh = CONFIG["BASE_THRESHOLD"] * self.news_multiplier
                    
                    side = None
                    if avg_imb > dynamic_thresh: side = "BUY"
                    elif avg_imb < -dynamic_thresh: side = "SELL"

                    if side:
                        self.active_trade = {"side": side, "entry": price, "time": time.time()}
                        log.info(f"🚀 {side} ENTRY | Imbalance: {round(avg_imb,3)} | Price: {price}")

if __name__ == "__main__":
    asyncio.run(HybridSentinel().run())
