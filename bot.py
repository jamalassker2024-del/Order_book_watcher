import asyncio
import logging
import requests
import sys
from decimal import Decimal
import time

# --- SCALPER CONFIG ---
CONFIG = {
    "SYMBOL": "BTCUSDT",
    "TRADE_SIZE_USD": Decimal("100.0"), 
    "BASE_THRESHOLD": Decimal("0.15"),   
    "MIN_NET_PROFIT_USD": Decimal("0.05"), 
    "STOP_LOSS_USD": Decimal("-0.15"),    
    "FEE_USD_ESTIMATE": Decimal("0.08"),   
    "LOG_INTERVAL_SEC": 5 # Only update the log every 5 seconds
}

class FastScalper:
    def __init__(self):
        self.balance = Decimal("100.00")
        self.active_trade = None
        self.last_log_time = 0

    async def get_market(self):
        try:
            # Combined ticker + depth fetch
            url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            res = requests.get(url, timeout=0.5).json()
            bid, ask = Decimal(res['bidPrice']), Decimal(res['askPrice'])
            
            d_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=5"
            d_res = requests.get(d_url, timeout=0.5).json()
            b_w = sum(Decimal(b[1]) for b in d_res['bids'])
            a_w = sum(Decimal(a[1]) for a in d_res['asks'])
            imb = (b_w - a_w) / (b_w + a_w)
            
            return imb, bid, ask
        except: return None, None, None

    async def run(self):
        print(f"🚀 CLOUD SCALPER ACTIVE | Target: +${CONFIG['MIN_NET_PROFIT_USD']} Net")
        
        while True:
            imb, bid, ask = await self.get_market()
            now = time.time()
            if imb is None: continue

            if self.active_trade:
                t = self.active_trade
                curr_p = bid if t['side'] == "BUY" else ask
                qty = CONFIG['TRADE_SIZE_USD'] / t['entry']
                net_pnl = ((curr_p - t['entry']) if t['side'] == "BUY" else (t['entry'] - curr_p)) * qty - CONFIG['FEE_USD_ESTIMATE']

                # --- THROTTLED LOGGING ---
                if now - self.last_log_time > CONFIG["LOG_INTERVAL_SEC"]:
                    print(f"📊 {t['side']} ACTIVE | Net: ${round(net_pnl, 3)} | Price: {curr_p}")
                    self.last_log_time = now

                # EXIT LOGIC
                if net_pnl >= CONFIG['MIN_NET_PROFIT_USD'] or net_pnl <= CONFIG['STOP_LOSS_USD']:
                    self.balance += net_pnl
                    print(f"✅ EXIT {t['side']} | PnL: ${round(net_pnl, 3)} | Bal: ${round(self.balance, 2)}")
                    self.active_trade = None
            
            else:
                # Only log scanning every 10 seconds to keep it super clean
                if now - self.last_log_time > 10:
                    print(f"📡 SCANNING... Imbalance: {round(imb, 3)}")
                    self.last_log_time = now

                if abs(imb) > CONFIG['BASE_THRESHOLD']:
                    side = "BUY" if imb > 0 else "SELL"
                    entry_p = ask if side == "BUY" else bid
                    self.active_trade = {"side": side, "entry": entry_p}
                    print(f"🚀 {side} OPEN @ {entry_p}")
                    self.last_log_time = now # Reset timer to show first trade update immediately

            await asyncio.sleep(0.1) 

if __name__ == "__main__":
    asyncio.run(FastScalper().run())
