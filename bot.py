import asyncio
import json
import logging
import requests
import feedparser
import sys
from decimal import Decimal
import time

# --- PRODUCTION CONFIG ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("50.0"),  
    "BASE_THRESHOLD": Decimal("0.22"),   # Only trade on strong imbalance
    "FEE_RATE": Decimal("0.0004"),       # 0.04% (Binance Futures Maker/Taker avg)
    "SLIPPAGE_BPS": Decimal("0.0002"),   # 2 basis points expected slippage
    "TARGET_PROFIT": Decimal("0.0030"),  # 0.3% Realized profit target
    "STOP_LOSS": Decimal("-0.0050"),     # 0.5% Stop loss
    "NEWS_FEEDS": ["https://cryptopanic.com/news/rss/"]
}

logging.basicConfig(level=logging.INFO, format='%(message)s')
log = logging.getLogger("Sentinel")

class SentinelMaster:
    def __init__(self):
        self.balance = Decimal("100.00") # Starting real equity
        self.active_trade = None
        self.news_multiplier = Decimal("1.0")
        self.hot_keywords = ["BREAKING", "SEC", "ETF", "LIQUIDATION", "PUMP", "DUMP"]

    async def scan_news(self):
        while True:
            try:
                heat = False
                for url in CONFIG["NEWS_FEEDS"]:
                    feed = feedparser.parse(url)
                    for entry in feed.entries[:3]:
                        if any(w in entry.title.upper() for w in self.hot_keywords):
                            heat = True; break
                self.news_multiplier = Decimal("0.7") if heat else Decimal("1.0")
            except: pass
            await asyncio.sleep(60)

    async def get_market_data(self):
        """Fetches real-time spread and order book weight"""
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=20"
            res = requests.get(url, timeout=1).json()
            
            bids, asks = res['bids'], res['asks']
            best_bid, best_ask = Decimal(bids[0][0]), Decimal(asks[0][0])
            
            # Weight Calculation (Price * Volume)
            b_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in bids)
            a_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in asks)
            imbalance = (b_w - a_w) / (b_w + a_w)
            
            return imbalance, best_bid, best_ask
        except: return None, None, None

    async def run(self):
        asyncio.create_task(self.scan_news())
        print(f"\n--- ⚔️ SENTINEL V1 REAL-WORLD ACTIVE ---")
        
        while True:
            imb, bid, ask = await self.get_market_data()
            if imb is None: continue

            # --- 1. TRADE MANAGEMENT (If Trade is Open) ---
            if self.active_trade:
                t = self.active_trade
                # We exit at Bid if we Bought, and at Ask if we Sold
                current_p = bid if t['side'] == "BUY" else ask
                
                # Math: (Difference / Entry) - (2x Fees) - (Slippage)
                raw_move = (current_p - t['entry']) / t['entry'] if t['side'] == "BUY" else (t['entry'] - current_p) / t['entry']
                net_roi = raw_move - (CONFIG["FEE_RATE"] * 2) - CONFIG["SLIPPAGE_BPS"]
                live_pnl = CONFIG["TRADE_SIZE_USD"] * net_roi

                # Live Terminal Wiggle
                color = "\033[92m" if live_pnl > 0 else "\033[91m"
                sys.stdout.write(f"\r 🟢 {t['side']} OPEN | PnL: {color}${round(live_pnl, 2)}\033[0m ({round(net_roi*100, 3)}%) | BTC: {current_p}  ")
                sys.stdout.flush()

                if net_roi >= CONFIG["TARGET_PROFIT"] or net_roi <= CONFIG["STOP_LOSS"]:
                    self.balance += live_pnl
                    print(f"\n✅ CLOSED {t['side']} | Net: ${round(live_pnl,2)} | New Bal: ${round(self.balance, 2)}")
                    self.active_trade = None
                    await asyncio.sleep(5) # Cooldown to avoid re-entry

            # --- 2. ENTRY LOGIC (If No Trade) ---
            else:
                dynamic_thresh = CONFIG["BASE_THRESHOLD"] * self.news_multiplier
                sys.stdout.write(f"\r 📡 SCANNING... Imb: {round(imb, 3)} | Threshold: {round(dynamic_thresh, 3)}   ")
                sys.stdout.flush()

                if imb > dynamic_thresh:
                    # Enter at Ask (The price you pay to buy immediately)
                    self.active_trade = {"side": "BUY", "entry": ask}
                    print(f"\n🚀 LONG ENTRY @ {ask} (Threshold: {round(dynamic_thresh, 2)})")
                
                elif imb < -dynamic_thresh:
                    # Enter at Bid (The price you get to sell immediately)
                    self.active_trade = {"side": "SELL", "entry": bid}
                    print(f"\n🚀 SHORT ENTRY @ {bid} (Threshold: {round(dynamic_thresh, 2)})")

            await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(SentinelMaster().run())
