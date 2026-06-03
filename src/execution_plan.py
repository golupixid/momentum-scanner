"""
Layer 6 — Hourly Execution Plan (HIGH and MODERATE signals only).
Entry zone, stop placement, nearest resistance (T1), R:R calculation.
"""
import logging
from datetime import datetime
import pandas as pd
import numpy as np
import pytz
from src.indicators import add_hourly_indicators
from ta.volatility import AverageTrueRange

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

ATR_MULT = 1.5
MIN_RR = 1.5


def get_timing_status(dt: datetime = None) -> dict:
    """
    GOOD: 10AM-11:30AM and 1PM-2:30PM
    AVOID: 9:15-10AM and 2:30-3:30PM
    """
    now = dt or datetime.now(IST)
    hour = now.hour
    minute = now.minute
    time_val = hour * 60 + minute

    good_windows = [(10 * 60, 11 * 60 + 30), (13 * 60, 14 * 60 + 30)]
    avoid_windows = [(9 * 60 + 15, 10 * 60), (14 * 60 + 30, 15 * 60 + 30)]

    for s, e in good_windows:
        if s <= time_val <= e:
            return {"status": "GOOD", "emoji": "🟢", "label": "GOOD TIMING"}

    for s, e in avoid_windows:
        if s <= time_val <= e:
            return {"status": "AVOID", "emoji": "🔴", "label": "AVOID — volatile window"}

    return {"status": "NEUTRAL", "emoji": "🟡", "label": "Acceptable"}


def _get_entry_zone(df_hourly: pd.DataFrame) -> tuple:
    """
    Entry zone: convergence of 8 EMA and 20 EMA on hourly.
    Returns (entry_low, entry_high).
    """
    if df_hourly is None or df_hourly.empty:
        return (0.0, 0.0)
    df = add_hourly_indicators(df_hourly)
    last = df.iloc[-1]
    ema8 = float(last.get("ema8h", 0)) if "ema8h" in df.columns else 0
    ema20 = float(last.get("ema20h", 0)) if "ema20h" in df.columns else 0
    if ema8 <= 0 or ema20 <= 0:
        # Fallback: last 2 lows
        lows = df["Low"].tail(2).values
        return (float(min(lows)), float(max(lows)))
    return (round(min(ema8, ema20), 2), round(max(ema8, ema20), 2))


def _get_stop_loss(df_hourly: pd.DataFrame, entry: float) -> tuple:
    """
    Two stops: ATR(14)*1.5 below entry, and nearest hourly swing low.
    Returns (atr_stop, swing_stop).
    """
    if df_hourly is None or df_hourly.empty:
        return (0.0, 0.0)

    df = add_hourly_indicators(df_hourly)

    # ATR stop
    atr_val = 0.0
    if "atr_h" in df.columns:
        atr_series = df["atr_h"].dropna()
        if not atr_series.empty:
            atr_val = float(atr_series.iloc[-1])
    atr_stop = round(entry - ATR_MULT * atr_val, 2) if atr_val > 0 else 0.0

    # Swing low: lowest low in last 5 sessions (roughly 25-30 candles)
    swing_stop = 0.0
    if len(df) >= 5:
        lows = df["Low"].tail(30).values
        local_lows = [lows[i] for i in range(1, len(lows) - 1)
                      if lows[i] < lows[i-1] and lows[i] < lows[i+1]]
        if local_lows:
            swing_stop = round(float(max(local_lows)), 2)  # nearest swing low

    return (atr_stop, swing_stop)


def _get_t1_resistance(df_hourly: pd.DataFrame) -> float:
    """T1: Prior hourly high (last 5 sessions, ~25-30 candles)."""
    if df_hourly is None or df_hourly.empty:
        return 0.0
    highs = df_hourly["High"].tail(30)
    if highs.empty:
        return 0.0
    # Find last swing high
    highs_arr = highs.values
    swing_highs = [highs_arr[i] for i in range(1, len(highs_arr) - 1)
                   if highs_arr[i] > highs_arr[i-1] and highs_arr[i] > highs_arr[i+1]]
    if swing_highs:
        return round(float(max(swing_highs)), 2)
    return round(float(highs.max()), 2)


def calculate_rr(entry: float, stop: float, t1: float) -> float:
    """R:R = (T1 - entry) / (entry - stop)."""
    risk = entry - stop
    reward = t1 - entry
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def get_rr_rating(rr: float) -> dict:
    """Rate R:R: >=2.0 EXCELLENT, 1.5-2.0 GOOD, <1.5 WAIT."""
    if rr >= 2.0:
        return {"label": "EXCELLENT", "emoji": "🟢", "act": True}
    elif rr >= MIN_RR:
        return {"label": "GOOD", "emoji": "🟡", "act": True}
    else:
        return {"label": "WAIT", "emoji": "🔴", "act": False}


def build_execution_plan(symbol: str, signal: dict, df_hourly: pd.DataFrame,
                          scan_time: datetime = None) -> dict:
    """
    Full execution plan for a HIGH/MODERATE signal.
    Returns plan dict with entry, stop, T1, R:R, timing.
    """
    plan = {
        "symbol": symbol, "entry_low": 0.0, "entry_high": 0.0,
        "stop_atr": 0.0, "stop_swing": 0.0, "t1": 0.0,
        "rr_atr": 0.0, "rr_swing": 0.0, "rr_rating": {},
        "timing": {}, "error": "",
    }

    if df_hourly is None or df_hourly.empty:
        plan["error"] = "No hourly data"
        return plan

    close = signal.get("close", 0.0)
    if close <= 0:
        plan["error"] = "Invalid close price"
        return plan

    entry_low, entry_high = _get_entry_zone(df_hourly)
    if entry_low <= 0:
        entry_low = entry_high = close  # fallback

    entry_mid = (entry_low + entry_high) / 2

    # Fix 7: SL must be calculated from entry_low (lower band), not entry_mid
    stop_atr, stop_swing = _get_stop_loss(df_hourly, entry_low)
    t1 = _get_t1_resistance(df_hourly)

    # Only accept stops that are strictly BELOW entry_low (tightest valid stop)
    valid_stops = [s for s in [stop_atr, stop_swing] if 0 < s < entry_low]
    if valid_stops:
        best_stop = max(valid_stops)  # highest = tightest stop below entry_low
    else:
        best_stop = round(entry_low * 0.97, 2)  # 3% below entry_low as fallback

    # Validation: SL must be strictly less than entry_low — skip signal if not
    if best_stop >= entry_low:
        plan["error"] = "SL validation failed: stop not below entry zone lower band"
        return plan

    rr = calculate_rr(entry_mid, best_stop, t1)
    timing = get_timing_status(scan_time)

    plan.update({
        "entry_low": entry_low, "entry_high": entry_high,
        "stop_atr": stop_atr, "stop_swing": stop_swing,
        "stop_recommended": best_stop,
        "t1": t1, "rr": rr,
        "rr_rating": get_rr_rating(rr),
        "timing": timing,
    })

    return plan


def format_execution_line(plan: dict) -> str:
    """One-line Telegram execution summary."""
    if plan.get("error"):
        return f"⚠️ Exec: {plan['error']}"
    rr_r = plan["rr_rating"]
    timing = plan["timing"]
    return (f"📊 EXECUTION: Entry ₹{plan['entry_low']:.0f}-{plan['entry_high']:.0f} "
            f"| Stop ₹{plan['stop_recommended']:.0f} "
            f"| T1 ₹{plan['t1']:.0f} | R:R {plan['rr']:.1f} {rr_r.get('emoji','')} "
            f"| Timing: {timing.get('label','')} {timing.get('emoji','')}")


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)
    from datetime import datetime, timedelta
    end = datetime.now()
    start = end - timedelta(days=30)
    df = yf.download("RELIANCE.NS", start=start.strftime("%Y-%m-%d"),
                     end=end.strftime("%Y-%m-%d"), interval="1h",
                     auto_adjust=True, progress=False)
    plan = build_execution_plan("RELIANCE", {"close": 1350.0}, df)
    print(plan)
    print(format_execution_line(plan))
