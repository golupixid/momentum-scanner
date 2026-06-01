"""
Layer 8 — Risk and Exit Structure.
Exit types: SL_HIT, T1_HIT, T2_HIT, TRAIL_EXIT, EXPIRED, STRUCTURE_EXIT,
            SENTIMENT_EXIT, TREND_EXIT.
Also monitors active signals for exit conditions.
"""
import logging
from datetime import datetime
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"

MAX_HOLD_DAYS = {
    "20D_HIGH": 15,
    "W_PATTERN": 20,
    "BB_BREAKOUT": 12,
    "EMA_CROSS": 15,
    "RETEST_RECOVERY": 20,
    "SHORT_COVER": 8,
    "LONG_UNWIND": 3,
}


def check_exit_conditions(signal: dict, current_price: float,
                           current_date: datetime = None) -> dict:
    """
    Check all exit conditions for an active signal.
    Returns {'exit': bool, 'type': str, 'action': str, 'urgency': str}
    """
    result = {"exit": False, "type": None, "action": None, "urgency": "normal"}
    if not signal or current_price <= 0:
        return result

    entry = float(signal.get("entry", 0))
    sl = float(signal.get("sl", 0))
    t1 = float(signal.get("t1", 0))
    t2 = float(signal.get("t2", t1 * 1.05) if t1 > 0 else 0)
    pattern = signal.get("pattern", "")
    signal_date = signal.get("scan_date", "")

    # SL Hit
    if sl > 0 and current_price <= sl:
        return {"exit": True, "type": "SL_HIT",
                "action": "Full exit immediately", "urgency": "urgent"}

    # T2 Hit
    if t2 > 0 and current_price >= t2:
        return {"exit": True, "type": "T2_HIT",
                "action": "Exit remaining 50%", "urgency": "normal"}

    # T1 Hit (partial — trail remainder)
    if t1 > 0 and current_price >= t1:
        return {"exit": True, "type": "T1_HIT",
                "action": "Exit 50% + trail SL to entry", "urgency": "normal"}

    # Max hold expired
    max_hold = MAX_HOLD_DAYS.get(pattern, 15)
    if signal_date:
        try:
            sig_date = datetime.strptime(signal_date, "%Y-%m-%d")
            now = (current_date or datetime.now()).replace(tzinfo=None)
            hold_days = (now - sig_date).days
            if hold_days >= max_hold:
                return {"exit": True, "type": "EXPIRED",
                        "action": "Exit at market — max hold reached", "urgency": "normal"}
        except Exception:
            pass

    return result


def check_structure_break(df_daily: pd.DataFrame) -> bool:
    """
    Tier 2: Structure break for momentum signals only.
    Close below 20 EMA daily.
    """
    if df_daily is None or df_daily.empty or "ema20" not in df_daily.columns:
        return False
    last = df_daily.iloc[-1]
    close = float(last["Close"])
    ema20 = float(last["ema20"]) if not pd.isna(last.get("ema20", float("nan"))) else close
    return close < ema20


def check_tier3_urgent(df_daily: pd.DataFrame) -> bool:
    """
    Tier 3: Urgent exit — price below 20EMA + RSI falling + high volume.
    """
    if df_daily is None or df_daily.empty:
        return False
    if "ema20" not in df_daily.columns or "rsi" not in df_daily.columns:
        return False
    if len(df_daily) < 2:
        return False
    last = df_daily.iloc[-1]
    prev = df_daily.iloc[-2]
    close = float(last["Close"])
    ema20 = float(last.get("ema20", close))
    rsi_now = float(last.get("rsi", 50))
    rsi_prev = float(prev.get("rsi", 50))
    vol_ratio = float(last.get("vol_ratio", 1.0))

    return close < ema20 and rsi_now < rsi_prev and vol_ratio > 1.5


def get_exit_label(signal_type: str, exit_type: str) -> str:
    """Map exit type to research journal label."""
    labels = {
        "SL_HIT": "SL_HIT",
        "T1_HIT": "T1_HIT",
        "T2_HIT": "T2_HIT",
        "TRAIL_EXIT": "TRAIL_EXIT",
        "EXPIRED": "EXPIRED",
        "STRUCTURE_EXIT": "STRUCTURE_EXIT",
        "SENTIMENT_EXIT": "SENTIMENT_EXIT",
        "TREND_EXIT": "TREND_EXIT",
    }
    return labels.get(exit_type, exit_type)


def format_exit_alert(symbol: str, exit_info: dict, current_price: float) -> str:
    """Format exit alert for Telegram."""
    exit_type = exit_info.get("type", "")
    action = exit_info.get("action", "")
    urgency = exit_info.get("urgency", "normal")
    emoji = "🚨" if urgency == "urgent" else "⚠️"
    return (f"{emoji} EXIT ALERT: {symbol}\n"
            f"Type: {exit_type} | Price: ₹{current_price:.2f}\n"
            f"Action: {action}")


if __name__ == "__main__":
    # Quick test
    sig = {"entry": 1000, "sl": 960, "t1": 1080, "t2": 1120,
           "pattern": "20D_HIGH", "scan_date": "2026-05-01"}
    result = check_exit_conditions(sig, 955.0)
    print("SL hit:", result)
    result2 = check_exit_conditions(sig, 1085.0)
    print("T1 hit:", result2)
