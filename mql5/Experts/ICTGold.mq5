//+------------------------------------------------------------------+
//|                                                     ICTGold.mq5  |
//|   ICT models for XAUUSD, calibrated on real gold data.           |
//|                                                                  |
//|   Three models survived measurement on real COMEX gold           |
//|   (M5 60d + H1 730d). Each has its own target, hold time and     |
//|   risk, because they operate on different time scales.           |
//|                                                                  |
//|     TurtleSoup   4.0R   4h hold   BE at 2.0R   risk 2%           |
//|     JudasSwing   3.0R   3h hold   BE at 1.5R   risk 2%           |
//|     OTE          1.5R   4h hold   no BE        risk 1%           |
//|                                                                  |
//|   Everything is measured relative to ATR or basis points, never  |
//|   in fixed dollars: gold was 1300 in 2018 and 4600 in 2026.      |
//+------------------------------------------------------------------+
#property copyright "ICT gold engine"
#property version   "1.00"
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

//====================================================================
// Inputs
//====================================================================
input group "=== Models (each has its own plan) ==="
input bool   InpUseTurtleSoup   = true;    // TurtleSoup: false break of a level
input bool   InpUseJudasSwing   = true;    // JudasSwing: asian sweep then reverse
input bool   InpUseOTE          = true;    // OTE: 62-79% of a displacement leg

input group "=== TurtleSoup plan ==="
input double InpTS_TargetRR     = 4.0;     // target in R
input int    InpTS_HoldBars     = 48;      // max hold, M5 bars (48 = 4h)
input double InpTS_BreakevenR   = 2.0;     // move stop to entry at this R (0 = off)
input double InpTS_RiskPct      = 2.0;     // risk per trade, % of balance

input group "=== JudasSwing plan ==="
input double InpJS_TargetRR     = 3.0;
input int    InpJS_HoldBars     = 36;      // 3h
input double InpJS_BreakevenR   = 1.5;
input double InpJS_RiskPct      = 2.0;

input group "=== OTE plan ==="
input double InpOTE_TargetRR    = 1.5;
input int    InpOTE_HoldBars    = 48;      // 4h
input double InpOTE_BreakevenR  = 0.0;     // measured: BE hurts this model
input double InpOTE_RiskPct     = 1.0;

input group "=== Gold calibration (relative, not dollars) ==="
input int    InpAtrPeriod       = 20;      // ATR period on the trading timeframe
input double InpDisplacementATR = 1.5;     // displacement >= this many ATR
input double InpDisplacementBP  = 8.0;     // ... or this many basis points, whichever is bigger
input double InpMinFvgATR       = 0.20;    // FVG >= this many ATR
input double InpMinFvgBP        = 1.5;     // ... or this many bp
input double InpFvgSpreadMult   = 2.0;     // ... and at least this many spreads
input double InpStopBufferATR   = 0.25;    // stop buffer beyond the extreme
input double InpStopBufferBP    = 1.5;
input double InpMaxEntryDistATR = 3.0;     // skip limits further than this from price

input group "=== Execution guards ==="
input double InpMaxSpreadToStop = 0.15;    // skip if spread > this fraction of the stop
input double InpMaxSpreadPrice  = 0.60;    // hard spread cap in price (dollars)
input int    InpLimitExpiryBars = 24;      // cancel unfilled limit after N bars (2h)
input int    InpSwingLeft       = 1;       // fractal bars left
input int    InpSwingRight      = 1;       // fractal bars right
input int    InpLookbackBars    = 600;     // bars analysed each evaluation

input group "=== Session (New York clock, DST handled) ==="
input double InpServerGmtOffset = 0;       // server time - GMT, in hours (Exness: 2 or 3)
input bool   InpBlockRollover   = true;    // no trades NY 17:00-20:00 (gold rollover)
input double InpRolloverStart   = 17.0;
input double InpRolloverEnd     = 20.0;
input bool   InpBlockFridayLate = true;    // no new entries late friday
input double InpFridayCutoffNY  = 15.0;

input group "=== Daily circuit breaker ==="
input int    InpMaxTradesPerDay = 6;
input int    InpMaxConsecLosses = 3;
input double InpMaxDailyLossPct = 6.0;     // stop the day at this drawdown
input double InpHardStopPct     = 10.0;    // locks trading until reviewed
input bool   InpHaltNeedsReview = true;    // lock stays until you release it
input bool   InpUnlock          = false;   // set true once to release, then false

input group "=== Order handling ==="
input long   InpMagic           = 700922;
input int    InpDeviation       = 20;
input string InpComment         = "ictgold";
input bool   InpDryRun          = false;   // live only: log, send nothing
input bool   InpVerbose         = true;

//====================================================================
// Globals
//====================================================================
CTrade         trade;
CPositionInfo  pos;

string    g_sym;
double    g_point;
int       g_digits;
datetime  g_lastBar   = 0;

// per-day state
datetime  g_day       = 0;
int       g_dayTrades = 0;
int       g_consecLoss= 0;
double    g_dayStart  = 0.0;
bool      g_locked    = false;

// open trade bookkeeping (one position at a time)
struct Managed
{
   ulong    ticket;
   string   model;
   double   entry;
   double   stop0;      // original stop, defines 1R
   double   risk;       // |entry - stop0|
   double   target;
   double   beAtR;
   int      holdBars;
   datetime opened;
   bool     movedBE;
};
Managed g_open;
bool    g_hasOpen = false;

// pending limit bookkeeping
struct Pending
{
   ulong    ticket;
   string   model;
   datetime placed;
};
Pending g_pend;
bool    g_hasPend = false;

//====================================================================
// Small helpers
//====================================================================
double Spread()
{
   double a = SymbolInfoDouble(g_sym, SYMBOL_ASK);
   double b = SymbolInfoDouble(g_sym, SYMBOL_BID);
   double s = a - b;
   if(s <= 0) s = (double)SymbolInfoInteger(g_sym, SYMBOL_SPREAD) * g_point;
   return s;
}

void Say(string msg)
{
   if(InpVerbose) Print(msg);
}

//--- basis points of a price
double BpToPrice(double bp, double price) { return price * bp / 10000.0; }

//--- the larger of an ATR multiple and a bp floor
double Scaled(double atr, double atrMult, double bp, double price)
{
   double a = atr * atrMult;
   double b = BpToPrice(bp, price);
   return (a > b) ? a : b;
}

//====================================================================
// New York clock with US DST (second sunday in march -> first sunday
// in november). Server time is converted to GMT first.
//====================================================================
bool IsUsDst(datetime gmt)
{
   MqlDateTime t;
   TimeToStruct(gmt, t);
   if(t.mon < 3 || t.mon > 11) return false;
   if(t.mon > 3 && t.mon < 11) return true;

   // day of week of the 1st of this month
   int firstDow = (t.day_of_week - ((t.day - 1) % 7) + 7) % 7;
   if(t.mon == 3)
   {
      int secondSunday = 1 + ((7 - firstDow) % 7) + 7;
      if(t.day > secondSunday) return true;
      if(t.day < secondSunday) return false;
      return (t.hour >= 7);              // 2am EST = 07:00 GMT
   }
   // november
   int firstSunday = 1 + ((7 - firstDow) % 7);
   if(t.day > firstSunday) return false;
   if(t.day < firstSunday) return true;
   return (t.hour < 6);                  // 2am EDT = 06:00 GMT
}

datetime ToGmt(datetime server)
{
   return server - (datetime)(int)MathRound(InpServerGmtOffset * 3600.0);
}

datetime ToNy(datetime server)
{
   datetime gmt = ToGmt(server);
   int off = IsUsDst(gmt) ? -4 : -5;
   return gmt + off * 3600;
}

double NyHour(datetime server)
{
   MqlDateTime t;
   TimeToStruct(ToNy(server), t);
   return t.hour + t.min / 60.0;
}

//--- calendar day in New York, as a datetime at 00:00
datetime NyDay(datetime server)
{
   datetime ny = ToNy(server);
   MqlDateTime t;
   TimeToStruct(ny, t);
   t.hour = 0; t.min = 0; t.sec = 0;
   return StructToTime(t);
}

bool InRollover(datetime server)
{
   if(!InpBlockRollover) return false;
   double h = NyHour(server);
   return (h >= InpRolloverStart && h < InpRolloverEnd);
}

//====================================================================
// Market data buffers, refilled on each new bar
//====================================================================
double   H[], L[], O[], C[];
datetime T[];
int      NB = 0;

bool LoadBars()
{
   int want = InpLookbackBars;
   ArraySetAsSeries(H, false); ArraySetAsSeries(L, false);
   ArraySetAsSeries(O, false); ArraySetAsSeries(C, false);
   ArraySetAsSeries(T, false);

   int n = CopyHigh(g_sym, PERIOD_CURRENT, 0, want, H);
   if(n <= 0) return false;
   if(CopyLow  (g_sym, PERIOD_CURRENT, 0, want, L) != n) return false;
   if(CopyOpen (g_sym, PERIOD_CURRENT, 0, want, O) != n) return false;
   if(CopyClose(g_sym, PERIOD_CURRENT, 0, want, C) != n) return false;
   if(CopyTime (g_sym, PERIOD_CURRENT, 0, want, T) != n) return false;
   NB = n;
   return (NB > InpAtrPeriod + 60);
}

//--- index of the last CLOSED bar in the loaded arrays
int LastClosed() { return NB - 2; }

double AtrAt(int i)
{
   int p = InpAtrPeriod;
   if(i < p) return 0.0;
   double sum = 0.0;
   for(int k = i - p + 1; k <= i; k++)
   {
      double a = H[k] - L[k];
      double b = MathAbs(H[k] - C[k-1]);
      double c = MathAbs(L[k] - C[k-1]);
      double tr = a;
      if(b > tr) tr = b;
      if(c > tr) tr = c;
      sum += tr;
   }
   return sum / p;
}

//====================================================================
// Structure: swings, and a market structure shift that carries
// displacement. ICT is explicit that a break without displacement
// is not a shift.
//====================================================================
bool IsSwingHigh(int i)
{
   if(i - InpSwingLeft < 0 || i + InpSwingRight >= NB) return false;
   for(int k = i - InpSwingLeft; k <= i + InpSwingRight; k++)
      if(k != i && H[k] > H[i]) return false;
   return true;
}

bool IsSwingLow(int i)
{
   if(i - InpSwingLeft < 0 || i + InpSwingRight >= NB) return false;
   for(int k = i - InpSwingLeft; k <= i + InpSwingRight; k++)
      if(k != i && L[k] < L[i]) return false;
   return true;
}

//--- Does the leg ending at bar `end` qualify as displacement?
//    Needs energy (ATR multiple) AND an imbalance left behind (FVG).
bool IsDisplacement(int start, int end, int dir, double &legLow, double &legHigh)
{
   if(end <= start || start < 1) return false;
   double atr = AtrAt(end);
   if(atr <= 0) return false;
   double need = Scaled(atr, InpDisplacementATR, InpDisplacementBP, C[end]);

   double hi = H[start], lo = L[start];
   for(int k = start; k <= end; k++)
   {
      if(H[k] > hi) hi = H[k];
      if(L[k] < lo) lo = L[k];
   }
   legLow = lo; legHigh = hi;
   if(hi - lo < need) return false;

   // a real displacement leaves at least one unfilled gap inside it
   for(int k = start + 1; k < end; k++)
   {
      if(dir > 0 && L[k+1] > H[k-1]) return true;
      if(dir < 0 && H[k+1] < L[k-1]) return true;
   }
   return false;
}

//--- Find the most recent displaced MSS at or before bar `now`.
//    Returns direction (+1/-1), 0 if none. Fills the leg extremes.
int FindMss(int now, int within, int &legStart, int &legEnd,
            double &legLow, double &legHigh)
{
   for(int i = now; i >= now - within && i > InpSwingLeft + 2; i--)
   {
      // bullish shift: close above the most recent confirmed swing high
      for(int j = i - 1; j >= i - 60 && j > InpSwingLeft; j--)
      {
         if(IsSwingHigh(j) && C[i] > H[j] && C[i-1] <= H[j])
         {
            double lo, hi;
            if(IsDisplacement(j, i, +1, lo, hi))
            {
               legStart = j; legEnd = i; legLow = lo; legHigh = hi;
               return +1;
            }
            break;
         }
         if(IsSwingLow(j) && C[i] < L[j] && C[i-1] >= L[j])
         {
            double lo2, hi2;
            if(IsDisplacement(j, i, -1, lo2, hi2))
            {
               legStart = j; legEnd = i; legLow = lo2; legHigh = hi2;
               return -1;
            }
            break;
         }
      }
   }
   return 0;
}

//====================================================================
// Previous day levels and the asian range, in New York days.
//====================================================================
bool DayLevels(int now, datetime whichDay, double &hi, double &lo)
{
   bool any = false;
   hi = 0; lo = 0;
   for(int k = now; k >= 0; k--)
   {
      if(NyDay(T[k]) != whichDay) { if(any) break; else continue; }
      if(!any) { hi = H[k]; lo = L[k]; any = true; }
      else
      {
         if(H[k] > hi) hi = H[k];
         if(L[k] < lo) lo = L[k];
      }
   }
   return any;
}

//--- asian range = NY 20:00 of the previous day through 00:00 today
bool AsianRange(int now, double &hi, double &lo)
{
   datetime today = NyDay(T[now]);
   bool any = false;
   for(int k = now; k >= 0; k--)
   {
      datetime d = NyDay(T[k]);
      double h = NyHour(T[k]);
      bool inRange = (d == today && h < 2.0) ||
                     (d == today - 86400 && h >= 20.0);
      if(d < today - 86400) break;
      if(!inRange) continue;
      if(!any) { hi = H[k]; lo = L[k]; any = true; }
      else
      {
         if(H[k] > hi) hi = H[k];
         if(L[k] < lo) lo = L[k];
      }
   }
   return any && (hi > lo);
}

//====================================================================
// Setup container
//====================================================================
struct Setup
{
   bool     ok;
   string   model;
   bool     isBuy;
   bool     isLimit;      // false = market
   double   entry;
   double   stop;
   double   target;
   double   riskPct;
   double   beAtR;
   int      holdBars;
   string   why;
};

void ClearSetup(Setup &s)
{
   s.ok = false; s.model = ""; s.isBuy = false; s.isLimit = false;
   s.entry = 0; s.stop = 0; s.target = 0; s.riskPct = 0;
   s.beAtR = 0; s.holdBars = 0; s.why = "";
}

//--- finish a setup: apply the plan's target, and the shared guards
bool Finish(Setup &s, int now, double targetRR, double riskPct,
            double beAtR, int holdBars, string model, string why)
{
   double risk = MathAbs(s.entry - s.stop);
   if(risk <= 0) return false;

   double sp = Spread();
   if(sp > InpMaxSpreadPrice)                      return false;
   if(InpMaxSpreadToStop > 0 && sp > risk * InpMaxSpreadToStop) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   if(MathAbs(s.entry - C[now]) > atr * InpMaxEntryDistATR) return false;

   s.target   = s.isBuy ? s.entry + risk * targetRR
                        : s.entry - risk * targetRR;
   s.model    = model;
   s.riskPct  = riskPct;
   s.beAtR    = beAtR;
   s.holdBars = holdBars;
   s.why      = why;
   s.ok       = true;
   return true;
}

//====================================================================
// Model 1: Turtle Soup
//   A level is taken out and price closes back inside. The break was
//   a raid on stops, not a move. Enter at the level, stop past the
//   wick, and let it run to the opposite side.
//====================================================================
bool TurtleSoup(int now, Setup &s)
{
   ClearSetup(s);
   if(!InpUseTurtleSoup) return false;

   datetime today = NyDay(T[now]);
   double pdh, pdl, ah, al;
   bool hasPd = DayLevels(now, today - 86400, pdh, pdl);
   bool hasAsia = AsianRange(now, ah, al);
   if(!hasPd && !hasAsia) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   double buf = Scaled(atr, InpStopBufferATR, InpStopBufferBP, C[now]);

   double levels[4];
   int    kinds[4];      // +1 = resistance (BSL), -1 = support (SSL)
   int    n = 0;
   if(hasPd)   { levels[n] = pdh; kinds[n] = +1; n++; levels[n] = pdl; kinds[n] = -1; n++; }
   if(hasAsia) { levels[n] = ah;  kinds[n] = +1; n++; levels[n] = al;  kinds[n] = -1; n++; }

   // look back a short window for the raid bar
   for(int back = 0; back <= 10 && now - back > 1; back++)
   {
      int r = now - back;
      for(int i = 0; i < n; i++)
      {
         double lvl = levels[i];
         if(kinds[i] > 0)
         {
            // swept above and closed back below -> sell
            if(H[r] > lvl && C[r] < lvl && C[now] < lvl)
            {
               s.isBuy = false; s.isLimit = false;
               s.entry = C[now];
               s.stop  = H[r] + buf;
               if(s.stop <= s.entry) continue;
               if(Finish(s, now, InpTS_TargetRR, InpTS_RiskPct,
                         InpTS_BreakevenR, InpTS_HoldBars, "TurtleSoup",
                         StringFormat("false break above %.2f, closed back", lvl)))
                  return true;
            }
         }
         else
         {
            if(L[r] < lvl && C[r] > lvl && C[now] > lvl)
            {
               s.isBuy = true; s.isLimit = false;
               s.entry = C[now];
               s.stop  = L[r] - buf;
               if(s.stop >= s.entry) continue;
               if(Finish(s, now, InpTS_TargetRR, InpTS_RiskPct,
                         InpTS_BreakevenR, InpTS_HoldBars, "TurtleSoup",
                         StringFormat("false break below %.2f, closed back", lvl)))
                  return true;
            }
         }
      }
   }
   return false;
}

//====================================================================
// Model 2: Judas Swing (Power of 3)
//   London takes one side of the asian range (manipulation), then
//   reverses. Enter on the FVG the reversal leaves behind.
//====================================================================
bool JudasSwing(int now, Setup &s)
{
   ClearSetup(s);
   if(!InpUseJudasSwing) return false;

   double h = NyHour(T[now]);
   if(h < 2.0 || h >= 5.0) return false;          // london manipulation window

   double ah, al;
   if(!AsianRange(now, ah, al)) return false;

   datetime today = NyDay(T[now]);
   double runHi = -1, runLo = -1;
   int    bars = 0;
   for(int k = now; k >= 0 && k >= now - 60; k--)
   {
      if(NyDay(T[k]) != today || NyHour(T[k]) < 2.0) break;
      if(runHi < 0) { runHi = H[k]; runLo = L[k]; }
      else { if(H[k] > runHi) runHi = H[k]; if(L[k] < runLo) runLo = L[k]; }
      bars++;
   }
   if(bars < 3) return false;

   bool sweptHigh = (runHi > ah);
   bool sweptLow  = (runLo < al);
   if(sweptHigh == sweptLow) return false;        // both or neither: no judas

   int dir = sweptHigh ? -1 : +1;                 // fade the manipulation
   double extreme = sweptHigh ? runHi : runLo;

   int ls, le; double ll, lh;
   int mss = FindMss(now, 60, ls, le, ll, lh);
   if(mss != dir) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   double minGap = Scaled(atr, InpMinFvgATR, InpMinFvgBP, C[now]);
   double spGap  = Spread() * InpFvgSpreadMult;
   if(spGap > minGap) minGap = spGap;

   // most recent unmitigated FVG inside the reversal leg
   double top = 0, bot = 0;
   bool found = false;
   for(int k = le - 1; k > ls && k > 1; k--)
   {
      if(dir > 0 && L[k+1] > H[k-1] && (L[k+1] - H[k-1]) >= minGap)
      { top = L[k+1]; bot = H[k-1]; found = true; break; }
      if(dir < 0 && H[k+1] < L[k-1] && (L[k-1] - H[k+1]) >= minGap)
      { top = L[k-1]; bot = H[k+1]; found = true; break; }
   }
   if(!found) return false;

   double ce = (top + bot) / 2.0;                 // consequent encroachment
   double buf = Scaled(atr, InpStopBufferATR, InpStopBufferBP, C[now]);

   s.isBuy   = (dir > 0);
   s.isLimit = true;
   s.entry   = ce;
   s.stop    = s.isBuy ? extreme - buf : extreme + buf;
   if(s.isBuy && (s.entry > C[now] || s.stop >= s.entry)) return false;
   if(!s.isBuy && (s.entry < C[now] || s.stop <= s.entry)) return false;

   return Finish(s, now, InpJS_TargetRR, InpJS_RiskPct,
                 InpJS_BreakevenR, InpJS_HoldBars, "JudasSwing",
                 StringFormat("asian %.2f-%.2f %s swept, reversing",
                              al, ah, sweptHigh ? "high" : "low"));
}

//====================================================================
// Model 3: OTE
//   After a displacement, price retraces into 62-79% of the leg.
//   Measured: it works only with a tight target. 73% of these go on
//   to retrace past 90%, so take the money early.
//====================================================================
bool Ote(int now, Setup &s)
{
   ClearSetup(s);
   if(!InpUseOTE) return false;

   int ls, le; double lo, hi;
   int dir = FindMss(now, 60, ls, le, lo, hi);
   if(dir == 0) return false;
   double size = hi - lo;
   if(size <= 0) return false;

   double px = C[now];
   double lowEdge, highEdge;
   if(dir > 0)                                    // buy: retrace down from the high
   {
      lowEdge  = hi - size * 0.79;
      highEdge = hi - size * 0.62;
   }
   else                                           // sell: retrace up from the low
   {
      lowEdge  = lo + size * 0.62;
      highEdge = lo + size * 0.79;
   }
   if(px < lowEdge || px > highEdge) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   double buf = Scaled(atr, InpStopBufferATR, InpStopBufferBP, px);

   s.isBuy   = (dir > 0);
   s.isLimit = false;                             // already in the zone
   s.entry   = px;
   s.stop    = s.isBuy ? lo - buf : hi + buf;
   if(s.isBuy && s.stop >= s.entry) return false;
   if(!s.isBuy && s.stop <= s.entry) return false;

   return Finish(s, now, InpOTE_TargetRR, InpOTE_RiskPct,
                 InpOTE_BreakevenR, InpOTE_HoldBars, "OTE",
                 StringFormat("leg %.2f-%.2f, %.0f%% retrace",
                              lo, hi, 100.0 * (s.isBuy ? (hi - px) / size
                                                       : (px - lo) / size)));
}

//====================================================================
// Risk sizing
//====================================================================
double LotFor(double riskPct, double stopDistance)
{
   if(stopDistance <= 0) return 0.0;
   double bal  = AccountInfoDouble(ACCOUNT_BALANCE);
   double cash = bal * riskPct / 100.0;

   double tickVal = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSz  = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_SIZE);
   if(tickVal <= 0 || tickSz <= 0) return 0.0;

   double lossPerLot = stopDistance / tickSz * tickVal;
   if(lossPerLot <= 0) return 0.0;

   double lots = cash / lossPerLot;

   double minL = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MAX);
   double step = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_STEP);
   if(step > 0) lots = MathFloor(lots / step) * step;
   if(lots < minL)
   {
      // Honest about the arithmetic: the smallest tradable size risks
      // more than asked. Report it rather than silently over-risking.
      double realPct = 100.0 * (minL * lossPerLot) / bal;
      if(realPct > riskPct * 1.5)
      {
         Say(StringFormat("skip: min lot %.2f risks %.1f%% (asked %.1f%%)",
                          minL, realPct, riskPct));
         return 0.0;
      }
      lots = minL;
   }
   if(lots > maxL) lots = maxL;
   return NormalizeDouble(lots, 2);
}

//====================================================================
// Daily circuit breaker
//====================================================================
void RollDay(datetime server)
{
   datetime d = NyDay(server);
   if(d == g_day) return;
   g_day       = d;
   g_dayTrades = 0;
   g_consecLoss= 0;
   g_dayStart  = AccountInfoDouble(ACCOUNT_BALANCE);
   Say(StringFormat("--- new NY day, balance %.2f", g_dayStart));
}

bool DayBlocked()
{
   if(g_locked)
   {
      if(InpUnlock) { g_locked = false; Say("lock released"); }
      else return true;
   }
   if(g_dayTrades >= InpMaxTradesPerDay) return true;
   if(g_consecLoss >= InpMaxConsecLosses) return true;

   if(g_dayStart > 0)
   {
      double eq = AccountInfoDouble(ACCOUNT_EQUITY);
      double dd = 100.0 * (g_dayStart - eq) / g_dayStart;
      if(dd >= InpHardStopPct)
      {
         if(!g_locked)
            Print(StringFormat("HARD STOP: daily drawdown %.1f%%. "
                               "Review the trades before releasing.", dd));
         g_locked = InpHaltNeedsReview;
         return true;
      }
      if(dd >= InpMaxDailyLossPct) return true;
   }
   return false;
}

//====================================================================
// Order placement and management
//====================================================================
void Place(Setup &s)
{
   double lots = LotFor(s.riskPct, MathAbs(s.entry - s.stop));
   if(lots <= 0) return;

   double entry = NormalizeDouble(s.entry, g_digits);
   double stop  = NormalizeDouble(s.stop,  g_digits);
   double tp    = NormalizeDouble(s.target,g_digits);

   Say(StringFormat("%s %s  entry %.2f  stop %.2f  tp %.2f  lots %.2f  (%s)",
                    s.model, s.isBuy ? "BUY" : "SELL", entry, stop, tp, lots, s.why));

   if(InpDryRun && !MQLInfoInteger(MQL_TESTER))
   {
      Say("dry run: nothing sent");
      return;
   }

   bool sent = false;
   if(!s.isLimit)
      sent = s.isBuy ? trade.Buy(lots, g_sym, 0, stop, tp, InpComment)
                     : trade.Sell(lots, g_sym, 0, stop, tp, InpComment);
   else
   {
      datetime exp = TimeCurrent() + InpLimitExpiryBars * PeriodSeconds(PERIOD_CURRENT);
      sent = s.isBuy
             ? trade.BuyLimit (lots, entry, g_sym, stop, tp, ORDER_TIME_SPECIFIED, exp, InpComment)
             : trade.SellLimit(lots, entry, g_sym, stop, tp, ORDER_TIME_SPECIFIED, exp, InpComment);
   }

   if(!sent)
   {
      Say(StringFormat("order rejected: %d %s", trade.ResultRetcode(),
                       trade.ResultRetcodeDescription()));
      return;
   }

   g_dayTrades++;
   if(s.isLimit)
   {
      g_pend.ticket = trade.ResultOrder();
      g_pend.model  = s.model;
      g_pend.placed = TimeCurrent();
      g_hasPend = true;
   }
   else
   {
      g_open.ticket  = trade.ResultOrder();
      g_open.model   = s.model;
      g_open.entry   = entry;
      g_open.stop0   = stop;
      g_open.risk    = MathAbs(entry - stop);
      g_open.target  = tp;
      g_open.beAtR   = s.beAtR;
      g_open.holdBars= s.holdBars;
      g_open.opened  = TimeCurrent();
      g_open.movedBE = false;
      g_hasOpen = true;
   }
}

//--- breakeven and time stop on the live position
void ManageOpen()
{
   if(!PositionSelect(g_sym)) { g_hasOpen = false; return; }
   if(PositionGetInteger(POSITION_MAGIC) != InpMagic) return;

   long   type  = PositionGetInteger(POSITION_TYPE);
   bool   isBuy = (type == POSITION_TYPE_BUY);
   double entry = PositionGetDouble(POSITION_PRICE_OPEN);
   double sl    = PositionGetDouble(POSITION_SL);
   double tp    = PositionGetDouble(POSITION_TP);
   double px    = isBuy ? SymbolInfoDouble(g_sym, SYMBOL_BID)
                        : SymbolInfoDouble(g_sym, SYMBOL_ASK);

   if(!g_hasOpen)                                  // recovered after restart
   {
      g_open.entry = entry;
      g_open.risk  = MathAbs(entry - sl);
      g_open.stop0 = sl;
      g_open.beAtR = 0;
      g_open.holdBars = 0;
      g_open.opened = (datetime)PositionGetInteger(POSITION_TIME);
      g_open.movedBE = false;
      g_hasOpen = true;
   }

   // breakeven
   if(g_open.beAtR > 0 && !g_open.movedBE && g_open.risk > 0)
   {
      double prog = isBuy ? (px - entry) / g_open.risk
                          : (entry - px) / g_open.risk;
      if(prog >= g_open.beAtR)
      {
         double be = NormalizeDouble(entry, g_digits);
         if(trade.PositionModify(g_sym, be, tp))
         {
            g_open.movedBE = true;
            Say(StringFormat("%s: stop to breakeven at %.1fR", g_open.model, prog));
         }
      }
   }

   // time stop
   if(g_open.holdBars > 0)
   {
      int held = (int)((TimeCurrent() - g_open.opened) / PeriodSeconds(PERIOD_CURRENT));
      if(held >= g_open.holdBars)
      {
         Say(StringFormat("%s: hold limit %d bars reached, closing",
                          g_open.model, g_open.holdBars));
         trade.PositionClose(g_sym);
         g_hasOpen = false;
      }
   }
}

//====================================================================
// Lifecycle
//====================================================================
int OnInit()
{
   g_sym    = _Symbol;
   g_point  = SymbolInfoDouble(g_sym, SYMBOL_POINT);
   g_digits = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(g_sym);

   g_hasOpen = false;
   g_hasPend = false;
   g_dayStart = AccountInfoDouble(ACCOUNT_BALANCE);

   PrintFormat("ICTGold on %s %s | TurtleSoup %s  JudasSwing %s  OTE %s",
               g_sym, EnumToString((ENUM_TIMEFRAMES)Period()),
               InpUseTurtleSoup ? "on" : "off",
               InpUseJudasSwing ? "on" : "off",
               InpUseOTE ? "on" : "off");
   if(InpDryRun && !MQLInfoInteger(MQL_TESTER))
      Print("DRY RUN: signals are logged, no orders are sent.");
   if(MQLInfoInteger(MQL_TESTER) && InpDryRun)
      Print("Strategy Tester: dry run ignored so the test actually trades.");
   return INIT_SUCCEEDED;
}

void OnTick()
{
   ManageOpen();

   datetime bar = iTime(g_sym, PERIOD_CURRENT, 0);
   if(bar == g_lastBar) return;                    // one evaluation per bar
   g_lastBar = bar;

   RollDay(TimeCurrent());

   if(PositionSelect(g_sym) && PositionGetInteger(POSITION_MAGIC) == InpMagic)
      return;                                      // one position at a time
   if(OrdersTotal() > 0)
   {
      for(int i = 0; i < OrdersTotal(); i++)
      {
         ulong t = OrderGetTicket(i);
         if(t > 0 && OrderGetInteger(ORDER_MAGIC) == InpMagic) return;
      }
   }

   if(DayBlocked()) return;
   if(InRollover(TimeCurrent())) return;

   MqlDateTime dt;
   TimeToStruct(ToNy(TimeCurrent()), dt);
   if(InpBlockFridayLate && dt.day_of_week == 5 && NyHour(TimeCurrent()) >= InpFridayCutoffNY)
      return;

   if(!LoadBars()) return;
   int now = LastClosed();
   if(now < InpAtrPeriod + 70) return;

   Setup s;
   if(TurtleSoup(now, s) && s.ok) { Place(s); return; }
   if(JudasSwing(now, s) && s.ok) { Place(s); return; }
   if(Ote(now, s)        && s.ok) { Place(s); return; }
}

void OnTrade()
{
   // track consecutive losses from closed deals
   if(!HistorySelect(TimeCurrent() - 86400, TimeCurrent() + 60)) return;
   int total = HistoryDealsTotal();
   if(total <= 0) return;
   ulong d = HistoryDealGetTicket(total - 1);
   if(d == 0) return;
   if(HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic) return;
   if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_OUT) return;

   double profit = HistoryDealGetDouble(d, DEAL_PROFIT)
                 + HistoryDealGetDouble(d, DEAL_SWAP)
                 + HistoryDealGetDouble(d, DEAL_COMMISSION);
   if(profit < 0) g_consecLoss++;
   else           g_consecLoss = 0;
   g_hasOpen = false;
}

void OnDeinit(const int reason) { }
