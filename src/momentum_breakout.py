"""
Layer 4 — Momentum Breakout Signals (Q1).
Patterns: 20D High, W-Pattern, BB Breakout.
All require: Close > 20 EMA + Vol >= 1.3x + Supertrend bullish (10,3).
"""
import logging
import pandas as pd
import numpy as np
from src.indicators import (add_daily_indicators, add_weekly_indicators,
                             is_supertrend_bullish, is_volume_sufficient)

logger = logging.getLogger(__name__)

W_PATTERN_LOOKBACK = 30  # bars
W_TOLERANCE = {"Large": 0.025, "Mid": 0.035, "Small": 0.050}
VOL_MULTIPLIER = 1.3


def _base_conditions_met(df: pd.DataFrame) -> bool:
    """Check: Close > 20 EMA AND Vol >= 1.3x AND Supertrend bullish."""
    if df.empty:
        return False
    last = df.iloc[-1]
    close = float(last["Close"])
    ema20 = float(last["ema20"]) if "ema20" in df.columns and not pd.isna(last.get("ema20")) else None
    if ema20 is None or close <= ema20:
        return False
    if not is_volume_sufficient(df, VOL_MULTIPLIER):
        return False
    if not is_supertrend_bullish(df):
        return False
    return True


def check_20d_high_breakout(df: pd.DataFrame) -> dict:
    """
    Q1 Pattern 1: Close > highest high of last 20 bars (shifted to avoid lookahead).
    Returns {'triggered': bool, 'pattern': '20D_HIGH', 'close': float, 'high_20d': float}
    """
    result = {"triggered": False, "pattern": "20D_HIGH", "close": 0.0, "high_20d": 0.0}
    if df.empty or "high_20d" not in df.columns:
        return result
    if not _base_conditions_met(df):
        return result

    last = df.iloc[-1]
    close = float(last["Close"])
    high_20d = float(last["high_20d"]) if not pd.isna(last.get("high_20d", float("nan"))) else None
    if high_20d is None or high_20d <= 0:
        return result

    result["close"] = close
    result["high_20d"] = high_20d
    if close > high_20d:
        result["triggered"] = True
    return result


def check_w_pattern(df_daily: pd.DataFrame, df_weekly: pd.DataFrame,
                    cap_type: str = "Large") -> dict:
    """
    Q1 Pattern 2: W-Pattern (double bottom) neckline breakout.
    30 bar lookback. Tolerance by cap type. Returns signal details.
    Both daily+weekly confirmed = max conviction eligible.
    """
    result = {"triggered": False, "pattern": "W_PATTERN",
              "daily": False, "weekly": False, "both": False,
              "neckline": 0.0, "close": 0.0}

    if df_daily is None or df_daily.empty or len(df_daily) < W_PATTERN_LOOKBACK:
        return result

    if not _base_conditions_met(df_daily):
        return result

    tolerance = W_TOLERANCE.get(cap_type, 0.035)
    close = float(df_daily.iloc[-1]["Close"])
    result["close"] = close

    # Daily W-pattern check
    lows = df_daily["Low"].tail(W_PATTERN_LOOKBACK).values
    highs = df_daily["High"].tail(W_PATTERN_LOOKBACK).values

    daily_w = _detect_w_pattern(lows, highs, tolerance)
    if daily_w["detected"]:
        result["daily"] = True
        result["neckline"] = daily_w["neckline"]
        if close > daily_w["neckline"]:
            result["triggered"] = True

    # Weekly W-pattern check
    if df_weekly is not None and len(df_weekly) >= 10:
        w_lows = df_weekly["Low"].tail(W_PATTERN_LOOKBACK).values
        w_highs = df_weekly["High"].tail(W_PATTERN_LOOKBACK).values
        weekly_w = _detect_w_pattern(w_lows, w_highs, tolerance)
        if weekly_w["detected"] and df_weekly.iloc[-1]["Close"] > weekly_w["neckline"]:
            result["weekly"] = True

    result["both"] = result["daily"] and result["weekly"]
    return result


def _detect_w_pattern(lows: np.ndarray, highs: np.ndarray,
                       tolerance: float) -> dict:
    """
    Detect W-pattern: two roughly equal lows with a peak (neckline) between.
    Returns {'detected': bool, 'neckline': float, 'left_low': float, 'right_low': float}
    """
    result = {"detected": False, "neckline": 0.0, "left_low": 0.0, "right_low": 0.0}
    if len(lows) < 10:
        return result

    # Find all local lows
    local_lows = []
    for i in range(1, len(lows) - 1):
        if lows[i] < lows[i - 1] and lows[i] < lows[i + 1]:
            local_lows.append((i, lows[i]))

    if len(local_lows) < 2:
        return result

    # Try last two significant lows
    left_idx, left_low = local_lows[-2]
    right_idx, right_low = local_lows[-1]

    # Check lows are roughly equal
    avg_low = (left_low + right_low) / 2
    if avg_low <= 0:
        return result
    diff_pct = abs(left_low - right_low) / avg_low
    if diff_pct > tolerance:
        return result

    # Neckline = highest high between the two lows
    between_highs = highs[left_idx:right_idx + 1]
    if len(between_highs) == 0:
        return result
    neckline = float(np.max(between_highs))

    result["detected"] = True
    result["neckline"] = neckline
    result["left_low"] = left_low
    result["right_low"] = right_low
    return result


def check_bb_breakout(df: pd.DataFrame) -> dict:
    """
    Q1 Pattern 3: Close > upper Bollinger Band (20,2).
    Returns signal details.
    """
    result = {"triggered": False, "pattern": "BB_BREAKOUT", "close": 0.0, "bb_upper": 0.0}
    if df.empty or "bb_upper" not in df.columns:
        return result
    if not _base_conditions_met(df):
        return result

    last = df.iloc[-1]
    close = float(last["Close"])
    bb_upper = float(last["bb_upper"]) if not pd.isna(last.get("bb_upper", float("nan"))) else None
    if bb_upper is None:
        return result

    result["close"] = close
    result["bb_upper"] = bb_upper
    if close > bb_upper:
        result["triggered"] = True
    return result


def get_momentum_signals(symbol: str, df_daily: pd.DataFrame,
                          df_weekly: pd.DataFrame = None,
                          cap_type: str = "Large") -> list:
    """
    Run all 3 momentum pattern checks. Returns list of triggered signal dicts.
    Each dict: {symbol, pattern, close, vol_ratio, details, signal_type='momentum'}
    """
    if df_daily is None or df_daily.empty:
        return []

    df_daily = add_daily_indicators(df_daily)

    signals = []
    last = df_daily.iloc[-1]
    close = float(last["Close"])
    vol_ratio = float(last.get("vol_ratio", 0)) if "vol_ratio" in df_daily.columns else 0.0

    # Pattern 1: 20D High
    r1 = check_20d_high_breakout(df_daily)
    if r1["triggered"]:
        signals.append({
            "symbol": symbol, "pattern": "20D_HIGH", "close": close,
            "vol_ratio": vol_ratio, "details": r1, "signal_type": "momentum",
        })

    # Pattern 2: W-Pattern
    r2 = check_w_pattern(df_daily, df_weekly, cap_type)
    if r2["triggered"]:
        signals.append({
            "symbol": symbol, "pattern": "W_PATTERN", "close": close,
            "vol_ratio": vol_ratio, "details": r2, "signal_type": "momentum",
            "w_both": r2.get("both", False),
        })

    # Pattern 3: BB Breakout
    r3 = check_bb_breakout(df_daily)
    if r3["triggered"]:
        signals.append({
            "symbol": symbol, "pattern": "BB_BREAKOUT", "close": close,
            "vol_ratio": vol_ratio, "details": r3, "signal_type": "momentum",
        })

    return signals


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)
    df_d = yf.download("RELIANCE.NS", period="6mo", interval="1d",
                       auto_adjust=True, progress=False)
    df_w = yf.download("RELIANCE.NS", period="1y", interval="1wk",
                       auto_adjust=True, progress=False)
    sigs = get_momentum_signals("RELIANCE", df_d, df_w, "Large")
    print(f"Momentum signals for RELIANCE: {len(sigs)}")
    for s in sigs:
        print(f"  {s['pattern']}: close={s['close']:.2f}")
