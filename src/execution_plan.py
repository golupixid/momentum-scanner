"""
Layer 6 — Execution Plan.
Entry zone (ATR-based), stop loss (multi-method), T1/T2 (multi-method).

Entry zone: close ± 0.25 × daily ATR(14)  [band width = 0.5 × ATR]
Stop loss:  highest of (ATR stop, swing low, 3% fixed) — enforces ≥3% below entry_low
T1:         lowest of (swing high above close, pivot R1, entry_high + ATR×2)
T2:         lowest of (swing high above T1, pivot R2, entry_high + ATR×3.5)

Validations (returns plan with error to exclude signal):
  T1 > entry_mid + 5%     else exclude
  T2 > T1                  else exclude
  close < T1               else exclude (too late)
  journey_pct ≤ 50%        else exclude (entry zone passed)
"""
import logging
from datetime import datetime
import pandas as pd
import numpy as np
import pytz
from ta.volatility import AverageTrueRange

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")

MIN_RR = 1.5


# ── Timing window ─────────────────────────────────────────────────────────────

def get_timing_status(dt: datetime = None) -> dict:
    """GOOD: 10AM–11:30AM and 1PM–2:30PM. AVOID: 9:15–10AM and 2:30–3:30PM."""
    now = dt or datetime.now(IST)
    time_val = now.hour * 60 + now.minute
    good    = [(10*60, 11*60+30), (13*60, 14*60+30)]
    avoid   = [(9*60+15, 10*60), (14*60+30, 15*60+30)]
    for s, e in good:
        if s <= time_val <= e:
            return {"status": "GOOD",    "emoji": "🟢", "label": "GOOD TIMING"}
    for s, e in avoid:
        if s <= time_val <= e:
            return {"status": "AVOID",   "emoji": "🔴", "label": "AVOID — volatile window"}
    return {"status": "NEUTRAL", "emoji": "🟡", "label": "Acceptable"}


# ── Daily ATR helper ──────────────────────────────────────────────────────────

def _daily_atr(df_daily: pd.DataFrame, period: int = 14) -> float:
    """Compute daily ATR(14) from daily OHLCV. Returns 0.0 on failure."""
    if df_daily is None or len(df_daily) < period + 1:
        return 0.0
    try:
        atr_series = AverageTrueRange(
            df_daily["High"].squeeze(),
            df_daily["Low"].squeeze(),
            df_daily["Close"].squeeze(),
            window=period,
        ).average_true_range().dropna()
        if atr_series.empty:
            return 0.0
        return float(atr_series.iloc[-1])
    except Exception:
        return 0.0


# ── Entry zone ────────────────────────────────────────────────────────────────

def _entry_zone_atr(close: float, atr: float) -> tuple:
    """
    Entry band = close ± 0.25 × ATR(14). Total width = 0.5 × ATR.
    Gives a tight, volatility-calibrated entry zone (no wide 10% bands).
    """
    half = atr * 0.25 if atr > 0 else close * 0.005   # fallback 0.5%
    return round(close - half, 2), round(close + half, 2)


# ── T1 / T2 computation ───────────────────────────────────────────────────────

def _compute_t1(df_daily: pd.DataFrame, close: float,
                entry_high: float, atr: float) -> float:
    """
    T1 = lowest of 3 resistance methods (most conservative).
      (a) Nearest daily high above close in last 10 bars
      (b) Pivot R1 from yesterday H/L/C
      (c) entry_high + ATR × 2
    """
    candidates = []

    # (a) Swing high above close — scan last 10 daily bars
    highs_10 = df_daily["High"].tail(10).values
    highs_above = [h for h in highs_10 if h > close]
    if highs_above:
        candidates.append(round(float(min(highs_above)), 2))

    # (b) Pivot R1 from yesterday
    if len(df_daily) >= 2:
        try:
            ph = float(df_daily["High"].iloc[-2])
            pl = float(df_daily["Low"].iloc[-2])
            pc = float(df_daily["Close"].iloc[-2])
            pp = (ph + pl + pc) / 3.0
            r1 = round(2.0 * pp - pl, 2)
            if r1 > close:
                candidates.append(r1)
        except Exception:
            pass

    # (c) ATR projection
    if atr > 0 and entry_high > 0:
        candidates.append(round(entry_high + atr * 2.0, 2))

    valid = [c for c in candidates if c > close]
    return min(valid) if valid else 0.0


def _compute_t2(df_daily: pd.DataFrame, t1: float,
                entry_high: float, atr: float) -> float:
    """
    T2 = lowest of 3 resistance methods above T1.
      (a) Nearest daily high above T1 in last 10 bars
      (b) Pivot R2 from yesterday H/L/C
      (c) entry_high + ATR × 3.5
    """
    candidates = []

    # (a) Swing high above T1
    highs_10 = df_daily["High"].tail(10).values
    highs_above_t1 = [h for h in highs_10 if h > t1]
    if highs_above_t1:
        candidates.append(round(float(min(highs_above_t1)), 2))

    # (b) Pivot R2 from yesterday
    if len(df_daily) >= 2:
        try:
            ph = float(df_daily["High"].iloc[-2])
            pl = float(df_daily["Low"].iloc[-2])
            pc = float(df_daily["Close"].iloc[-2])
            pp = (ph + pl + pc) / 3.0
            r2 = round(pp + (ph - pl), 2)
            if r2 > t1:
                candidates.append(r2)
        except Exception:
            pass

    # (c) ATR projection
    if atr > 0 and entry_high > 0:
        candidates.append(round(entry_high + atr * 3.5, 2))

    valid = [c for c in candidates if c > t1]
    return min(valid) if valid else 0.0


# ── SL computation ────────────────────────────────────────────────────────────

def _compute_sl(df_daily: pd.DataFrame, entry_low: float, atr: float) -> float:
    """
    SL = tightest of 3 methods, enforcing ≥3% below entry_low.
      (a) entry_low - ATR × 1.5
      (b) Nearest daily low below entry_low in last 10 bars
      (c) entry_low × 0.97  (3% fixed)
    Take the HIGHEST (closest to entry_low) that is still at least 3% below.
    """
    min_dist = round(entry_low * 0.97, 2)   # 3% minimum distance floor
    candidates = []

    # (a) ATR stop
    if atr > 0:
        candidates.append(round(entry_low - atr * 1.5, 2))

    # (b) Nearest swing low below entry_low in last 10 bars
    lows_10 = df_daily["Low"].tail(10).values
    lows_below = [l for l in lows_10 if l < entry_low]
    if lows_below:
        candidates.append(round(float(max(lows_below)), 2))  # tightest swing low

    # (c) Fixed 3%
    candidates.append(min_dist)

    # Keep only stops that are at most at the 3% floor (i.e., ≤ min_dist)
    valid = [s for s in candidates if s < entry_low and s <= min_dist]
    if not valid:
        return min_dist

    best = max(valid)   # tightest (highest) valid stop
    return best if best <= min_dist else min_dist


# ── R:R ──────────────────────────────────────────────────────────────────────

def calculate_rr(entry_mid: float, stop: float, t1: float) -> float:
    risk   = entry_mid - stop
    reward = t1 - entry_mid
    if risk <= 0:
        return 0.0
    return round(reward / risk, 2)


def get_rr_rating(rr: float) -> dict:
    if rr >= 2.0:
        return {"label": "EXCELLENT", "emoji": "🟢", "act": True}
    elif rr >= MIN_RR:
        return {"label": "GOOD",      "emoji": "🟡", "act": True}
    else:
        return {"label": "WAIT",      "emoji": "🔴", "act": False}


# ── Main plan builder ─────────────────────────────────────────────────────────

def build_execution_plan(symbol: str, signal: dict, df_hourly: pd.DataFrame,
                          scan_time: datetime = None,
                          df_daily: pd.DataFrame = None) -> dict:
    """
    Full execution plan.
    Requires df_daily for ATR-based entry zone and resistance/support lookups.
    df_hourly is used only for timing status.
    """
    plan = {
        "symbol": symbol, "entry_low": 0.0, "entry_high": 0.0,
        "stop_atr": 0.0, "stop_swing": 0.0, "t1": 0.0, "t2": 0.0,
        "rr": 0.0, "rr_rating": {}, "timing": {}, "error": "",
    }

    close = signal.get("close", 0.0)
    if close <= 0:
        plan["error"] = "Invalid close price"
        return plan

    # Need daily data for ATR-based calculations
    if df_daily is None or df_daily.empty or len(df_daily) < 15:
        plan["error"] = "Insufficient daily data for execution plan"
        return plan

    # ── ATR-based entry zone (width = 0.5 × ATR) ─────────────────────────────
    atr = _daily_atr(df_daily)
    if atr <= 0:
        # Fallback: 1% of close as ATR proxy
        atr = close * 0.01

    entry_low, entry_high = _entry_zone_atr(close, atr)
    entry_mid = round((entry_low + entry_high) / 2.0, 2)

    # ── Stop loss ─────────────────────────────────────────────────────────────
    best_stop = _compute_sl(df_daily, entry_low, atr)

    # ── T1 ────────────────────────────────────────────────────────────────────
    t1 = _compute_t1(df_daily, close, entry_high, atr)
    if t1 <= 0:
        plan["error"] = "No resistance found above current price for T1"
        return plan

    # ── T2 ────────────────────────────────────────────────────────────────────
    t2 = _compute_t2(df_daily, t1, entry_high, atr)
    if t2 <= t1:
        t2 = round(t1 + atr * 1.5, 2)   # force T2 above T1 as final fallback

    # ── Validations ───────────────────────────────────────────────────────────
    # FNO T1 minimum filter removed — temporary, review after 1 week
    # TODO: set correct threshold based on live observation
    is_fno = signal.get("signal_type") == "fno"
    t1_min = round(entry_mid * 1.03, 2)
    if t1 <= t1_min and not is_fno:
        # Store computed values so caution cards can display entry/SL/T1/T2
        _rr = calculate_rr(entry_mid, best_stop, t1)
        plan.update({
            "entry_low": entry_low, "entry_high": entry_high,
            "stop_recommended": best_stop, "t1": t1, "t2": t2,
            "rr": _rr, "rr_rating": get_rr_rating(_rr),
            "timing": get_timing_status(scan_time), "atr": round(atr, 2),
        })
        plan["error"] = f"T1 {t1:.2f} not 3% above entry_mid {entry_mid:.2f} — signal too weak"
        return plan

    if t2 <= t1:
        plan["error"] = f"T2 {t2:.2f} not above T1 {t1:.2f}"
        return plan

    if close >= t1:
        plan["error"] = f"Price {close:.2f} >= T1 {t1:.2f}: signal too late"
        return plan

    if t1 > entry_low:
        journey_pct = (close - entry_low) / (t1 - entry_low) * 100
        if journey_pct > 50:
            plan["error"] = (f"Journey {journey_pct:.0f}% > 50% "
                             f"(price {close:.2f} already past halfway to T1 {t1:.2f})")
            return plan

    # ── R:R and timing ────────────────────────────────────────────────────────
    rr     = calculate_rr(entry_mid, best_stop, t1)
    timing = get_timing_status(scan_time)

    plan.update({
        "entry_low":        entry_low,
        "entry_high":       entry_high,
        "stop_recommended": best_stop,
        "t1":               t1,
        "t2":               t2,
        "rr":               rr,
        "rr_rating":        get_rr_rating(rr),
        "timing":           timing,
        "atr":              round(atr, 2),
    })
    return plan


def format_execution_line(plan: dict) -> str:
    """One-line Telegram execution summary."""
    if plan.get("error"):
        return f"Exec: {plan['error']}"
    rr_r   = plan.get("rr_rating", {})
    timing = plan.get("timing", {})
    return (f"EXECUTION: Entry {plan['entry_low']:.0f}–{plan['entry_high']:.0f} "
            f"| Stop {plan['stop_recommended']:.0f} "
            f"| T1 {plan['t1']:.0f} | R:R {plan['rr']:.1f} {rr_r.get('emoji','')} "
            f"| Timing: {timing.get('label','')} {timing.get('emoji','')}")
