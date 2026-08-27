//+------------------------------------------------------------------+
//|                                                 CrowConcept.mq5  |
//|  Crow Concept rule engine as a native MT5 Expert Advisor.        |
//|                                                                  |
//|  Pipeline (top-down), mirroring the Python `crowcode` package:   |
//|    0. Gates      session / news / friday close / daily risk       |
//|    1. HTF bias   market structure (BOS,CHOCH) + Wyckoff phase     |
//|    2. MTF zone   order block or FVG that is still valid           |
//|    3. LTF entry  liquidity sweep, THEN a CHOCH in the same way    |
//|    4. Order      limit at the zone edge (market if price is in)   |
//|    5. Manage     2R -> breakeven, 3R -> partial, SL never back    |
//|                                                                  |
//|  THIS SINGLE FILE IS THE WHOLE EA. Nothing else is required -    |
//|  no includes, no .set file, no Python. Copy it into              |
//|      <MT5 data folder>/MQL5/Experts/                             |
//|  compile with F7, drag it onto a XAUUSD chart. Done.             |
//|                                                                  |
//|  Defaults are already the intended setup (Exness, XAUUSD):       |
//|      M15 > M5 > M1 | risk 2% | stop 20 pips | target 1:3         |
//|      1 pip = $0.10, so 20 pips = $2.00 = $2 risk on 0.01 lot     |
//|                                                                  |
//|  Comments are in English so the file compiles cleanly regardless  |
//|  of the editor's encoding.                                       |
//+------------------------------------------------------------------+
#property copyright "crowcode"
#property version   "1.00"

#include <Trade/Trade.mqh>

//--- How the stop distance is decided
enum ENUM_STOP_MODE
  {
   STOP_STRUCTURE,   // structure only - skip setups outside Min/Max
   STOP_CLAMP,       // widen a tight stop to Min; skip if structure needs more than Max
   STOP_FIXED        // always exactly InpFixedSLPrice - ignore structure
  };

//====================================================================
// Inputs
//====================================================================
input group "=== Timeframes (top-down) ==="
input ENUM_TIMEFRAMES  InpHTF              = PERIOD_M15;  // HTF: direction
input ENUM_TIMEFRAMES  InpMTF              = PERIOD_M5;   // MTF: zone (POI)
input ENUM_TIMEFRAMES  InpLTF              = PERIOD_M1;   // LTF: trigger

input group "=== Structure ==="
input int              InpSwingLeft        = 2;           // fractal bars left
input int              InpSwingRight       = 2;           // fractal bars right
input bool             InpBreakOnClose     = true;        // confirm break by close
input int              InpHtfBars          = 200;         // HTF bars analysed
input int              InpMtfBars          = 300;         // MTF bars analysed
input int              InpLtfBars          = 400;         // LTF bars analysed

input group "=== Entry model ==="
input bool             InpRequireSweep     = true;        // require liquidity sweep
input bool             InpRequireChoch     = true;        // require CHOCH after sweep
input int              InpSweepLookback    = 60;          // LTF bars to look back
input bool             InpUseOrderBlocks   = true;        // zone type: order block
input bool             InpUseFVG           = true;        // zone type: fair value gap
input bool             InpMarketIfAtZone   = true;        // enter at market inside zone
input double           InpSlBufferATR      = 0.25;        // SL buffer (LTF ATR)
input double           InpMaxEntryDistATR  = 3.0;         // skip zones further than this
input int              InpLimitExpiryBars  = 120;         // pending expiry in LTF bars

input group "=== XAUUSD stop guards (price = gold dollars) ==="
input double           InpMinSLPrice       = 2.00;        // reject stops tighter than this (20 pips)
input double           InpMaxSLPrice       = 2.50;        // reject stops wider than this (25 pips)
input double           InpMaxSpreadRatio   = 0.18;        // max spread / stop distance
input ENUM_STOP_MODE   InpStopMode         = STOP_FIXED;  // stop sizing rule
input double           InpFixedSLPrice     = 2.00;        // STOP_FIXED: stop in price ($2.00 = 20 pips)

input group "=== Risk management ==="
input double           InpRiskPercent      = 2.0;         // risk per trade (% balance)
input double           InpMinRR            = 3.0;         // minimum reward:risk
input double           InpTargetRR         = 3.0;         // fixed target when no liquidity
input double           InpBreakevenAtR     = 1.5;         // move SL to entry at this R
input double           InpPartialAtR       = 2.0;         // partial close at this R (must be < target)
input double           InpPartialFraction  = 0.5;         // fraction closed
input int              InpMaxTradesPerDay  = 6;           // hard cap
input int              InpMaxConsecLosses  = 2;           // stop the day after N losses
input double           InpMaxDailyLossPct  = 6.0;         // stop the day at this drawdown
input int              InpBeBufferPoints   = 10;          // breakeven buffer (points)

input group "=== Filters ==="
input bool             InpUseSessions      = true;        // trade only listed sessions
input double           InpSession1Start    = 7.0;         // London start (GMT hours)
input double           InpSession1End      = 12.0;        // London end
input double           InpSession2Start    = 12.0;        // New York start
input double           InpSession2End      = 17.0;        // New York end
input bool             InpBlockFridayClose = true;        // no new entries late friday
input double           InpFridayCutoff     = 19.0;        // GMT hour
input string           InpNewsTimes        = "";          // "2024.02.02 13:30;..." (GMT)
input int              InpNewsBeforeMin    = 15;          // blackout before news
input int              InpNewsAfterMin     = 30;          // blackout after news
input int              InpMaxSpreadPoints  = 60;          // skip when spread is wider

input group "=== Circuit breaker (stop, review, then resume) ==="
input double           InpHardStopPct      = 10.0;        // daily loss that locks trading
input bool             InpHaltRequiresReview = true;      // lock stays until you release it
input bool             InpUnlock           = false;       // set true once to release, then false
input bool             InpWriteJournal     = true;        // append trades to a JSONL file

input group "=== Execution ==="
input long             InpMagic            = 700911;      // magic number
input int              InpDeviation        = 20;          // slippage (points)
input string           InpComment          = "crowcode";  // order comment
input bool             InpDryRun           = true;        // live only: log, send nothing
                                                          // (ignored in the Strategy Tester -
                                                          //  a dry run there means zero trades)
input double           InpServerGmtOffset  = 0;           // server time - GMT, in hours
                                                          // Exness is +2 (winter) / +3 (summer).
                                                          // The tester makes TimeGMT() == server
                                                          // time, so sessions shift without this.
input bool             InpVerbose          = true;        // print rejection reasons

//====================================================================
// Globals
//====================================================================
CTrade         trade;
datetime       g_lastBarTime  = 0;
string         g_sym;
double         g_point;
int            g_digits;
datetime       g_newsTimes[];
string         g_lastReject   = "";
bool           g_dryRun       = false;   // always false in the Strategy Tester
bool           g_isTester     = false;

//--- why we did not trade (printed in OnDeinit)
string         g_rejectNames[];
int            g_rejectCounts[];
int            g_signalCount  = 0;

#define LOCK_DAY_VAR  "CC_LOCK_DAY"
#define LOCK_PCT_VAR  "CC_LOCK_PCT"
#define JOURNAL_FILE  "crowcode_journal.jsonl"

#define DIR_NONE   0
#define DIR_BULL   1
#define DIR_BEAR  -1

//--- swing point
struct Swing
  {
   int      idx;          // bar index in the analysed array
   double   price;
   bool     isHigh;
   int      confirmedAt;  // usable only from this index onward
  };

//--- structure break event
struct BreakEvent
  {
   int      idx;
   int      dir;          // DIR_BULL / DIR_BEAR
   bool     isChoch;      // false => BOS
   double   level;
  };

//--- point of interest (order block / fair value gap)
struct Zone
  {
   int      idx;
   int      dir;          // DIR_BULL => buy zone, DIR_BEAR => sell zone
   double   top;
   double   bottom;
   string   kind;
  };

//--- liquidity sweep
struct Sweep
  {
   int      idx;
   int      dir;          // DIR_BULL => swept lows (buy), DIR_BEAR => swept highs
   double   level;
   double   extreme;
  };

//+------------------------------------------------------------------+
int OnInit()
  {
   g_sym    = _Symbol;
   g_point  = SymbolInfoDouble(g_sym, SYMBOL_POINT);
   g_digits = (int)SymbolInfoInteger(g_sym, SYMBOL_DIGITS);

   trade.SetExpertMagicNumber(InpMagic);
   trade.SetDeviationInPoints(InpDeviation);
   trade.SetTypeFillingBySymbol(g_sym);
   trade.LogLevel(LOG_LEVEL_ERRORS);

   g_isTester = (bool)MQLInfoInteger(MQL_TESTER);
   //--- A dry run inside the tester means zero trades, which is useless.
   g_dryRun = InpDryRun && !g_isTester;

   ParseNewsTimes(InpNewsTimes);

   if(InpSwingLeft < 1 || InpSwingRight < 1)
     {
      Print("CrowConcept: swing left/right must be >= 1");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpRiskPercent <= 0.0 || InpRiskPercent > 20.0)
     {
      Print("CrowConcept: risk percent out of sane range");
      return(INIT_PARAMETERS_INCORRECT);
     }

   if(InpStopMode != STOP_FIXED &&
      InpMinSLPrice > 0.0 && InpMaxSLPrice > 0.0 && InpMinSLPrice >= InpMaxSLPrice)
     {
      Print("CrowConcept: InpMinSLPrice must be smaller than InpMaxSLPrice");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpStopMode == STOP_FIXED && InpFixedSLPrice <= 0.0)
     {
      Print("CrowConcept: STOP_FIXED needs InpFixedSLPrice > 0");
      return(INIT_PARAMETERS_INCORRECT);
     }
   if(InpPartialAtR >= InpTargetRR && InpPartialFraction > 0.0)
      PrintFormat("CrowConcept WARNING: partial at %.1fR >= target %.1fR - "
                  "the partial can only fire at the target, so it never really happens.",
                  InpPartialAtR, InpTargetRR);
   if(InpBreakevenAtR >= InpPartialAtR)
      PrintFormat("CrowConcept WARNING: breakeven %.1fR >= partial %.1fR - check the order.",
                  InpBreakevenAtR, InpPartialAtR);
   if(InpHardStopPct > 0.0 && InpHardStopPct <= InpMaxDailyLossPct)
      PrintFormat("CrowConcept WARNING: circuit %.1f%% <= daily cap %.1f%% - "
                  "the daily cap always fires first, so the circuit is dead.",
                  InpHardStopPct, InpMaxDailyLossPct);
   if(InpMaxDailyLossPct < InpRiskPercent * InpMaxConsecLosses)
      PrintFormat("CrowConcept WARNING: daily cap %.1f%% < risk x consecutive losses %.1f%% - "
                  "the consecutive-loss rule will never be reached.",
                  InpMaxDailyLossPct, InpRiskPercent * InpMaxConsecLosses);
   if(InpMaxDailyLossPct < InpRiskPercent)
      PrintFormat("CrowConcept WARNING: daily loss cap %.1f%% is below per-trade risk %.1f%% "
                  "- the first loss would end the day",
                  InpMaxDailyLossPct, InpRiskPercent);

   GoldSanityCheck();

   if(InpUnlock)
     {
      if(ReleaseLock())
         Print("CrowConcept: lock released. Set InpUnlock back to false.");
      else
         Print("CrowConcept: no active lock to release.");
     }
   else if(IsLocked())
     {
      PrintFormat("CrowConcept: LOCKED since %.0f (daily loss %.2f%%). "
                  "Review the trades, fix what needs fixing, then re-attach with "
                  "InpUnlock=true. New entries are blocked; open positions are still managed.",
                  GlobalVariableGet(LOCK_DAY_VAR), GlobalVariableGet(LOCK_PCT_VAR));
     }

   if(g_isTester)
      PrintFormat("CrowConcept: Strategy Tester - dry run ignored, orders WILL be simulated. "
                  "stop mode=%s", EnumToString(InpStopMode));
   else if(g_dryRun)
      Print("CrowConcept: DRY RUN - signals are logged, no orders are sent. "
            "Set InpDryRun=false only after demo testing.");
   else
      PrintFormat("CrowConcept: *** LIVE ORDERS ENABLED *** %s risk=%.2f%% magic=%I64d",
                  g_sym, InpRiskPercent, InpMagic);

   PrintFormat("CrowConcept loss ladder | trade -%.1f%% | %d in a row -%.1f%% | "
               "day -%.1f%% | circuit -%.1f%%",
               InpRiskPercent, InpMaxConsecLosses,
               InpRiskPercent * InpMaxConsecLosses, InpMaxDailyLossPct, InpHardStopPct);
   PrintFormat("CrowConcept ready | %s | HTF=%s MTF=%s LTF=%s | risk=%.2f%% | dry_run=%s",
               g_sym, EnumToString(InpHTF), EnumToString(InpMTF), EnumToString(InpLTF),
               InpRiskPercent, (g_dryRun ? "true" : "false"));
   return(INIT_SUCCEEDED);
  }

//+------------------------------------------------------------------+
//| Why did it not trade? Sorted, so the first line is the real       |
//| reason. Zero trades with everything under "session" means the GMT |
//| offset is wrong; under "sizing" means the account is too small.   |
//+------------------------------------------------------------------+
void PrintRejectSummary()
  {
   int n = ArraySize(g_rejectNames);
   PrintFormat("=== CrowConcept: %d signals taken ===", g_signalCount);
   if(n == 0)
     {
      Print("  Nothing was even evaluated. Check: chart symbol is gold, "
            "history is downloaded for HTF/MTF/LTF, algo trading is enabled.");
      return;
     }
   for(int a = 0; a < n - 1; a++)
      for(int b = a + 1; b < n; b++)
         if(g_rejectCounts[b] > g_rejectCounts[a])
           {
            int    ci = g_rejectCounts[a]; g_rejectCounts[a] = g_rejectCounts[b]; g_rejectCounts[b] = ci;
            string sn = g_rejectNames[a];  g_rejectNames[a]  = g_rejectNames[b];  g_rejectNames[b]  = sn;
           }
   Print("  filter counts (most frequent first):");
   for(int i = 0; i < n; i++)
      PrintFormat("    %-16s %d", g_rejectNames[i], g_rejectCounts[i]);
  }

void OnDeinit(const int reason)
  {
   PrintRejectSummary();
  }

//+------------------------------------------------------------------+
//| XAUUSD specific startup checks.                                  |
//| Most "the EA never trades" reports come down to one of these.    |
//+------------------------------------------------------------------+
void GoldSanityCheck()
  {
   double perUnit = MoneyPerPriceUnit(1.0);
   if(MathAbs(perUnit - 100.0) > 1.0)
      PrintFormat("CrowConcept WARNING: 1 lot moves %.2f per $1 (gold standard is 100). "
                  "Wrong symbol, or a micro/cent account?", perUnit);

   //--- can the account even afford the smallest setup this preset allows?
   double vmin = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
   double bal  = AccountInfoDouble(ACCOUNT_BALANCE);
   if(InpMinSLPrice > 0.0 && InpRiskPercent > 0.0 && perUnit > 0.0)
     {
      double needMin = vmin * InpMinSLPrice * perUnit * 100.0 / InpRiskPercent;
      double needMax = vmin * InpMaxSLPrice * perUnit * 100.0 / InpRiskPercent;
      PrintFormat("CrowConcept: balance needed %.0f (stop $%.2f) .. %.0f (stop $%.2f); you have %.0f",
                  needMin, InpMinSLPrice, needMax, InpMaxSLPrice, bal);
      if(bal < needMin)
         PrintFormat("CrowConcept WARNING: balance below %.0f - every signal will be "
                     "rejected for size. Raise risk %% or use a higher timeframe preset.", needMin);
     }

   //--- broker stop distance vs the tightest stop we would ever place
   double stopDist = MinStopDistance();
   if(InpMinSLPrice > 0.0 && stopDist >= InpMinSLPrice)
      PrintFormat("CrowConcept WARNING: broker minimum stop distance $%.2f >= InpMinSLPrice $%.2f "
                  "- orders will be rejected. Widen InpMinSLPrice or change preset.",
                  stopDist, InpMinSLPrice);

   //--- typical spread vs the tightest stop
   long spread = SymbolInfoInteger(g_sym, SYMBOL_SPREAD);
   if(InpMaxSpreadRatio > 0.0 && InpMinSLPrice > 0.0)
     {
      double allowed = InpMinSLPrice * InpMaxSpreadRatio;
      if(spread * g_point > allowed)
         PrintFormat("CrowConcept NOTE: spread $%.2f exceeds $%.2f allowed for the tightest "
                     "setup - narrow-stop signals will be filtered out.",
                     spread * g_point, allowed);
     }
  }

//+------------------------------------------------------------------+
//| Main loop: manage first, then look for a new setup.              |
//+------------------------------------------------------------------+
void OnTick()
  {
   ManageOpenPositions();

   datetime barTime = iTime(g_sym, InpLTF, 0);
   if(barTime == g_lastBarTime)
      return;                       // evaluate once per closed LTF bar
   g_lastBarTime = barTime;

   ReviewPendingOrders();

   if(CountMyPositions() > 0 || CountMyOrders() > 0)
      return;                       // one setup at a time

   TryNewSetup();
  }

//====================================================================
// Bar helpers
//====================================================================

//--- Load closed bars (index 0 = oldest). The forming bar is excluded.
bool LoadRates(ENUM_TIMEFRAMES tf, int count, MqlRates &out[])
  {
   ArrayFree(out);
   ArraySetAsSeries(out, false);
   int got = CopyRates(g_sym, tf, 1, count, out);   // start=1 skips the live bar
   if(got <= 10)
      return(false);
   ArraySetAsSeries(out, false);                    // index 0 = oldest
   return(true);
  }

double AverageTrueRange(const MqlRates &r[], int period)
  {
   int n = ArraySize(r);
   if(n < 2)
      return(0.0);
   if(period > n - 1)
      period = n - 1;
   double sum = 0.0;
   for(int i = n - period; i < n; i++)
     {
      double tr = MathMax(r[i].high - r[i].low,
                          MathMax(MathAbs(r[i].high - r[i-1].close),
                                  MathAbs(r[i].low  - r[i-1].close)));
      sum += tr;
     }
   return(period > 0 ? sum / period : 0.0);
  }

//====================================================================
// Structure: swings, BOS, CHOCH
//====================================================================
int FindSwings(const MqlRates &r[], Swing &out[])
  {
   int n = ArraySize(r);
   ArrayFree(out);
   int count = 0;
   for(int i = InpSwingLeft; i < n - InpSwingRight; i++)
     {
      bool isHigh = true, isLow = true;
      bool strictHigh = false, strictLow = false;
      for(int k = i - InpSwingLeft; k <= i + InpSwingRight; k++)
        {
         if(k == i)
            continue;
         if(r[i].high < r[k].high) isHigh = false;
         if(r[i].high > r[k].high) strictHigh = true;
         if(r[i].low  > r[k].low)  isLow  = false;
         if(r[i].low  < r[k].low)  strictLow = true;
        }
      if(isHigh && strictHigh)
        {
         ArrayResize(out, count + 1);
         out[count].idx = i; out[count].price = r[i].high;
         out[count].isHigh = true; out[count].confirmedAt = i + InpSwingRight;
         count++;
        }
      if(isLow && strictLow)
        {
         ArrayResize(out, count + 1);
         out[count].idx = i; out[count].price = r[i].low;
         out[count].isHigh = false; out[count].confirmedAt = i + InpSwingRight;
         count++;
        }
     }
   return(count);
  }

//--- Walk bar by bar, collecting BOS/CHOCH. Returns final bias.
int AnalyseStructure(const MqlRates &r[], BreakEvent &events[])
  {
   Swing sw[];
   int ns = FindSwings(r, sw);
   ArrayFree(events);
   int ne = 0;

   int bias = DIR_NONE;
   int lastHigh = -1, lastLow = -1;          // indices into sw[]
   int n = ArraySize(r);

   for(int i = 0; i < n; i++)
     {
      for(int s = 0; s < ns; s++)
        {
         if(sw[s].confirmedAt != i)
            continue;
         if(sw[s].isHigh) lastHigh = s;
         else             lastLow  = s;
        }

      double refUp = InpBreakOnClose ? r[i].close : r[i].high;
      double refDn = InpBreakOnClose ? r[i].close : r[i].low;

      if(lastHigh >= 0 && i > sw[lastHigh].idx && refUp > sw[lastHigh].price)
        {
         ArrayResize(events, ne + 1);
         events[ne].idx = i; events[ne].dir = DIR_BULL;
         events[ne].isChoch = (bias == DIR_BEAR);
         events[ne].level = sw[lastHigh].price;
         ne++;
         bias = DIR_BULL;
         lastHigh = -1;
        }
      else if(lastLow >= 0 && i > sw[lastLow].idx && refDn < sw[lastLow].price)
        {
         ArrayResize(events, ne + 1);
         events[ne].idx = i; events[ne].dir = DIR_BEAR;
         events[ne].isChoch = (bias == DIR_BULL);
         events[ne].level = sw[lastLow].price;
         ne++;
         bias = DIR_BEAR;
         lastLow = -1;
        }
     }
   return(bias);
  }

//====================================================================
// Wyckoff: quantile range, spring / upthrust, coarse phase
//====================================================================
double Quantile(const double &src[], double q)
  {
   int n = ArraySize(src);
   if(n == 0)
      return(0.0);
   double tmp[];
   ArrayResize(tmp, n);
   ArrayCopy(tmp, src);
   ArraySort(tmp);
   if(n == 1)
      return(tmp[0]);
   double pos = q * (n - 1);
   int lo = (int)MathFloor(pos);
   int hi = MathMin(lo + 1, n - 1);
   return(tmp[lo] + (tmp[hi] - tmp[lo]) * (pos - lo));
  }

//--- Returns DIR_BULL (accumulation, buy only), DIR_BEAR, or DIR_NONE.
int WyckoffBias(const MqlRates &r[])
  {
   int n = ArraySize(r);
   if(n < 40)
      return(DIR_NONE);

   int look = MathMin(120, n);
   double highs[], lows[];
   ArrayResize(highs, look);
   ArrayResize(lows, look);
   for(int i = 0; i < look; i++)
     {
      highs[i] = r[n - look + i].high;
      lows[i]  = r[n - look + i].low;
     }

   double top    = Quantile(highs, 0.95);
   double bottom = Quantile(lows, 0.05);
   double atr    = AverageTrueRange(r, 14);
   if(atr <= 0.0 || top <= bottom || (top - bottom) > 12.0 * atr)
      return(DIR_NONE);             // trending, not ranging

   double tol = 0.5 * atr;
   int topTouch = 0, botTouch = 0;
   for(int i = n - look; i < n; i++)
     {
      if(r[i].high >= top - tol)    topTouch++;
      if(r[i].low  <= bottom + tol) botTouch++;
     }
   if(topTouch < 2 || botTouch < 2)
      return(DIR_NONE);

   //--- shakeout detection inside the range
   bool spring = false, upthrust = false;
   for(int i = n - look; i < n; i++)
     {
      if(r[i].low < bottom && r[i].close > bottom && (bottom - r[i].low) <= 1.2 * atr)
         spring = true;
      if(r[i].high > top && r[i].close < top && (r[i].high - top) <= 1.2 * atr)
         upthrust = true;
     }

   //--- schematic from the move that preceded the range
   int preFrom = MathMax(0, n - look - look);
   int preTo   = n - look;
   int schematic = DIR_NONE;
   if(preTo - preFrom >= 10)
     {
      double drift = r[preTo - 1].close - r[preFrom].close;
      if(drift < 0) schematic = DIR_BULL;     // fell into the range => accumulation
      if(drift > 0) schematic = DIR_BEAR;     // rose into the range => distribution
     }
   if(schematic == DIR_NONE)
     {
      if(spring && !upthrust)  schematic = DIR_BULL;
      if(upthrust && !spring)  schematic = DIR_BEAR;
     }
   if(schematic == DIR_NONE)
      return(DIR_NONE);

   //--- only phases C/D/E are tradable directions
   double last = r[n-1].close;
   bool broke  = (schematic == DIR_BULL) ? (last > top + 0.5 * atr)
                                         : (last < bottom - 0.5 * atr);
   bool shaken = (schematic == DIR_BULL) ? spring : upthrust;
   if(broke || shaken)
      return(schematic);
   return(DIR_NONE);                 // phase A/B: no directional edge yet
  }

//====================================================================
// Liquidity: sweeps, order blocks, fair value gaps
//====================================================================
bool FindLastSweep(const MqlRates &r[], int wantDir, Sweep &out)
  {
   Swing sw[];
   int ns = FindSwings(r, sw);
   int n  = ArraySize(r);
   int from = MathMax(1, n - InpSweepLookback);
   bool found = false;

   for(int i = from; i < n; i++)
     {
      double rng = r[i].high - r[i].low;
      if(rng <= 0.0)
         continue;
      double lo = MathMax(0, i - InpSweepLookback);

      double priorHigh = -DBL_MAX, priorLow = DBL_MAX;
      for(int s = 0; s < ns; s++)
        {
         if(sw[s].idx >= i || sw[s].idx < lo || sw[s].confirmedAt > i - 1)
            continue;
         if(sw[s].isHigh) priorHigh = MathMax(priorHigh, sw[s].price);
         else             priorLow  = MathMin(priorLow,  sw[s].price);
        }

      if(wantDir == DIR_BEAR && priorHigh > -DBL_MAX)
         if(r[i].high > priorHigh && r[i].close < priorHigh)
           {
            double wick = r[i].high - MathMax(r[i].open, r[i].close);
            if(wick / rng >= 0.25)
              { out.idx = i; out.dir = DIR_BEAR; out.level = priorHigh; out.extreme = r[i].high; found = true; }
           }

      if(wantDir == DIR_BULL && priorLow < DBL_MAX)
         if(r[i].low < priorLow && r[i].close > priorLow)
           {
            double wick = MathMin(r[i].open, r[i].close) - r[i].low;
            if(wick / rng >= 0.25)
              { out.idx = i; out.dir = DIR_BULL; out.level = priorLow; out.extreme = r[i].low; found = true; }
           }
     }
   return(found);
  }

//--- Zone is dead once a later candle CLOSES through its far edge.
bool ZoneInvalidated(const MqlRates &r[], const Zone &z, int upto)
  {
   for(int k = z.idx + 1; k <= upto && k < ArraySize(r); k++)
     {
      if(z.dir == DIR_BULL && r[k].close < z.bottom) return(true);
      if(z.dir == DIR_BEAR && r[k].close > z.top)    return(true);
     }
   return(false);
  }

int CollectZones(const MqlRates &r[], const BreakEvent &events[], int dir, Zone &out[])
  {
   int n = ArraySize(r);
   ArrayFree(out);
   int cnt = 0;

   if(InpUseOrderBlocks)
     {
      int ne = ArraySize(events);
      for(int e = MathMax(0, ne - 25); e < ne; e++)
        {
         if(events[e].dir != dir)
            continue;
         for(int j = events[e].idx - 1; j >= MathMax(0, events[e].idx - 30); j--)
           {
            bool bull = (r[j].close >= r[j].open);
            if((dir == DIR_BULL && !bull) || (dir == DIR_BEAR && bull))
              {
               ArrayResize(out, cnt + 1);
               out[cnt].idx = j; out[cnt].dir = dir;
               out[cnt].top = r[j].high; out[cnt].bottom = r[j].low;
               out[cnt].kind = "order_block";
               cnt++;
               break;
              }
           }
        }
     }

   if(InpUseFVG)
     {
      double atr = AverageTrueRange(r, 14);
      double minGap = atr * 0.1;
      for(int i = MathMax(2, n - 200); i < n; i++)
        {
         if(dir == DIR_BULL && (r[i].low - r[i-2].high) > minGap)
           {
            ArrayResize(out, cnt + 1);
            out[cnt].idx = i; out[cnt].dir = DIR_BULL;
            out[cnt].top = r[i].low; out[cnt].bottom = r[i-2].high;
            out[cnt].kind = "fvg";
            cnt++;
           }
         if(dir == DIR_BEAR && (r[i-2].low - r[i].high) > minGap)
           {
            ArrayResize(out, cnt + 1);
            out[cnt].idx = i; out[cnt].dir = DIR_BEAR;
            out[cnt].top = r[i-2].low; out[cnt].bottom = r[i].high;
            out[cnt].kind = "fvg";
            cnt++;
           }
        }
     }
   return(cnt);
  }

//--- Nearest still-valid zone on the correct side of price.
bool PickZone(const MqlRates &r[], const Zone &zones[], int dir, double price,
              double tol, Zone &out)
  {
   int n = ArraySize(r);
   bool found = false;
   double best = 0.0;

   for(int i = 0; i < ArraySize(zones); i++)
     {
      if(ZoneInvalidated(r, zones[i], n - 1))
         continue;
      if(dir == DIR_BULL)
        {
         if(zones[i].bottom > price + tol)
            continue;                        // zone is above price: not a pullback
         if(!found || zones[i].top > best)
           { best = zones[i].top; out = zones[i]; found = true; }
        }
      else
        {
         if(zones[i].top < price - tol)
            continue;
         if(!found || zones[i].bottom < best)
           { best = zones[i].bottom; out = zones[i]; found = true; }
        }
     }
   return(found);
  }

//====================================================================
// Filters
//====================================================================
void ParseNewsTimes(string csv)
  {
   ArrayFree(g_newsTimes);
   if(StringLen(csv) == 0)
      return;
   string parts[];
   int n = StringSplit(csv, ';', parts);
   int cnt = 0;
   for(int i = 0; i < n; i++)
     {
      string s = parts[i];
      StringTrimLeft(s); StringTrimRight(s);
      if(StringLen(s) == 0)
         continue;
      datetime t = StringToTime(s);
      if(t > 0)
        {
         ArrayResize(g_newsTimes, cnt + 1);
         g_newsTimes[cnt] = t;
         cnt++;
        }
     }
   if(cnt > 0)
      PrintFormat("CrowConcept: %d news blackout window(s) loaded", cnt);
  }

//+------------------------------------------------------------------+
//| GMT for the session/news filters.                                |
//| Live: TimeGMT() is correct. Tester: TimeGMT() equals the modeled |
//| server time, so the London/NY windows land 2-3 hours off unless  |
//| InpServerGmtOffset is set.                                       |
//+------------------------------------------------------------------+
datetime NowGmt()
  {
   if(InpServerGmtOffset != 0.0)
      return(TimeCurrent() - (int)(InpServerGmtOffset * 3600));
   return(g_isTester ? TimeCurrent() : TimeGMT());
  }

bool InSession(datetime gmt)
  {
   if(!InpUseSessions)
      return(true);
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   double h = dt.hour + dt.min / 60.0;
   if(h >= InpSession1Start && h < InpSession1End) return(true);
   if(h >= InpSession2Start && h < InpSession2End) return(true);
   return(false);
  }

bool InNewsBlackout(datetime gmt)
  {
   for(int i = 0; i < ArraySize(g_newsTimes); i++)
     {
      datetime a = g_newsTimes[i] - InpNewsBeforeMin * 60;
      datetime b = g_newsTimes[i] + InpNewsAfterMin * 60;
      if(gmt >= a && gmt <= b)
         return(true);
     }
   return(false);
  }

bool FridayBlocked(datetime gmt)
  {
   if(!InpBlockFridayClose)
      return(false);
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   return(dt.day_of_week == 5 && (dt.hour + dt.min / 60.0) >= InpFridayCutoff);
  }

//====================================================================
// Account / position bookkeeping
//====================================================================
int CountMyPositions()
  {
   int c = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !PositionSelectByTicket(t))
         continue;
      if(PositionGetString(POSITION_SYMBOL) == g_sym &&
         PositionGetInteger(POSITION_MAGIC) == InpMagic)
         c++;
     }
   return(c);
  }

int CountMyOrders()
  {
   int c = 0;
   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong t = OrderGetTicket(i);
      if(t == 0 || !OrderSelect(t))
         continue;
      if(OrderGetString(ORDER_SYMBOL) == g_sym &&
         OrderGetInteger(ORDER_MAGIC) == InpMagic)
         c++;
     }
   return(c);
  }

//--- Aggregate today's closed positions: trade count, consecutive losses, pnl.
void TodayStats(int &trades, int &consecLosses, double &pnl)
  {
   trades = 0; consecLosses = 0; pnl = 0.0;

   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   dt.hour = 0; dt.min = 0; dt.sec = 0;
   datetime dayStart = StructToTime(dt);

   if(!HistorySelect(dayStart, TimeCurrent() + 3600))
      return;

   ulong  ids[];
   double sums[];
   datetime last[];
   int m = 0;

   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      if(HistoryDealGetString(d, DEAL_SYMBOL) != g_sym)              continue;
      if(HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic)           continue;
      if(HistoryDealGetInteger(d, DEAL_ENTRY) != DEAL_ENTRY_OUT)     continue;

      ulong pid = (ulong)HistoryDealGetInteger(d, DEAL_POSITION_ID);
      double p  = HistoryDealGetDouble(d, DEAL_PROFIT)
                + HistoryDealGetDouble(d, DEAL_COMMISSION)
                + HistoryDealGetDouble(d, DEAL_SWAP);
      datetime tclose = (datetime)HistoryDealGetInteger(d, DEAL_TIME);

      int at = -1;
      for(int k = 0; k < m; k++)
         if(ids[k] == pid) { at = k; break; }
      if(at < 0)
        {
         ArrayResize(ids, m + 1); ArrayResize(sums, m + 1); ArrayResize(last, m + 1);
         ids[m] = pid; sums[m] = 0.0; last[m] = 0;
         at = m; m++;
        }
      sums[at] += p;
      if(tclose > last[at]) last[at] = tclose;
     }

   //--- order positions by close time so "consecutive" means something
   for(int a = 0; a < m - 1; a++)
      for(int b = a + 1; b < m; b++)
         if(last[b] < last[a])
           {
            datetime tt = last[a]; last[a] = last[b]; last[b] = tt;
            double   ss = sums[a]; sums[a] = sums[b]; sums[b] = ss;
            ulong    ii = ids[a];  ids[a]  = ids[b];  ids[b]  = ii;
           }

   for(int k = 0; k < m; k++)
     {
      trades++;
      pnl += sums[k];
      if(sums[k] < 0.0) consecLosses++;
      else              consecLosses = 0;
     }
  }

bool RiskGateOpen(string &why)
  {
   int trades, losses;
   double pnl;
   TodayStats(trades, losses, pnl);

   if(trades >= InpMaxTradesPerDay)
     { why = StringFormat("daily trade cap reached (%d)", trades); return(false); }
   if(losses >= InpMaxConsecLosses)
     { why = StringFormat("%d consecutive losses - done for the day", losses); return(false); }

   double bal = AccountInfoDouble(ACCOUNT_BALANCE);
   if(bal > 0 && -pnl >= bal * InpMaxDailyLossPct / 100.0)
     { why = StringFormat("daily loss cap hit (%.2f)", pnl); return(false); }
   return(true);
  }

//====================================================================
// Sizing
//====================================================================
double MoneyPerPriceUnit(double lots)
  {
   double tickValue = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_VALUE);
   double tickSize  = SymbolInfoDouble(g_sym, SYMBOL_TRADE_TICK_SIZE);
   if(tickSize > 0.0 && tickValue > 0.0)
      return(lots * tickValue / tickSize);
   return(lots * SymbolInfoDouble(g_sym, SYMBOL_TRADE_CONTRACT_SIZE));
  }

double NormalizeVolume(double v)
  {
   double vmin  = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
   double vmax  = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MAX);
   double vstep = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_STEP);
   if(vstep <= 0.0)
      return(MathMax(vmin, MathMin(v, vmax)));
   double stepped = MathFloor(v / vstep + 1e-9) * vstep;
   stepped = NormalizeDouble(stepped, 2);
   if(stepped < vmin)
      return(0.0);
   return(MathMin(stepped, vmax));
  }

double CalcLots(double entry, double sl)
  {
   double dist = MathAbs(entry - sl);
   if(dist <= 0.0)
      return(0.0);
   double balance    = AccountInfoDouble(ACCOUNT_BALANCE);
   double riskMoney  = balance * InpRiskPercent / 100.0;
   double perUnit    = MoneyPerPriceUnit(1.0);
   if(perUnit <= 0.0)
      return(0.0);
   double lots = riskMoney / (dist * perUnit);

   //--- leverage cap: never let one position exceed what the account supports
   long lev = AccountInfoInteger(ACCOUNT_LEVERAGE);
   if(lev > 0)
     {
      double contract = SymbolInfoDouble(g_sym, SYMBOL_TRADE_CONTRACT_SIZE);
      if(contract > 0 && entry > 0)
         lots = MathMin(lots, (balance * lev) / (contract * entry));
     }
   return(NormalizeVolume(lots));
  }

double MinStopDistance()
  {
   long stops = SymbolInfoInteger(g_sym, SYMBOL_TRADE_STOPS_LEVEL);
   return((double)stops * g_point);
  }

//====================================================================
// Setup search
//====================================================================
void CountReject(string rule)
  {
   for(int i = 0; i < ArraySize(g_rejectNames); i++)
      if(g_rejectNames[i] == rule)
        { g_rejectCounts[i]++; return; }
   int n = ArraySize(g_rejectNames);
   ArrayResize(g_rejectNames, n + 1);
   ArrayResize(g_rejectCounts, n + 1);
   g_rejectNames[n]  = rule;
   g_rejectCounts[n] = 1;
  }

void Reject(string rule, string detail)
  {
   CountReject(rule);
   if(!InpVerbose || g_isTester)
      return;                       // the tester gets one summary in OnDeinit instead
   if(rule == g_lastReject)
      return;                       // live: log only when the reason changes
   g_lastReject = rule;
   PrintFormat("CrowConcept skip [%s] %s", rule, detail);
  }

void TryNewSetup()
  {
   datetime gmt = NowGmt();

   if(!InSession(gmt))            { Reject("session", "outside London/NewYork"); return; }
   if(FridayBlocked(gmt))         { Reject("friday", "late friday - no new entries"); return; }
   if(InNewsBlackout(gmt))        { Reject("news", "high impact news window"); return; }

   if(IsLocked())
     {
      Reject("locked", "circuit breaker - review, then re-attach with InpUnlock=true");
      return;
     }

   int trades_, losses_;
   double pnlToday;
   TodayStats(trades_, losses_, pnlToday);
   if(CircuitBreakerHit(pnlToday))
     { Reject("locked", "circuit breaker tripped"); return; }

   string why;
   if(!RiskGateOpen(why))         { Reject("risk_gate", why); return; }

   long spread = SymbolInfoInteger(g_sym, SYMBOL_SPREAD);
   if(spread > InpMaxSpreadPoints)
     { Reject("spread", StringFormat("spread %d pts too wide", (int)spread)); return; }

   MqlRates htf[], mtf[], ltf[];
   if(!LoadRates(InpHTF, InpHtfBars, htf) ||
      !LoadRates(InpMTF, InpMtfBars, mtf) ||
      !LoadRates(InpLTF, InpLtfBars, ltf))
     { Reject("data", "not enough history"); return; }

   //--- 1) HTF direction
   BreakEvent htfEvents[];
   int structBias  = AnalyseStructure(htf, htfEvents);
   int wyckoffBias = WyckoffBias(htf);

   int dir = structBias;
   if(wyckoffBias != DIR_NONE)
     {
      if(structBias != DIR_NONE && wyckoffBias != structBias)
        { Reject("htf_bias", "structure and Wyckoff disagree"); return; }
      dir = wyckoffBias;
     }
   if(dir == DIR_NONE)            { Reject("htf_bias", "no HTF direction"); return; }

   //--- 2) MTF confirmation and zone
   BreakEvent mtfEvents[];
   int mtfBias = AnalyseStructure(mtf, mtfEvents);
   if(mtfBias != DIR_NONE && mtfBias != dir)
     { Reject("mtf_conflict", "MTF still correcting against HTF"); return; }

   Zone zones[];
   int nz = CollectZones(mtf, mtfEvents, dir, zones);
   if(nz == 0)                    { Reject("poi", "no valid order block / FVG"); return; }

   //--- 3) LTF trigger: sweep first, then CHOCH
   Sweep sweep;
   sweep.idx = -1;
   if(InpRequireSweep)
     {
      if(!FindLastSweep(ltf, dir, sweep))
        { Reject("sweep", "opposing liquidity not swept"); return; }
     }

   if(InpRequireChoch)
     {
      BreakEvent ltfEvents[];
      AnalyseStructure(ltf, ltfEvents);
      int ne = ArraySize(ltfEvents);
      int chochIdx = -1;
      for(int i = ne - 1; i >= 0; i--)
         if(ltfEvents[i].isChoch)
           {
            if(ltfEvents[i].dir != dir)
              { Reject("choch", "last CHOCH points the other way"); return; }
            chochIdx = ltfEvents[i].idx;
            break;
           }
      if(chochIdx < 0)            { Reject("choch", "no CHOCH on LTF"); return; }
      if(sweep.idx >= 0 && chochIdx < sweep.idx)
        { Reject("choch", "CHOCH happened before the sweep"); return; }
      if((ArraySize(ltf) - 1 - chochIdx) > InpSweepLookback / 2)
        { Reject("choch", "CHOCH is stale"); return; }
     }

   //--- 4) build the order
   double ltfAtr = AverageTrueRange(ltf, 14);
   if(ltfAtr <= 0.0)              { Reject("data", "ATR unavailable"); return; }

   double ask = SymbolInfoDouble(g_sym, SYMBOL_ASK);
   double bid = SymbolInfoDouble(g_sym, SYMBOL_BID);
   double ref = (dir == DIR_BULL) ? ask : bid;
   double tol = ltfAtr * 0.25;

   Zone z;
   if(!PickZone(mtf, zones, dir, ref, tol, z))
     { Reject("entry", "no reachable pullback zone"); return; }

   bool atZone = (ref >= z.bottom - tol && ref <= z.top + tol);
   bool market = atZone && InpMarketIfAtZone;

   //--- limit goes on the proximal edge of the zone
   double entry = market ? ref : ((dir == DIR_BULL) ? z.top : z.bottom);
   if(!market && MathAbs(entry - ref) > InpMaxEntryDistATR * ltfAtr)
     { Reject("distance", "zone too far - waiting for the pullback"); return; }

   double buffer = ltfAtr * InpSlBufferATR;
   double sl;
   if(dir == DIR_BULL)
     {
      double base = z.bottom;
      if(sweep.idx >= 0) base = MathMin(base, sweep.extreme);
      sl = base - buffer;
     }
   else
     {
      double base = z.top;
      if(sweep.idx >= 0) base = MathMax(base, sweep.extreme);
      sl = base + buffer;
     }

   double risk = MathAbs(entry - sl);
   if(risk <= 0.0)                { Reject("rr", "degenerate stop"); return; }

   //--- STOP_FIXED: the stop is a number, not a structure. Always exactly
   //--- InpFixedSLPrice. This is what "my stop is 20 pips" literally means,
   //--- and it is the mode that reliably produces trades.
   if(InpStopMode == STOP_FIXED && InpFixedSLPrice > 0.0)
     {
      sl = NormalizeDouble((dir == DIR_BULL) ? entry - InpFixedSLPrice
                                             : entry + InpFixedSLPrice, g_digits);
      risk = InpFixedSLPrice;
     }
   else
     {
      //--- structure decides the stop; the guards only accept or reject it
      if(InpMinSLPrice > 0.0 && risk < InpMinSLPrice)
        {
         if(InpStopMode == STOP_CLAMP)
           {
            sl = NormalizeDouble((dir == DIR_BULL) ? entry - InpMinSLPrice
                                                   : entry + InpMinSLPrice, g_digits);
            risk = InpMinSLPrice;
           }
         else
           {
            Reject("sl_too_tight", StringFormat("stop $%.2f below the $%.2f floor",
                                                risk, InpMinSLPrice));
            return;
           }
        }
      if(InpMaxSLPrice > 0.0 && risk > InpMaxSLPrice)
        {
         Reject("sl_too_wide", StringFormat("stop $%.2f above the $%.2f cap",
                                            risk, InpMaxSLPrice));
         return;
        }
     }

   //--- "M1 dies to the spread": price the cost against the stop, not in isolation
   if(InpMaxSpreadRatio > 0.0)
     {
      double spreadPrice = (ask - bid);
      if(spreadPrice > risk * InpMaxSpreadRatio)
        {
         Reject("spread_ratio", StringFormat("spread $%.2f > %.0f%% of the $%.2f stop",
                                             spreadPrice, InpMaxSpreadRatio * 100.0, risk));
         return;
        }
     }

   //--- the target always follows the stop actually used, so 1:R holds
   double tp = (dir == DIR_BULL) ? entry + risk * InpTargetRR
                                 : entry - risk * InpTargetRR;
   double rr = InpTargetRR;

   entry = NormalizeDouble(entry, g_digits);
   sl    = NormalizeDouble(sl, g_digits);
   tp    = NormalizeDouble(tp, g_digits);

   double minDist = MinStopDistance();
   if(minDist > 0 && (MathAbs(entry - sl) < minDist || MathAbs(entry - tp) < minDist))
     { Reject("stops_level", "SL/TP inside the broker's minimum distance"); return; }

   double lots = CalcLots(entry, sl);
   if(lots <= 0.0)                { Reject("sizing", "computed volume below minimum"); return; }

   g_lastReject = "";
   g_signalCount++;
   PrintFormat("CrowConcept SIGNAL %s %s | entry=%.*f sl=%.*f tp=%.*f rr=1:%.1f lots=%.2f zone=%s",
               (dir == DIR_BULL ? "BUY" : "SELL"), (market ? "MARKET" : "LIMIT"),
               g_digits, entry, g_digits, sl, g_digits, tp, rr, lots, z.kind);

   if(g_dryRun)
     {
      Print("CrowConcept: dry run - order not sent");
      return;
     }

   bool ok = false;
   if(market)
     {
      ok = (dir == DIR_BULL) ? trade.Buy(lots, g_sym, 0.0, sl, tp, InpComment)
                             : trade.Sell(lots, g_sym, 0.0, sl, tp, InpComment);
     }
   else
     {
      if(dir == DIR_BULL && entry >= ask)
        { Reject("limit_side", "buy limit above the market"); return; }
      if(dir == DIR_BEAR && entry <= bid)
        { Reject("limit_side", "sell limit below the market"); return; }

      datetime expiry = TimeCurrent() + InpLimitExpiryBars * PeriodSeconds(InpLTF);
      ok = (dir == DIR_BULL)
           ? trade.BuyLimit(lots, entry, g_sym, sl, tp, ORDER_TIME_SPECIFIED, expiry, InpComment)
           : trade.SellLimit(lots, entry, g_sym, sl, tp, ORDER_TIME_SPECIFIED, expiry, InpComment);

      if(!ok)   // some brokers reject ORDER_TIME_SPECIFIED - fall back to GTC
        {
         ok = (dir == DIR_BULL)
              ? trade.BuyLimit(lots, entry, g_sym, sl, tp, ORDER_TIME_GTC, 0, InpComment)
              : trade.SellLimit(lots, entry, g_sym, sl, tp, ORDER_TIME_GTC, 0, InpComment);
        }
     }

   if(ok)
     {
      RememberRisk(trade.ResultOrder(), entry, risk);
      JournalOrder(trade.ResultOrder(), dir, market, entry, sl, tp, lots, z.kind, rr);
     }
   else
      PrintFormat("CrowConcept: order failed retcode=%d %s",
                  trade.ResultRetcode(), trade.ResultRetcodeDescription());
  }

//====================================================================
// Circuit breaker
//
// The daily risk gate already stops trading for the day, but it resets
// at midnight. That lets a bad day repeat itself unchanged. This lock
// does not reset: it stays until a human looks at what happened and
// releases it deliberately (InpUnlock=true on re-attach, or delete
// CC_LOCK_DAY in the Global Variables window, F3).
//====================================================================
double TodayKey()
  {
   MqlDateTime dt;
   TimeToStruct(TimeCurrent(), dt);
   return((double)(dt.year * 10000 + dt.mon * 100 + dt.day));
  }

bool IsLocked()
  {
   return(GlobalVariableCheck(LOCK_DAY_VAR));
  }

bool ReleaseLock()
  {
   if(!IsLocked())
      return(false);
   GlobalVariableDel(LOCK_DAY_VAR);
   GlobalVariableDel(LOCK_PCT_VAR);
   JournalWrite(StringFormat("{\"kind\":\"lock_released\",\"ts\":\"%s\"}",
                             IsoGmt(TimeCurrent())));
   return(true);
  }

//--- Returns true when the day's loss has tripped the breaker.
bool CircuitBreakerHit(double pnlToday)
  {
   if(InpHardStopPct <= 0.0)
      return(false);
   double balance = AccountInfoDouble(ACCOUNT_BALANCE);
   double base = balance - pnlToday;             // balance at the start of the day
   if(base <= 0.0)
      return(false);
   double lossPct = -pnlToday / base * 100.0;
   if(lossPct < InpHardStopPct)
      return(false);

   if(!IsLocked())
     {
      GlobalVariableSet(LOCK_DAY_VAR, TodayKey());
      GlobalVariableSet(LOCK_PCT_VAR, lossPct);
      PrintFormat("CrowConcept: *** CIRCUIT BREAKER *** daily loss %.2f%% >= %.2f%%. "
                  "Trading is locked until released.", lossPct, InpHardStopPct);
      JournalWrite(StringFormat(
         "{\"kind\":\"circuit_breaker\",\"ts\":\"%s\",\"loss_pct\":%.2f,\"loss\":%.2f}",
         IsoGmt(TimeCurrent()), lossPct, pnlToday));
      if(!InpHaltRequiresReview)
         ReleaseLock();                          // just sit out the day
     }
   return(true);
  }

//====================================================================
// Journal - written so the Python review tool can read it directly:
//   python3 -m crowcode review --journal <MQL5/Files>/crowcode_journal.jsonl
//====================================================================
string IsoGmt(datetime serverTime)
  {
   datetime gmt = serverTime - (TimeCurrent() - NowGmt());
   MqlDateTime dt;
   TimeToStruct(gmt, dt);
   return(StringFormat("%04d-%02d-%02dT%02d:%02d:%02d+00:00",
                       dt.year, dt.mon, dt.day, dt.hour, dt.min, dt.sec));
  }

void JournalWrite(string line)
  {
   if(!InpWriteJournal)
      return;
   int h = FileOpen(JOURNAL_FILE, FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI);
   if(h == INVALID_HANDLE)
      return;
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, line + "\r\n");
   FileClose(h);
  }

void JournalOrder(ulong ticket, int dir, bool market, double entry, double sl,
                  double tp, double lots, string zone, double rr)
  {
   JournalWrite(StringFormat(
      "{\"kind\":\"order\",\"ts\":\"%s\",\"result_ticket\":%I64u,\"side\":\"%s\","
      "\"type\":\"%s\",\"entry\":%.*f,\"sl\":%.*f,\"tp\":%.*f,\"volume\":%.2f,"
      "\"rr\":%.2f,\"reasons\":[\"%s\",\"%s\"]}",
      IsoGmt(TimeCurrent()), ticket, (dir == DIR_BULL ? "buy" : "sell"),
      (market ? "market" : "limit"),
      g_digits, entry, g_digits, sl, g_digits, tp, lots, rr,
      StringFormat("HTF(%s)", EnumToString(InpHTF)),
      StringFormat("POI: %s", zone)));
  }

//--- Called when a tracked position has disappeared: resolve it from history.
void JournalClosed(ulong ticket, double risk)
  {
   if(!InpWriteJournal)
      return;
   if(!HistorySelectByPosition(ticket))
      return;

   double pnl = 0.0, vol = 0.0;
   datetime opened = 0, closed = 0;
   int total = HistoryDealsTotal();
   for(int i = 0; i < total; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0)
         continue;
      pnl += HistoryDealGetDouble(d, DEAL_PROFIT)
           + HistoryDealGetDouble(d, DEAL_COMMISSION)
           + HistoryDealGetDouble(d, DEAL_SWAP);
      datetime t = (datetime)HistoryDealGetInteger(d, DEAL_TIME);
      if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN)
        {
         opened = t;
         vol = HistoryDealGetDouble(d, DEAL_VOLUME);
        }
      else if(t > closed)
         closed = t;
     }
   if(opened == 0 || vol <= 0.0)
      return;

   double riskMoney = risk * MoneyPerPriceUnit(vol);
   double r = (riskMoney > 0.0) ? pnl / riskMoney : 0.0;
   JournalWrite(StringFormat(
      "{\"kind\":\"closed\",\"ts\":\"%s\",\"ticket\":%I64u,\"pnl\":%.2f,\"r\":%.2f,"
      "\"volume\":%.2f,\"opened_at\":\"%s\",\"closed_at\":\"%s\"}",
      IsoGmt(TimeCurrent()), ticket, pnl, r, vol, IsoGmt(opened), IsoGmt(closed)));
  }

//====================================================================
// Position management: 2R breakeven, 3R partial
//====================================================================
//--- Initial risk must survive a breakeven move (and a terminal restart),
//--- so it is stored in a terminal global variable keyed by ticket.
string RiskVarName(ulong ticket) { return(StringFormat("CC_R_%I64u", ticket)); }
string DoneVarName(ulong ticket) { return(StringFormat("CC_P_%I64u", ticket)); }

void RememberRisk(ulong ticket, double entry, double risk)
  {
   if(ticket == 0)
      return;
   GlobalVariableSet(RiskVarName(ticket), risk);
  }

double RecallRisk(ulong ticket, double entry, double sl)
  {
   string name = RiskVarName(ticket);
   if(GlobalVariableCheck(name))
      return(GlobalVariableGet(name));
   double fallback = MathAbs(entry - sl);      // unknown (restart) - use current SL
   if(fallback > 0.0)
      GlobalVariableSet(name, fallback);
   return(fallback);
  }

void ForgetTicket(ulong ticket)
  {
   GlobalVariableDel(RiskVarName(ticket));
   GlobalVariableDel(DoneVarName(ticket));
  }

void ManageOpenPositions()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || !PositionSelectByTicket(ticket))
         continue;
      if(PositionGetString(POSITION_SYMBOL) != g_sym)        continue;
      if(PositionGetInteger(POSITION_MAGIC) != InpMagic)     continue;

      long   type   = PositionGetInteger(POSITION_TYPE);
      bool   isBuy  = (type == POSITION_TYPE_BUY);
      double entry  = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl     = PositionGetDouble(POSITION_SL);
      double tp     = PositionGetDouble(POSITION_TP);
      double vol    = PositionGetDouble(POSITION_VOLUME);
      double price  = isBuy ? SymbolInfoDouble(g_sym, SYMBOL_BID)
                            : SymbolInfoDouble(g_sym, SYMBOL_ASK);

      double risk = RecallRisk(ticket, entry, sl);
      if(risk <= 0.0)
         continue;

      double r = (isBuy ? (price - entry) : (entry - price)) / risk;

      //--- 2R: move stop to breakeven (never backwards)
      if(r >= InpBreakevenAtR)
        {
         double buf   = InpBeBufferPoints * g_point;
         double newSl = NormalizeDouble(isBuy ? entry + buf : entry - buf, g_digits);
         bool forward = (sl == 0.0) || (isBuy ? newSl > sl : newSl < sl);
         bool farEnough = MathAbs(price - newSl) >= MinStopDistance();
         if(forward && farEnough && !g_dryRun)
           {
            if(trade.PositionModify(ticket, newSl, tp))
               PrintFormat("CrowConcept: %I64u moved to breakeven at %.*f", ticket, g_digits, newSl);
           }
        }

      //--- 3R: take the partial once
      if(r >= InpPartialAtR && InpPartialFraction > 0.0 && !GlobalVariableCheck(DoneVarName(ticket)))
        {
         double part = NormalizeVolume(vol * InpPartialFraction);
         double rest = NormalizeDouble(vol - part, 2);
         double vmin = SymbolInfoDouble(g_sym, SYMBOL_VOLUME_MIN);
         if(part > 0.0 && rest >= vmin)
           {
            if(!g_dryRun && trade.PositionClosePartial(ticket, part))
              {
               GlobalVariableSet(DoneVarName(ticket), 1.0);
               PrintFormat("CrowConcept: %I64u partial close %.2f at %.1fR", ticket, part, r);
              }
           }
         else
            GlobalVariableSet(DoneVarName(ticket), 1.0);   // too small to split
        }
     }

   CleanupGlobals();
  }

//--- Drop stored risk for positions that no longer exist.
void CleanupGlobals()
  {
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
     {
      string name = GlobalVariableName(i);
      if(StringFind(name, "CC_R_") != 0 && StringFind(name, "CC_P_") != 0)
         continue;
      string idPart = StringSubstr(name, 5);
      ulong  ticket = (ulong)StringToInteger(idPart);
      if(ticket != 0 && !PositionSelectByTicket(ticket))
        {
         if(StringFind(name, "CC_R_") == 0)
            JournalClosed(ticket, GlobalVariableGet(name));   // record before forgetting
         GlobalVariableDel(name);
        }
     }
  }

//====================================================================
// Pending order housekeeping
//====================================================================
void ReviewPendingOrders()
  {
   MqlRates ltf[];
   bool haveRates = LoadRates(InpLTF, 5, ltf);
   double lastClose = haveRates ? ltf[ArraySize(ltf) - 1].close : 0.0;

   for(int i = OrdersTotal() - 1; i >= 0; i--)
     {
      ulong ticket = OrderGetTicket(i);
      if(ticket == 0 || !OrderSelect(ticket))
         continue;
      if(OrderGetString(ORDER_SYMBOL) != g_sym)      continue;
      if(OrderGetInteger(ORDER_MAGIC) != InpMagic)   continue;

      long     type    = OrderGetInteger(ORDER_TYPE);
      bool     isBuy   = (type == ORDER_TYPE_BUY_LIMIT || type == ORDER_TYPE_BUY_STOP);
      datetime setup   = (datetime)OrderGetInteger(ORDER_TIME_SETUP);
      datetime expires = (datetime)OrderGetInteger(ORDER_TIME_EXPIRATION);
      double   osl     = OrderGetDouble(ORDER_SL);

      //--- manual expiry for brokers that ignore ORDER_TIME_SPECIFIED
      if(expires == 0)
        {
         datetime deadline = setup + InpLimitExpiryBars * PeriodSeconds(InpLTF);
         if(TimeCurrent() >= deadline)
           {
            if(!g_dryRun && trade.OrderDelete(ticket))
               PrintFormat("CrowConcept: pending %I64u expired", ticket);
            continue;
           }
        }

      //--- premise broken: price already closed beyond the setup's stop
      if(haveRates && osl > 0.0)
        {
         bool broken = isBuy ? (lastClose < osl) : (lastClose > osl);
         if(broken)
           {
            if(!g_dryRun && trade.OrderDelete(ticket))
               PrintFormat("CrowConcept: pending %I64u cancelled - structure broken", ticket);
           }
        }
     }
  }
//+------------------------------------------------------------------+
