import asyncio
import logging
import requests
from decimal import Decimal
import time

# --- AGGRESSIVE WINNER CONFIG ---
CONFIG = {
    "SYMBOL": "PEPEUSDT",
    "TRADE_SIZE_USD": Decimal("1.00"),
    "IMBALANCE_THRESH": Decimal("0.18"), # Lowered for more action
    "FEE_RATE": Decimal("0.001"),        # 0.1% Binance Fee
    "POLL_SPEED": 0.5,                   # High speed
    "TAKE_PROFIT": Decimal("0.005"),     # 0.5% Target (Covers fees + profit)
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger("WinnerBot")

class AggressiveWinner:
    def __init__(self):
        self.shadow_balance = Decimal("30.00")
        self.last_imb = Decimal("0")

    async def get_market(self):
        try:
            # Combined call for speed
            price_url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            depth_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=5"
            
            p_res = requests.get(price_url, timeout=1).json()
            d_res = requests.get(depth_url, timeout=1).json()

            bid_w = sum(Decimal(b[0]) * Decimal(b[1]) for b in d_res['bids'])
            ask_w = sum(Decimal(a[0]) * Decimal(a[1]) for a in d_res['asks'])
            imb = (bid_w - ask_w) / (bid_w + ask_w)

            return {"imb": imb, "ask": Decimal(p_res['askPrice']), "bid": Decimal(p_res['bidPrice'])}
        except: return None

    async def run(self):
        logger.info(f"⚡ AGGRESSIVE WINNER START | Target: {CONFIG['SYMBOL']}")
        
        while True:
            data = await self.get_market()
            if not data: continue

            imb = data["imb"]
            # Visual Pulse in logs
            if abs(imb) > 0.1:
                logger.info(f"🔍 Monitoring Imbalance: {round(imb, 3)}")

            # --- TRIGGER LOGIC ---
            if abs(imb) > CONFIG["IMBALANCE_THRESH"]:
                side = "BUY" if imb > 0 else "SELL"
                entry_p = data["ask"] if side == "BUY" else data["bid"]
                
                logger.info(f"🚀 {side} ENTERED at {entry_p}")
                
                # --- AGGRESSIVE PROFIT TRACKING ---
                # We wait up to 30 seconds for a "Winning Spike"
                start_trade = time.time()
                while time.time() - start_trade < 30:
                    await asyncio.sleep(0.5)
                    check = await self.get_market()
                    if not check: continue
                    
                    current_exit = check["bid"] if side == "BUY" else check["ask"]
                    move = (current_exit - entry_p) / entry_p if side == "BUY" else (entry_p - current_exit) / entry_p
                    net = move - (CONFIG["FEE_RATE"] * 2)

                    # EXIT IF WE HIT PROFIT TARGET
                    if net >= CONFIG["TAKE_PROFIT"]:
                        self.shadow_balance += (CONFIG["TRADE_SIZE_USD"] * net)
                        logger.info(f"💰 WINNER! Net: +{round(net*100, 3)}% | Bal: ${round(self.shadow_balance, 3)}")
                        break
                    
                    # EMERGENCY EXIT (If it goes against us -0.5%)
                    if net < Decimal("-0.005"):
                        self.shadow_balance += (CONFIG["TRADE_SIZE_USD"] * net)
                        logger.info(f"⚠️ STOP LOSS | Net: {round(net*100, 3)}% | Bal: ${round(self.shadow_balance, 3)}")
                        break
                
                await asyncio.sleep(2) # Short cooldown

            await asyncio.sleep(CONFIG["POLL_SPEED"])

if __name__ == "__main__":
    asyncio.run(AggressiveWinner().run())
