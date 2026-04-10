import asyncio
import json
import logging
import websockets
from decimal import Decimal
from collections import deque
import time

CONFIG = {
    "SYMBOL": "pepeusdt",
    "TRADE_SIZE": Decimal("1.0"),

    "LEVELS": 10,                 # depth levels used
    "IMB_THRESHOLD": Decimal("0.15"),
    "DELTA_THRESHOLD": Decimal("0.05"),  # acceleration

    "MAX_TRADES": 7,
    "TP": Decimal("0.004"),
    "SL": Decimal("-0.004"),
    "FEE": Decimal("0.001"),

    "COOLDOWN": 1.0
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger("ORDERFLOW")

class Trade:
    def __init__(self, side, entry):
        self.side = side
        self.entry = entry
        self.time = time.time()

class OrderBookBot:
    def __init__(self):
        self.balance = Decimal("30")
        self.trades = []

        self.imb_history = deque(maxlen=5)
        self.last_entry = 0

    def calc_imbalance(self, bids, asks):
        bid_vol = sum(Decimal(b[1]) for b in bids[:CONFIG["LEVELS"]])
        ask_vol = sum(Decimal(a[1]) for a in asks[:CONFIG["LEVELS"]])

        if bid_vol + ask_vol == 0:
            return Decimal("0")

        return (bid_vol - ask_vol) / (bid_vol + ask_vol)

    def get_price(self, bids, asks, side):
        return Decimal(asks[0][0]) if side == "BUY" else Decimal(bids[0][0])

    async def run(self):
        url = f"wss://stream.binance.com:9443/ws/{CONFIG['SYMBOL']}@depth@100ms"

        log.info("🚀 Order Book Imbalance Bot Started")

        async with websockets.connect(url) as ws:
            while True:
                data = json.loads(await ws.recv())

                bids = data.get("b", [])
                asks = data.get("a", [])

                if not bids or not asks:
                    continue

                imb = self.calc_imbalance(bids, asks)
                self.imb_history.append(imb)

                # 🔥 detect acceleration
                if len(self.imb_history) < 2:
                    continue

                delta = self.imb_history[-1] - self.imb_history[0]

                now = time.time()

                # 🚀 ENTRY LOGIC (pressure + acceleration)
                if (
                    abs(imb) > CONFIG["IMB_THRESHOLD"]
                    and abs(delta) > CONFIG["DELTA_THRESHOLD"]
                    and len(self.trades) < CONFIG["MAX_TRADES"]
                    and now - self.last_entry > CONFIG["COOLDOWN"]
                ):
                    side = "BUY" if imb > 0 else "SELL"
                    entry = self.get_price(bids, asks, side)

                    self.trades.append(Trade(side, entry))
                    self.last_entry = now

                    log.info(f"🚀 {side} @ {entry} | Imb: {round(imb,3)} Δ:{round(delta,3)}")

                # 🔄 MANAGE TRADES
                still_open = []

                for t in self.trades:
                    current = self.get_price(bids, asks, t.side)

                    move = (
                        (current - t.entry) / t.entry
                        if t.side == "BUY"
                        else (t.entry - current) / t.entry
                    )

                    net = move - (CONFIG["FEE"] * 2)

                    if net >= CONFIG["TP"] or net <= CONFIG["SL"]:
                        pnl = CONFIG["TRADE_SIZE"] * net
                        self.balance += pnl

                        log.info(
                            f"{'💰 WIN' if net>0 else '❌ LOSS'} "
                            f"{t.side} {round(net*100,3)}% | Bal: {round(self.balance,2)}"
                        )
                    else:
                        still_open.append(t)

                self.trades = still_open
