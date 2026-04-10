import asyncio
import logging
import aiohttp
import time
from decimal import Decimal
from collections import deque

# --- TUNED FOR PROFITABILITY ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("2.0"),

    "IMB_THRESHOLD": Decimal("0.85"), # 🎯 Strict: Only high-conviction imbalances
    "IMB_ACCEL": Decimal("0.20"),     # 🚀 Momentum requirement
    
    "VOL_MIN": Decimal("0.0006"),     # 📊 Volatility Filter: Price must move 0.06% in history
    
    "TP": Decimal("0.0045"),          # ✅ 0.45% Take Profit (4.5x the fee)
    "SL": Decimal("0.0020"),          # ❌ 0.20% Stop Loss (Protects capital)
    
    "FEE": Decimal("0.001"),          # 0.1% Binance Spread/Fee
    "MAX_HOLD": 180,                  # ⌛ 3-minute patience (prevents "TIME" losses)
    "POLL": 0.4,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("V21-EXECUTIONER")

class SniperV21:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_hist = deque(maxlen=20) # 20 ticks for better trend/vol analysis
        self.imb_hist = deque(maxlen=10)
        self.last_trade = 0

    async def fetch(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=20"
            async with session.get(url, timeout=1) as r:
                d = await r.json()
                bid, ask = Decimal(d['bids'][0][0]), Decimal(d['asks'][0][0])
                mid = (bid + ask) / 2
                bid_vol = sum(Decimal(x[1]) for x in d['bids'])
                ask_vol = sum(Decimal(x[1]) for x in d['asks'])
                imb = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                return mid, imb
        except:
            return None, None

    async def run(self):
        log.info("🎯 V21 EXECUTIONER LIVE | Volatility Filter Active")
        async with aiohttp.ClientSession() as session:
            while True:
                price, imb = await self.fetch(session)
                if price is None: continue

                self.price_hist.append(price)
                self.imb_hist.append(imb)

                if len(self.price_hist) < 20:
                    await asyncio.sleep(1)
                    continue

                # --- 1. VOLATILITY CHECK (Anti-Chop) ---
                high = max(self.price_hist)
                low = min(self.price_hist)
                current_vol = (high - low) / low
                is_volatile = current_vol >= CONFIG["VOL_MIN"]

                # --- 2. TREND & MOMENTUM ---
                imb_change = self.imb_hist[-1] - self.imb_hist[0]
                price_trend_up = price > self.price_hist[0]
                price_trend_down = price < self.price_hist[0]

                # --- 3. THE TRIGGER ---
                up_sig = imb > CONFIG["IMB_THRESHOLD"] and imb_change > CONFIG["IMB_ACCEL"] and price_trend_up and is_volatile
                down_sig = imb < -CONFIG["IMB_THRESHOLD"] and imb_change < -CONFIG["IMB_ACCEL"] and price_trend_down and is_volatile

                # Cooldown & Entry
                if (time.time() - self.last_trade < 15):
                    await asyncio.sleep(CONFIG["POLL"])
                    continue

                direction = "BUY" if up_sig else "SELL" if down_sig else None

                if direction:
                    entry = price
                    self.last_trade = time.time()
                    log.info(f"🚀 {direction} ENTRY @ {entry} | Vol: {round(current_vol*100,3)}%")

                    start_time = time.time()
                    while True:
                        p, _ = await self.fetch(session)
                        if p is None: continue

                        raw_move = (p - entry) / entry if direction == "BUY" else (entry - p) / entry
                        
                        if raw_move >= CONFIG["TP"]:
                            res = "✅ TP"
                            break
                        elif raw_move <= -CONFIG["SL"]:
                            res = "❌ SL"
                            break
                        elif time.time() - start_time > CONFIG["MAX_HOLD"]:
                            res = "⌛ TIME"
                            break
                        
                        await asyncio.sleep(0.3)

                    # Calculation
                    net_pnl = CONFIG["TRADE_SIZE"] * (raw_move - CONFIG["FEE"])
                    self.balance += net_pnl
                    log.info(f"{res} | Move: {round(raw_move*100,3)}% | Net: ${round(net_pnl,4)} | Bal: ${round(self.balance,3)}")

                await asyncio.sleep(CONFIG["POLL"])

if __name__ == "__main__":
    asyncio.run(SniperV21().run())
