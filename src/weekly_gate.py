"""
Layer 3 — Weekly Structural Gate.
ELIGIBLE: Close > 20W EMA by >2% → HIGH conviction possible
MARGINAL: Close > 20W EMA within 2% → MODERATE max cap
EXCLUDED: Close < 20W EMA → skip entirely
Also detects sector rotation: sector below 20D EMA last week → above this week.
"""
import logging
import pandas as pd
from src.indicators import add_weekly_indicators

logger = logging.getLogger(__name__)

GATE_MARGINAL_PCT = 2.0  # within 2% of 20W EMA = MARGINAL


def classify_weekly_gate(df_weekly: pd.DataFrame) -> dict:
    """
    Returns {'status': 'ELIGIBLE'|'MARGINAL'|'EXCLUDED', 'pct_above': float}
    Uses last bar of weekly data.
    """
    if df_weekly is None or df_weekly.empty or len(df_weekly) < 20:
        return {"status": "EXCLUDED", "pct_above": 0.0, "reason": "insufficient data"}

    df = add_weekly_indicators(df_weekly)
    if f"ema20w" not in df.columns or df["ema20w"].dropna().empty:
        return {"status": "EXCLUDED", "pct_above": 0.0, "reason": "ema calc failed"}

    last = df.iloc[-1]
    close = float(last["Close"])
    ema20w = float(last["ema20w"]) if not pd.isna(last["ema20w"]) else None

    if ema20w is None or ema20w <= 0:
        return {"status": "EXCLUDED", "pct_above": 0.0, "reason": "invalid ema"}

    pct_above = (close - ema20w) / ema20w * 100

    if pct_above < 0:
        return {"status": "EXCLUDED", "pct_above": round(pct_above, 2)}
    elif pct_above <= GATE_MARGINAL_PCT:
        return {"status": "MARGINAL", "pct_above": round(pct_above, 2)}
    else:
        return {"status": "ELIGIBLE", "pct_above": round(pct_above, 2)}


def apply_weekly_gate(universe_symbols: list, weekly_data: dict) -> dict:
    """
    Apply L1 weekly gate to all symbols.
    Returns {'eligible': [...], 'marginal': [...], 'excluded': [...], 'details': {symbol: gate_result}}
    """
    eligible, marginal, excluded = [], [], []
    details = {}

    for sym in universe_symbols:
        df = weekly_data.get(sym)
        result = classify_weekly_gate(df)
        details[sym] = result
        status = result["status"]
        if status == "ELIGIBLE":
            eligible.append(sym)
        elif status == "MARGINAL":
            marginal.append(sym)
        else:
            excluded.append(sym)

    logger.info(f"Weekly gate: {len(eligible)} eligible, {len(marginal)} marginal, {len(excluded)} excluded")
    return {"eligible": eligible, "marginal": marginal, "excluded": excluded,
            "passing": eligible + marginal, "details": details}


def get_conviction_cap(gate_status: str) -> str:
    """Return max allowed conviction based on weekly gate result."""
    if gate_status == "ELIGIBLE":
        return "HIGH"
    elif gate_status == "MARGINAL":
        return "MODERATE"
    return "NONE"


def detect_sector_rotation(sector: str, sector_weekly_prev: dict,
                            sector_weekly_curr: dict) -> bool:
    """
    True if sector was below 20D EMA last week and now above.
    sector_weekly_prev/curr: {sector_name: {'above_ema': bool, ...}}
    """
    prev = sector_weekly_prev.get(sector, {})
    curr = sector_weekly_curr.get(sector, {})
    was_below = not prev.get("above_ema", True)
    now_above = curr.get("above_ema", False)
    return was_below and now_above


if __name__ == "__main__":
    import yfinance as yf
    logging.basicConfig(level=logging.INFO)
    df = yf.download("RELIANCE.NS", period="1y", interval="1wk",
                     auto_adjust=True, progress=False)
    result = classify_weekly_gate(df)
    print(f"RELIANCE weekly gate: {result}")
