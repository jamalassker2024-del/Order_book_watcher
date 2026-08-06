
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
#property version   "1.01"
#property strict

#include <Trade/Trade.mqh>
#include <Trade/AccountInfo.mqh>
#include <Trade/SymbolInfo.mqh>

CTrade obj_Trade;
CAccountInfo obj_Account;
CSymbolInfo obj_Symbol;

//+------------------------------------------------------------------+
//| Input Parameters                                                  |
//+------------------------------------------------------------------+
//--- Risk Management ---
input double   RiskPercent      = 1.0;        // Risk per trade (% of balance)
input double   MaxDailyLossPct  = 10.0;       // Max daily loss (%)
input double   MaxDrawdownPct   = 20.0;       // Max account drawdown (%)
input int      MaxPositions     = 2;          // Maximum concurrent positions

//--- Entry Settings (Relaxed for Active Trading) ---
input int      LookbackCandles  = 5;          // Candles to scan for liquidity (5-10)
input int      MinVolumeRatio   = 100;        // Min volume ratio to average (%) [Was 150]
input int      MinBodyPercent   = 20;         // Min body % of candle range [Was 60]
input double   MinATR           = 50.0;       // Minimum ATR in points [Was 200]
input int      CooldownBars     = 1;          // Cooldown after exit (bars) [Was 3]

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
//--- Time & Bar Tracking ---
datetime lastBarTime = 0;
datetime lastTradeTime = 0;
int barsSinceLastTrade = 0;

//--- Balance Tracking ---
double dailyStartBalance = 0;
double peakBalance = 0;
double dailyLoss = 0;
double currentDrawdown = 0;

//--- ATR & Volume ---
double currentATR = 0;
double avgVolume = 0;

//--- Order Tracking ---
int totalTradesToday = 0;
int consecutiveLosses = 0;

//--- Broker Detection ---
int minStopLevel = 0;
double lotStep = 0;
double lotMin = 0;
long fillMode = 0;

//--- Performance Stats ---
int totalTrades = 0;
int winningTrades = 0;
int losingTrades = 0;
double totalProfit = 0;

//--- Top 20 Crypto Symbols ---
string Top20CryptoSymbols[20] = {
   "BTCUSD", "ETHUSD", "USDTUSD", "BNBUSD", "SOLUSD", 
   "USDCUSD", "XRPUSD", "ADAUSD", "AVAXUSD", "DOGEUSD", 
   "DOTUSD", "TRXUSD", "LINKUSD", "MATICUSD", "TONUSD", 
   "SHIBUSD", "LTCUSD", "BCHUSD", "UNIUSD", "ATOMUSD"
};

//+------------------------------------------------------------------+
//| Expert initialization function                                   |
//+------------------------------------------------------------------+
int OnInit() {
   Print("═══════════════════════════════════════════════════════════");
   Print("  LIQUIDITY SWEEP + ORDER FLOW EA v1.01 (Relaxed Rules)");
   Print("═══════════════════════════════════════════════════════════");
   
   //--- Initialize Objects ---
   obj_Trade.SetExpertMagicNumber(MagicNumber);
   obj_Trade.SetDeviationInPoints(SlippagePts);
   
   //--- Get Symbol Info ---
   obj_Symbol.Name(_Symbol);
   obj_Symbol.Refresh();
   
   //--- Detect Broker Settings ---
   DetectBrokerSettings();
   
   //--- Initialize Balance Tracking ---
   dailyStartBalance = obj_Account.Balance();
   peakBalance = dailyStartBalance;
   
   //--- Print Configuration ---
   PrintConfiguration();
   
   return(INIT_SUCCEEDED);
}

//+------------------------------------------------------------------+
//| Detect Broker Settings Function                                  |
//+------------------------------------------------------------------+
void DetectBrokerSettings() {
   //--- Minimum Stop Level ---
   minStopLevel = (int)SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL);
   if (minStopLevel == 0) {
      minStopLevel = 100;  
      Print("[WARNING] Broker stop level not reported, using default: ", minStopLevel);
   }
   
   //--- Lot Step ---
   lotStep = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   if (lotStep == 0) lotStep = 0.01;
   
   //--- Lot Min ---
   lotMin = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   if (lotMin == 0) lotMin = 0.01;
   
   //--- Fill Mode ---
   uint filling = (uint)SymbolInfoInteger(_Symbol, SYMBOL_FILLING_MODE);
   if((filling & SYMBOL_FILLING_FOK) != 0) {
      obj_Trade.SetTypeFilling(ORDER_FILLING_FOK);
   } else if((filling & SYMBOL_FILLING_IOC) != 0) {
      obj_Trade.SetTypeFilling(ORDER_FILLING_IOC);
   } else {
      obj_Trade.SetTypeFilling(ORDER_FILLING_RETURN);
   }
   
   Print("[INFO] Broker Settings Detected:");
   Print("  Min Stop Level: ", minStopLevel, " points");
   Print("  Lot Step: ", lotStep);
   Print("  Lot Min: ", lotMin);
}

//+------------------------------------------------------------------+
//| Print Configuration                                              |
//+------------------------------------------------------------------+
void PrintConfiguration() {
   Print("═══════════════════════════════════════════════════════════");
   Print("  CONFIGURATION");
   Print("───────────────────────────────────────────────────────────");
   Print("  Risk Per Trade   : ", RiskPercent, "%");
   Print("  Max Daily Loss   : ", MaxDailyLossPct, "%");
   Print("  Max Drawdown     : ", MaxDrawdownPct, "%");
   Print("  Max Positions    : ", MaxPositions);
   Print("  Lookback Candles : ", LookbackCandles);
   Print("  Min Volume Ratio : ", MinVolumeRatio, "%");
   Print("  Min Body %       : ", MinBodyPercent, "%");
   Print("  Min ATR          : ", MinATR);
   Print("  ATR Multiplier SL: ", ATRMultiplierSL);
   Print("  ATR Multiplier TP: ", ATRMultiplierTP);
   Print("  Cooldown Bars    : ", CooldownBars);
   Print("  Max Holding Bars : ", MaxHoldingBars);
   Print("═══════════════════════════════════════════════════════════");
}

//+------------------------------------------------------------------+
//| Expert deinitialization function                                 |
//+------------------------------------------------------------------+
void OnDeinit(const int reason) {
   PrintPerformance();
   Print("═══════════════════════════════════════════════════════════");
   Print("  EA Stopped - Reason: ", reason);
   Print("═══════════════════════════════════════════════════════════");
}

//+------------------------------------------------------------------+
//| Expert tick function                                             |
//+------------------------------------------------------------------+
void OnTick() {
   obj_Symbol.Refresh();
   
   if (!IsNewBar()) return;
   
   UpdateTracking();
   if (!PassesRiskChecks()) return;
   UpdateIndicators();
   
   int posCount = CountPositions();
   if (posCount > 0) {
      ManagePositions();
      return;
   }
   
   if (barsSinceLastTrade < CooldownBars) return;
   
   if (posCount < MaxPositions) {
      CheckForEntry();
   }
}

//+------------------------------------------------------------------+
//| Check if New Bar                                                 |
//+------------------------------------------------------------------+
bool IsNewBar() {
   datetime currentBarTime = iTime(_Symbol, _Period, 0); // Dynamic chart timeframe
   if (currentBarTime == lastBarTime) return false;
   lastBarTime = currentBarTime;
   barsSinceLastTrade++;
   return true;
}

//+------------------------------------------------------------------+
//| Update Tracking Variables                                        |
//+------------------------------------------------------------------+
void UpdateTracking() {
   double currentBalance = obj_Account.Balance();
   double currentEquity = obj_Account.Equity();
   
   if (currentEquity > peakBalance) {
      peakBalance = currentEquity;
   }
   
   dailyLoss = dailyStartBalance - currentEquity;
   currentDrawdown = ((peakBalance - currentEquity) / peakBalance) * 100;
}

//+------------------------------------------------------------------+
//| Pass Risk Checks                                                 |
//+------------------------------------------------------------------+
bool PassesRiskChecks() {
   if ((dailyLoss / dailyStartBalance) * 100 >= MaxDailyLossPct) return false;
   if (currentDrawdown >= MaxDrawdownPct) return false;
   
   int spread = GetSpreadPoints();
   if (spread > 10000) {
      Print("[SKIP] Spread too high: ", spread, " points");
      return false;
   }
   
   return true;
}

//+------------------------------------------------------------------+
//| Update Indicators                                                |
//+------------------------------------------------------------------+
void UpdateIndicators() {
   currentATR = CalculateATR(14);
   avgVolume = CalculateAverageVolume(20);
}

//+------------------------------------------------------------------+
//| Calculate ATR                                                    |
//+------------------------------------------------------------------+
double CalculateATR(int period) {
   double atrArray[];
   int atrHandle = iATR(_Symbol, _Period, period);
   
   if (CopyBuffer(atrHandle, 0, 0, 1, atrArray) <= 0) {
      IndicatorRelease(atrHandle);
      return 100;
   }
   
   double atrPoints = atrArray[0] / _Point;
   IndicatorRelease(atrHandle);
   return (atrPoints > 0) ? atrPoints : 100;
}

//+------------------------------------------------------------------+
//| Calculate Average Volume                                         |
//+------------------------------------------------------------------+
double CalculateAverageVolume(int period) {
   long volumeArray[];
   double total = 0;
   if (CopyTickVolume(_Symbol, _Period, 1, period, volumeArray) <= 0) return 1;
   
   for (int i = 0; i < period; i++) {
      total += volumeArray[i];
   }
   return (total / period);
}

//+------------------------------------------------------------------+
//| Count Open Positions                                             |
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
//| Get Spread in Points                                             |
//+------------------------------------------------------------------+
int GetSpreadPoints() {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   return (int)((ask - bid) / _Point);
}

//+------------------------------------------------------------------+
//| Check For Entry                                                  |
//+------------------------------------------------------------------+
void CheckForEntry() {
   if (currentATR < MinATR) {
      Print("[DEBUG] Skipping: ATR ", currentATR, " < MinATR ", MinATR);
      return;
   }
   
   int direction = DetectLiquiditySweep();
   if (direction == 0) return;
   
   EnterTrade(direction);
}

//+------------------------------------------------------------------+
//| Detect Liquidity Sweep                                           |
//+------------------------------------------------------------------+
int DetectLiquiditySweep() {
   int direction = 0;
   
   double highBuffer[];
   double lowBuffer[];
   double closeBuffer[];
   double openBuffer[];
   long volumeBuffer[];
   
   int lookback = LookbackCandles;
   ArraySetAsSeries(highBuffer, true);
   ArraySetAsSeries(lowBuffer, true);
   ArraySetAsSeries(closeBuffer, true);
   ArraySetAsSeries(openBuffer, true);
   ArraySetAsSeries(volumeBuffer, true);
   
   if (CopyHigh(_Symbol, _Period, 0, lookback + 2, highBuffer) < lookback + 2) return 0;
   if (CopyLow(_Symbol, _Period, 0, lookback + 2, lowBuffer) < lookback + 2) return 0;
   if (CopyClose(_Symbol, _Period, 0, lookback + 2, closeBuffer) < lookback + 2) return 0;
   if (CopyOpen(_Symbol, _Period, 0, lookback + 2, openBuffer) < lookback + 2) return 0;
   if (CopyTickVolume(_Symbol, _Period, 0, lookback + 2, volumeBuffer) < lookback + 2) return 0;
   
   double swingHigh = highBuffer[ArrayMaximum(highBuffer, 2, lookback)];
   double swingLow = lowBuffer[ArrayMinimum(lowBuffer, 2, lookback)];
   
   double currentHigh = highBuffer[1];
   double currentLow = lowBuffer[1];
   double currentClose = closeBuffer[1];
   double currentOpen = openBuffer[1];
   long currentVolume = volumeBuffer[1];
   
   //--- Sweep UP ---
   if (currentHigh > swingHigh && currentClose < swingHigh) {
      double bodyPercent = GetBodyPercent(currentOpen, currentClose, currentHigh, currentLow);
      if (bodyPercent >= MinBodyPercent) {
         double reqVolume = avgVolume * MinVolumeRatio / 100.0;
         if ((double)currentVolume >= reqVolume) {
            direction = 1;  // SELL
         } else {
            Print("[DEBUG] Sweep UP: Vol ", currentVolume, " < Req ", reqVolume);
         }
      } else {
         Print("[DEBUG] Sweep UP: Body ", DoubleToString(bodyPercent, 1), "% < Min ", MinBodyPercent, "%");
      }
   }
   
   //--- Sweep DOWN ---
   if (currentLow < swingLow && currentClose > swingLow) {
      double bodyPercent = GetBodyPercent(currentOpen, currentClose, currentHigh, currentLow);
      if (bodyPercent >= MinBodyPercent) {
         double reqVolume = avgVolume * MinVolumeRatio / 100.0;
         if ((double)currentVolume >= reqVolume) {
            direction = -1;  // BUY
         } else {
            Print("[DEBUG] Sweep DOWN: Vol ", currentVolume, " < Req ", reqVolume);
         }
      } else {
         Print("[DEBUG] Sweep DOWN: Body ", DoubleToString(bodyPercent, 1), "% < Min ", MinBodyPercent, "%");
      }
   }
   
   return direction;
}

//+------------------------------------------------------------------+
//| Get Body Percent of Candle                                       |
//+------------------------------------------------------------------+
double GetBodyPercent(double open, double close, double high, double low) {
   double range = high - low;
   if (range == 0) return 0;
   double body = MathAbs(open - close);
   return (body / range) * 100;
}

//+------------------------------------------------------------------+
//| Calculate Lot Size                                               |
//+------------------------------------------------------------------+
double CalculateLotSize() {
   double balance = obj_Account.Balance();
   double riskAmount = balance * (RiskPercent / 100);
   double stopLossPoints = currentATR * ATRMultiplierSL;
   
   double tickValue = obj_Symbol.TickValue();
   if (tickValue <= 0) tickValue = 1.0;
   if (stopLossPoints <= 0) stopLossPoints = 1.0;
   
   double lotSize = riskAmount / (stopLossPoints * tickValue);
   
   lotSize = MathRound(lotSize / lotStep) * lotStep;
   if (lotSize < lotMin) lotSize = lotMin;
   if (lotSize > SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX)) {
      lotSize = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   }
   
   return lotSize;
}

//+------------------------------------------------------------------+
//| Enter Trade                                                      |
//+------------------------------------------------------------------+
void EnterTrade(int direction) {
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double lotSize = CalculateLotSize();
   
   double slPoints = currentATR * ATRMultiplierSL;
   double tpPoints = currentATR * ATRMultiplierTP;
   
   if (slPoints < minStopLevel) slPoints = minStopLevel;
   if (tpPoints < minStopLevel * 2) tpPoints = minStopLevel * 2;
   
   bool success = false;
   string comment = "";
   
   for (int retry = 0; retry < OrderRetryCount; retry++) {
      if (direction == 1) {  // SELL
         double sl = bid + slPoints * _Point;
         double tp = bid - tpPoints * _Point;
         comment = "Sweep_Up_Rej";
         success = obj_Trade.Sell(lotSize, _Symbol, bid, sl, tp, comment);
      } else if (direction == -1) {  // BUY
         double sl = ask - slPoints * _Point;
         double tp = ask + tpPoints * _Point;
         comment = "Sweep_Down_Rej";
         success = obj_Trade.Buy(lotSize, _Symbol, ask, sl, tp, comment);
      }
      
      if (success) break;
      
      Sleep(100);
      obj_Symbol.Refresh();
      ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   }
   
   if (success) {
      totalTradesToday++;
      barsSinceLastTrade = 0;
      Print("[✓] ORDER EXECUTED: ", comment, " | Lot: ", lotSize, " | SL: ", slPoints, " | TP: ", tpPoints);
   } else {
      Print("[ERROR] Order execution failed. Code: ", GetLastError());
   }
}

//+------------------------------------------------------------------+
//| Manage Positions                                                 |
//+------------------------------------------------------------------+
void ManagePositions() {
   for (int i = PositionsTotal() - 1; i >= 0; i--) {
      if (!PositionSelectByTicket(PositionGetTicket(i))) continue;
      if (PositionGetInteger(POSITION_MAGIC) != MagicNumber) continue;
      
      ulong ticket = PositionGetTicket(i);
      double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
      double currentPrice = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY) 
                           ? SymbolInfoDouble(_Symbol, SYMBOL_BID)
                           : SymbolInfoDouble(_Symbol, SYMBOL_ASK);
      
      double slPoints = currentATR * ATRMultiplierSL;
      double rUnits = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
                     ? (currentPrice - openPrice) / (slPoints * _Point)
                     : (openPrice - currentPrice) / (slPoints * _Point);
      
      datetime openTime = (datetime)PositionGetInteger(POSITION_TIME);
      datetime currentTime = TimeCurrent();
      int barsHeld = (int)((currentTime - openTime) / PeriodSeconds(_Period));
      
      if (barsHeld >= MaxHoldingBars) {
         ClosePosition(ticket, "Time Exit");
         continue;
      }
      
      if (rUnits >= TrailingStart) {
         double trailStep = TrailingStep * slPoints * _Point;
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

//+------------------------------------------------------------------+
//| Close Position                                                   |
//+------------------------------------------------------------------+
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
//| Print Performance                                                |
//+------------------------------------------------------------------+
void PrintPerformance() {
   double winRate = (totalTrades > 0) ? (double)winningTrades / totalTrades * 100 : 0;
   Print("═══════════════════════════════════════════════════════════");
   Print("  PERFORMANCE SUMMARY");
   Print("───────────────────────────────────────────────────────────");
   Print("  Total Trades    : ", totalTrades);
   Print("  Winning Trades  : ", winningTrades);
   Print("  Losing Trades   : ", losingTrades);
   Print("  Win Rate        : ", winRate, "%");
   Print("  Total Profit    : $", totalProfit);
   Print("  Current Balance : $", obj_Account.Balance());
   Print("  Daily Loss      : $", dailyLoss);
   Print("  Drawdown        : ", currentDrawdown, "%");
   Print("═══════════════════════════════════════════════════════════");
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
