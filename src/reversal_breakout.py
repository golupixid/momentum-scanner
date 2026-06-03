"""
Layer 4 — Reversal Signals (Q2).
Setup A: Fresh 9x16 EMA cross today + Close > 20 EMA + Vol >= 1.3x
Setup B: Retest Recovery (prior breakout + shakeout + recovery + fresh 9x16 cross)
Setup C1: Short Covering (FNO) — OI falling >5% + price rising + PCR>1.0 + Close>20EMA
Setup C2: Long Unwinding (FNO) — OI falling + price falling
"""
import logging
import pandas as pd
import numpy as np
from src.indicators import add_daily_indicators, is_volume_sufficient

logger = logging.getLogger(__name__)

VOL_MULTIPLIER = 1.3


def check_ema_cross_a(df: pd.DataFrame) -> dict:
    """
    Setup A: Fresh 9 EMA crossed ABOVE 16 EMA today (not already crossed).
    + Close > 20 EMA + Vol >= 1.3x
    """
    result = {"triggered": False, "setup": "A_EMA_CROSS", "close": 0.0, "vol_ratio": 0.0}
    if df.empty or "ema9" not in df.columns or "ema16" not in df.columns:
        return result
    if len(df) < 3:
        return result

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last["Close"])
    ema9_now = float(last["ema9"]) if not pd.isna(last.get("ema9", float("nan"))) else None
    ema16_now = float(last["ema16"]) if not pd.isna(last.get("ema16", float("nan"))) else None
    ema9_prev = float(prev["ema9"]) if not pd.isna(prev.get("ema9", float("nan"))) else None
    ema16_prev = float(prev["ema16"]) if not pd.isna(prev.get("ema16", float("nan"))) else None
    ema20 = float(last["ema20"]) if "ema20" in df.columns and not pd.isna(last.get("ema20", float("nan"))) else None

    if None in (ema9_now, ema16_now, ema9_prev, ema16_prev, ema20):
        return result

    # Fresh cross: 9 was below 16 yesterday, now above 16
    fresh_cross = (ema9_prev <= ema16_prev) and (ema9_now > ema16_now)
    above_20 = close > ema20
    vol_ok = is_volume_sufficient(df, VOL_MULTIPLIER)

    result["close"] = close
    result["vol_ratio"] = float(last["vol_ratio"]) if "vol_ratio" in df.columns else 0.0
    result["ema9"] = ema9_now
    result["ema16"] = ema16_now
    result["ema20"] = ema20

    if fresh_cross and above_20 and vol_ok:
        result["triggered"] = True

    return result


def check_retest_recovery_b(df: pd.DataFrame) -> dict:
    """
    Setup B: Retest Recovery.
    Requires: prior breakout level → shakeout below → recovery above → fresh 9x16 cross.
    Approximated by: 20D high breakout in last 20 bars + pullback below breakout + recovery + fresh cross.
    """
    result = {"triggered": False, "setup": "B_RETEST", "close": 0.0, "vol_ratio": 0.0}
    if df.empty or len(df) < 25:
        return result
    if "ema9" not in df.columns or "ema20" not in df.columns:
        return result

    # Need fresh EMA cross as prerequisite
    ema_cross = check_ema_cross_a(df)
    if not ema_cross["triggered"]:
        return result

    close_series = df["Close"].values
    high_series = df["High"].values

    # Look back 20 bars for a prior breakout (close > prior 20D high)
    lookback = 20
    if len(df) < lookback + 5:
        return result

    recent = df.tail(lookback + 5)
    close_rec = recent["Close"].values
    high_rec = recent["High"].values

    # Find if there was a prior high and a shakeout below it
    prior_high = np.max(high_rec[:-lookback]) if len(high_rec) > lookback else 0
    if prior_high <= 0:
        return result

    # Check for shakeout: any close below prior_high * 0.98 in recent bars
    shakeout = any(c < prior_high * 0.98 for c in close_rec[-10:])
    if not shakeout:
        return result

    # Current close recovered above prior high
    curr_close = float(df.iloc[-1]["Close"])
    if curr_close > prior_high:
        result["triggered"] = True
        result["close"] = curr_close
        result["vol_ratio"] = ema_cross["vol_ratio"]
        result["prior_high"] = prior_high

    return result


def check_short_covering_c1(df: pd.DataFrame, oi_data: dict = None,
                              pcr: float = None) -> dict:
    """
    Setup C1: Short Covering (FNO).
    OI falling >5% + price rising + PCR > 1.0 + Close > 20 EMA.
    oi_data: {'prev_oi': float, 'curr_oi': float}
    """
    result = {"triggered": False, "setup": "C1_SHORT_COVER", "close": 0.0,
              "oi_change_pct": 0.0, "pcr": pcr or 0.0}
    if df.empty or "ema20" not in df.columns:
        return result

    last = df.iloc[-1]
    close = float(last["Close"])
    ema20 = float(last["ema20"]) if not pd.isna(last.get("ema20", float("nan"))) else None
    if ema20 is None:
        return result

    result["close"] = close

    # Price rising (last 2 bars)
    price_rising = len(df) >= 2 and close > float(df.iloc[-2]["Close"])

    # Fix 5: OI falling > 2% (relaxed from 5% to catch volume-proxy signals too)
    oi_falling = False
    oi_change = 0.0
    if oi_data and oi_data.get("oi_change_pct") is not None:
        oi_change = float(oi_data["oi_change_pct"])
        oi_falling = oi_change < -2.0
    elif oi_data and oi_data.get("prev_oi", 0) > 0:
        prev_oi = float(oi_data["prev_oi"])
        curr_oi = float(oi_data["curr_oi"])
        oi_change = (curr_oi - prev_oi) / prev_oi * 100
        oi_falling = oi_change < -2.0

    result["oi_change_pct"] = oi_change

    # PCR > 1.0 (optional when using volume proxy — allow None to pass)
    pcr_ok = (pcr is None) or (pcr > 1.0)

    above_ema20 = close > ema20

    if oi_falling and price_rising and pcr_ok and above_ema20:
        result["triggered"] = True

    return result


def check_long_unwinding_c2(df: pd.DataFrame, oi_data: dict = None) -> dict:
    """
    Setup C2: Long Unwinding (FNO).
    OI falling + price falling. ACTIVE signal in Weak/Strong Bear regime.
    """
    result = {"triggered": False, "setup": "C2_LONG_UNWIND",
              "close": 0.0, "oi_change_pct": 0.0}
    if df.empty:
        return result

    last = df.iloc[-1]
    close = float(last["Close"])
    result["close"] = close

    # Price falling
    price_falling = len(df) >= 2 and close < float(df.iloc[-2]["Close"])

    # OI falling (any amount)
    oi_falling = False
    oi_change = 0.0
    if oi_data and oi_data.get("prev_oi", 0) > 0:
        prev_oi = oi_data["prev_oi"]
        curr_oi = oi_data["curr_oi"]
        oi_change = (curr_oi - prev_oi) / prev_oi * 100
        oi_falling = oi_change < 0

    result["oi_change_pct"] = oi_change

    if oi_falling and price_falling:
        result["triggered"] = True

    return result


def get_reversal_signals(symbol: str, df_daily: pd.DataFrame,
                          is_fno: bool = False, oi_data: dict = None,
                          pcr: float = None) -> list:
    """
    Run all reversal checks. Returns list of triggered signal dicts.
    """
    if df_daily is None or df_daily.empty:
        return []

    df_daily = add_daily_indicators(df_daily)
    signals = []
    last = df_daily.iloc[-1]
    close = float(last["Close"])
    vol_ratio = float(last["vol_ratio"]) if "vol_ratio" in df_daily.columns else 0.0

    # Setup A
    ra = check_ema_cross_a(df_daily)
    if ra["triggered"]:
        signals.append({
            "symbol": symbol, "setup": "A", "pattern": "EMA_CROSS",
            "close": close, "vol_ratio": vol_ratio,
            "details": ra, "signal_type": "reversal",
        })

    # Setup B
    rb = check_retest_recovery_b(df_daily)
    if rb["triggered"]:
        signals.append({
            "symbol": symbol, "setup": "B", "pattern": "RETEST_RECOVERY",
            "close": close, "vol_ratio": vol_ratio,
            "details": rb, "signal_type": "reversal",
        })

    # FNO-only signals
    if is_fno:
        rc1 = check_short_covering_c1(df_daily, oi_data, pcr)
        if rc1["triggered"]:
            signals.append({
                "symbol": symbol, "setup": "C1", "pattern": "SHORT_COVER",
                "close": close, "vol_ratio": vol_ratio,
                "details": rc1, "signal_type": "fno",
            })

        rc2 = check_long_unwinding_c2(df_daily, oi_data)
        if rc2["triggered"]:
            signals.append({
                "symbol": symbol, "setup": "C2", "pattern": "LONG_UNWIND",
                "close": close, "vol_ratio": vol_ratio,
                "details": rc2, "signal_type": "fno",
            })

    return signals


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)
    df = yf.download("HDFCBANK.NS", period="6mo", interval="1d",
                     auto_adjust=True, progress=False)
    sigs = get_reversal_signals("HDFCBANK", df, is_fno=True)
    print(f"Reversal signals for HDFCBANK: {len(sigs)}")
    for s in sigs:
        print(f"  {s['pattern']}: close={s['close']:.2f}")
