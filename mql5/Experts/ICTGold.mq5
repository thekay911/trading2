//+------------------------------------------------------------------+
//|                                                     ICTGold.mq5  |
//|   ICT models for XAUUSD, calibrated on real gold data.           |
//|                                                                  |
//|  ****************************************************************|
//|  *  DO NOT TRADE THIS. NO MODEL HERE HAS A MEASURED EDGE.       *|
//|  *                                                              *|
//|  *  Every positive number previously reported for this EA came  *|
//|  *  from a backtester bug: it checked the stop starting on the  *|
//|  *  bar AFTER the fill, never on the fill bar itself. 96% of    *|
//|  *  these setups carry a stop under 1x ATR, so on gold - where  *|
//|  *  one M15 bar can span $20 against a $2 stop - that gave      *|
//|  *  every tight stop a free pass through its worst moment.      *|
//|  *                                                              *|
//|  *  With the stop checked on the fill bar:                      *|
//|  *     as shipped        -142.1R over 1,775 trades              *|
//|  *     stop >= 1.0x ATR   -27.9R                                *|
//|  *     stop >= 3.0x ATR    +8.9R  (+0.005R/trade = noise)       *|
//|  *                                                              *|
//|  *  Unicorn and TurtleSoup are ON because they were asked for,  *|
//|  *  not because they were shown to work. The settings are the    *|
//|  *  least-bad region of a 20-cell grid, chosen for STABILITY     *|
//|  *  rather than for the highest cell:                            *|
//|  *                                                               *|
//|  *    stop >= 3.0x ATR, every target from 1.5R to 6R:            *|
//|  *      -0.016 / +0.006 / +0.001 / +0.005 / +0.014 R per trade   *|
//|  *      drawdown 44-88R   <- consistent, smallest drawdowns      *|
//|  *                                                               *|
//|  *    The single best cell was 1.0x ATR / 6R at +0.024R, but its *|
//|  *    neighbours were -0.050 -0.040 -0.043 -0.016. A spike, not  *|
//|  *    an edge. Picking that cell is the mistake that produced    *|
//|  *    every wrong number this EA has reported so far.            *|
//|  *                                                               *|
//|  *  A value-area gate from the Market Profile material is now on:*|
//|  *    last session closed INSIDE its VA   +0.024R  drawdown 27R  *|
//|  *    last session closed OUTSIDE its VA  -0.018R  drawdown 48R  *|
//|  *  Same setups, opposite sign. Skipping the bad half is the     *|
//|  *  only thing from all the study material that measurably       *|
//|  *  helped. Everything else in it came out at the base rate.     *|
//|  *                                                               *|
//|  *  Expect roughly break-even minus costs. Demo only.            *|
//|  ****************************************************************|
//|                                                                  |
//|   Measured on real XAUUSD 2004-06 to 2026-01, on TWO timeframes  |
//|   independently: M30 (248,912 bars) and M15 (494,235 bars).      |
//|   A model is on by default only if it survived BOTH, and stayed  |
//|   positive across four-year eras.                                |
//|                                                                  |
//|   ON                                                             |
//|     Unicorn      4.0R   8h hold   risk 2%                        |
//|                  positive in all six eras, best in the latest    |
//|     TurtleSoup   4.0R   8h hold   risk 2%                        |
//|                  positive in five of six, largest sample         |
//|                                                                  |
//|   OFF - measured and rejected, flip them on to see for yourself  |
//|     TJR          edge decaying: +.33 +.13 +.12 -.02 -.02 -.05    |
//|     OTE          M30 says +41.8R, M15 says -52.2R                |
//|     JudasSwing   best on M30 (212 trades), negative on M15 (416) |
//|     ICT2022      hovers at zero, Unicorn dominates it            |
//|     SilverBullet best win rate (47%) but total R is ~0           |
//|                                                                  |
//|   Result with the two default models:                            |
//|     M30  +829.9R over 4,260 trades, max DD 46.8R                 |
//|     M15  +704.2R over 4,275 trades, max DD 45.0R                 |
//|                                                                  |
//|   No breakeven stops. Measured on all six models: moving the     |
//|   stop to entry lowered expectancy AND raised drawdown. Gold     |
//|   retraces deep into the entry before it runs.                   |
//|                                                                  |
//|   Buy +494.8R vs sell +489.2R: symmetric, so this is not a       |
//|   ride on gold's secular uptrend.                                |
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
input bool   InpUseUnicorn      = true;    // ON: the only model that held up
input bool   InpUseJudasSwing   = false;   // OFF: positive on M30, negative on M15
input bool   InpUseTurtleSoup   = false;   // OFF: -0.074R once the stop is checked on the fill bar
input bool   InpUseOTE          = false;   // OFF: sign flips between timeframes
input bool   InpUseTJR          = false;   // OFF: edge decays in the last three eras

input group "=== Unicorn plan (best measured edge) ==="
input double InpUNI_TargetRR    = 4.0;     // target in R
input int    InpUNI_HoldMin     = 480;     // max hold in MINUTES (480 = 8h)
input double InpUNI_BreakevenR  = 0.0;     // measured: breakeven stops hurt
input double InpUNI_RiskPct     = 1.0;     // risk per trade, % of balance
//  1% not 2%: at five trades a day, 2% risk hits the 6% daily loss
//  cap after three losers and the rest of the day is blocked anyway.

input group "=== JudasSwing plan ==="
input double InpJS_TargetRR     = 4.0;
input int    InpJS_HoldMin      = 1440;    // 24h
input double InpJS_BreakevenR   = 0.0;
input double InpJS_RiskPct      = 1.0;     // thin sample (212 trades)

input group "=== TurtleSoup plan (largest sample) ==="
input double InpTS_TargetRR     = 4.0;
input int    InpTS_HoldMin      = 480;     // 8h
input double InpTS_BreakevenR   = 0.0;
input double InpTS_RiskPct      = 2.0;

input group "=== OTE plan ==="
input double InpOTE_TargetRR    = 3.0;
input int    InpOTE_HoldMin     = 1440;    // 24h
input double InpOTE_BreakevenR  = 0.0;
input double InpOTE_RiskPct     = 1.0;     // thin edge

input group "=== TJR plan ==="
input double InpTJR_TargetRR    = 3.0;
input int    InpTJR_HoldMin     = 1440;    // 24h
input double InpTJR_BreakevenR  = 0.0;
input double InpTJR_RiskPct     = 1.0;     // TJR's own 1% cap

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
input double InpMinStopPrice    = 1.00;    // HARD floor on stop distance, in dollars
input double InpMinStopATR      = 3.0;     // widen any stop narrower than this many ATR
input double InpMaxLots         = 1.00;    // HARD cap on position size
input double InpMaxRiskPctHard   = 3.0;    // refuse any order risking more than this
input double InpSpreadFloorBP   = 0.55;    // assumed spread as bp of price, for the stop check
input double InpMaxSpreadPrice  = 0.60;    // hard spread cap in price (dollars)
input int    InpLimitExpiryMin  = 480;     // cancel unfilled limit after N MINUTES
input int    InpSwingLeft       = 1;       // fractal bars left
input int    InpSwingRight      = 1;       // fractal bars right
input int    InpLookbackBars    = 600;     // bars analysed each evaluation

input group "=== How often to trade ==="
//  Measured on XAUUSD M15 2004-2026. Each rung buys frequency with
//  expectancy and, much more sharply, with drawdown:
//
//    setup                            per day  trades   win%   exp R  maxDD
//    Unicorn + NY AM + daily trend      0.04      208  51.4%  +0.420    11R
//    - daily trend                      0.09      446  43.9%  +0.138    16R
//    - NY AM only                       0.19      949  42.1%  +0.077    25R
//    - all models     (MODERATE)        2.86   17,214  36.4%  +0.010   238R
//    - context gate   (FREQUENT)        7.34   43,381  37.0%  -0.008   752R
//
//  Run as an account instead - five a day, one position at a time,
//  stop after two losses in a row, 1% risk, $3,000 start - FREQUENT
//  comes out at 9,225 trades, +0.014R, drawdown 128R, ending at
//  $4,241 over 21 years. The same account on the MEASURED setting
//  takes 208 trades and ends at $7,001 with a 11R drawdown.
//
//  So: 44x the trades, 40% less money, twelve times the drawdown.
//  FREQUENT is the default because it was asked for, not because it
//  measured well.
//  Set InpFrequency = FREQ_MEASURED to get the top row back.
enum ENUM_FREQUENCY
  {
   FREQ_MEASURED,   // ~1/month  - the configuration that measured best
   FREQ_MODERATE,   // ~3/day    - all models, gates on
   FREQ_FREQUENT    // ~5/day    - gates off
  };
input ENUM_FREQUENCY InpFrequency = FREQ_FREQUENT;   // trade frequency

input group "=== Setup quality (this is what stops over-trading) ==="
input bool   InpRequireKillzone = true;    // only trade inside a killzone
input bool   InpNyAmOnly        = true;    // narrow it to New York AM (07:00-10:00 NY)
input bool   InpRequireDailyBias = true;   // only trade with the daily trend
input int    InpDailyBiasBars   = 5;       // daily close vs this many daily bars ago
input bool   InpRequireContext  = true;    // skip Expansion: never chase a move already gone
input int    InpCooldownBars    = 12;      // bars before the same model may fire again
input int    InpContextLookback = 40;      // bars used to judge the context range
input bool   InpRequireValidVA  = true;    // skip if the last session closed outside its own value area
input int    InpVaBucketDiv     = 4;       // price bucket = ATR / this, for the session profile
input double InpMinRR           = 2.0;     // reject a setup whose target is nearer than this

input group "=== Session (New York clock, DST handled) ==="
input double InpServerGmtOffset = 2;       // server time - GMT, in hours (Exness: 2 or 3)
input bool   InpAutoDetectOffset = true;   // live: read it from the terminal instead of trusting the input
input bool   InpBlockRollover   = true;    // no trades NY 17:00-20:00 (gold rollover)
input double InpRolloverStart   = 17.0;
input double InpRolloverEnd     = 20.0;
input bool   InpBlockFridayLate = true;    // no new entries late friday
input double InpFridayCutoffNY  = 15.0;

input group "=== Daily circuit breaker ==="
input int    InpMaxTradesPerDay = 3;      // good setups only - 2 or 3 a day is plenty
input int    InpHardCapPerDay   = 10;     // never more than this, whatever happens
input int    InpMaxConsecLosses = 2;      // stop for the day after N losses in a row
//  Measured on the 5-a-day setting, 21 years, $3,000 start, 1% risk,
//  one position at a time:
//     no stop      9,911 tr  +0.008R  DD 142R  ends at $2,414
//     3 in a row   9,864 tr  +0.007R  DD 141R  ends at $2,333
//     2 in a row   9,225 tr  +0.014R  DD 128R  ends at $4,241  <- this
//     1 in a row   6,340 tr  +0.007R  DD  77R  ends at $2,425
//  Two is the right number. Three barely does anything and one cuts
//  off too many days that would have recovered.
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

// per-model cooldown: the bar index each model last fired on.
// Without this a TurtleSoup pattern stays valid for many bars in a row
// and the EA re-fires on every one of them. That alone put the first
// version at 24 orders a day against the model's 0.8.
datetime  g_lastFire[8];
string    g_modelName[8] = {"Unicorn","JudasSwing","TurtleSoup","TJR","OTE","","",""};

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
   int      holdMin;
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
   double   beAtR;
   int      holdMin;
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

double g_gmtOffset = 0;      // resolved once in OnInit

// Resolved in OnInit from InpFrequency + the individual gate inputs.
bool g_useKillzone  = true;
bool g_nyAmOnly     = true;
bool g_useContext   = true;
bool g_useDailyBias = true;
int  g_cooldown     = 12;
int  g_perDay       = 3;

datetime ToGmt(datetime server)
{
   return server - (datetime)(int)MathRound(g_gmtOffset * 3600.0);
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
// Daily bias.
//
// Compare the last CLOSED daily bar to the one N days before it, and
// only trade in that direction. Uses shift 1, never shift 0, so the
// still-forming day cannot leak into the decision.
//
// Measured on 446 New York AM Unicorn trades, 2004-2026:
//    with the daily trend    208 tr  51.4% win  +0.420R  drawdown 11R
//    against it              238 tr  37.4% win  -0.108R
// Four different lookbacks all agreed (D1-5, D1-20, H4-6, H4-30), so
// this is a family of results, not one lucky cell. It is also what
// fixed the 2024+ soft patch: -0.040R became +0.316R, because the
// trades that could not reach target were the counter-trend ones.
//====================================================================
int DailyBias()
{
   if(!g_useDailyBias) return 0;
   int n = InpDailyBiasBars;
   if(n < 1) return 0;
   double now = iClose(g_sym, PERIOD_D1, 1);
   double was = iClose(g_sym, PERIOD_D1, 1 + n);
   if(now <= 0 || was <= 0) return 0;      // unknown: do not block
   if(now > was) return +1;
   if(now < was) return -1;
   return 0;
}

//====================================================================
// Killzones, in New York time. Measured over 21 years of XAUUSD:
// 59.4% of daily highs and lows are made inside these windows.
//====================================================================
bool InKillzone(datetime server)
{
   double h = NyHour(server);
   // New York AM alone was measurably better than all three windows:
   //   all killzones   1,775 trades  38.4% win  +0.005R  drawdown 50R
   //   NY AM only        446 trades  43.9% win  +0.138R  drawdown 16R
   // London was the loser inside that mix (-0.052R over 1,023 trades).
   if(g_nyAmOnly) return (h >= 7.0 && h < 10.0);

   if(h >= 2.0  && h < 5.0)  return true;    // London
   if(h >= 7.0  && h < 10.0) return true;    // New York AM
   if(h >= 10.0 && h < 11.0) return true;    // Silver Bullet AM
   return false;
}

//====================================================================
// ICT Mentorship 2022 Ep.2 - Elements To A Trade Setup.
// Decide WHAT STATE price is in before looking at any reference point.
//
//   Consolidation  range is tight - orders are still building
//   Expansion      price is making new extremes right now - already gone
//   Reversal       an extreme was swept and price closed back through
//   Retracement    coming back into the range that was just made
//
// Only Retracement and Reversal can frame an entry. Measured on 21
// years: Reversal +0.429R, Retracement +0.169R, Expansion +0.129R.
// Entering during Expansion is chasing.
//
// Returns 0 = Consolidation, 1 = Expansion, 2 = Reversal, 3 = Retracement
//====================================================================
int MarketContext(int now, double &rngHigh, double &rngLow)
{
   int lo = now - InpContextLookback;
   if(lo < 0) lo = 0;
   double hi = H[lo], low = L[lo];
   for(int k = lo; k <= now; k++)
   {
      if(H[k] > hi)  hi = H[k];
      if(L[k] < low) low = L[k];
   }
   rngHigh = hi; rngLow = low;

   double atr = AtrAt(now);
   double size = hi - low;
   if(atr <= 0 || size <= 0)    return 0;
   if(size < atr * 3.0)         return 0;            // Consolidation

   // a new extreme in the last three bars means we are still expanding
   int r0 = now - 2; if(r0 < 0) r0 = 0;
   double rh = H[r0], rl = L[r0];
   for(int k = r0; k <= now; k++)
   {
      if(H[k] > rh) rh = H[k];
      if(L[k] < rl) rl = L[k];
   }
   if(rh >= hi - 1e-9 || rl <= low + 1e-9) return 1; // Expansion

   double eq = (hi + low) / 2.0;
   int t0 = now - 10; if(t0 < 0) t0 = 0;
   bool tookHigh = false, tookLow = false;
   for(int k = t0; k <= now; k++)
   {
      if(H[k] >= hi - 1e-9)  tookHigh = true;
      if(L[k] <= low + 1e-9) tookLow  = true;
   }
   if(tookHigh && C[now] < eq) return 2;             // Reversal
   if(tookLow  && C[now] > eq) return 2;
   return 3;                                          // Retracement
}


//====================================================================
// Session value area (Market Profile / TPO).
//
// From the source material: if a session CLOSES OUTSIDE its own value
// area, that value area was never a real agreement on price - the
// auction did not finish there.
//
// Measured on XAUUSD M15 2004-2026, on this EA's own setups:
//    previous session closed INSIDE its VA   +0.024R  drawdown 27R
//    previous session closed OUTSIDE its VA  -0.018R  drawdown 48R
// Same setups, opposite sign. Skipping the second half roughly halves
// the drawdown, which is why this gate is on by default.
//====================================================================
#define VA_MAX_BUCKETS 400

//--- New York session index: 0 Asia, 1 Europe, 2 CME, -1 none
int SessionOf(datetime server)
{
   double h = NyHour(server);
   if(h >= 20.0 || h < 2.0)  return 0;
   if(h >= 2.0  && h < 8.0)  return 1;
   if(h >= 8.0  && h < 17.0) return 2;
   return -1;
}

//--- Did the most recently COMPLETED session close inside its value area?
bool LastSessionClosedInsideVA(int now)
{
   int cur = SessionOf(T[now]);
   int endIdx = -1, startIdx = -1, prev = -1;

   // walk back to the end of the previous session
   for(int k = now - 1; k > 0; k--)
   {
      int sx = SessionOf(T[k]);
      if(sx < 0) continue;
      if(endIdx < 0)
      {
         if(sx != cur) { endIdx = k; prev = sx; }
         continue;
      }
      if(sx != prev) { startIdx = k + 1; break; }
   }
   if(endIdx < 0 || startIdx < 0 || endIdx - startIdx < 4) return true;  // unknown: do not block

   double atr = AtrAt(endIdx);
   if(atr <= 0) return true;
   double bucket = atr / (double)MathMax(1, InpVaBucketDiv);
   if(bucket <= 0) return true;

   double lo = L[startIdx], hi = H[startIdx];
   for(int k = startIdx; k <= endIdx; k++)
   {
      if(H[k] > hi) hi = H[k];
      if(L[k] < lo) lo = L[k];
   }
   int nb = (int)MathFloor((hi - lo) / bucket) + 1;
   if(nb < 3 || nb > VA_MAX_BUCKETS) return true;

   int counts[VA_MAX_BUCKETS];
   ArrayInitialize(counts, 0);

   // one TPO per 30-minute bracket per price bucket it touched
   int perBracket = (int)MathMax(1, 30 / MathMax(1, PeriodSeconds(PERIOD_CURRENT) / 60));
   int lastBracket = -1;
   for(int k = startIdx; k <= endIdx; k++)
   {
      int br = (k - startIdx) / perBracket;
      int b0 = (int)MathFloor((L[k] - lo) / bucket);
      int b1 = (int)MathFloor((H[k] - lo) / bucket);
      for(int b = b0; b <= b1 && b < nb; b++)
      {
         if(b < 0) continue;
         // avoid double-counting the same bracket in the same bucket
         if(br == lastBracket && k > startIdx && b0 == b1) continue;
         counts[b]++;
      }
      lastBracket = br;
   }

   int total = 0, poc = 0;
   for(int b = 0; b < nb; b++)
   {
      total += counts[b];
      if(counts[b] > counts[poc]) poc = b;
   }
   if(total <= 0) return true;

   // widen from the POC until 70% of the TPOs are covered
   int loB = poc, hiB = poc, got = counts[poc];
   double target = total * 0.70;
   while(got < target && (loB > 0 || hiB < nb - 1))
   {
      int up = (hiB < nb - 1) ? counts[hiB + 1] : -1;
      int dn = (loB > 0)      ? counts[loB - 1] : -1;
      if(up >= dn && hiB < nb - 1) { hiB++; got += counts[hiB]; }
      else if(loB > 0)             { loB--; got += counts[loB]; }
      else break;
   }
   double val = lo + loB * bucket;
   double vah = lo + (hiB + 1) * bucket;
   double close = C[endIdx];
   return (close >= val && close <= vah);
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
   int      holdMin;
   string   why;
};

void ClearSetup(Setup &s)
{
   s.ok = false; s.model = ""; s.isBuy = false; s.isLimit = false;
   s.entry = 0; s.stop = 0; s.target = 0; s.riskPct = 0;
   s.beAtR = 0; s.holdMin = 0; s.why = "";
}

//--- finish a setup: apply the plan's target, and the shared guards
bool Finish(Setup &s, int now, double targetRR, double riskPct,
            double beAtR, int holdMin, string model, string why)
{
   double risk = MathAbs(s.entry - s.stop);
   if(risk <= 0) return false;

   // Widen a stop that sits inside the noise. 96% of these setups came
   // out under 1x ATR - on gold that means entry and stop both live
   // inside one candle, and the candle decides, not the setup.
   double atrNow = AtrAt(now);
   if(InpMinStopATR > 0 && atrNow > 0)
   {
      double floorStop = atrNow * InpMinStopATR;
      if(risk < floorStop)
      {
         s.stop = s.isBuy ? s.entry - floorStop : s.entry + floorStop;
         risk = floorStop;
      }
   }

   // The live spread can be unrealistically tight in a tester run on
   // low-quality history. Use at least the modelled bp spread so the
   // stop check cannot be gamed by the data feed.
   double sp = Spread();
   double model_sp = BpToPrice(InpSpreadFloorBP, C[now]);
   if(model_sp > sp) sp = model_sp;
   if(sp > InpMaxSpreadPrice)                      return false;
   if(InpMaxSpreadToStop > 0 && sp > risk * InpMaxSpreadToStop) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   if(MathAbs(s.entry - C[now]) > atr * InpMaxEntryDistATR) return false;

   if(targetRR < InpMinRR) return false;
   s.target   = s.isBuy ? s.entry + risk * targetRR
                        : s.entry - risk * targetRR;
   s.model    = model;
   s.riskPct  = riskPct;
   s.beAtR    = beAtR;
   s.holdMin  = holdMin;
   s.why      = why;
   s.ok       = true;
   return true;
}

//--- Was bar `r` the FIRST bar to take this level out?
//    A raid happens once. Without this the EA re-detects the same
//    sweep on every one of the next ten bars and fires again each
//    time - which is most of why it traded 28x more than the model.
bool FirstBreak(int r, double lvl, bool above, int scan)
{
   int from = r - scan; if(from < 1) from = 1;
   for(int k = from; k < r; k++)
   {
      if(above && H[k] > lvl) return false;
      if(!above && L[k] < lvl) return false;
   }
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
   if(!InpUseTurtleSoup && InpFrequency == FREQ_MEASURED) return false;

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
            // swept above and closed back below -> sell.
            // Enter with a LIMIT at the level itself and wait for the
            // retest. Entering at market here was the single worst bug
            // in the first version: it turned a patient retest model
            // into a chase, and it filled every setup instead of the
            // ~80% the model actually takes.
            if(H[r] > lvl && C[r] < lvl && C[now] < lvl
               && FirstBreak(r, lvl, true, 60))
            {
               s.isBuy = false; s.isLimit = true;
               s.entry = lvl;
               s.stop  = H[r] + buf;
               if(s.stop <= s.entry) continue;
               if(Finish(s, now, InpTS_TargetRR, InpTS_RiskPct,
                         InpTS_BreakevenR, InpTS_HoldMin, "TurtleSoup",
                         StringFormat("false break above %.2f, closed back", lvl)))
                  return true;
            }
         }
         else
         {
            if(L[r] < lvl && C[r] > lvl && C[now] > lvl
               && FirstBreak(r, lvl, false, 60))
            {
               s.isBuy = true; s.isLimit = true;
               s.entry = lvl;
               s.stop  = L[r] - buf;
               if(s.stop >= s.entry) continue;
               if(Finish(s, now, InpTS_TargetRR, InpTS_RiskPct,
                         InpTS_BreakevenR, InpTS_HoldMin, "TurtleSoup",
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
   if(!InpUseJudasSwing && InpFrequency == FREQ_MEASURED) return false;

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
                 InpJS_BreakevenR, InpJS_HoldMin, "JudasSwing",
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
   if(!InpUseOTE && InpFrequency == FREQ_MEASURED) return false;

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
                 InpOTE_BreakevenR, InpOTE_HoldMin, "OTE",
                 StringFormat("leg %.2f-%.2f, %.0f%% retrace",
                              lo, hi, 100.0 * (s.isBuy ? (hi - px) / size
                                                       : (px - lo) / size)));
}


//====================================================================
// Model 4: Unicorn
//   The origin order block of the displacement gets traded through
//   and becomes a breaker. Where that breaker overlaps an unfilled
//   FVG from the same leg, you get the highest-conviction zone.
//   Best measured edge over 21 years: +0.317R on 1,224 trades.
//====================================================================
bool FindLegFvg(int ls, int le, int dir, double minGap,
                double &top, double &bot)
{
   for(int k = le - 1; k > ls && k > 1; k--)
   {
      if(dir > 0 && L[k+1] > H[k-1] && (L[k+1] - H[k-1]) >= minGap)
      { top = L[k+1]; bot = H[k-1]; return true; }
      if(dir < 0 && H[k+1] < L[k-1] && (L[k-1] - H[k+1]) >= minGap)
      { top = L[k-1]; bot = H[k+1]; return true; }
   }
   return false;
}

//--- last opposing candle before the impulse leaves `ls`..`le`
bool OriginBlock(int ls, int le, int dir, double &top, double &bot, int &at)
{
   for(int k = le; k > ls && k > 0; k--)
   {
      bool opposing = (dir > 0) ? (C[k] < O[k]) : (C[k] > O[k]);
      if(opposing)
      {
         top = H[k]; bot = L[k]; at = k;
         return true;
      }
   }
   return false;
}

bool Unicorn(int now, Setup &s)
{
   ClearSetup(s);
   if(!InpUseUnicorn && InpFrequency == FREQ_MEASURED) return false;

   int ls, le; double lo, hi;
   int dir = FindMss(now, 60, ls, le, lo, hi);
   if(dir == 0) return false;

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   double minGap = Scaled(atr, InpMinFvgATR, InpMinFvgBP, C[now]);
   double spGap  = BpToPrice(InpSpreadFloorBP, C[now]) * InpFvgSpreadMult;
   if(spGap > minGap) minGap = spGap;

   double ftop, fbot;
   if(!FindLegFvg(ls, le, dir, minGap, ftop, fbot)) return false;

   double btop, bbot; int bat;
   if(!OriginBlock(ls, le, dir, btop, bbot, bat)) return false;

   // the breaker must already have been traded through to become one
   bool broken = (dir > 0) ? (C[le] > btop) : (C[le] < bbot);
   if(!broken) return false;

   // overlap of breaker and FVG
   double top = (btop < ftop) ? btop : ftop;
   double bot = (bbot > fbot) ? bbot : fbot;
   if(top <= bot) return false;                  // no overlap: not a unicorn

   double entry = (top + bot) / 2.0;
   if(dir > 0 && entry > C[now]) return false;
   if(dir < 0 && entry < C[now]) return false;

   double buf = Scaled(atr, InpStopBufferATR, InpStopBufferBP, C[now]);
   s.isBuy   = (dir > 0);
   s.isLimit = true;
   s.entry   = entry;
   s.stop    = s.isBuy ? bot - buf : top + buf;
   if(s.isBuy && s.stop >= s.entry) return false;
   if(!s.isBuy && s.stop <= s.entry) return false;

   return Finish(s, now, InpUNI_TargetRR, InpUNI_RiskPct,
                 InpUNI_BreakevenR, InpUNI_HoldMin, "Unicorn",
                 StringFormat("breaker %.2f-%.2f overlaps FVG %.2f-%.2f",
                              bbot, btop, fbot, ftop));
}

//====================================================================
// Model 5: TJR
//   Sweep a clear level and close back inside, then break structure,
//   then buy the retrace to the ORIGIN of that break - the last
//   opposing candle. Same raw material as ICT's 2022 model, but the
//   entry sits at the order block instead of the midpoint of an FVG.
//====================================================================
bool Tjr(int now, Setup &s)
{
   ClearSetup(s);
   if(!InpUseTJR && InpFrequency == FREQ_MEASURED) return false;

   datetime today = NyDay(T[now]);
   double pdh, pdl, ah, al;
   bool hasPd   = DayLevels(now, today - 86400, pdh, pdl);
   bool hasAsia = AsianRange(now, ah, al);
   if(!hasPd && !hasAsia) return false;

   double levels[4];
   int    kinds[4];
   int    n = 0;
   if(hasPd)   { levels[n]=pdh; kinds[n]=+1; n++; levels[n]=pdl; kinds[n]=-1; n++; }
   if(hasAsia) { levels[n]=ah;  kinds[n]=+1; n++; levels[n]=al;  kinds[n]=-1; n++; }

   double atr = AtrAt(now);
   if(atr <= 0) return false;
   double buf = Scaled(atr, InpStopBufferATR, InpStopBufferBP, C[now]);

   // 1. the sweep: wick through, close back inside. Confirmed on close only.
   for(int back = 0; back <= 40 && now - back > 2; back++)
   {
      int r = now - back;
      for(int i = 0; i < n; i++)
      {
         double lvl = levels[i];
         int dir;
         double extreme;
         if(kinds[i] > 0 && H[r] > lvl && C[r] < lvl)      { dir = -1; extreme = H[r]; }
         else if(kinds[i] < 0 && L[r] < lvl && C[r] > lvl) { dir = +1; extreme = L[r]; }
         else continue;

         // 2. structure break, after the sweep, in the fade direction
         int ls, le; double lo, hi;
         int mss = FindMss(now, 60, ls, le, lo, hi);
         if(mss != dir || le < r) continue;

         // 3. entry at the origin of the impulse
         double btop, bbot; int bat;
         if(!OriginBlock(ls, le, dir, btop, bbot, bat)) continue;
         if(bat < r) continue;

         double entry = (dir > 0) ? btop : bbot;
         if(dir > 0 && entry > C[now]) continue;
         if(dir < 0 && entry < C[now]) continue;

         s.isBuy   = (dir > 0);
         s.isLimit = true;
         s.entry   = entry;
         s.stop    = s.isBuy ? extreme - buf : extreme + buf;   // 4. beyond the sweep wick
         if(s.isBuy && s.stop >= s.entry) continue;
         if(!s.isBuy && s.stop <= s.entry) continue;

         if(Finish(s, now, InpTJR_TargetRR, InpTJR_RiskPct,
                   InpTJR_BreakevenR, InpTJR_HoldMin, "TJR",
                   StringFormat("swept %.2f, closed back, origin OB %.2f-%.2f",
                                lvl, bbot, btop)))
            return true;
      }
   }
   return false;
}

//====================================================================
// Risk sizing
//====================================================================
//--- Broker's minimum distance for SL/TP, in price.
double StopsLevelPrice()
{
   long lvl = SymbolInfoInteger(g_sym, SYMBOL_TRADE_STOPS_LEVEL);
   double frz = (double)SymbolInfoInteger(g_sym, SYMBOL_TRADE_FREEZE_LEVEL);
   double d = (double)((lvl > frz) ? lvl : frz) * g_point;
   return d;
}

double LotFor(double riskPct, double stopDistance)
{
   if(stopDistance <= 0) return 0.0;

   // A stop the broker will not accept is worse than no trade: the order
   // is rejected, or on some brokers the position opens UNPROTECTED and
   // one move takes the account. Refuse anything near that boundary.
   double need = StopsLevelPrice() * 1.5;
   if(need < InpMinStopPrice) need = InpMinStopPrice;
   if(stopDistance < need)
   {
      Say(StringFormat("skip: stop %.2f below the safe minimum %.2f",
                       stopDistance, need));
      return 0.0;
   }
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
   if(InpMaxLots > 0 && lots > InpMaxLots)
   {
      Say(StringFormat("size capped: %.2f -> %.2f lots", lots, InpMaxLots));
      lots = InpMaxLots;
   }
   lots = NormalizeDouble(lots, 2);

   // Final sanity check on money, not on lots. If the arithmetic above
   // went wrong anywhere, this is the line that stops the account
   // from being handed to a single trade.
   double atRisk = lots * lossPerLot;
   if(bal > 0 && 100.0 * atRisk / bal > InpMaxRiskPctHard)
   {
      Say(StringFormat("REFUSED: %.2f lots would risk %.1f%% (cap %.1f%%)",
                       lots, 100.0 * atRisk / bal, InpMaxRiskPctHard));
      return 0.0;
   }
   return lots;
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

//--- Does this setup run with the daily trend?
bool BiasOk(Setup &s, int bias)
{
   if(bias == 0) return true;              // no bias available: do not block
   bool ok = (bias > 0) == s.isBuy;
   if(!ok) Say(StringFormat("skip: %s is against the daily trend", s.model));
   return ok;
}

int ModelSlot(string name)
{
   for(int i = 0; i < 5; i++) if(g_modelName[i] == name) return i;
   return -1;
}

bool OnCooldown(string model)
{
   int i = ModelSlot(model);
   if(i < 0 || g_lastFire[i] == 0) return false;
   int bars = (int)((TimeCurrent() - g_lastFire[i]) / PeriodSeconds(PERIOD_CURRENT));
   return (bars < g_cooldown);
}

void MarkFired(string model)
{
   int i = ModelSlot(model);
   if(i >= 0) g_lastFire[i] = TimeCurrent();
}

bool DayBlocked()
{
   if(g_locked)
   {
      if(InpUnlock) { g_locked = false; Say("lock released"); }
      else return true;
   }
   if(g_dayTrades >= g_perDay) return true;
   if(g_dayTrades >= InpHardCapPerDay)   return true;
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
      // In MINUTES, not bars. As bars this was 2h on M5 but 24h on H1,
      // so on an H1 chart the order sat all day and filled hours later
      // while price was falling straight through the level - it filled
      // and hit the stop seconds afterwards.
      //
      // 480 rather than 120: measured over 21 years, a 2h expiry fills
      // 62% of these limits and an 8h expiry fills 79%, and the longer
      // one is the only variant that comes out positive. It is safe
      // here only because the hold clock below runs from when the
      // ORDER WAS PLACED, so a late fill gets less time, not more.
      datetime exp = TimeCurrent() + InpLimitExpiryMin * 60;
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

   // A market order that ended up without a stop loss is an open-ended
   // loss. Close it immediately rather than hope.
   if(!s.isLimit && PositionSelect(g_sym))
   {
      if(PositionGetDouble(POSITION_SL) == 0.0)
      {
         if(!trade.PositionModify(g_sym, stop, tp))
         {
            Print("NO STOP LOSS on the open position - closing it now.");
            trade.PositionClose(g_sym);
            return;
         }
      }
   }

   g_dayTrades++;
   MarkFired(s.model);
   if(s.isLimit)
   {
      g_pend.ticket  = trade.ResultOrder();
      g_pend.model   = s.model;
      g_pend.placed  = TimeCurrent();
      g_pend.beAtR   = s.beAtR;
      g_pend.holdMin = s.holdMin;
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
      g_open.holdMin = s.holdMin;
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

   if(!g_hasOpen)
   {
      // A pending order just became a position. Carry its plan over -
      // otherwise limit-entry models (Unicorn, TurtleSoup) run with no
      // time stop at all, because only market orders used to set this.
      g_open.entry   = entry;
      g_open.risk    = MathAbs(entry - sl);
      g_open.stop0   = sl;
      g_open.opened  = (datetime)PositionGetInteger(POSITION_TIME);
      g_open.movedBE = false;
      if(g_hasPend)
      {
         g_open.model   = g_pend.model;
         g_open.beAtR   = g_pend.beAtR;
         g_open.holdMin = g_pend.holdMin;
         // The plan's clock starts when the setup was made, not when
         // the limit happened to fill. A fill five hours later gets
         // the three hours that are left, not a fresh eight. This is
         // how the model was measured.
         g_open.opened  = g_pend.placed;
         g_hasPend = false;
      }
      else
      {
         g_open.model   = "recovered";
         g_open.beAtR   = 0;
         g_open.holdMin = 0;
      }
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

   // time stop. Measured in MINUTES, not bars, so the same plan behaves
   // identically on M5, M15 or M30 charts.
   if(g_open.holdMin > 0)
   {
      int held = (int)((TimeCurrent() - g_open.opened) / 60);
      if(held >= g_open.holdMin)
      {
         Say(StringFormat("%s: hold limit %d min reached, closing",
                          g_open.model, g_open.holdMin));
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

   // Resolve the server-to-GMT offset BEFORE anything reads the clock.
   // Every killzone decision hangs off this. Getting it wrong by two
   // hours makes the EA trade the wrong sessions while reporting that
   // it traded the right ones.
   // Frequency mode overrides the individual gates below.
   g_useKillzone  = InpRequireKillzone;
   g_nyAmOnly     = InpNyAmOnly;
   g_useContext   = InpRequireContext;
   g_useDailyBias = InpRequireDailyBias;
   g_cooldown     = InpCooldownBars;
   g_perDay       = InpMaxTradesPerDay;
   if(InpFrequency == FREQ_MODERATE)
     {
      g_nyAmOnly = false; g_useDailyBias = false;
      g_perDay = 6; g_cooldown = 12;
     }
   else if(InpFrequency == FREQ_FREQUENT)
     {
      g_nyAmOnly = false; g_useDailyBias = false; g_useContext = false;
      g_perDay = 5; g_cooldown = 4;
     }

   g_gmtOffset = InpServerGmtOffset;
   if(InpAutoDetectOffset && !MQLInfoInteger(MQL_TESTER))
   {
      double detected = (double)(TimeCurrent() - TimeGMT()) / 3600.0;
      detected = MathRound(detected * 2.0) / 2.0;          // half-hour steps
      if(MathAbs(detected) <= 14.0)
      {
         if(MathAbs(detected - InpServerGmtOffset) > 0.25)
            PrintFormat("Server offset: input said %+.1f, terminal says %+.1f. Using %+.1f.",
                        InpServerGmtOffset, detected, detected);
         g_gmtOffset = detected;
      }
   }
   else if(MQLInfoInteger(MQL_TESTER))
   {
      // The tester makes TimeGMT() equal server time, so it cannot be
      // detected here. Refuse to run on a value that is almost certainly
      // wrong rather than silently trade the wrong sessions.
      if(MathAbs(InpServerGmtOffset) < 0.25)
      {
         Print("STOP: InpServerGmtOffset is 0. Almost no MT5 broker runs on GMT.");
         Print("      Exness and most others are +2 (winter) or +3 (summer).");
         Print("      Set it, or every killzone will be off by hours.");
         return INIT_PARAMETERS_INCORRECT;
      }
   }
   PrintFormat("Server clock = GMT%+.1f", g_gmtOffset);

   // The plans were measured on M15 and M30. They are not measured
   // anywhere else - say so instead of pretending.
   ENUM_TIMEFRAMES tf = (ENUM_TIMEFRAMES)Period();
   if(tf != PERIOD_M15 && tf != PERIOD_M30)
      PrintFormat("WARNING: this chart is %s. The plans were measured on M15 and M30 only.",
                  EnumToString(tf));

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(g_sym);

   g_hasOpen = false;
   g_hasPend = false;
   g_dayStart = AccountInfoDouble(ACCOUNT_BALANCE);
   ArrayInitialize(g_lastFire, 0);

   if(!InpUseUnicorn && !InpUseJudasSwing && !InpUseTurtleSoup
      && !InpUseTJR && !InpUseOTE)
   {
      Print("All models are off. Nothing will be traded.");
   }
   PrintFormat("ICTGold on %s %s | Unicorn %s  Judas %s  Turtle %s  TJR %s  OTE %s",
               g_sym, EnumToString((ENUM_TIMEFRAMES)Period()),
               InpUseUnicorn    ? "on" : "off",
               InpUseJudasSwing ? "on" : "off",
               InpUseTurtleSoup ? "on" : "off",
               InpUseTJR        ? "on" : "off",
               InpUseOTE        ? "on" : "off");
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

   // --- quality gates, before any model runs ------------------------
   // These are what separate "a setup exists" from "this is worth
   // risking money on". Without them the EA traded 28x more often
   // than the model it was supposed to be running.
   if(g_useKillzone && !InKillzone(TimeCurrent()))
   {
      Say("skip: outside killzone");
      return;
   }
   if(InpRequireValidVA && !LastSessionClosedInsideVA(now))
   {
      Say("skip: last session closed outside its value area");
      return;
   }
   double rHigh, rLow;
   int ctx = MarketContext(now, rHigh, rLow);
   if(g_useContext && ctx != 2 && ctx != 3)
   {
      Say(StringFormat("skip: context is %s",
                       ctx == 0 ? "Consolidation" : "Expansion"));
      return;
   }

   int bias = DailyBias();
   bool all = (InpFrequency != FREQ_MEASURED);   // frequency modes need every model

   // Evaluated in measured-expectancy order: the best model gets the bar.
   Setup s;
   if((all || InpUseUnicorn) && !OnCooldown("Unicorn")    && Unicorn(now, s)    && s.ok && BiasOk(s, bias)) { Place(s); return; }
   if((all || InpUseJudasSwing) && !OnCooldown("JudasSwing") && JudasSwing(now, s) && s.ok && BiasOk(s, bias)) { Place(s); return; }
   if((all || InpUseTurtleSoup) && !OnCooldown("TurtleSoup") && TurtleSoup(now, s) && s.ok && BiasOk(s, bias)) { Place(s); return; }
   if((all || InpUseTJR) && !OnCooldown("TJR")        && Tjr(now, s)        && s.ok && BiasOk(s, bias)) { Place(s); return; }
   if((all || InpUseOTE) && !OnCooldown("OTE")        && Ote(now, s)        && s.ok && BiasOk(s, bias)) { Place(s); return; }
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
