import asyncio
import logging
import aiohttp
import time
from decimal import Decimal
from collections import deque

# --- KILLER CONFIG ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("1.50"),      # Pass Binance $1.00 min
    "WINNER_THRESHOLD": Decimal("0.65"),    # Weighted imbalance
    "VOL_SPIKE_FACTOR": Decimal("1.5"),     # Vol must be 1.5x average
    "MAX_WALL_DISTANCE": Decimal("0.002"),  # Ignore orders > 0.2% away (Fake Walls)
    "TRADE_DURATION": 12,                   # Ultra-fast scalp
    "POLL_SPEED": 0.3,                      # 300ms reaction time
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("KILLER-V18")

class KillerBot:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_history = deque(maxlen=10)
        self.vol_history = deque(maxlen=20)
        self.last_trade_time = 0

    async def get_market_data(self, session):
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit=50"
            async with session.get(url, timeout=1) as resp:
                data = await resp.json()
                mid_price = (Decimal(data['bids'][0][0]) + Decimal(data['asks'][0][0])) / 2
                
                # --- WEIGHTED IMBALANCE (Anti-Fake Wall) ---
                bid_weighted_vol = Decimal("0")
                ask_weighted_vol = Decimal("0")
                
                for p_str, q_str in data['bids']:
                    p, q = Decimal(p_str), Decimal(q_str)
                    dist = (mid_price - p) / mid_price
                    if dist < CONFIG["MAX_WALL_DISTANCE"]: # Only count real liquidity
                        bid_weighted_vol += q * (1 - dist * 100) # Closer = heavier
                
                for p_str, q_str in data['asks']:
                    p, q = Decimal(p_str), Decimal(q_str)
                    dist = (p - mid_price) / mid_price
                    if dist < CONFIG["MAX_WALL_DISTANCE"]:
                        ask_weighted_vol += q * (1 - dist * 100)

                imbalance = (bid_weighted_vol - ask_weighted_vol) / (bid_weighted_vol + ask_weighted_vol)
                return imbalance, mid_price, (bid_weighted_vol + ask_weighted_vol)
        except:
            return None, None, None

    async def run(self):
        logger.info("💀 V18 KILLER MODE: ACTIVE")
        async with aiohttp.ClientSession() as session:
            while True:
                imb, price, total_vol = await self.get_market_data(session)
                if price is None: continue

                self.price_history.append(price)
                self.vol_history.append(total_vol)

                # --- WAIT FOR WARMUP ---
                if len(self.vol_history) < 20:
                    await asyncio.sleep(0.5)
                    continue

                # --- KILLER LOGIC ---
                avg_vol = sum(self.vol_history) / len(self.vol_history)
                is_vol_spike = total_vol > (avg_vol * CONFIG["VOL_SPIKE_FACTOR"])
                trend_up = price > self.price_history[0]
                trend_down = price < self.price_history[0]

                direction = None
                # Condition: High Imbalance + Trend + Volume Confirmation
                if imb > CONFIG["WINNER_THRESHOLD"] and trend_up and is_vol_spike:
                    direction = "BUY"
                elif imb < -CONFIG["WINNER_THRESHOLD"] and trend_down and is_vol_spike:
                    direction = "SELL"

                if direction and (time.time() - self.last_trade_time > 10):
                    logger.info(f"🎯 KILLER {direction} | Imb: {round(imb,2)} | Vol Spike: YES")
                    entry_price = price
                    self.last_trade_time = time.time()
                    
                    await asyncio.sleep(CONFIG["TRADE_DURATION"])
                    
                    _, exit_price, _ = await self.get_market_data(session)
                    move = (exit_price - entry_price) / entry_price if direction == "BUY" else (entry_price - exit_price) / entry_price
                    
                    # Net Profit (Fee subtraction)
                    net_pnl = CONFIG["TRADE_SIZE_USD"] * (move - Decimal("0.001"))
                    self.balance += net_pnl
                    
                    status = "💰 PROFIT" if net_pnl > 0 else "💀 REJECTION"
                    logger.info(f"{status}: ${round(net_pnl, 4)} | Bal: ${round(self.balance, 2)}")

                await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(KillerBot().run())
