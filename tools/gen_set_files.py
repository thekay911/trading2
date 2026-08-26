"""프리셋(config.py) → MT5 .set 파일 생성기.

파이썬 프리셋과 EA 입력값이 어긋나는 것을 막는다.
값을 바꾸면 이 스크립트를 다시 돌리고 결과를 커밋할 것.

    python3 tools/gen_set_files.py
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crowcode.config import PRESETS, CrowConfig

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "mql5", "Presets")

# ENUM_TIMEFRAMES 의 실제 정수값 (MT5 .set 은 숫자로 저장한다)
TF_ENUM = {
    "M1": 1, "M5": 5, "M15": 15, "M30": 30,
    "H1": 16385, "H4": 16388, "D1": 16408,
}


def build(cfg: CrowConfig) -> str:
    lines = [
        f"; CrowConcept preset: {cfg.name}  (generated from crowcode/config.py)",
        f"; {cfg.htf} > {cfg.mtf} > {cfg.ltf} | risk {cfg.risk_pct}% | "
        f"stop ${cfg.min_sl_price}-${cfg.max_sl_price}",
        "; Load in MT5: EA properties -> Load, then set InpDryRun=true for the first weeks.",
        "",
        f"InpHTF={TF_ENUM[cfg.htf]}",
        f"InpMTF={TF_ENUM[cfg.mtf]}",
        f"InpLTF={TF_ENUM[cfg.ltf]}",
        f"InpSwingLeft={cfg.swing_left}",
        f"InpSwingRight={cfg.swing_right}",
        f"InpBreakOnClose={'true' if cfg.structure_break_on_close else 'false'}",
        f"InpRequireSweep={'true' if cfg.require_liquidity_sweep else 'false'}",
        f"InpRequireChoch={'true' if cfg.require_choch else 'false'}",
        f"InpSweepLookback={cfg.sweep_lookback}",
        f"InpUseOrderBlocks={'true' if 'order_block' in cfg.poi_types else 'false'}",
        f"InpUseFVG={'true' if 'fvg' in cfg.poi_types else 'false'}",
        f"InpMarketIfAtZone={'true' if cfg.market_if_at_poi else 'false'}",
        f"InpSlBufferATR={cfg.sl_buffer_atr}",
        f"InpMaxEntryDistATR={cfg.max_entry_distance_atr}",
        f"InpLimitExpiryBars={cfg.limit_expiry_bars}",
        f"InpMinSLPrice={cfg.min_sl_price}",
        f"InpMaxSLPrice={cfg.max_sl_price}",
        f"InpMaxSpreadRatio={cfg.max_spread_ratio}",
        f"InpRiskPercent={cfg.risk_pct}",
        f"InpMinRR={cfg.min_rr}",
        f"InpTargetRR={cfg.target_rr}",
        f"InpBreakevenAtR={cfg.breakeven_at_r}",
        f"InpPartialAtR={cfg.partial_at_r}",
        f"InpPartialFraction={cfg.partial_fraction}",
        f"InpMaxTradesPerDay={cfg.max_trades_per_day}",
        f"InpMaxConsecLosses={cfg.max_consecutive_losses}",
        f"InpMaxDailyLossPct={cfg.max_daily_loss_pct}",
        f"InpSession1Start={cfg.sessions[0].start_hour}",
        f"InpSession1End={cfg.sessions[0].end_hour}",
        f"InpSession2Start={cfg.sessions[1].start_hour if len(cfg.sessions) > 1 else 0.0}",
        f"InpSession2End={cfg.sessions[1].end_hour if len(cfg.sessions) > 1 else 0.0}",
        f"InpBlockFridayClose={'false' if cfg.trade_on_friday_close else 'true'}",
        f"InpNewsBeforeMin={cfg.news_blackout_before_min}",
        f"InpNewsAfterMin={cfg.news_blackout_after_min}",
        "InpDryRun=true",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, cfg in PRESETS.items():
        path = os.path.join(OUT_DIR, f"CrowConcept-{name}.set")
        with open(path, "w", encoding="utf-8", newline="\r\n") as fh:
            fh.write(build(cfg))
        print("wrote", os.path.relpath(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
