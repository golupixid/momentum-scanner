"""
5 sequential hard filters applied before any indicator calculation.
L1: Weekly EMA gate (done in weekly_gate.py, pre-applied here)
L2: Corporate actions (±1 trading day)
L2B: Sector bleeding (entire sector blocked, except FNO C2)
L3: Global bleeding (GIFT < -1.5% or 70%+ basket)
L4: Liquidity + quality (vol < 50k, price < 20, < 50 days data)
"""
import logging
from datetime import datetime, timedelta
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"
CORP_ACTIONS_PATH = DATA_DIR / "corporate_actions" / "actions.csv"

MIN_AVG_VOLUME = 50_000
MIN_PRICE = 20.0
MIN_DATA_DAYS = 50

BLOCKED_ACTION_TYPES = {"results", "dividend", "split", "bonus", "buyback", "rights"}
# AGM and EGM do NOT block

_corp_actions_cache = None


def load_corporate_actions() -> pd.DataFrame:
    global _corp_actions_cache
    if _corp_actions_cache is not None:
        return _corp_actions_cache
    if not CORP_ACTIONS_PATH.exists():
        return pd.DataFrame(columns=["symbol", "action_type", "action_date"])
    df = pd.read_csv(CORP_ACTIONS_PATH)
    df.columns = [c.strip().lower() for c in df.columns]
    df["action_date"] = pd.to_datetime(df["action_date"], errors="coerce")
    _corp_actions_cache = df
    return df


def _has_corporate_action_near(symbol: str, corp_actions: pd.DataFrame,
                                 today: datetime = None) -> bool:
    """Check if symbol has a blocking corporate action within ±1 trading day."""
    if corp_actions.empty:
        return False
    today = today or datetime.now(IST).replace(tzinfo=None)
    df = corp_actions[corp_actions["symbol"].str.upper() == symbol.upper()]
    if df.empty:
        return False
    for _, row in df.iterrows():
        action_type = str(row.get("action_type", "")).lower().strip()
        if action_type not in BLOCKED_ACTION_TYPES:
            continue
        action_date = row["action_date"]
        if pd.isna(action_date):
            continue
        diff = abs((action_date.date() - today.date()).days)
        if diff <= 1:
            return True
    return False


def filter_l2_corporate_actions(symbols: list, today: datetime = None) -> tuple:
    """Returns (passing, blocked) lists."""
    corp_actions = load_corporate_actions()
    today = today or datetime.now(IST).replace(tzinfo=None)
    passing, blocked = [], []
    for sym in symbols:
        if _has_corporate_action_near(sym, corp_actions, today):
            blocked.append(sym)
        else:
            passing.append(sym)
    return passing, blocked


def filter_l2b_sector_bleeding(symbols: list, symbol_sector_map: dict,
                                 sector_status: dict) -> tuple:
    """
    Block symbols whose sector is bleeding.
    Returns (passing, blocked).
    Note: FNO C2 signals are still generated even from blocked symbols —
    that logic is handled in fno_signals.py, not here.
    """
    passing, blocked = [], []
    for sym in symbols:
        sector = symbol_sector_map.get(sym, "Unknown")
        info = sector_status.get(sector, {})
        if info.get("bleeding", False):
            blocked.append(sym)
        else:
            passing.append(sym)
    return passing, blocked


def filter_l3_global_bleeding(symbols: list, global_bleeding: bool) -> tuple:
    """
    If global bleeding: block all symbols (FNO C2 exception handled downstream).
    Returns (passing, blocked).
    """
    if global_bleeding:
        return [], list(symbols)
    return list(symbols), []


def filter_l4_liquidity(symbols: list, daily_data: dict) -> tuple:
    """
    Filter: avg volume >= 50k, price >= Rs.20, >= 50 days of data.
    Returns (passing, blocked).
    """
    passing, blocked = [], []
    for sym in symbols:
        df = daily_data.get(sym)
        if df is None or df.empty:
            blocked.append(sym)
            continue
        if len(df) < MIN_DATA_DAYS:
            blocked.append(sym)
            continue
        close = df["Close"].dropna()
        volume = df["Volume"].dropna()
        if close.empty or volume.empty:
            blocked.append(sym)
            continue
        last_price = float(close.iloc[-1])
        avg_vol = float(volume.tail(20).mean())
        if last_price < MIN_PRICE or avg_vol < MIN_AVG_VOLUME:
            blocked.append(sym)
        else:
            passing.append(sym)
    return passing, blocked


def apply_all_filters(symbols: list, daily_data: dict, weekly_data: dict,
                       symbol_sector_map: dict, sector_status: dict,
                       global_status, today: datetime = None) -> dict:
    """
    Apply all 5 hard filters in sequence. Returns result dict with passing list
    and per-layer blocked lists for logging.
    Note: L1 (20W EMA weekly gate) is applied in weekly_gate.py before this.
    """
    from src.global_markets import GlobalStatus
    report = {"l1_excluded": [], "l2_blocked": [], "l2b_blocked": [],
              "l3_blocked": [], "l4_blocked": [], "passing": []}

    # L2: Corporate actions
    passing, l2_blocked = filter_l2_corporate_actions(symbols, today)
    report["l2_blocked"] = l2_blocked
    logger.info(f"L2 corp actions: {len(l2_blocked)} blocked, {len(passing)} remain")

    # L2B: Sector bleeding
    passing, l2b_blocked = filter_l2b_sector_bleeding(passing, symbol_sector_map, sector_status)
    report["l2b_blocked"] = l2b_blocked
    logger.info(f"L2B sector bleeding: {len(l2b_blocked)} blocked, {len(passing)} remain")

    # L3: Global bleeding
    is_bleeding = global_status.bleeding if hasattr(global_status, "bleeding") else bool(global_status)
    passing, l3_blocked = filter_l3_global_bleeding(passing, is_bleeding)
    report["l3_blocked"] = l3_blocked
    if is_bleeding:
        logger.warning(f"L3 global bleeding: ALL {len(l3_blocked)} blocked (FNO C2 exception applies)")

    # L4: Liquidity
    passing, l4_blocked = filter_l4_liquidity(passing, daily_data)
    report["l4_blocked"] = l4_blocked
    logger.info(f"L4 liquidity: {len(l4_blocked)} blocked, {len(passing)} pass")

    report["passing"] = passing
    logger.info(f"Total passing hard filters: {len(passing)}")
    return report


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    # Quick smoke test
    df = pd.DataFrame({"Close": [100.0]*60, "Volume": [200000]*60})
    daily_data = {"RELIANCE": df}
    p, b = filter_l4_liquidity(["RELIANCE", "MISSING"], daily_data)
    print(f"L4 test: passing={p}, blocked={b}")
