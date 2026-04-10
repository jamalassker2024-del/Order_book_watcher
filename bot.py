import asyncio
import logging
import aiohttp
import time
import math
from decimal import Decimal
from collections import deque

# OPTIMIZED CONFIG FOR $3 BALANCE / $0.30 TRADE SIZE
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE": Decimal("0.30"),
    "IMB_THRESHOLD": Decimal("0.65"),
    "VOL_MIN": Decimal("0.0001"),
    "TP": Decimal("0.0187"),
    "SL": Decimal("0.0080"),
    "FEE": Decimal("0.001"),
    "MAX_HOLD": 300,
    "POLL": 0.2,
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
log = logging.getLogger("SniperV24-PRO")

class SniperV24:
    def __init__(self):
        self.balance = Decimal("3.00")
        self.price_hist = deque(maxlen=20)
        self.last_trade = 0
        self.base_url = "https://api.binance.com/api/v3/depth"

    async def get_imbalance(self, session):
        """Fetches order book and calculates the bid/ask imbalance."""
        try:
            params = {"symbol": CONFIG["SYMBOL"], "limit": 10}
            async with session.get(self.base_url, params=params) as resp:
                data = await resp.json()
                
                # Fetching best bid and best ask to calculate mid price
                best_bid = Decimal(data['bids'][0][0])
                best_ask = Decimal(data['asks'][0][0])
                mid_price = (best_bid + best_ask) / 2

                # Calculate total volume for top 10 levels
                bids_vol = sum(Decimal(b[1]) for b in data['bids'])
                asks_vol = sum(Decimal(a[1]) for a in data['asks'])
                total = bids_vol + asks_vol
                
                imbalance = (bids_vol / total) if total > 0 else Decimal("0.5")
                return imbalance, mid_price
        except Exception as e:
            log.error(f"Fetch Error: {e}")
            return None, None

    async def run(self):
        log.info(f"Starting SniperV24 on {CONFIG['SYMBOL']} with ${CONFIG['TRADE_SIZE']} trades...")
        async with aiohttp.ClientSession() as session:
            while True:
                try:
                    # 1. Check for Imbalance
                    imbalance, price = await self.get_imbalance(session)
                    if imbalance is None:
                        continue
                        
                    self.price_hist.append(price)

                    # 2. Entry Logic: If bid volume > threshold, go Long
                    if imbalance > CONFIG["IMB_THRESHOLD"]:
                        entry_price = price
                        tp_price = entry_price * (1 + CONFIG["TP"])
                        sl_price = entry_price * (1 - CONFIG["SL"])
                        
                        log.info(f"🚀 BUY at {entry_price} | Imbalance: {imbalance:.2f}")
                        log.info(f"🎯 Targets: TP {tp_price:.2f} | SL {sl_price:.2f}")

                        # 3. Trade Monitoring Loop
                        start_time = time.time()
                        while time.time() - start_time < CONFIG["MAX_HOLD"]:
                            await asyncio.sleep(CONFIG["POLL"])
                            _, current_price = await self.get_imbalance(session)
                            
                            if current_price is None:
                                continue

                            if current_price >= tp_price:
                                profit = (CONFIG["TRADE_SIZE"] * CONFIG["TP"]) - (CONFIG["TRADE_SIZE"] * CONFIG["FEE"] * 2)
                                self.balance += profit
                                log.info(f"💰 TAKE PROFIT! Net: +${profit:.4f} | New Bal: ${self.balance:.4f}")
                                break
                                
                            elif current_price <= sl_price:
                                loss = (CONFIG["TRADE_SIZE"] * CONFIG["SL"]) + (CONFIG["TRADE_SIZE"] * CONFIG["FEE"] * 2)
                                self.balance -= loss
                                log.info(f"💀 STOP LOSS HIT. Net: -${loss:.4f} | New Bal: ${self.balance:.4f}")
                                break

                    await asyncio.sleep(CONFIG["POLL"])

                except Exception as e:
                    log.error(f"Loop Error: {e}")
                    await asyncio.sleep(1)

if __name__ == "__main__":
    bot = SniperV24()
    asyncio.run(bot.run())
