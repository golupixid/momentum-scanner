"""
Signal deduplication registry. Tracks active signals to prevent re-sending
and blocks symbols where a trade is still live (not yet hit T1, SL, or expired).
"""
import logging
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"
ACTIVE_SIGNALS_FILE = DATA_DIR / "active_signals" / "active_signals.csv"

# Max holding period per pattern (calendar days)
PATTERN_EXPIRY_DAYS = {
    "20D_HIGH": 15,
    "W_PATTERN": 20,
    "BB_BREAKOUT": 12,
    "EMA_CROSS": 15,
    "RETEST_RECOVERY": 20,
    "SHORT_COVER": 8,
    "LONG_UNWIND": 3,
}
DEFAULT_EXPIRY_DAYS = 10

COLUMNS = [
    "signal_id", "symbol", "signal_type", "pattern", "conviction",
    "entry", "sl", "t1", "t2", "scan_date", "scan_time",
    "expiry_date", "status",
]


def _load_registry() -> pd.DataFrame:
    if not ACTIVE_SIGNALS_FILE.exists():
        return pd.DataFrame(columns=COLUMNS)
    try:
        df = pd.read_csv(ACTIVE_SIGNALS_FILE)
        # Back-fill expiry_date column if missing (old data)
        if "expiry_date" not in df.columns:
            df["expiry_date"] = ""
        return df
    except Exception as e:
        logger.warning(f"Could not load registry: {e}")
        return pd.DataFrame(columns=COLUMNS)


def _save_registry(df: pd.DataFrame):
    ACTIVE_SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ACTIVE_SIGNALS_FILE, index=False)


def cleanup_and_get_blocked(daily_data: dict = None) -> set:
    """
    Fix 4: Read active_signals.csv, remove signals that have:
      - Hit T1 (current price >= t1)
      - Hit SL (current price <= sl)
      - Expired (today > expiry_date)
    Returns set of symbols that are still active (blocked from new signals).
    """
    df = _load_registry()
    if df.empty:
        return set()

    today_str = date.today().strftime("%Y-%m-%d")
    to_remove = []

    for idx, row in df.iterrows():
        if row.get("status") != "ACTIVE":
            to_remove.append(idx)
            continue

        symbol = str(row.get("symbol", ""))
        entry = float(row.get("entry", 0) or 0)
        t1 = float(row.get("t1", 0) or 0)
        sl = float(row.get("sl", 0) or 0)
        expiry = str(row.get("expiry_date", "") or "")

        # Remove invalid legacy signals where SL was stored above entry (pre-fix bug)
        if sl > 0 and entry > 0 and sl >= entry:
            logger.info(f"Registry: {symbol} has invalid SL (sl={sl:.2f} >= entry={entry:.2f}) — removing stale signal")
            to_remove.append(idx)
            continue

        # Check expiry
        if expiry and expiry <= today_str:
            logger.info(f"Registry: {symbol} expired ({expiry}) — removing")
            to_remove.append(idx)
            continue

        # Check T1 / SL hit using latest daily close
        if daily_data and symbol in daily_data:
            df_sym = daily_data[symbol]
            if df_sym is not None and not df_sym.empty:
                current_price = float(df_sym["Close"].iloc[-1])
                if t1 > 0 and current_price >= t1:
                    logger.info(f"Registry: {symbol} hit T1 ₹{t1:.2f} (curr ₹{current_price:.2f}) — removing")
                    to_remove.append(idx)
                    continue
                if sl > 0 and current_price <= sl:
                    logger.info(f"Registry: {symbol} hit SL ₹{sl:.2f} (curr ₹{current_price:.2f}) — removing")
                    to_remove.append(idx)
                    continue

    if to_remove:
        df = df.drop(index=to_remove).reset_index(drop=True)
        _save_registry(df)

    active_df = df[df["status"] == "ACTIVE"]
    blocked = set(active_df["symbol"].dropna().unique())
    logger.info(f"Active registry: {len(blocked)} symbols blocked from new signals: {sorted(blocked)}")
    return blocked


def is_duplicate(symbol: str, signal_type: str, pattern: str,
                  check_date: date = None) -> bool:
    """Return True if this symbol+signal_type+pattern was already sent today."""
    df = _load_registry()
    if df.empty:
        return False
    today = check_date or date.today()
    today_str = today.strftime("%Y-%m-%d")
    match = df[
        (df["symbol"] == symbol) &
        (df["signal_type"] == signal_type) &
        (df["pattern"] == pattern) &
        (df["scan_date"] == today_str) &
        (df["status"] == "ACTIVE")
    ]
    return not match.empty


def register_signal(signal: dict, plan: dict = None) -> str:
    """Register a new signal. Returns signal_id."""
    df = _load_registry()
    now_ist = datetime.now(IST)
    signal_id = str(uuid.uuid4())[:8].upper()
    pattern = signal.get("pattern", "")
    expiry_days = PATTERN_EXPIRY_DAYS.get(pattern, DEFAULT_EXPIRY_DAYS)
    expiry_date = (date.today() + timedelta(days=expiry_days)).strftime("%Y-%m-%d")

    entry = plan.get("entry_low", 0) if plan else 0
    sl = plan.get("stop_recommended", 0) if plan else 0
    t1 = plan.get("t1", 0) if plan else 0
    t2_estimate = plan.get("t2", 0) if plan else 0  # pre-computed risk-based T2

    new_row = {
        "signal_id": signal_id,
        "symbol": signal.get("symbol", ""),
        "signal_type": signal.get("signal_type", ""),
        "pattern": pattern,
        "conviction": signal.get("conviction", ""),
        "entry": entry,
        "sl": sl,
        "t1": t1,
        "t2": t2_estimate,
        "scan_date": now_ist.strftime("%Y-%m-%d"),
        "scan_time": now_ist.strftime("%H:%M"),
        "expiry_date": expiry_date,
        "status": "ACTIVE",
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    _save_registry(df)
    return signal_id


def dedup_signals(signals: list) -> list:
    """Filter out signals already sent today. Returns non-duplicate signals."""
    unique = []
    for sig in signals:
        sym = sig.get("symbol", "")
        stype = sig.get("signal_type", "")
        pattern = sig.get("pattern", "")
        if not is_duplicate(sym, stype, pattern):
            unique.append(sig)
        else:
            logger.debug(f"Dedup: {sym} {pattern} already sent today")
    return unique


def get_active_signals_today() -> list:
    """Return list of signal dicts active today."""
    df = _load_registry()
    if df.empty:
        return []
    today = date.today().strftime("%Y-%m-%d")
    today_df = df[(df["scan_date"] == today) & (df["status"] == "ACTIVE")]
    return today_df.to_dict("records")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Active today:", get_active_signals_today())
    blocked = cleanup_and_get_blocked()
    print("Blocked symbols:", blocked)
