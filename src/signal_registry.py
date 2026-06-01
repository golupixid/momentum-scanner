"""
Signal deduplication registry. Tracks active signals to prevent re-sending within same day.
Also manages signal IDs and active signal lifecycle.
"""
import logging
import uuid
from datetime import datetime, date
from pathlib import Path
import pandas as pd
import pytz

logger = logging.getLogger(__name__)
IST = pytz.timezone("Asia/Kolkata")
DATA_DIR = Path(__file__).parent.parent / "data"
ACTIVE_SIGNALS_FILE = DATA_DIR / "active_signals" / "active_signals.csv"

_registry_cache = None


def _load_registry() -> pd.DataFrame:
    global _registry_cache
    if not ACTIVE_SIGNALS_FILE.exists():
        return pd.DataFrame(columns=["signal_id", "symbol", "signal_type", "pattern",
                                      "conviction", "entry", "sl", "t1", "t2",
                                      "scan_date", "scan_time", "status"])
    df = pd.read_csv(ACTIVE_SIGNALS_FILE)
    _registry_cache = df
    return df


def _save_registry(df: pd.DataFrame):
    ACTIVE_SIGNALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ACTIVE_SIGNALS_FILE, index=False)


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

    new_row = {
        "signal_id": signal_id,
        "symbol": signal.get("symbol", ""),
        "signal_type": signal.get("signal_type", ""),
        "pattern": signal.get("pattern", ""),
        "conviction": signal.get("conviction", ""),
        "entry": plan.get("entry_low", 0) if plan else 0,
        "sl": plan.get("stop_recommended", 0) if plan else 0,
        "t1": plan.get("t1", 0) if plan else 0,
        "t2": 0,
        "scan_date": now_ist.strftime("%Y-%m-%d"),
        "scan_time": now_ist.strftime("%H:%M"),
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
    test_sig = {"symbol": "RELIANCE", "signal_type": "momentum", "pattern": "20D_HIGH"}
    print("Is duplicate:", is_duplicate("RELIANCE", "momentum", "20D_HIGH"))
    sid = register_signal(test_sig)
    print("Registered:", sid)
    print("Is duplicate now:", is_duplicate("RELIANCE", "momentum", "20D_HIGH"))
