
FROM python:3.11-slim-bookworm

USER root
ENV DEBIAN_FRONTEND=noninteractive
ENV DISPLAY=:1
ENV WINEPREFIX=/root/.wine
ENV WINEARCH=win64
ENV WINEDEBUG=-all

RUN dpkg --add-architecture i386 && apt-get update && apt-get install -y --no-install-recommends \
    wine wine64 wine32:i386 winbind xvfb fluxbox x11vnc novnc websockify \
    wget curl procps cabextract unzip dos2unix xdotool \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir mt5linux rpyc
RUN wget -q https://download.mql5.com/cdn/web/metaquotes.software.corp/mt5/mt5setup.exe -O /root/mt5setup.exe

# =========================================================
# V16.3 - PROFIT-MAX VELOCITY BOT (ULTRA PROFITABILITY)
# =========================================================
RUN cat > /root/VALETAX_TICK_BOT_V16.mq5 << 'EOF'
//+------------------------------------------------------------------+
//|                                          LiquiditySweep_Flow.mq5  |
//|                                      BTC Liquidity Sweep + Order  |
//|                                                      Flow Trader  |
//+------------------------------------------------------------------+
#property copyright "LiquiditySweep EA"
#property version   "1.02"
#property strict

#include <Trade/Trade.mqh>
#include <Trade/AccountInfo.mqh>
#include <Trade/SymbolInfo.mqh>

CTrade obj_Trade;
CAccountInfo obj_Account;

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
//--- Risk Management ---
input double   RiskPercent      = 1.0;        // Risk per trade (% of balance)
input double   MaxDailyLossPct  = 10.0;       // Max daily loss (%)
input double   MaxDrawdownPct   = 20.0;       // Max account drawdown (%)
input int      MaxPositions     = 2;          // Maximum concurrent positions

//--- Entry Settings ---
input int      LookbackCandles  = 7;          // Candles to scan for liquidity (5-10)
input int      MinVolumeRatio   = 150;        // Min volume ratio to average (%)
input int      MinBodyPercent   = 60;         // Min body % of candle range
input double   MinATR           = 200.0;      // Minimum ATR in points
input int      CooldownBars     = 3;          // Cooldown after exit (bars)

//--- Exit Settings ---
input double   ATRMultiplierSL  = 1.5;        // ATR multiplier for SL
input double   ATRMultiplierTP  = 2.5;        // ATR multiplier for TP
input double   TrailingStart    = 1.0;        // Start trailing at 1R
input double   TrailingStep     = 0.5;        // Trailing step in R
input int      MaxHoldingBars   = 20;         // Max bars to hold before exit

//--- Broker Settings ---
input int      MagicNumber      = 20260803;   // EA Magic Number
input int      OrderRetryCount  = 3;          // Retry attempts for failed orders
input int      SlippagePts      = 50;         // Slippage tolerance

//+------------------------------------------------------------------+
//| Global Variables                                                  |
//+------------------------------------------------------------------+
string Top20CryptoSymbols[20] = {
   "BTCUSD", "ETHUSD", "USDTUSD", "BNBUSD", "SOLUSD", 
   "USDCUSD", "XRPUSD", "ADAUSD", "AVAXUSD", "DOGEUSD", 
   "DOTUSD", "TRXUSD", "LINKUSD", "MATICUSD", "TONUSD", 
   "SHIBUSD", "LTCUSD", "BCHUSD", "UNIUSD", "ATOMUSD"
};

//--- Multi-Symbol Tracking ---
datetime lastBarTime[20] = {0};
int barsSinceLastTrade[20] = {0};

//--- Balance Tracking ---
double dailyStartBalance = 0;
double peakBalance = 0;
double dailyLoss = 0;
double currentDrawdown = 0;

//--- Order Tracking ---
int totalTradesToday = 0;
int consecutiveLosses = 0;

//--- Performance Stats ---
int totalTrades = 0;
int winningTrades = 0;
int losingTrades = 0;
double totalProfit = 0;

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   Print("═══════════════════════════════════════════════════════════");
   Print("  LIQUIDITY SWEEP + ORDER FLOW EA v1.02");
   Print("  Multi-Asset Matrix - Top 20 Crypto Scanner");
   Print("═══════════════════════════════════════════════════════════");
   
   obj_Trade.SetExpertMagicNumber(MagicNumber);
   obj_Trade.SetDeviationInPoints(SlippagePts);
   
   dailyStartBalance = obj_Account.Balance();
   peakBalance = dailyStartBalance;
   
   // Initialize arrays and add symbols to Market Watch
   for(int i = 0; i < 20; i++) {
      barsSinceLastTrade[i] = CooldownBars + 1; // Allows immediate trading
      SymbolSelect(Top20CryptoSymbols[i], true);
   }
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   Print("═══════════════════════════════════════════════════════════");
   Print("  EA Stopped - Reason: ", reason);
   Print("═══════════════════════════════════════════════════════════");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick() {
   UpdateTracking();
   if (!PassesRiskChecks()) return;
   
   // Unlocked Position Management: Modifies open trades on every tick across all charts
   if (CountPositions() > 0) {
      ManagePositions();
   }
   
   // Scan Top 20 Array independently
   for (int i = 0; i < 20; i++) {
      string sym = Top20CryptoSymbols[i];
      
      // Verify market watch access
      if (!SymbolInfoInteger(sym, SYMBOL_SELECT)) continue; 
      
      if (!IsNewBar(sym, i)) continue;
      if (barsSinceLastTrade[i] < CooldownBars) continue;
      
      if (CountPositions() < MaxPositions) {
         CheckForEntry(sym, i);
      }
   }
}

//+------------------------------------------------------------------+
//| Check if New Bar per Symbol                                      |
//+------------------------------------------------------------------+
bool IsNewBar(string sym, int index) {
   datetime currentBarTime = (datetime)SeriesInfoInteger(sym, _Period, SERIES_LASTBAR_DATE);
   if (currentBarTime == lastBarTime[index] || currentBarTime == 0) return false;
   
   lastBarTime[index] = currentBarTime;
   barsSinceLastTrade[index]++;
   return true;
}

//+------------------------------------------------------------------+
//| Update Account Drawdown Limits                                   |
//+------------------------------------------------------------------+
void UpdateTracking() {
   double currentEquity = obj_Account.Equity();
   if (currentEquity > peakBalance) peakBalance = currentEquity;
   
   dailyLoss = dailyStartBalance - currentEquity;
   currentDrawdown = ((peakBalance - currentEquity) / peakBalance) * 100;
}

bool PassesRiskChecks() {
   if ((dailyLoss / dailyStartBalance) * 100 >= MaxDailyLossPct) return false;
   if (currentDrawdown >= MaxDrawdownPct) return false;
   return true;
}

//+------------------------------------------------------------------+
//| Count Open Positions Globally                                    |
//+------------------------------------------------------------------+
int CountPositions() {
   int count = 0;
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      if (PositionSelectByTicket(PositionGetTicket(i))) {
         if (PositionGetInteger(POSITION_MAGIC) == MagicNumber) {
            count++;
         }
      }
   }
   return count;
}

//+------------------------------------------------------------------+
//| Indicators Calculation per Symbol                                |
//+------------------------------------------------------------------+
double CalculateATR(string sym, int period) {
   double atrArray[];
   int atrHandle = iATR(sym, _Period, period);
   
   if (CopyBuffer(atrHandle, 0, 0, 1, atrArray) <= 0) {
      IndicatorRelease(atrHandle);
      return 0;
   }
   
   double point = SymbolInfoDouble(sym, SYMBOL_POINT);
   double atrPoints = (point > 0) ? atrArray[0] / point : 0;
   
   IndicatorRelease(atrHandle);
   return atrPoints;
}

double CalculateAverageVolume(string sym, int period) {
   long volumeArray[];
   double total = 0;
   if (CopyTickVolume(sym, _Period, 1, period, volumeArray) <= 0) return 1;
   
   for (int i = 0; i < period; i++) total += volumeArray[i];
   return (total / period);
}

//+------------------------------------------------------------------+
//| Check For Entry Trigger                                          |
//+------------------------------------------------------------------+
void CheckForEntry(string sym, int index) {
   int spread = (int)((SymbolInfoDouble(sym, SYMBOL_ASK) - SymbolInfoDouble(sym, SYMBOL_BID)) / SymbolInfoDouble(sym, SYMBOL_POINT));
   if (spread > 10000) return;
   
   double currentATR = CalculateATR(sym, 14);
   if (currentATR < MinATR) return;
   
   double avgVolume = CalculateAverageVolume(sym, 20);
   int direction = DetectLiquiditySweep(sym, avgVolume);
   if (direction == 0) return;
   
   EnterTrade(sym, direction, currentATR, index);
}

//+------------------------------------------------------------------+
//| Detect Liquidity Sweep Engine                                    |
//+------------------------------------------------------------------+
int DetectLiquiditySweep(string sym, double avgVol) {
   int direction = 0;
   
   double highBuffer[], lowBuffer[], closeBuffer[], openBuffer[];
   long volumeBuffer[];
   
   int lookback = LookbackCandles;
   ArraySetAsSeries(highBuffer, true);
   ArraySetAsSeries(lowBuffer, true);
   ArraySetAsSeries(closeBuffer, true);
   ArraySetAsSeries(openBuffer, true);
   ArraySetAsSeries(volumeBuffer, true);
   
   if (CopyHigh(sym, _Period, 0, lookback + 2, highBuffer) < lookback + 2) return 0;
   if (CopyLow(sym, _Period, 0, lookback + 2, lowBuffer) < lookback + 2) return 0;
   if (CopyClose(sym, _Period, 0, lookback + 2, closeBuffer) < lookback + 2) return 0;
   if (CopyOpen(sym, _Period, 0, lookback + 2, openBuffer) < lookback + 2) return 0;
   if (CopyTickVolume(sym, _Period, 0, lookback + 2, volumeBuffer) < lookback + 2) return 0;
   
   double swingHigh = highBuffer[ArrayMaximum(highBuffer, 2, lookback)];
   double swingLow = lowBuffer[ArrayMinimum(lowBuffer, 2, lookback)];
   
   double currentHigh = highBuffer[1];
   double currentLow = lowBuffer[1];
   double currentClose = closeBuffer[1];
   double currentOpen = openBuffer[1];
   long currentVolume = volumeBuffer[1];
   
   if (currentHigh > swingHigh && currentClose < swingHigh) {
      double bodyPercent = GetBodyPercent(currentOpen, currentClose, currentHigh, currentLow);
      if (bodyPercent >= MinBodyPercent) {
         if ((double)currentVolume >= (avgVol * MinVolumeRatio / 100.0)) direction = 1;
      }
   }
   
   if (currentLow < swingLow && currentClose > swingLow) {
      double bodyPercent = GetBodyPercent(currentOpen, currentClose, currentHigh, currentLow);
      if (bodyPercent >= MinBodyPercent) {
         if ((double)currentVolume >= (avgVol * MinVolumeRatio / 100.0)) direction = -1;
      }
   }
   
   return direction;
}

double GetBodyPercent(double open, double close, double high, double low) {
   double range = high - low;
   if (range == 0) return 0;
   double body = MathAbs(open - close);
   return (body / range) * 100;
}

//+------------------------------------------------------------------+
//| Trade Execution                                                  |
//+------------------------------------------------------------------+
void EnterTrade(string sym, int direction, double currentATR, int symIndex) {
   double ask = SymbolInfoDouble(sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(sym, SYMBOL_BID);
   
   double minLevel = SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL);
   if (minLevel == 0) minLevel = 100;
   
   double lotStep = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double lotMin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   if(lotStep == 0) lotStep = 0.01;
   if(lotMin == 0) lotMin = 0.01;
   
   double tickValue = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_VALUE);
   if (tickValue <= 0) tickValue = 1.0;
   
   double slPoints = currentATR * ATRMultiplierSL;
   double tpPoints = currentATR * ATRMultiplierTP;
   
   if (slPoints < minLevel) slPoints = minLevel;
   if (tpPoints < minLevel * 2) tpPoints = minLevel * 2;
   
   double riskAmount = obj_Account.Balance() * (RiskPercent / 100);
   double pointSize = SymbolInfoDouble(sym, SYMBOL_POINT);
   
   double lotSize = riskAmount / (slPoints * tickValue);
   lotSize = MathRound(lotSize / lotStep) * lotStep;
   if (lotSize < lotMin) lotSize = lotMin;
   
   uint filling = (uint)SymbolInfoInteger(sym, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0) obj_Trade.SetTypeFilling(ORDER_FILLING_FOK);
   else if((filling & SYMBOL_FILLING_IOC) != 0) obj_Trade.SetTypeFilling(ORDER_FILLING_IOC);
   else obj_Trade.SetTypeFilling(ORDER_FILLING_RETURN);

   bool success = false;
   string comment = (direction == 1) ? "Sweep_Up" : "Sweep_Down";
   
   for (int retry = 0; retry < OrderRetryCount; retry++) {
      if (direction == 1) { 
         success = obj_Trade.Sell(lotSize, sym, bid, bid + slPoints * pointSize, bid - tpPoints * pointSize, comment);
      } else if (direction == -1) { 
         success = obj_Trade.Buy(lotSize, sym, ask, ask - slPoints * pointSize, ask + tpPoints * pointSize, comment);
      }
      
      if (success) break;
      Sleep(100);
      ask = SymbolInfoDouble(sym, SYMBOL_ASK);
      bid = SymbolInfoDouble(sym, SYMBOL_BID);
   }
   
   if (success) {
      totalTradesToday++;
      totalTrades++;
      barsSinceLastTrade[symIndex] = 0;
      Print("[✓] EXECUTED on ", sym, " | Lot: ", lotSize);
   }
}

//+------------------------------------------------------------------+
//| Dynamic Asset Position Management                                |
//+------------------------------------------------------------------+
void ManagePositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      if (!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if (PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      ulong ticket = PositionGetTicket(i);
      string posSym = PositionGetString(POSITION_SYMBOL);
      
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentPrice = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) 
                           ? SymbolInfoDouble(posSym, SYMBOL_BID)
                           : SymbolInfoDouble(posSym, SYMBOL_ASK);
      
      double currentATR = CalculateATR(posSym, 14);
      double slPoints = currentATR * ATRMultiplierSL;
      double pointSize = SymbolInfoDouble(posSym, SYMBOL_POINT);
      
      double rUnits = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                     ? (currentPrice - openPrice) / (slPoints * pointSize)
                     : (openPrice - currentPrice) / (slPoints * pointSize);
      
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      datetime currentTime = TimeCurrent();
      int barsHeld = (int)((currentTime - openTime) / PeriodSeconds(_Period));
      
      if (barsHeld >= MaxHoldingBars) {
         ClosePosition(ticket, "Time Exit");
         continue;
      }
      
      if (rUnits >= TrailingStart) {
         double trailStep = TrailingStep * slPoints * pointSize;
         double newSL = 0;
         
         if (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) {
            newSL = currentPrice - trailStep;
            if (newSL > PositionGetDouble(POSITION_SL)) {
               obj_Trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
            }
         } else {
            newSL = currentPrice + trailStep;
            if (newSL < PositionGetDouble(POSITION_SL) || PositionGetDouble(POSITION_SL) == 0) {
               obj_Trade.PositionModify(ticket, newSL, PositionGetDouble(POSITION_TP));
            }
         }
      }
   }
}

void ClosePosition(ulong ticket, string reason) {
   if (obj_Trade.PositionClose(ticket)) {
      double profit = PositionGetDouble(POSITION_PROFIT);
      if (profit > 0) {
         winningTrades++;
         consecutiveLosses = 0;
      } else {
         losingTrades++;
         consecutiveLosses++;
      }
      totalProfit += profit;
      Print("[X] CLOSED: ", reason, " | Profit: $", profit);
   }
}
//+------------------------------------------------------------------+



EOF

# ============================================
# 3. INSTALLATION & ENTRYPOINT
# ============================================
RUN cat > /entrypoint.sh << 'EOF'
#!/bin/bash
set -e
rm -rf /tmp/.X*
Xvfb :1 -screen 0 1280x1024x24 -ac &
sleep 2
fluxbox &
x11vnc -display :1 -forever -shared -nopw -rfbport 5900 &
websockify --web=/usr/share/novnc 8080 0.0.0.0:5900 &
wineboot --init
sleep 5
MT5_EXE="/root/.wine/drive_c/Program Files/MetaTrader 5/terminal64.exe"
[ ! -f "$MT5_EXE" ] && wine /root/mt5setup.exe /auto && sleep 90
wine "$MT5_EXE" &
sleep 30

DATA_DIR=$(find /root/.wine -type d -path "*MetaQuotes/Terminal/*/MQL5" | head -n 1)
[ -z "$DATA_DIR" ] && DATA_DIR="/root/.wine/drive_c/Program Files/MetaTrader 5/MQL5"
mkdir -p "$DATA_DIR/Experts"
cp /root/VALETAX_TICK_BOT_V16.mq5 "$DATA_DIR/Experts/VALETAX_TICK_BOT_V16.mq5"
wine "/root/.wine/drive_c/Program Files/MetaTrader 5/metaeditor64.exe" /compile:"$DATA_DIR/Experts/VALETAX_TICK_BOT_V16.mq5" /log:"/root/compile.log"

python3 -m mt5linux --host 0.0.0.0 --port 8001 &
tail -f /dev/null
EOF

RUN chmod +x /entrypoint.sh && dos2unix /entrypoint.sh
EXPOSE 8080 8001
CMD ["/bin/bash", "/entrypoint.sh"]
