import asyncio
import logging
import aiohttp # ✅ Real async requests
from decimal import Decimal
from collections import deque
import time

# --- CONFIG ---
CONFIG = {
    "BINANCE_SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("1.20"), # ✅ Set to >$1 to pass Binance minimums
    "WINNER_THRESHOLD": Decimal("0.70"), # Slightly more active
    "SPREAD_LIMIT": Decimal("0.0005"), 
    "FEE": Decimal("0.001"), 
    "TRADE_DURATION": 15, # Quick scalping
    "DEPTH": 20,
    "POLL_SPEED": 0.5, # Twice as fast
    "COOLDOWN": 5
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("SNIPER-V17.5")

class SniperBot:
    def __init__(self):
        self.balance = Decimal("77.69")
        self.price_history = deque(maxlen=10) 
        self.last_trade_time = 0

    async def get_market_data(self, session):
        """Non-blocking async data fetch"""
        try:
            url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['BINANCE_SYMBOL']}&limit={CONFIG['DEPTH']}"
            async with session.get(url, timeout=2) as response:
                data = await response.json()
                bids = data['bids']
                asks = data['asks']
                
                # Weighting Volume
                bid_vol = sum(Decimal(b[1]) for b in bids)
                ask_vol = sum(Decimal(a[1]) for a in asks)
                imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
                
                best_bid = Decimal(bids[0][0])
                best_ask = Decimal(asks[0][0])
                mid_price = (best_bid + best_ask) / 2
                spread = (best_ask - best_bid) / best_bid
                
                return imbalance, mid_price, spread
        except Exception as e:
            return None, None, None

    async def run(self):
        logger.info("⚡ V17.5 ASYNC SNIPER ONLINE")
        async with aiohttp.ClientSession() as session:
            while True:
                imb, price, spread = await self.get_market_data(session)
                
                if price is None:
                    await asyncio.sleep(1)
                    continue

                self.price_history.append(price)
                
                # --- LOGGING ---
                if len(self.price_history) >= 10:
                    trend = "UP" if price > self.price_history[0] else "DOWN"
                    logger.info(f"Price: {price} | Imb: {round(imb,3)} | Trend: {trend} | Spread: {round(spread,5)}")

                # --- FILTERS ---
                now = time.time()
                if now - self.last_trade_time < CONFIG["COOLDOWN"]:
                    await asyncio.sleep(CONFIG["POLL_SPEED"])
                    continue

                if len(self.price_history) < 10:
                    await asyncio.sleep(CONFIG["POLL_SPEED"])
                    continue

                # --- SIGNAL ---
                direction = None
                if imb > CONFIG["WINNER_THRESHOLD"] and price > self.price_history[0]:
                    direction = "BUY"
                elif imb < -CONFIG["WINNER_THRESHOLD"] and price < self.price_history[0]:
                    direction = "SELL"

                if direction:
                    if spread > CONFIG["SPREAD_LIMIT"]:
                        logger.info(f"⛔ Trade Skipped: Spread {round(spread,5)} too wide")
                        continue
                        
                    logger.info(f"🚀 {direction} EXECUTED at {price}")
                    entry_price = price
                    self.last_trade_time = now
                    
                    await asyncio.sleep(CONFIG["TRADE_DURATION"])
                    
                    _, exit_price, _ = await self.get_market_data(session)
                    
                    # PnL Calculation
                    move = (exit_price - entry_price) / entry_price if direction == "BUY" else (entry_price - exit_price) / entry_price
                    net_pnl = CONFIG["TRADE_SIZE_USD"] * (move - CONFIG["FEE"])
                    
                    self.balance += net_pnl
                    log_type = logger.info if net_pnl > 0 else logger.warning
                    log_type(f"{'💰 WIN' if net_pnl > 0 else '💀 LOSS'} | Net PnL: ${round(net_pnl, 5)}")
                    logger.info(f"🏦 New Balance: ${round(self.balance, 2)}")

                await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(SniperBot().run())
