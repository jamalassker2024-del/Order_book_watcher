import asyncio
import json
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
    "MIN_NET_PROFIT_USD": Decimal("0.05"), # Closes when you are UP 5 cents AFTER fees
    "STOP_LOSS_USD": Decimal("-0.15"),    # Stop loss at 15 cents loss
    "FEE_USD_ESTIMATE": Decimal("0.08"),   # Est. cost to open+close $100 position
}

class FastScalper:
    def __init__(self):
        self.balance = Decimal("100.00")
        self.active_trade = None

    async def get_market(self):
        try:
            # Using the ticker endpoint for faster price updates than depth
            url = f"https://api.binance.com/api/v3/ticker/bookTicker?symbol={CONFIG['SYMBOL']}"
            res = requests.get(url, timeout=0.5).json()
            bid, ask = Decimal(res['bidPrice']), Decimal(res['askPrice'])
            
            # Depth for imbalance
            d_url = f"https://api.binance.com/api/v3/depth?symbol={CONFIG['SYMBOL']}&limit=5"
            d_res = requests.get(d_url, timeout=0.5).json()
            b_w = sum(Decimal(b[1]) for b in d_res['bids'])
            a_w = sum(Decimal(a[1]) for a in d_res['asks'])
            imb = (b_w - a_w) / (b_w + a_w)
            
            return imb, bid, ask
        except: return None, None, None

    async def run(self):
        print(f"🚀 SCALPER START | Target: +${CONFIG['MIN_NET_PROFIT_USD']} Net per trade")
        
        while True:
            imb, bid, ask = await self.get_market()
            if imb is None: continue

            if self.active_trade:
                t = self.active_trade
                curr_p = bid if t['side'] == "BUY" else ask
                
                # Gross PnL
                diff = (curr_p - t['entry']) if t['side'] == "BUY" else (t['entry'] - curr_p)
                qty = CONFIG['TRADE_SIZE_USD'] / t['entry']
                gross_pnl = diff * qty
                
                # NET PnL (Gross - Fees)
                net_pnl = gross_pnl - CONFIG['FEE_USD_ESTIMATE']

                # LIVE WIGGLE (Single Line)
                color = "\033[92m" if net_pnl > 0 else "\033[91m"
                sys.stdout.write(f"\r 🟢 {t['side']} | Net: {color}${round(net_pnl, 3)}\033[0m | Price: {curr_p}    ")
                sys.stdout.flush()

                # FAST CLOSE LOGIC
                if net_pnl >= CONFIG['MIN_NET_PROFIT_USD'] or net_pnl <= CONFIG['STOP_LOSS_USD']:
                    self.balance += net_pnl
                    print(f"\n✅ EXIT | Net: ${round(net_pnl, 3)} | Bal: ${round(self.balance, 2)}")
                    self.active_trade = None
                    # Immediate jump to next check (No sleep)
            
            else:
                sys.stdout.write(f"\r 📡 SCANNING... Imbalance: {round(imb, 3)}   ")
                sys.stdout.flush()

                if abs(imb) > CONFIG['BASE_THRESHOLD']:
                    side = "BUY" if imb > 0 else "SELL"
                    entry_p = ask if side == "BUY" else bid
                    self.active_trade = {"side": side, "entry": entry_p}
                    print(f"\n🚀 {side} OPEN @ {entry_p}")

            await asyncio.sleep(0.1) # High frequency poll

if __name__ == "__main__":
    asyncio.run(FastScalper().run())
